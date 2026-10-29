# Contributing

English | [Русский](CONTRIBUTING-RU.md)

## Environment and Verification

Use Python 3.10+; Python 3.12 was used for reproducible verification:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
scripts/verify-mcp.sh
```

The script runs all unit and integration tests, checks coverage of `yandex_mcp` and `mcp_server` against an 80% threshold, compiles Python files, and runs `pip check`. The coverage threshold does not apply to the file exporters. There is no separate project build step.

Integration tests use controlled API responses and a real MCP process over stdio. They do not require a token, an organization, or access to Yandex. Live verification is performed separately using your own test data and the environment settings described in the README; do not include tokens or private issue content in reports.

If your environment is located elsewhere, set `MCP_PYTHON=/absolute/path/to/python`. On Windows, run the commands from the script in your shell or use a POSIX environment.

## Making Changes

Before making changes, inspect the current implementation and similar code. Keep changes small, and discuss new requirements and unrelated refactoring separately. Follow the existing Python style; write comments, docstrings, and diagnostic messages in English.

When changing behavior, add tests for successful and error scenarios. Unit tests belong in `tests/`, and integration tests belong in `it/`. Do not disable or weaken tests just to make verification pass. After making changes, run the entire verification script and review the final diff.

Use descriptive branch names, such as `fix/organization-settings`, and short, meaningful commit messages in English. Include a GitHub issue number when one exists; a corporate task number is not required.

In pull requests, describe the problem, the resulting behavior, and the checks performed. For bug reports, include the Python version, operating system, command used, and sanitized error text. Use fictional issues, organizations, and attachments in examples and tests.

GitHub Actions and static analysis are not configured yet; run the local checks yourself.
