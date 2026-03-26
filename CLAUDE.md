# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Purpose

Typer CLI + Streamlit admin UI for managing a self-hosted MLX inference cluster. The CLI handles model deployment, service orchestration (mlx_lm.server via launchd), health monitoring, and LiteLLM proxy config generation. The admin UI (`admin/thunder_admin/`) provides a web interface for cluster configuration, deployment, and monitoring — backed by PostgreSQL and deployed as a Docker container on the gateway node.

**Cluster context:** Mixed macOS (Apple Silicon) + Linux inference clusters. Typical setup: multiple Mac compute nodes running vllm-mlx + one Linux gateway running LiteLLM proxy, Open WebUI, and Grafana via Docker Compose. Multiple clusters may exist with different hardware configurations. Detailed docs (topology, node setup, infra stack, model registry, benchmarks, runbooks) live in the Obsidian vault at `/Users/gnezim/_projects/gnezim/knowledge/projects/personal/inference-cluster/`.

## Commands

```bash
# Install dependencies
uv sync

# CLI commands
uv run thunder-forge --help
uv run thunder-forge generate-config      # Generate LiteLLM config from node-assignments.yaml
uv run thunder-forge generate-config --check  # Validate config is in sync
uv run thunder-forge ensure-models        # Download/sync models to inference nodes
uv run thunder-forge deploy               # Deploy vllm-mlx services to nodes
uv run thunder-forge deploy --node msm1   # Deploy to a single node
uv run thunder-forge health               # Check cluster health

# Dev
uv run pytest tests/ -v                   # Run tests
uv run ruff check src/ tests/             # Lint
uv run ruff format src/ tests/            # Format
```

Always use `uv run` -- never run `python` or `pytest` directly.

## Architecture

**Config-driven, stateless.** All state is computed on-demand from a single YAML source of truth: `configs/node-assignments.yaml`.

```
src/thunder_forge/
├── cli.py                  # Typer CLI entrypoint (generate-config, ensure-models, deploy, health)
└── cluster/
    ├── config.py           # YAML loading + Pydantic models, LiteLLM config generation
    ├── deploy.py           # Plist generation, SSH deploy, launchctl management
    ├── health.py           # SSH reachability + vllm-mlx service health checks
    ├── models.py           # Model download/sync to inference nodes
    └── ssh.py              # Shared SSH/SCP helpers (ssh_run, scp_content, run_local)

configs/
├── node-assignments.yaml   # Node inventory + model assignments (gitignored)
└── litellm-config.yaml     # Generated LiteLLM proxy config

docker/
└── docker-compose.yml      # LiteLLM, Open WebUI, PostgreSQL on the gateway node

scripts/
└── setup-node.sh           # Bootstrap script for new nodes (inference / infra roles)

tests/                      # pytest tests
```

## Python & Tooling

- **Python**: >=3.12
- **Package manager**: `uv` exclusively -- never use pip
- **Linter/formatter**: `ruff` (line-length 120, rules `E, F, I, UP`)
- **Tests**: `pytest`
- **Build**: hatchling

## Conventions

- DRY/KISS/YAGNI. Follow repo conventions exactly; don't guess.
- Business logic in `src/thunder_forge/cluster/*.py`; CLI handlers in `cli.py` stay thin.
- Use `ssh_run` / `scp_content` from `ssh.py` for all remote operations -- no raw `subprocess` SSH calls.

## README.md

- **Never delete or replace original content in README.md.** The README contains foundational project context (Shared Goals philosophy, ecosystem map, privacy principles, etc.) that predates the cluster CLI. When updating the README, add new sections or update existing ones -- do not rewrite the file from scratch.

## Git

- Conventional commits: `feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, `test:`
- Do not include `Co-Authored-By` lines in commit messages.
