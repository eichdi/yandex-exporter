#!/usr/bin/env python3
"""Export a Yandex Tracker issue tree as Markdown files."""

import argparse
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.parse import urlparse
from urllib.request import Request, urlopen


API_URL = "https://api.tracker.yandex.net/v3"
TOKEN_ENVIRONMENT_VARIABLE = "YANDEX_TRACKER_OAUTH_TOKEN"
INVALID_FILE_NAME_CHARACTERS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
NEXT_PAGE_LINK = re.compile(r'<([^>]+)>;\s*rel="?next"?')


class TrackerError(RuntimeError):
    """Raised when Tracker cannot provide a valid response."""


@dataclass(frozen=True)
class Attachment:
    id: str
    name: str


@dataclass(frozen=True)
class Issue:
    key: str
    summary: str
    description: str
    children: tuple["Issue", ...] = ()
    status: str = ""
    issue_type: str = ""
    priority: str = ""
    assignee: str = ""
    author: str = ""
    created_at: str = ""
    updated_at: str = ""
    deadline: str = ""
    comments: tuple[str, ...] = ()
    attachments: tuple[Attachment, ...] = ()


class TrackerClient:
    """Minimal client for reading issues from Yandex Tracker."""

    def __init__(
        self, token: str, opener: Callable = urlopen, *, organization_id: str
    ):
        self.token = token
        self.opener = opener
        self.organization_id = organization_id

    def get_issue(self, key: str) -> Issue:
        query = urlencode({"expand": "attachments"})
        response, _ = self.request_json(f"/issues/{key}?{query}")
        return replace(issue_from_response(response, key), comments=self.get_comments(key))

    def get_comments(self, issue_key: str) -> tuple[str, ...]:
        path = f"/issues/{issue_key}/comments?perPage=100"
        comments = []
        while path:
            response, headers = self.request_json(path)
            if not isinstance(response, list):
                raise TrackerError(f"Comment request for {issue_key} returned an invalid response")
            comments.extend(comment_texts(response))
            path = next_page_path(headers)
        return tuple(comments)

    def download_attachment(self, issue_key: str, attachment: Attachment) -> bytes:
        path = "/issues/{}/attachments/{}/{}".format(
            quote(issue_key, safe=""),
            quote(attachment.id, safe=""),
            quote(attachment.name, safe=""),
        )
        return self.request_bytes(path)

    def get_child_issues(self, parent_key: str) -> list[Issue]:
        page = 1
        total_pages = 1
        issues = []
        while page <= total_pages:
            query = urlencode({"perPage": 100, "page": page})
            response, headers = self.request_json(
                f"/issues/_search?{query}",
                {"filter": {"parent": parent_key}, "order": "+key"},
            )
            if not isinstance(response, list):
                raise TrackerError(f"Child issue search for {parent_key} returned an invalid response")
            issues.extend(issue_from_response(issue, parent_key) for issue in response)
            total_pages = total_page_count(headers, parent_key)
            page += 1
        return issues

    def request_json(self, path: str, body: dict | None = None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = Request(
            f"{API_URL}{path}",
            data=data,
            headers={
                "Authorization": f"OAuth {self.token}",
                "Content-Type": "application/json",
                "X-Cloud-Org-ID": self.organization_id,
            },
            method="GET" if body is None else "POST",
        )
        try:
            with self.opener(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8")), response.headers
        except HTTPError as error:
            raise TrackerError(f"Tracker request failed for {path}: HTTP {error.code}") from error
        except URLError as error:
            raise TrackerError(f"Tracker request failed for {path}: {error.reason}") from error
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise TrackerError(f"Tracker request returned invalid JSON for {path}") from error

    def request_bytes(self, path: str) -> bytes:
        request = Request(
            f"{API_URL}{path}",
            headers={
                "Authorization": f"OAuth {self.token}",
                "X-Cloud-Org-ID": self.organization_id,
            },
        )
        try:
            with self.opener(request, timeout=30) as response:
                return response.read()
        except HTTPError as error:
            raise TrackerError(f"Attachment download failed for {path}: HTTP {error.code}") from error
        except URLError as error:
            raise TrackerError(f"Attachment download failed for {path}: {error.reason}") from error


def issue_from_response(response: object, context: str) -> Issue:
    if not isinstance(response, dict):
        raise TrackerError(f"Issue response for {context} is invalid")
    key = response.get("key")
    summary = response.get("summary")
    description = response.get("description")
    if not isinstance(key, str) or not isinstance(summary, str):
        raise TrackerError(f"Issue response for {context} has no key or summary")
    return Issue(
        key,
        summary,
        description if isinstance(description, str) else "",
        status=display_value(response.get("status")),
        issue_type=display_value(response.get("type")),
        priority=display_value(response.get("priority")),
        assignee=display_value(response.get("assignee")),
        author=display_value(response.get("createdBy")),
        created_at=string_value(response.get("createdAt")),
        updated_at=string_value(response.get("updatedAt")),
        deadline=string_value(response.get("deadline")),
        comments=comment_texts(response.get("comments")),
        attachments=attachments_from_response(response.get("attachments")),
    )


def display_value(value: object) -> str:
    if isinstance(value, dict):
        return string_value(value.get("display"))
    return ""


def string_value(value: object) -> str:
    return value if isinstance(value, str) else ""


def comment_texts(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(
        comment_text(comment)
        for comment in value
        if comment_text(comment)
    )


def comment_text(comment: object) -> str:
    if not isinstance(comment, dict):
        return ""
    return string_value(comment.get("text")) or display_value(comment)


def next_page_path(headers: object) -> str | None:
    if not hasattr(headers, "get"):
        return None
    link = headers.get("Link", "")
    match = NEXT_PAGE_LINK.search(link) if isinstance(link, str) else None
    if match is None:
        return None
    parsed_url = urlparse(match.group(1))
    if parsed_url.path.startswith("/v3/"):
        path = parsed_url.path.removeprefix("/v3")
        return f"{path}?{parsed_url.query}" if parsed_url.query else path
    api_path = match.group(1).find("/v3/")
    if api_path >= 0:
        return match.group(1)[api_path + len("/v3"):]
    raise TrackerError("Comment pagination link has an invalid URL")


def attachments_from_response(value: object) -> tuple[Attachment, ...]:
    if not isinstance(value, list):
        return ()
    attachments = []
    for attachment in value:
        if not isinstance(attachment, dict):
            continue
        identifier = attachment.get("id")
        name = attachment.get("name") or attachment.get("display")
        if isinstance(identifier, (str, int)) and isinstance(name, str):
            attachments.append(Attachment(str(identifier), name))
    return tuple(attachments)


def total_page_count(headers: object, parent_key: str) -> int:
    try:
        total_pages = int(headers.get("X-Total-Pages", "1"))
    except (AttributeError, TypeError, ValueError) as error:
        raise TrackerError(f"Child issue search for {parent_key} returned an invalid page count") from error
    if total_pages < 0:
        raise TrackerError(f"Child issue search for {parent_key} returned an invalid page count")
    return total_pages


def load_issue_tree(client: TrackerClient, key: str) -> Issue:
    issue = client.get_issue(key)
    children = tuple(load_issue_tree(client, child.key) for child in client.get_child_issues(key))
    return replace(issue, children=children)


def safe_stem(issue: Issue) -> str:
    summary = INVALID_FILE_NAME_CHARACTERS.sub("_", issue.summary).strip(" .")
    return f"{issue.key} — {summary or 'untitled'}"


def write_issue_tree(
    issue: Issue,
    output_directory: Path,
    attachment_downloader: Callable[[str, Attachment], bytes] | None = None,
) -> None:
    output_directory.mkdir(parents=True, exist_ok=True)
    remove_existing_export(output_directory, issue.key)
    write_issue(issue, output_directory, attachment_downloader)


def remove_existing_export(output_directory: Path, issue_key: str) -> None:
    prefix = f"{issue_key} — "
    for path in output_directory.iterdir():
        if path.name.startswith(prefix) and (path.is_dir() or path.suffix == ".md"):
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()


def write_issue(
    issue: Issue,
    directory: Path,
    attachment_downloader: Callable[[str, Attachment], bytes] | None,
) -> None:
    stem = safe_stem(issue)
    issue_directory = directory / stem
    issue_directory.mkdir()
    (issue_directory / f"{stem}.md").write_text(render_issue(issue), encoding="utf-8")
    write_attachments(issue, issue_directory / "Вложения", attachment_downloader)
    child_directory = issue_directory / "Подзадачи"
    child_directory.mkdir()
    for child in issue.children:
        write_issue(child, child_directory, attachment_downloader)


def write_attachments(
    issue: Issue,
    attachments_directory: Path,
    attachment_downloader: Callable[[str, Attachment], bytes] | None,
) -> None:
    attachments_directory.mkdir()
    if issue.attachments and attachment_downloader is None:
        raise TrackerError(f"Attachment downloader is required for issue {issue.key}")
    for attachment in issue.attachments:
        path = unique_attachment_path(attachments_directory, attachment)
        path.write_bytes(attachment_downloader(issue.key, attachment))


def unique_attachment_path(directory: Path, attachment: Attachment) -> Path:
    name = safe_attachment_name(attachment)
    candidate = directory / name
    index = 2
    while candidate.exists():
        candidate = directory / f"{Path(name).stem} ({index}){Path(name).suffix}"
        index += 1
    return candidate


def safe_attachment_name(attachment: Attachment) -> str:
    name = INVALID_FILE_NAME_CHARACTERS.sub("_", Path(attachment.name).name).strip(" .")
    return name or f"attachment-{attachment.id}"


def render_issue(issue: Issue) -> str:
    fields = (
        ("Статус", issue.status),
        ("Тип", issue.issue_type),
        ("Приоритет", issue.priority),
        ("Исполнитель", issue.assignee),
        ("Автор", issue.author),
        ("Создана", issue.created_at),
        ("Обновлена", issue.updated_at),
        ("Дедлайн", issue.deadline),
    )
    rows = "\n".join(f"| {name} | {markdown_value(value)} |" for name, value in fields)
    comments = render_comments(issue.comments)
    return (
        f"# {issue.key} — {issue.summary}\n\n"
        f"| Поле | Значение |\n| --- | --- |\n{rows}\n\n"
        f"## Описание\n\n{issue.description or 'Нет описания.'}\n\n"
        f"## Комментарии\n\n{comments}\n"
    )


def markdown_value(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>") or "—"


def render_comments(comments: tuple[str, ...]) -> str:
    if not comments:
        return "Нет комментариев."
    return "\n\n".join(f"### {index}\n\n{comment}" for index, comment in enumerate(comments, 1))


def parse_arguments(arguments: Sequence[str] | None):
    parser = argparse.ArgumentParser(
        description="Export a Yandex Tracker issue and all nested child issues."
    )
    parser.add_argument("issue_key", help="Key of the root issue, for example ABC-123")
    parser.add_argument(
        "output_directory",
        nargs="?",
        type=Path,
        help="Directory for exported files; defaults to the current directory",
    )
    return parser.parse_args(arguments)


def main(arguments: Sequence[str] | None = None) -> int:
    parsed_arguments = parse_arguments(arguments)
    token = os.getenv(TOKEN_ENVIRONMENT_VARIABLE)
    if not token:
        print(f"{TOKEN_ENVIRONMENT_VARIABLE} is not set", file=sys.stderr)
        return 2
    organization_id = os.getenv("IDENTITY_HUB", "").strip()
    if not organization_id:
        print("IDENTITY_HUB is not set or is blank", file=sys.stderr)
        return 2
    output_directory = parsed_arguments.output_directory or Path.cwd()
    try:
        client = TrackerClient(token, organization_id=organization_id)
        issue = load_issue_tree(client, parsed_arguments.issue_key)
        write_issue_tree(issue, output_directory, client.download_attachment)
    except (OSError, TrackerError) as error:
        print(f"Export failed: {error}", file=sys.stderr)
        return 1
    print(f"Exported {issue.key} to {output_directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
