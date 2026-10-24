# Self-Evolving Agent

**A self-hosted AI agent that operates Ubuntu, rewrites its own source, and restarts itself to apply the change.**

[![CI](https://github.com/xieshengGZ/self-evolving-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/xieshengGZ/self-evolving-agent/actions/workflows/ci.yml)
[![version](https://img.shields.io/badge/version-1.2.0-blue)](docs/VERSIONING.md)
[![license](https://img.shields.io/github/license/xieshengGZ/self-evolving-agent)](LICENSE)
[![python](https://img.shields.io/badge/python-3.12-3776AB)](https://www.python.org/)

[中文](README.zh-CN.md) · [Architecture](docs/ARCHITECTURE.md) · [Roadmap](docs/ROADMAP.md) · [Releases](docs/releases/v1.2.0.md)

![Self-Evolving Agent demo](docs/assets/demo.gif)

> **v1.2.0 stable. Early-stage infrastructure.** It runs shell commands and can modify its own code. Use a disposable VM, not a production host. See [Security](#security).

## Why this exists

Most "agents" are chat wrappers around a model. This project is a long-running Linux daemon: it plans with DeepSeek, executes real shell/file actions, verifies the plan, persists every event, and can update its own source and restart itself. New tools and HTTP endpoints are loaded as hot-reloadable plugins, so most extensions do not need a process restart.

It is intentionally small and inspectable: FastAPI, SQLite, a plugin registry, and one agent loop. No cloud control plane, no vector database, no hidden orchestration service.

## What it can do

- **Operate the host** — shell, read/write files, inspect processes and services, run commands with timeouts.
- **Plan before acting** — a `planning → executing → verifying → done/replan` state machine with deterministic acceptance checks.
- **Survive restarts** — sessions, messages, events, plans, context and artifacts are persisted in SQLite or on disk.
- **Rewrite itself** — backup, syntax check, `execv` restart, and rollback if the new process does not come back.
- **Extend without restarting** — tools, planners, classifiers, context sections and HTTP ops are plugins.
- **Stream everything** — the web UI receives tool output and state changes over SSE.
- **Stay within budget** — priority-based context sections, token budgeting, compaction, and large-result artifact offload.
- **Recover manually** — a `/manual` rescue page can inspect logs, run commands, and restart the service.

## Quick start

Requirements: Ubuntu 22.04/24.04, Python 3.12+, `systemd`, and a DeepSeek API key.

```bash
git clone https://github.com/xieshengGZ/self-evolving-agent.git
cd self-evolving-agent
./install.sh
```

The installer creates `.venv`, installs dependencies, asks for your API key once, generates a random web token, and installs a systemd service. If you run it as root, it warns and installs the service as root; a normal user + `sudo` is recommended. Then open:

```text
http://<your-host>:8088
```

The token is stored in `.env` as `AGENT_WEB_TOKEN`. Put it into the web UI's token box.

<details>
<summary>Manual install</summary>

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
# edit .env: DEEPSEEK_API_KEY and AGENT_WEB_TOKEN
./deploy.sh
```
</details>

### Configuration

| Variable | Default | Purpose |
|---|---|---|
| `DEEPSEEK_API_KEY` | required | Model API key |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | OpenAI-compatible endpoint |
| `DEEPSEEK_MODEL` | `deepseek-chat` | Model name |
| `AGENT_WEB_TOKEN` | required | Bearer token for the web/API |
| `AGENT_PORT` | `8088` | HTTP port |
| `AGENT_MAX_STEPS` | `20` | Max actions per task |
| `AGENT_CONTEXT_BUDGET` | `12000` | Context token budget |
| `AGENT_ARTIFACT_OFFLOAD` | `1` | Offload large tool results to `state/artifacts/` |

See [`.env.example`](.env.example) for the full list.

## How it works

```text
Web UI / API
    │ prompt
    ▼
classifier ── chat / control ──► direct answer or stop/restart
    │ task
    ▼
planning plugin ──► plan with acceptance checks
    │
    ▼
agent loop ──► DeepSeek ──► one JSON action ──► tools / plugins
    │                                              │
    │◄──────────── result + events (SSE) ──────────┘
    ▼
verify ──► done / replan (bounded)
```

| Layer | Location | Responsibility |
|---|---|---|
| Kernel | `app/kernel/` | loop, registry, plugin loading, SSE, budgets |
| Domain | `app/planning.py`, `app/context.py` | plan state machine, verification, context sections |
| Storage | `app/storage.py`, `state/agent.db` | sessions, messages, events, plans |
| Plugins | `plugins/*` | tools, planner, classifier, context sections |
| Web | `app/static/` | chat UI, context inspector, manual rescue |

## Screenshots

| Chat and context ledger | Plan execution and streaming |
|---|---|
| ![Chat](docs/assets/ui-chat.png) | ![Plan](docs/assets/ui-plan.png) |

## Security

This is a **single-user, trusted-host** tool. By design it has the same filesystem and shell access as the user running the service.

- Run it in a disposable VM or a dedicated host.
- Do not expose it directly to the public internet without a reverse proxy, TLS, and network controls.
- The web token is a bearer secret; use a long random value.
- Shell commands and plan acceptance commands are powerful. Review the actions you approve.
- Self-update has backup and rollback, but it is not a sandbox.
- Prompt injection from files or command output is still a risk.

Read [SECURITY.md](SECURITY.md) before deploying.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
make check                 # compileall + 102 tests
.venv/bin/python -m app.cli status
.venv/bin/python -m app.cli map
```

CI runs the same `make check` on Python 3.12. The version scheme is documented in [docs/VERSIONING.md](docs/VERSIONING.md): odd minor = development, even minor = stable.

## Roadmap

- **v2.1.0** — typed privileged operations, root helper, approval and audit trail
- **v2.2.0** — snapshots, rollback, sandbox policy
- Context quality: built-in LLM compaction and artifact retention limits

See [docs/ROADMAP.md](docs/ROADMAP.md).

## Contributing

Issues and pull requests are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md), keep plugins behind `app.kernel.api`, and include tests. If this project is useful to you, a star helps others find it.

## License

MIT — see [LICENSE](LICENSE).
