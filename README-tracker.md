# Export Yandex Tracker Issues

English | [Русский](README-tracker-RU.md)

`export_tracker.py` saves the selected issue, all accessible child issues at any depth, full comment text, and attachments to local files. Requests to Tracker are read-only.

## Setup

Python **3.10 or newer** is required. The script uses the Python standard library; the MCP SDK is not required for file exports.

Create an environment from the project directory:

```bash
python3.12 -m venv .venv
```

If Python is installed under a different name, use the command for your Python 3.10+ installation. On Windows, the interpreter path is `.venv\Scripts\python.exe`; the examples below assume a POSIX shell.

Set the required environment variables:

```bash
export YANDEX_TRACKER_OAUTH_TOKEN="YOUR_OAUTH_TOKEN"
export IDENTITY_HUB="YOUR_ORGANIZATION_ID"
```

The token must have `tracker:read` permission, and the user must have access to the exported issues. `IDENTITY_HUB` is sent in the `X-Cloud-Org-ID` header; other organization header types are not currently supported. There is no default organization value.

Access setup and loading settings from a local `.env` file are described in the [main README](README.md#authentication). The script does not read `.env` automatically. Do not save real tokens in documentation or commits.

## Usage

```bash
.venv/bin/python export_tracker.py ABC-123 exports/
```

The first argument is the root issue key; the second is an optional output directory. If omitted, the export is created in the current directory. Inside the repository, use `exports/`, which is excluded from Git. Quote paths that contain spaces.

Argument help:

```bash
.venv/bin/python export_tracker.py --help
```

## Output

```text
exports/
  ABC-123 — Issue title/
    ABC-123 — Issue title.md
    Вложения/
      example.pdf
    Подзадачи/
      ABC-124 — Child issue title/
        ABC-124 — Child issue title.md
        Вложения/
        Подзадачи/
```

The service directory names are in Russian: `Вложения/` contains attachments, and `Подзадачи/` contains child issues.

For each issue, the Markdown file contains its key and title, status, type, priority, assignee, author, creation and update dates, deadline, description, and full comment text. Comments are numbered; separate comment author and date metadata is not exported.

Attachments are downloaded into their owning issue's directory. Invalid filename characters are replaced, and duplicate attachment names receive numeric suffixes. The `Вложения/` and `Подзадачи/` directories are created even when empty.

## Repeated Runs and Errors

A repeated run deletes the previous export with the same root issue key in the output directory, including an export using the old title. Deletion happens before writing new files and downloading attachments. If this stage fails, the previous export is not restored, and the new export may remain incomplete. Keep a copy of any export you need before running it again.

Exit codes:

| Code | Meaning |
| --- | --- |
| `0` | Export completed |
| `1` | API or filesystem error |
| `2` | Invalid arguments or missing required settings |

If an error occurs, check the environment settings, the user's access to the issue, and write permissions for the output directory. Diagnostics go to stderr. The script does not perform interactive login or refresh tokens.

For reading issues directly in an MCP client, see the [MCP guide](README-mcp.md). For page exports, see the [Wiki guide](README-wiki.md).
