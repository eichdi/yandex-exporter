"""Read Yandex Tracker and Wiki data without creating local exports."""

import json
import os
import re
from dataclasses import asdict, replace
from typing import Any
from urllib.parse import quote

from mcp.types import CallToolResult

from attachment_content import attachment_result

from export_tracker import TrackerClient, TrackerError, load_issue_tree
from export_wiki import (
    WikiClient,
    WikiError,
    attachment_from_response,
    comment_from_response,
    load_wiki_tree,
    page_from_response,
    parse_wiki_url,
)


ISSUE_KEY = re.compile(r"[A-Za-z][A-Za-z0-9_]*-[0-9]+")


def validate_issue_key(value: str) -> str:
    if not ISSUE_KEY.fullmatch(value):
        raise ValueError("Expected a Tracker issue key, for example ABC-123")
    return value


def json_object(value) -> dict[str, Any]:
    """Convert dataclasses and their tuple fields to JSON-compatible objects."""
    return json.loads(json.dumps(asdict(value), ensure_ascii=False))


class YandexReader:
    """Adapt the existing read-only clients to MCP tool results."""

    def __init__(self, tracker: TrackerClient, wiki: WikiClient):
        self.tracker = tracker
        self.wiki = wiki

    @classmethod
    def from_environment(cls) -> "YandexReader":
        token = os.environ.get("YANDEX_TRACKER_OAUTH_TOKEN", "").strip()
        organization = os.environ.get("IDENTITY_HUB", "").strip()
        if not token:
            raise ValueError("YANDEX_TRACKER_OAUTH_TOKEN is not set")
        if not organization:
            raise ValueError("IDENTITY_HUB must not be blank")
        return cls(TrackerClient(token, organization_id=organization), WikiClient(token, organization))

    def get_tracker_issue(self, issue_key: str) -> dict[str, Any]:
        return json_object(self.tracker.get_issue(validate_issue_key(issue_key)))

    def get_tracker_attachment(self, issue_key: str, attachment_id: str) -> CallToolResult:
        validate_issue_key(issue_key)
        if not attachment_id.strip():
            raise ValueError("Attachment ID must not be blank")
        issue = self.tracker.get_issue(issue_key)
        attachment = next((item for item in issue.attachments if item.id == attachment_id), None)
        if attachment is None:
            raise TrackerError("Attachment not found on the requested issue")
        uri = "yandex-tracker://issues/" + quote(issue_key, safe="") + "/attachments/"
        uri += quote(attachment.id, safe="") + "/" + quote(attachment.name, safe="")
        return attachment_result(attachment.name, self.tracker.download_attachment(issue_key, attachment), uri)

    def list_tracker_children(self, issue_key: str) -> dict[str, Any]:
        children = self.tracker.get_child_issues(validate_issue_key(issue_key))
        return {"parent_key": issue_key, "children": [
            {"key": child.key, "summary": child.summary, "status": child.status} for child in children
        ]}

    def get_tracker_issue_with_children(self, issue_key: str) -> dict[str, Any]:
        return json_object(load_issue_tree(self.tracker, validate_issue_key(issue_key)))

    def get_wiki_page(self, url: str) -> dict[str, Any]:
        page = page_from_response(self.wiki.get_page_by_slug(parse_wiki_url(url)))
        comments = tuple(comment_from_response(value) for value in self.wiki.get_comments(page.id))
        attachments = tuple(
            replace(attachment_from_response(value), page_id=page.id)
            for value in self.wiki.get_attachments(page.id)
        )
        return json_object(replace(page, comments=comments, attachments=attachments))

    def get_wiki_attachment(self, url: str, attachment_id: int) -> CallToolResult:
        slug = parse_wiki_url(url)
        if attachment_id <= 0:
            raise ValueError("Attachment ID must be positive")
        page = page_from_response(self.wiki.get_page_by_slug(slug))
        attachment = next((attachment_from_response(item) for item in self.wiki.get_attachments(page.id)
                           if item.get("id") == attachment_id), None)
        if attachment is None:
            raise WikiError("Attachment not found on the requested page")
        if not attachment.is_downloadable:
            raise WikiError("Attachment is not downloadable")
        attachment = replace(attachment, page_id=page.id)
        uri = "yandex-wiki://pages/" + str(page.id) + "/attachments/" + str(attachment.id)
        uri += "/" + quote(attachment.name, safe="")
        return attachment_result(attachment.name, self.wiki.download_attachment(attachment), uri, attachment.mimetype)

    def list_wiki_descendants(self, url: str) -> dict[str, Any]:
        page = page_from_response(self.wiki.get_page_by_slug(parse_wiki_url(url)))
        descendants = [
            page_from_response(self.wiki.get_page_by_id(item["id"]))
            for item in self.wiki.get_descendants(page.id)
        ]
        return {"root_id": page.id, "root_slug": page.slug, "descendants": [
            {"id": item.id, "slug": item.slug, "title": item.title} for item in descendants
        ]}

    def get_wiki_page_with_children(self, url: str) -> dict[str, Any]:
        return json_object(load_wiki_tree(self.wiki, parse_wiki_url(url)))
