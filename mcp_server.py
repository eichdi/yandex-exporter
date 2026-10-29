#!/usr/bin/env python3
"""Serve read-only Yandex Tracker and Wiki tools through MCP over stdio."""

import logging
import sys
from functools import wraps

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import CallToolResult, ToolAnnotations

from export_tracker import TrackerError
from export_wiki import WikiError
from yandex_mcp import YandexReader


LOGGER = logging.getLogger(__name__)
TOOL_DESCRIPTIONS = {
    "get_tracker_attachment": (
        "Read one attachment from a Tracker issue using an ID from its attachment metadata. "
        "Returns text files as text, images as native images, and all other files as original binary resources."
    ),
    "get_wiki_attachment": (
        "Read one attachment from a Wiki page using an ID from its attachment metadata. "
        "Returns text files as text, images as native images, and all other files as original binary resources."
    ),
    "get_tracker_issue": "Read one Tracker issue by key, including full description, comments and attachment metadata.",
    "list_tracker_children": "List direct child issues by parent key, with keys, summaries and statuses.",
    "get_tracker_issue_with_children": (
        "Read an issue and all accessible descendant issues at every level, with full descriptions, comments "
        "and attachment metadata. Large trees may exceed the client's context; use individual reads if needed."
    ),
    "get_wiki_page": "Read one Yandex Wiki HTTPS URL, including original content, comments and attachment metadata.",
    "list_wiki_descendants": "List all accessible descendants of a Yandex Wiki HTTPS URL, with IDs, slugs and titles.",
    "get_wiki_page_with_children": (
        "Read a Wiki HTTPS URL and all accessible descendant pages at every level, with original content, comments "
        "and attachment metadata. Includes parent relationships and missing ancestor paths. "
        "Large trees may exceed the client's context; use individual reads if needed."
    ),
}


def safe_error(error: Exception, reader: YandexReader) -> str:
    if not isinstance(error, (TrackerError, WikiError, OSError, ValueError)):
        return "Unexpected error while reading Yandex data"
    message = str(error)
    for client in (reader.tracker, reader.wiki):
        token = client.token
        if token:
            message = message.replace(token, "[REDACTED]")
    return message


def tool_handler(method, reader: YandexReader):
    @wraps(method)
    def read(*args, **kwargs) -> dict | CallToolResult:
        try:
            return method(*args, **kwargs)
        except Exception as error:
            message = safe_error(error, reader)
            LOGGER.error("%s -> error, detail: %s", method.__name__, message)
            raise ToolError(message) from None

    return read


def create_server(reader: YandexReader) -> FastMCP:
    server = FastMCP(
        "yandex-reader", log_level="ERROR",
        instructions=(
            "Read Tracker and Wiki data. Issue/page tools list attachment metadata; use get_tracker_attachment "
            "or get_wiki_attachment to inspect relevant attachments on demand during analysis. Text files are "
            "returned as text, images as native images, and other files as original binary resources. "
            "Binary delivery does not imply the file's contents were understood; client format support varies. "
            "No local files are exported."
        ),
    )
    for name, description in TOOL_DESCRIPTIONS.items():
        server.add_tool(
            tool_handler(getattr(reader, name), reader), name=name, description=description,
            annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True),
        )
    return server


def main() -> int:
    try:
        reader = YandexReader.from_environment()
    except ValueError as error:
        print(f"MCP configuration error: {error}", file=sys.stderr)
        return 2
    create_server(reader).run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
