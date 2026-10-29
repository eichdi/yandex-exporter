"""Read Yandex Tracker and Wiki data without creating local exports."""

import json
import os
import re
from dataclasses import asdict, replace
from typing import Any

from export_tracker import TrackerClient, load_issue_tree
from export_wiki import (
    WikiClient,
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
