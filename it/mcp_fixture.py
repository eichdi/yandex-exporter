"""Launch the production server with deterministic API responses over stdio."""

import io
import json
import runpy
import sys
from functools import partial
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from export_tracker import TrackerClient
from export_wiki import WikiClient


class Response:
    def __init__(self, payload, headers=None):
        self.payload = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.payload


def wiki_page(identifier):
    slugs = {10: "space/root", 11: "space/root/child", 12: "space/root/child/grandchild",
             13: "space/root/missing/leaf", 20: "space/broken"}
    return {"id": identifier, "slug": slugs[identifier], "title": f"Страница {identifier}",
            "page_type": "wysiwyg", "content": f"Полный текст {identifier}\n{{{{widget}}}}",
            "attributes": {"created_at": "2026-10-28"},
            "owner": {"user": {"display_name": "Alice"}}}


def opener(request, timeout):
    if request.get_header("Authorization") != "OAuth fixture-secret":
        raise AssertionError("Missing authentication")
    if request.get_header("X-cloud-org-id") != "fixture-org":
        raise AssertionError("Incorrect organization")
    if timeout != 30:
        raise AssertionError("Missing request timeout")
    url = urlparse(request.full_url)
    path = url.path
    query = parse_qs(url.query)
    if url.hostname == "api.tracker.yandex.net":
        return tracker_response(request, path, query)
    if url.hostname == "api.wiki.yandex.net":
        return wiki_response(request, path, query)
    raise AssertionError("Unexpected API host")


def tracker_response(request, path, query):
    key = path.split("/")[3]
    if key == "ABC-500":
        raise URLError("connection failed with fixture-secret")
    if key in {"ABC-400", "ABC-401", "ABC-403", "ABC-404", "ABC-429", "ABC-503"}:
        raise HTTPError(request.full_url, int(key[4:]), "failed", {}, io.BytesIO())
    if key == "ABC-700":
        raise RuntimeError("Unexpected fixture-secret")
    if path.endswith("/_search"):
        parent = json.loads(request.data)["filter"]["parent"]
        children = {"ABC-1": ["ABC-2", "ABC-4"], "ABC-2": ["ABC-3"], "ABC-3": [], "ABC-4": [],
                    "ABC-9": ["ABC-403"]}[parent]
        page = int(query["page"][0])
        selected = children[page - 1:page]
        return Response([{"key": child, "summary": f"Task {child}", "status": {"display": "Open"}}
                         for child in selected], {"X-Total-Pages": str(len(children))})
    if path.endswith("/comments"):
        if "cursor" not in query:
            return Response([{"text": f"Comment {key}"}], {
                "Link": f'<https://api.tracker.yandex.net{path}?cursor=next>; rel="next"',
            })
        return Response([{"text": f"Reply {key}"}])
    return Response({"key": key, "summary": f"Task {key}",
                     "description": "Текст задачи\n" + "полный текст " * 6000,
                     "status": {"display": "Open"}, "assignee": {"display": "Alice"},
                     "attachments": [{"id": "42", "display": "api.yaml"}]})


def wiki_response(request, path, query):
    if path == "/v1/pages":
        return Response(wiki_page(20 if query["slug"][0] == "space/broken" else 10))
    identifier = int(path.split("/")[3])
    if identifier == 21:
        raise HTTPError(request.full_url, 403, "denied", {}, io.BytesIO())
    if path.endswith("/descendants"):
        if identifier == 20:
            return Response({"results": [{"id": 21, "slug": "space/broken/denied", "title": "Denied"}]})
        if "cursor" not in query:
            return Response({"results": [{"id": 11, "slug": "space/root/child"}], "next_cursor": "second"})
        return Response({"results": [
            {"id": 12, "slug": "space/root/child/grandchild"}, {"id": 13, "slug": "space/root/missing/leaf"},
        ]})
    if path.endswith("/thread"):
        return Response({"results": [{"id": 101, "body": "Ответ Wiki", "parent_id": 100, "thread_id": 100,
                                      "author": {"display_name": "Bob"}, "created_at": "2026-10-28T11:00:00Z"}]})
    if path.endswith("/comments"):
        return Response({"results": [{"id": 100, "body": "Комментарий Wiki", "author": {"display_name": "Alice"},
                                      "created_at": "2026-10-28T10:00:00Z"}]})
    if path.endswith("/attachments"):
        return Response({"results": [{"id": 200, "name": "diagram.png", "mimetype": "image/png",
                                      "is_downloadable": True}]})
    return Response(wiki_page(identifier))


if __name__ == "__main__":
    with patch("yandex_mcp.TrackerClient", partial(TrackerClient, opener=opener)), \
            patch("yandex_mcp.WikiClient", partial(WikiClient, opener=opener)):
        runpy.run_path(str(Path(__file__).resolve().parents[1] / "mcp_server.py"), run_name="__main__")
