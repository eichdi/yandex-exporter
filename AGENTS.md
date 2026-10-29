# Project Instructions

English | [Русский](AGENTS-RU.md)

- Read the entire task; inspect the implementation, similar code, and CONTRIBUTING.md.
- Do not invent requirements or perform unrelated refactoring. Propose such changes separately.
- Keep changes as small as reasonably possible and preserve other people's work.
- Follow the existing Python style: snake_case for functions, CamelCase for classes, UPPER_CASE for constants, and 4-space indentation.
- Do not embed tokens, real organization IDs, internal URLs, or user data in source code, examples, or tests.
- Do not expose secrets in errors or logs. MCP stdout is reserved for the protocol; send diagnostics to stderr.
- Before changing behavior, add a regression test. Do not weaken or remove tests just to make verification pass.
- Before completing the task, run `scripts/verify-mcp.sh`, review the final diff, and fix any issues found. Rerun the affected checks.
- Report actual results and verification limitations. Do not declare the task complete while known relevant failures remain.

These instructions are self-contained: no personal files from the developer's home directory are required.
