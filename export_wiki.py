#!/usr/bin/env python3
"""Export a Yandex Wiki page tree as local Markdown and attachment files."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path
from dataclasses import dataclass, replace
from http.client import IncompleteRead
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlencode, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen


API_URL = "https://api.wiki.yandex.net/v1"
API_HOST = "api.wiki.yandex.net"
WIKI_HOST = "wiki.yandex.ru"
REQUEST_TIMEOUT_SECONDS = 30
MAX_DOWNLOAD_ATTEMPTS = 10
INVALID_FILE_NAME_CHARACTERS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
MAX_STEM_LENGTH = 140


class WikiError(RuntimeError):
    """Raised when Wiki cannot provide a complete exportable response."""


def parse_wiki_url(value: str) -> str:
    """Return the decoded Wiki page slug from a browser URL."""
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != WIKI_HOST
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise WikiError("Expected an HTTPS URL from wiki.yandex.ru")
    slug = unquote(parsed.path).strip("/")
    if not slug:
        raise WikiError("Wiki page URL has no page path")
    return slug


@dataclass(frozen=True)
class Comment:
    id: int
    body: str
    author: str
    created_at: str
    parent_id: int | None
    thread_id: int | None
    inline_text: str
    resolve_status: str
    is_deleted: bool
    reactions: tuple[str, ...]


@dataclass(frozen=True)
class Attachment:
    id: int
    name: str
    description: str
    author: str
    created_at: str
    mimetype: str
    is_downloadable: bool
    download_url: str = ""
    page_id: int | None = None


@dataclass(frozen=True)
class WikiPage:
    id: int
    slug: str
    title: str
    page_type: str
    content: str
    attributes: dict
    owner: str
    comments: tuple[Comment, ...] = ()
    attachments: tuple[Attachment, ...] = ()


@dataclass(frozen=True)
class WikiTree:
    root_slug: str
    root_id: int
    pages_by_slug: dict[str, WikiPage]
    parent_by_slug: dict[str, str | None]
    path_only_slugs: list[str]


class WikiClient:
    """Small read-only client for the Yandex Wiki public API."""

    def __init__(self, token: str, organization_id: str, opener: Callable = urlopen):
        self.token = token
        self.organization_id = organization_id
        self.opener = opener

    def get_descendants(self, page_id: int) -> list[dict]:
        return self._get_results(f"/pages/{page_id}/descendants", {"show_all": "true"})

    def get_page_by_slug(self, slug: str) -> dict:
        return self._get_page("/pages", {"slug": slug})

    def get_page_by_id(self, page_id: int) -> dict:
        return self._get_page(f"/pages/{page_id}")

    def get_comments(self, page_id: int) -> list[dict]:
        comments = self._get_results(f"/pages/{page_id}/comments", {"order_by": "created_at"})
        by_id = {comment.get("id"): comment for comment in comments if isinstance(comment.get("id"), int)}
        for comment in list(by_id.values()):
            comment_id = comment["id"]
            thread = self._get_results(f"/pages/{page_id}/comments/{comment_id}/thread")
            for reply in thread:
                reply_id = reply.get("id")
                if isinstance(reply_id, int):
                    by_id[reply_id] = reply
        return sorted(by_id.values(), key=lambda comment: (str(comment.get("created_at", "")), comment["id"]))

    def get_attachments(self, page_id: int) -> list[dict]:
        return self._get_results(f"/pages/{page_id}/attachments")

    def download_attachment(self, attachment: Attachment) -> bytes:
        if attachment.page_id is None:
            raise WikiError(f"Attachment {attachment.id} has no page ID")
        path = "/pages/{}/attachments/{}/download".format(
            quote(str(attachment.page_id), safe=""), quote(str(attachment.id), safe="")
        )
        url = API_URL + path
        partial = b""
        for attempt in range(MAX_DOWNLOAD_ATTEMPTS):
            headers = self._headers()
            if partial:
                headers["Range"] = f"bytes={len(partial)}-"
            request = Request(url, headers=headers)
            try:
                with self.opener(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                    payload = response.read()
                if not isinstance(payload, bytes):
                    raise WikiError(f"Attachment {attachment.id} download returned invalid bytes")
                if partial and getattr(response, "status", 200) == 206:
                    return partial + payload
                return payload
            except HTTPError as error:
                if error.code not in (429, 500, 502, 503, 504) or attempt == MAX_DOWNLOAD_ATTEMPTS - 1:
                    raise WikiError(
                        f"Attachment {attachment.id} on page {attachment.page_id} download failed with HTTP {error.code}"
                    ) from error
            except IncompleteRead as error:
                partial += error.partial
                if attempt == MAX_DOWNLOAD_ATTEMPTS - 1:
                    raise WikiError(
                        f"Attachment {attachment.id} on page {attachment.page_id} download failed: {error}"
                    ) from error
            except (URLError, OSError) as error:
                if attempt == MAX_DOWNLOAD_ATTEMPTS - 1:
                    raise WikiError(
                        f"Attachment {attachment.id} on page {attachment.page_id} download failed: {error}"
                    ) from error
            time.sleep(0.5 * attempt)
        raise AssertionError("unreachable")

    def _get_page(self, path: str, parameters: dict[str, str] | None = None) -> dict:
        query = dict(parameters or {})
        query["fields"] = "content,attributes,owner,breadcrumbs,redirect"
        response = self._get_json(path, query)
        if not isinstance(response, dict):
            raise WikiError(f"Wiki API returned an invalid page response for {path}")
        return response

    def _get_results(self, path: str, parameters: dict[str, str] | None = None) -> list[dict]:
        parameters = dict(parameters or {})
        parameters["page_size"] = "100"
        results = []
        seen_cursors = set()
        while True:
            response = self._get_json(path, parameters)
            if not isinstance(response, dict) or not isinstance(response.get("results"), list):
                raise WikiError(f"Wiki API returned an invalid paginated response for {path}")
            page_results = response["results"]
            if not all(isinstance(item, dict) for item in page_results):
                raise WikiError(f"Wiki API returned a non-object result for {path}")
            results.extend(page_results)
            cursor = response.get("next_cursor")
            if cursor is None:
                return results
            if not isinstance(cursor, str) or not cursor:
                raise WikiError(f"Wiki API returned an invalid cursor for {path}")
            if cursor in seen_cursors:
                raise WikiError(f"Wiki API repeated a pagination cursor for {path}")
            seen_cursors.add(cursor)
            parameters["cursor"] = cursor

    def _get_json(self, path: str, parameters: dict[str, str] | None = None) -> object:
        query = urlencode(parameters or {})
        url = API_URL + path + (f"?{query}" if query else "")
        request = Request(url, headers=self._headers())
        try:
            with self.opener(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                payload = response.read()
        except HTTPError as error:
            raise WikiError(f"Wiki API request {path} failed with HTTP {error.code}") from error
        except URLError as error:
            raise WikiError(f"Wiki API request {path} failed: {error.reason}") from error
        try:
            return json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise WikiError(f"Wiki API returned invalid JSON for {path}") from error

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"OAuth {self.token}",
            "X-Cloud-Org-Id": self.organization_id,
        }


def load_wiki_tree(client: WikiClient, root_slug: str) -> WikiTree:
    """Load a root page, every accessible descendant, and page-local data."""
    root_data = client.get_page_by_slug(root_slug)
    root = page_from_response(root_data)
    descendants = client.get_descendants(root.id)
    pages = {root.slug: root}
    for descendant in descendants:
        page_id = descendant.get("id")
        if not isinstance(page_id, int):
            raise WikiError("Wiki API returned a descendant without an ID")
        page = page_from_response(client.get_page_by_id(page_id))
        if not is_descendant_slug(root.slug, page.slug):
            raise WikiError(f"Page {page.id} is outside the requested Wiki subtree")
        if page.slug in pages and pages[page.slug].id != page.id:
            raise WikiError(f"Wiki API returned conflicting pages for slug {page.slug}")
        pages[page.slug] = page

    enriched_pages = {}
    for slug, page in pages.items():
        comments = tuple(comment_from_response(value) for value in client.get_comments(page.id))
        attachments = tuple(
            replace(attachment_from_response(value), page_id=page.id)
            for value in client.get_attachments(page.id)
        )
        enriched_pages[slug] = WikiPage(
            **{**page.__dict__, "comments": comments, "attachments": attachments}
        )

    parent_by_slug, path_only_slugs = parent_map(root.slug, enriched_pages)
    return WikiTree(root.slug, root.id, enriched_pages, parent_by_slug, path_only_slugs)


def page_from_response(response: dict) -> WikiPage:
    identifier = response.get("id")
    slug = response.get("slug")
    title = response.get("title")
    page_type = response.get("page_type")
    content = response.get("content")
    if not isinstance(identifier, int) or not isinstance(slug, str) or not isinstance(title, str):
        raise WikiError("Wiki API returned a page without its required identity fields")
    if not isinstance(page_type, str) or not isinstance(content, str):
        raise WikiError(f"Wiki API did not return complete content for page {identifier}")
    attributes = response.get("attributes")
    owner = response.get("owner")
    return WikiPage(
        identifier,
        slug.strip("/"),
        title,
        page_type,
        content,
        attributes if isinstance(attributes, dict) else {},
        user_display_name(owner.get("user")) if isinstance(owner, dict) else "",
    )


def comment_from_response(response: dict) -> Comment:
    identifier = response.get("id")
    if not isinstance(identifier, int):
        raise WikiError("Wiki API returned a comment without an ID")
    parent_id = response.get("parent_id")
    thread_id = response.get("thread_id")
    return Comment(
        identifier,
        string_value(response.get("body")),
        user_display_name(response.get("author")),
        string_value(response.get("created_at")),
        parent_id if isinstance(parent_id, int) else None,
        thread_id if isinstance(thread_id, int) else None,
        string_value(response.get("inline_text")),
        string_value(response.get("resolve_status")),
        response.get("is_deleted") is True,
        tuple(
            string_value(reaction.get("type"))
            for reaction in response.get("reactions", [])
            if isinstance(reaction, dict) and string_value(reaction.get("type"))
        ),
    )


def attachment_from_response(response: dict) -> Attachment:
    identifier = response.get("id")
    name = response.get("name")
    if not isinstance(identifier, int) or not isinstance(name, str):
        raise WikiError("Wiki API returned an attachment without an ID or name")
    return Attachment(
        identifier,
        name,
        string_value(response.get("description")),
        user_display_name(response.get("user")),
        string_value(response.get("created_at")),
        string_value(response.get("mimetype")),
        response.get("is_downloadable") is True,
        string_value(response.get("download_url")),
    )


def parent_map(root_slug: str, pages: dict[str, WikiPage]) -> tuple[dict[str, str | None], list[str]]:
    parents = {root_slug: None}
    missing = []
    for slug in sorted(pages, key=lambda item: (item.count("/"), item)):
        if slug == root_slug:
            continue
        parent = direct_parent_slug(slug)
        while parent and parent != root_slug and parent not in pages and parent not in parents:
            grandparent = direct_parent_slug(parent)
            parents[parent] = grandparent
            missing.append(parent)
            parent = grandparent
        parents[slug] = direct_parent_slug(slug)
    return parents, missing


def direct_parent_slug(slug: str) -> str | None:
    parent, separator, _ = slug.rpartition("/")
    return parent if separator else None


def is_descendant_slug(root_slug: str, slug: str) -> bool:
    return slug == root_slug or slug.startswith(root_slug + "/")


def render_summary(page: WikiPage, attachments: list[tuple[Attachment, str]], children: list[tuple[str, str]]) -> str:
    """Render all currently available page data without rewriting its content."""
    fields = [("ID", str(page.id)), ("Ссылка", f"https://wiki.yandex.ru/{page.slug}/"), ("Тип", page.page_type)]
    if page.owner:
        fields.append(("Владелец", page.owner))
    fields.extend((name, markdown_value(value)) for name, value in sorted(page.attributes.items()))
    table = "\n".join(f"| {name} | {markdown_value(value)} |" for name, value in fields)
    attachment_lines = [
        f"- [{attachment.name}]({path})" + attachment_description(attachment)
        for attachment, path in attachments
    ]
    child_lines = [f"- [{title}]({path})" for title, path in children]
    return (
        f"<!-- yandex-wiki-export-root-id: {page.id} -->\n# {page.title}\n\n"
        f"| Поле | Значение |\n| --- | --- |\n{table}\n\n"
        f"## Содержимое\n\n{page.content or 'Нет содержимого.'}\n\n"
        f"## Вложения\n\n{chr(10).join(attachment_lines) or 'Нет вложений.'}\n\n"
        f"## Подстраницы\n\n{chr(10).join(child_lines) or 'Нет подстраниц.'}\n\n"
        f"## Комментарии\n\n{render_comments(page.comments)}\n"
    )


def render_comments(comments: tuple[Comment, ...]) -> str:
    if not comments:
        return "Нет комментариев."
    return "\n\n".join(render_comment(comment) for comment in comments)


def render_comment(comment: Comment) -> str:
    details = []
    if comment.parent_id is not None:
        details.append(f"Ответ на: {comment.parent_id}")
    if comment.inline_text:
        details.append(f"Привязка: {comment.inline_text}")
    if comment.resolve_status:
        details.append(f"Статус: {comment.resolve_status}")
    if comment.is_deleted:
        details.append("Удалён")
    if comment.reactions:
        details.append("Реакции: " + ", ".join(comment.reactions))
    suffix = "  \n" + "  \n".join(details) if details else ""
    return f"### Комментарий {comment.id}\n\n{comment.author or 'Неизвестный автор'} · {comment.created_at or 'Дата неизвестна'}{suffix}\n\n{comment.body}"


def attachment_description(attachment: Attachment) -> str:
    details = [value for value in (attachment.description, attachment.mimetype, attachment.created_at) if value]
    return " — " + "; ".join(details) if details else ""


def user_display_name(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    return string_value(value.get("display_name")) or string_value(value.get("username"))


def string_value(value: object) -> str:
    return value if isinstance(value, str) else ""


def markdown_value(value: object) -> str:
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    return string_value(value).replace("|", "\\|").replace("\n", "<br>") or "—"


def write_tree(
    tree: WikiTree,
    destination: Path,
    attachment_downloader: Callable[[Attachment], bytes],
) -> tuple[int, int, int]:
    """Write one already-loaded Wiki tree into an empty destination."""
    destination.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink():
        raise WikiError(f"Export destination is a symbolic link: {destination}")
    children_by_slug = {slug: [] for slug in tree.parent_by_slug}
    for slug, parent in tree.parent_by_slug.items():
        if parent is not None:
            children_by_slug.setdefault(parent, []).append(slug)
    for children in children_by_slug.values():
        children.sort()

    path_by_slug = {}
    write_node(
        tree.root_slug,
        destination,
        tree,
        children_by_slug,
        path_by_slug,
        attachment_downloader,
        None,
    )
    return (
        len(tree.pages_by_slug),
        sum(len(page.comments) for page in tree.pages_by_slug.values()),
        sum(len(page.attachments) for page in tree.pages_by_slug.values()),
    )


def write_node(
    slug: str,
    parent_directory: Path,
    tree: WikiTree,
    children_by_slug: dict[str, list[str]],
    path_by_slug: dict[str, Path],
    attachment_downloader: Callable[[Attachment], bytes],
    used_names: set[str] | None,
) -> None:
    page = tree.pages_by_slug.get(slug)
    base_name = safe_stem(page.title, page.id) if page else safe_path_segment(slug.rsplit("/", 1)[-1])
    directory_name = unique_name(base_name, page.id if page else None, used_names)
    directory = parent_directory / directory_name
    ensure_directory(directory)
    path_by_slug[slug] = directory
    children_directory = directory / "Подстраницы"
    attachments_directory = directory / "Вложения"
    ensure_directory(children_directory)
    ensure_directory(attachments_directory)

    child_names = set()
    for child_slug in children_by_slug.get(slug, []):
        write_node(
            child_slug,
            children_directory,
            tree,
            children_by_slug,
            path_by_slug,
            attachment_downloader,
            child_names,
        )

    if page is None:
        return
    attachment_links = write_attachments(page, attachments_directory, attachment_downloader)
    child_links = []
    for child_slug in children_by_slug.get(slug, []):
        child_page = tree.pages_by_slug.get(child_slug)
        if child_page is None:
            continue
        child_path = path_by_slug[child_slug]
        child_summary = child_path / summary_file_name(child_page)
        child_links.append((child_page.title, child_summary.relative_to(directory).as_posix()))
    summary = directory / summary_file_name(page)
    summary.write_text(render_summary(page, attachment_links, child_links), encoding="utf-8")


def ensure_directory(path: Path) -> None:
    if path.is_symlink():
        raise WikiError(f"Export path is a symbolic link: {path}")
    path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir():
        raise WikiError(f"Export path is not a directory: {path}")


def write_attachments(
    page: WikiPage,
    directory: Path,
    attachment_downloader: Callable[[Attachment], bytes],
) -> list[tuple[Attachment, str]]:
    links = []
    used_names = set()
    for attachment in page.attachments:
        if not attachment.is_downloadable:
            raise WikiError(f"Attachment {attachment.id} on page {page.id} is not downloadable")
        file_name = unique_name(safe_attachment_name(attachment), attachment.id, used_names)
        path = directory / file_name
        payload = attachment_downloader(attachment)
        if not isinstance(payload, bytes):
            raise WikiError(f"Attachment downloader returned non-bytes for attachment {attachment.id}")
        path.write_bytes(payload)
        links.append((attachment, f"Вложения/{file_name}"))
    return links


def safe_stem(title: str, page_id: int) -> str:
    return safe_path_segment(title) or str(page_id)


def safe_attachment_name(attachment: Attachment) -> str:
    safe_name = INVALID_FILE_NAME_CHARACTERS.sub("_", attachment.name).strip(" .")
    if not safe_name:
        return f"attachment-{attachment.id}"
    suffix = Path(safe_name).suffix
    stem = safe_name[: -len(suffix)] if suffix else safe_name
    return stem[: MAX_STEM_LENGTH - len(suffix)] + suffix


def safe_path_segment(value: str) -> str:
    return INVALID_FILE_NAME_CHARACTERS.sub("_", value).strip(" .")[:MAX_STEM_LENGTH]


def unique_name(name: str, identifier: int | None, used_names: set[str] | None) -> str:
    if used_names is None:
        return name
    normalized = name.casefold()
    if normalized not in used_names:
        used_names.add(normalized)
        return name
    suffix = f" ({identifier})" if identifier is not None else " (copy)"
    stem = Path(name).stem
    extension = Path(name).suffix
    candidate = stem[: MAX_STEM_LENGTH - len(extension) - len(suffix)] + suffix + extension
    index = 2
    while candidate.casefold() in used_names:
        indexed_suffix = f" ({identifier}-{index})" if identifier is not None else f" ({index})"
        candidate = stem[: MAX_STEM_LENGTH - len(extension) - len(indexed_suffix)] + indexed_suffix + extension
        index += 1
    used_names.add(candidate.casefold())
    return candidate


def summary_file_name(page: WikiPage) -> str:
    return f"{safe_stem(page.title, page.id)} — summary.md"


def publish_tree(
    tree: WikiTree,
    output_directory: Path,
    attachment_downloader: Callable[[Attachment], bytes],
) -> tuple[Path, tuple[int, int, int]]:
    """Stage an export, then replace only a previous export of the same root."""
    ensure_directory(output_directory)
    root_page = tree.pages_by_slug[tree.root_slug]
    root_name = safe_stem(root_page.title, root_page.id)
    target = output_directory / root_name
    existing = find_existing_exports(output_directory, tree.root_id)
    if target.exists() and target not in existing:
        raise WikiError(f"Output path already exists and is not this Wiki export: {target}")
    if len(existing) > 1:
        raise WikiError(f"More than one previous export has root ID {tree.root_id}")

    with tempfile.TemporaryDirectory(prefix=".wiki-export-", dir=output_directory) as staging_name:
        staging = Path(staging_name)
        counts = write_tree(tree, staging, attachment_downloader)
        staged_root = staging / root_name
        previous = existing[0] if existing else None
        backup = output_directory / f".{root_name}.backup"
        if backup.exists():
            raise WikiError(f"Previous interrupted export backup exists: {backup}")
        try:
            if previous is not None:
                previous.rename(backup)
            staged_root.rename(target)
        except OSError as error:
            if previous is not None and backup.exists() and not previous.exists():
                backup.rename(previous)
            raise WikiError(f"Could not publish Wiki export: {error}") from error
        if backup.exists():
            shutil.rmtree(backup)
    return target, counts


def find_existing_exports(output_directory: Path, root_id: int) -> list[Path]:
    marker = f"<!-- yandex-wiki-export-root-id: {root_id} -->"
    matches = []
    for candidate in output_directory.iterdir():
        if not candidate.is_dir() or candidate.is_symlink() or candidate.name.startswith("."):
            continue
        for summary in candidate.glob("* — summary.md"):
            if summary.is_file() and marker in summary.read_text(encoding="utf-8", errors="replace")[:512]:
                matches.append(candidate)
                break
    return matches


def parse_arguments(arguments: list[str] | None):
    parser = argparse.ArgumentParser(description="Export a Yandex Wiki page and all accessible descendants.")
    parser.add_argument("url", help="Wiki page URL, for example https://wiki.yandex.ru/space/page/")
    parser.add_argument("output_directory", nargs="?", type=Path, default=Path.cwd())
    return parser.parse_args(arguments)


def main(arguments: list[str] | None = None) -> int:
    parsed = parse_arguments(arguments)
    token = os.getenv("YANDEX_TRACKER_OAUTH_TOKEN")
    if not token:
        print("YANDEX_TRACKER_OAUTH_TOKEN is not set", file=sys.stderr)
        return 2
    organization_id = os.getenv("IDENTITY_HUB", "").strip()
    if not organization_id:
        print("IDENTITY_HUB is not set or is blank", file=sys.stderr)
        return 2
    try:
        slug = parse_wiki_url(parsed.url)
        client = WikiClient(token, organization_id)
        tree = load_wiki_tree(client, slug)
        path, counts = publish_tree(tree, parsed.output_directory, client.download_attachment)
    except (OSError, WikiError) as error:
        print(f"Wiki export failed: {error}", file=sys.stderr)
        return 1
    pages, comments, attachments = counts
    print(f"Exported {pages} pages, {comments} comments, and {attachments} attachments to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
