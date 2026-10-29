# MCP for Yandex Tracker and Wiki

English | [Русский](README-mcp-RU.md)

The local MCP server returns issue and page data directly to the chat for analysis.
It runs over stdio using the official Python MCP SDK.

## Installation

Python **3.10 or newer** is required. The server has been verified on Python 3.12 in this project.
The system `python3` on macOS may be older; specify the required version explicitly.

```bash
cd yandex-exporter
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-mcp.txt
```

The existing file exporters run separately and do not require the MCP SDK.

## Connecting an MCP Client

In your local MCP server settings, specify:

- Name: `yandex-reader`.
- Command: `/absolute/path/to/yandex-exporter/.venv/bin/python`.
- Arguments: `/absolute/path/to/yandex-exporter/mcp_server.py`.
- Environment: `YANDEX_TRACKER_OAUTH_TOKEN` with your token and `IDENTITY_HUB` with your organization ID.

For clients using a JSON `mcpServers` configuration:

```json
{
  "mcpServers": {
    "yandex-reader": {
      "command": "/absolute/path/to/yandex-exporter/.venv/bin/python",
      "args": ["/absolute/path/to/yandex-exporter/mcp_server.py"],
      "env": {
        "YANDEX_TRACKER_OAUTH_TOKEN": "YOUR_TOKEN",
        "IDENTITY_HUB": "YOUR_ORGANIZATION_ID"
      }
    }
  }
}
```

Replace `/absolute/path/to/yandex-exporter` with the absolute path to your project copy,
and `YOUR_ORGANIZATION_ID` with your organization ID.
Replace `YOUR_TOKEN` with your Yandex OAuth token in the client's local settings.
Do not send the token in chat. Use the client's secret store if available.
Configuration fields and formats vary by client; the command, argument, and environment values are the same.
Absolute paths let the server start from any working directory.

`IDENTITY_HUB` is required; there is no default value.
The same organization ID and token are used for Tracker and Wiki. The token must have
access to the required issues and pages; Wiki requires `wiki:read` permission.
The token is passed through the process environment, not as a tool argument.
The server does not perform interactive login, refresh tokens, or read `.env` automatically.
After changing the settings, restart the server through your MCP client.

To start it manually from a terminal with the environment already configured:

```bash
.venv/bin/python mcp_server.py
```

After a successful start, the server waits for MCP messages, so there is no regular text prompt.
stdout is reserved for the protocol; diagnostics go to stderr.
Without a token or organization ID, the process exits with code 2 and explains which setting is missing.
Token permissions are checked by the API when a tool is called.

## Tools

| Tool | Argument | Data |
| --- | --- | --- |
| `get_tracker_issue` | `issue_key`, for example `ABC-123` | One issue: fields, full description, comments, attachment metadata |
| `list_tracker_children` | `issue_key` | All direct child issues: key, title, and status |
| `get_tracker_issue_with_children` | `issue_key` | The root issue and all accessible child issues at every level, with full content |
| `get_wiki_page` | `url`, for example `https://wiki.yandex.ru/space/page/` | One page: source content, attributes, owner, comments, and attachment metadata |
| `list_wiki_descendants` | `url` | All accessible descendant pages at every level: ID, path, and title |
| `get_wiki_page_with_children` | `url` | The root page and all accessible descendants at every level, with full content |

All six tools perform read-only operations in Yandex. Results are not saved to disk.
Attachments are returned **as metadata only**: files are not downloaded, and content is not extracted
from PDFs, images, or other attachments. Text and comments are preserved without summarizing or truncating.

Example chat requests after connecting the server:

- “Read issue ABC-123 and analyze its requirements.”
- “Retrieve ABC-123 with all child issues and identify contradictions.”
- “Read https://wiki.yandex.ru/space/page/ with all descendant pages.”

## Result Formats

Data is available as `structuredContent` and as JSON in the MCP response's text content.

- Issue: `key`, `summary`, `description`, `status`, `issue_type`, `priority`, `assignee`,
  `author`, `created_at`, `updated_at`, `deadline`, `comments`, `attachments`, `children`.
  For a single issue, `children` is empty; a `with_children` call populates it recursively with the same objects.
  Tracker comments contain text, as in the existing exporter.
- Issue list: `parent_key` and `children`, where each item contains `key`, `summary`, and `status`.
- Page: `id`, `slug`, `title`, `page_type`, `content`, `attributes`, `owner`, `comments`, `attachments`.
  Wiki comments include authors, dates, IDs, and reply relationships. An attachment contains `page_id`.
- Page list: `root_id`, `root_slug`, and `descendants` with `id`, `slug`, and `title`.
  The Wiki API provides only IDs and paths in its descendants list, so titles are retrieved separately.
- Wiki tree: `root_id`, `root_slug`, `pages_by_slug`, `parent_by_slug`, `path_only_slugs`.
  `pages_by_slug` contains full pages; `parent_by_slug` defines the hierarchy, with `null` for the root's parent.
  `path_only_slugs` explicitly lists intermediate paths for which the API did not provide a page.

The `with_children` calls do not limit depth or truncate data. A very large tree
may exceed the MCP client's message or context limit, and loading may take
longer than its timeout. In that case, list descendants and read the required
objects individually; increase the client's call timeout if necessary.

Invalid arguments, HTTP 400/401/403/404/429/5xx responses, and read failures are returned as
MCP tool errors (`isError: true`). If a discovered descendant cannot be read,
the entire tree call reports an error. The API may omit objects hidden from the user,
so “all” means all objects accessible to the current token.
API requests have a 30-second timeout. Tool errors do not terminate the MCP session.

## Verification

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
scripts/verify-mcp.sh
```

The script runs all unit and integration tests, checks coverage of the new logic (at least 80%),
compiles Python files, and checks installed dependency consistency.
Integration tests launch a real MCP process over stdio with controlled API responses,
so they do not require a Yandex token or network access.
Live access to Tracker and Wiki is verified separately and requires a valid token.

Sources:

- [MCP authorization for stdio](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization).
- [Official Python MCP SDK](https://github.com/modelcontextprotocol/python-sdk/tree/v1.26.0).
- [Wiki descendants API](https://yandex.ru/support/wiki/ru/api-ref/pages/pages__descendants_by_id).
