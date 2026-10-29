# Security

English | [Русский](SECURITY-RU.md)

The token is passed through the environment. Store it in local configuration with restricted access or in a secret store. Do not include tokens, private exports, or real API responses in commits, issues, pull requests, or logs.

MCP returns issue/page data and requested attachment contents to the client: choose a client and model that are allowed to process these data. The server downloads selected attachments into memory and returns them as text, images or original binary resources without creating local exports. The file export CLIs save attachments to disk.

Before submitting changes, review `git diff --cached` and `git status --short`. `.gitignore` only covers the listed paths: exports created outside `exports/` need a separate check.

If you find a vulnerability, use private vulnerability reporting in the repository's Security tab on GitHub if the owner has enabled it. If there is no private channel, open an issue only to request a secure contact channel, without exploitation details, secrets, or user data. A dedicated security contact address has not been published yet.
