# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Purpose

Infrastructure/process layer for running self-hosted AI capabilities (Ollama node cluster management) as part of the Shared Goals ecosystem. Currently a FastAPI backend + Telegram Mini App for cluster monitoring; an upcoming rewrite transitions to a Typer CLI for MLX inference cluster management (see `docs/plans/`).

**Cluster context:** The target cluster is 4× Mac Studio M4 Max (128GB) + 1× Radxa ROCK 5 ITX+ (32GB, ARM64 Linux) with LiteLLM proxy, Open WebUI, and Grafana monitoring. Detailed docs (topology, node setup, infra stack, model registry, benchmarks, runbooks) live in the Obsidian vault at `/Users/gnezim/_projects/gnezim/knowledge/projects/personal/inference-cluster/`.

## Commands

```bash
make sync              # Install/update deps (uv sync --extra dev)
make serve             # Start FastAPI server (stops existing first)
make stop              # Kill processes on PORT (default 8000)
make test              # Run pytest
make format            # Run ruff format
make coverage          # Run tests with coverage
make check-i18n        # Validate translations.json

# Single test
uv run pytest tests/path/to/test_file.py::test_name -v

# Infrastructure
make setup-env         # Configure fabric IPs on nodes via SSH
make fabricnet-check   # Check fabric reachability
make ollama-ensure     # Install/configure/start Ollama on nodes
make ollama-check      # Check Ollama status on nodes
make local-hosts       # Update hub's /etc/hosts
```

Always use `make` targets and `uv run` — never run `uvicorn`, `python`, or `pytest` directly.

## Architecture

**Config-driven, no database.** All state is computed on-demand from `tf.yml` (gitignored; see `tf.example.yml` for structure).

```
src/
├── api/                    # FastAPI routes
│   ├── webhook.py          # App setup, /health, /webhook/telegram
│   └── mini_app.py         # Mini App API (/me, /status)
├── bot/app.py              # Telegram bot handlers (/start, /help)
├── services/               # Business logic (thin handlers, fat services)
│   ├── config_service.py   # YAML loading + Pydantic models (lru_cache)
│   ├── auth_service.py     # Telegram initData HMAC verification
│   ├── access_service.py   # Admin allowlist
│   ├── monitor_service.py  # TCP reachability probes (SSH + Ollama ports)
│   ├── fabricnet_service.py # macOS networksetup fabric IP config
│   ├── ssh_service.py      # SSH command wrapper
│   └── hosts_service.py    # /etc/hosts block generation
├── static/mini_app/        # Vanilla JS frontend + translations.json
├── thunder_forge/cli.py    # CLI entrypoint (serve, status subcommands)
└── main.py                 # Uvicorn runner

scripts/setup_env.py        # Cluster setup automation (fabricnet, hosts, ollama)
tests/{unit,contract}/      # Test directories
```

**Auth flow:** Telegram initData → HMAC-SHA256 verification (`auth_service`) → admin allowlist check (`access_service`). All Mini App API endpoints enforce this.

**Monitoring:** `monitor_service` does TCP socket probes to management and fabric IPs on SSH (22) and Ollama (11434) ports.

**Fabric networking:** macOS-specific — configures Thunderbolt Bridge IPv4 via SSH + `networksetup`. Generates managed `/etc/hosts` blocks.

## Python & Tooling

- **Python**: >=3.10 (plan targets >=3.12)
- **Package manager**: `uv` exclusively — never use pip
- **Linter/formatter**: `ruff` (plan config: line-length 120, rules `E, F, I, UP`)
- **Tests**: `pytest` in `tests/{unit,contract}`
- **Build**: setuptools (plan moves to hatchling)

## Docker Services

- `olla/compose.yaml` — Olla dashboard (port 40114)
- `openwebui/compose.yml` — Open WebUI (port 8333), connects to Ollama nodes on fabric network

## Conventions

- DRY/KISS/YAGNI. Follow repo conventions exactly; don't guess.
- Business logic in `src/services/*_service.py`; handlers/endpoints stay thin.
- i18n single source of truth: `src/static/mini_app/translations.json`. Run `make check-i18n` after string changes.
- Auth is centralized in `auth_service.py` — endpoints call `get_authenticated_user()`.

## Git

- Conventional commits: `feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, `test:`
- Do not include `Co-Authored-By` lines in commit messages.
