# Yandex Exporter

English | [Русский](README-RU.md)

Read Yandex Tracker issues and Yandex Wiki pages using local Python utilities:

- **Tracker CLI** — export an issue, all accessible child issues, comments, and attachments to Markdown. [Details](README-tracker.md).
- **Wiki CLI** — export a page tree, source content, metadata, comments, and attachments. [Details](README-wiki.md).
- **MCP server** — read issues and pages directly in an MCP client; attachments are returned as metadata only. [Setup](README-mcp.md).

All modes perform read-only operations in Yandex. The CLIs save data to local disk.

## Requirements and Installation

Use Python **3.10 or newer**; the full project verification suite was run on Python 3.12. The file exporters use only the standard library. MCP requires additional dependencies.

After cloning, open the project directory:

```bash
cd yandex-exporter
python3.12 -m venv .venv
# For MCP only:
.venv/bin/python -m pip install -r requirements-mcp.txt
```

If Python is installed under a different name, replace `python3.12` with the command for your Python 3.10+ installation. Commands assume a POSIX shell (Linux/macOS); on Windows, use `.venv\Scripts\python.exe` and configure the environment in your shell.

## Authentication

All modes require two environment variables:

| Variable | Value |
| --- | --- |
| `YANDEX_TRACKER_OAUTH_TOKEN` | An OAuth token for a user with access to the required objects |
| `IDENTITY_HUB` | Your Yandex Cloud Organization ID; sent in `X-Cloud-Org-ID` |

The project supports organizations using Yandex Cloud Organization authentication. Other organization header types are not currently supported.

Token setup and permissions are described in the official [Tracker](https://yandex.ru/support/tracker/ru/api/access) and [Wiki](https://yandex.ru/support/wiki/ru/api-ref/access) documentation. Wiki requires `wiki:read`; Tracker requires `tracker:read`. The token's user must have access to the required issues and pages. Find the organization ID in Tracker under Administration → Organizations. The applications do not perform login or refresh tokens.

Set the variables through your shell or a secret store. For local use in a POSIX shell:

```bash
cp .env.example .env
chmod 600 .env
# Fill in .env in a local editor, then:
set -a
. ./.env
set +a
```

`.env` contains shell assignments; only load your own trusted file. The programs do not read it automatically. Do not publish tokens, client configuration containing tokens, or exported data. `.env` and `exports/` are excluded from Git.

## Export Tracker Issues

```bash
.venv/bin/python export_tracker.py ABC-123 exports/
```

Setup, output structure, repeated runs, and error codes are described in the [Tracker guide](README-tracker.md). Without an output directory, the export is created in the current directory; use `exports/` when working inside the repository.

## Export Wiki Pages

```bash
.venv/bin/python export_wiki.py 'https://wiki.yandex.ru/space/page/' exports/
```

If downloading fails, the previous completed Wiki export is preserved. Format limitations are described in the [Wiki guide](README-wiki.md).

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for dependency setup and verification, [AGENTS.md](AGENTS.md) for agent instructions, and [SECURITY.md](SECURITY.md) for vulnerability reporting.

## License

[MIT](LICENSE).
