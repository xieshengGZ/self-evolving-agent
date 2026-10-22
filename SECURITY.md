# Security Policy

## Supported versions

| Version | Supported |
|---|---|
| 1.2.x | Yes |
| < 1.2 | No |

## Reporting a vulnerability

Please use GitHub's **private vulnerability reporting** on the Security tab. If that is unavailable, open a minimal issue asking for a private channel; do not post exploit details, tokens, or host information publicly.

Never include API keys, web tokens, or production logs in an issue.

## Threat model

Self-Evolving Agent is a **single-user, trusted-host** tool. It is not a sandbox and not multi-tenant.

- It executes shell commands with the permissions of the service user.
- It can write its own source, restart itself, and run plan acceptance commands.
- It reads model output, files, and command output; prompt injection is possible.
- The web API uses a bearer token. There is no user account system, SSO, or rate limiting.
- Plan and shell timeouts reduce accidental damage, not a malicious operator.

## Safe deployment checklist

- Run in a disposable VM or a dedicated host, not on your laptop or a production server.
- Use a dedicated non-root user that only has the privileges it needs.
- Put the service behind a reverse proxy with TLS; do not expose port 8088 directly.
- Use a long random `AGENT_WEB_TOKEN`.
- Keep `.env`, `state/`, and `backups/` out of Git.
- Take VM snapshots before enabling self-update or broad shell access.
- Review new plugins before loading them; plugins are trusted code.

## Out of scope

- Damage caused by commands you or the agent intentionally run.
- Prompt-injection payloads that require the model to be tricked.
- Missing hardening on a host that was exposed to the public internet against this guidance.
