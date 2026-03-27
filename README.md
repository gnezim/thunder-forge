# Thunder Forge

Infrastructure/process layer for running **self-hosted AI capabilities** to follow **Shared Goals**.

Thunder Forge focuses on operating the compute and automation stack that can work with **private data** (finance, healthcare, family/child privacy) without relying on third-party hosted agents.

- GitHub org: https://github.com/shared-goals/
- This repo: https://github.com/shared-goals/thunder-forge

## Shared Goals (idea)

A **personal image of Joy and Happiness** is treated as the base source of motives.

Details (RU): [Shared Goals — use case and concept](https://text.sharedgoals.ru/ru/p2-180-sharedgoals/#use_case)

- A *motive* is a reason to act (rooted in what brings joy/happiness).
- A *goal* is a direction or outcome shaped by one or more motives.
- When goals are **shared among coauthors**, motives combine and the overall dynamics increase.

## "Text" as a forkable source of goals

Shared Goals is developed as a living **Text** that anyone can fork and rewrite into their own.

- Concept Text (evolving): https://github.com/bongiozzo/whattodo
- Common build submodule: https://github.com/shared-goals/text-forge

`text-forge` transforms a Text repository into:

- a website (with link-sharing functionality in the publishing format)
- an EPUB book
- a combined Markdown corpus suitable for AI usage (RAG/MCP agents and skills)

## What Thunder Forge manages

Thunder Forge is the infrastructure/process layer for **self-hosted execution** of agents and skills.

Typical managed parts:

- **Nodes**: machines in a self-hosted cluster (e.g., several Mac Studios)
- **LLMs on nodes**: models served locally via Ollama (https://github.com/ollama/ollama)
- **Assistants**: AI assistants for task execution and automation
  - openclaw: https://github.com/openclaw/openclaw
- **Skills**: reusable tool capabilities agents can invoke
  - `github_repo` skills (agent skills): https://github.com/agentskills/agentskills

> Note: assistants like openclaw and skills catalogs are intended integration points. This repo is the
> operational "glue" and runbooks/specs layer to run them self-hosted.

## Ecosystem map

```mermaid
flowchart LR
  SG["Shared Goals<br/>joy/happiness -> motives"] --> G["Goals<br/>(shared among coauthors)"]

  T["Text<br/>(forkable Markdown)"] --> TF["text-forge<br/>site + EPUB + combined corpus"]
  TF --> K["AI-ready corpus<br/>(RAG/MCP input)"]

  G --> A["Agents<br/>(plan and act)"]
  K --> A

  A --> S["Skills<br/>(callable capabilities)"]
  S --> N["Self-hosted nodes<br/>(cluster machines)"]

  N --> O["Local LLMs<br/>(Ollama)"]

  W["Assistants<br/>(openclaw)"] --> A
  W --> S
```

## Privacy & self-hosting principles

Shared Goals activities can involve highly sensitive data.

- Prefer **self-hosted nodes** and **self-hosted agents** for private domains.
- Keep data access **least-privilege** (skills should request only what they need).
- Treat secrets and tokens as production-grade (no plaintext in repos).
- Make agent activity auditable (logs, runs, and permissions).

## Cluster Management

Thunder Forge manages an MLX inference cluster via two interfaces: a **web Admin UI** for day-to-day operation, and a **Typer CLI** for scripting and automation.

For full setup instructions, see [docs/setup-guide.md](docs/setup-guide.md).

### Admin UI

A Streamlit web interface (`admin/thunder_admin/`) deployed as a Docker container on the gateway node. After initial setup, all cluster management flows through here:

| Page                | What it does                                                        |
| ------------------- | ------------------------------------------------------------------- |
| **Dashboard**       | Live cluster health — node status, service reachability             |
| **Nodes**           | Manage compute node inventory and hardware specs                    |
| **Assignments**     | Assign models to nodes, configure memory budgets and server args    |
| **Models**          | Model registry and HuggingFace cache management                     |
| **Deploy**          | Trigger deployments; view launchd plist generation and SSH output   |
| **External Endpoints** | Register external OpenAI-compatible endpoints in LiteLLM        |
| **History**         | Deployment and event log                                            |
| **Users**           | Admin user management with per-user timezone preferences            |

### CLI

```bash
uv sync                                      # Install dependencies
uv run thunder-forge --help                  # See all commands
```

| Command            | Description                                                    |
| ------------------ | -------------------------------------------------------------- |
| `generate-config`  | Generate LiteLLM `proxy_config.yaml` from cluster state        |
| `ensure-models`    | Download/sync models to inference nodes via SSH                |
| `deploy`           | Deploy mlx_lm.server services to inference nodes (launchd)     |
| `health`           | Check SSH reachability and service status across all nodes      |

Use `uv run thunder-forge <command> --help` for per-command details.

### Infrastructure Stack

The gateway node runs these services via Docker Compose (`docker/`):

- **LiteLLM** -- OpenAI-compatible proxy routing requests to inference nodes
- **Open WebUI** -- Chat interface
- **PostgreSQL** -- Shared backend for LiteLLM and Thunder Admin
- **Thunder Admin** -- The Streamlit admin UI

Inference nodes (macOS, Apple Silicon) run `mlx_lm.server` managed as launchd services.

### CI/CD

Pushes to `main` that touch `configs/`, `src/thunder_forge/`, or `docker/` trigger the deploy workflow (`.github/workflows/deploy.yml`) on a self-hosted runner on the gateway node.

## Design

See the full design spec: [docs/specs/2026-03-14-thunder-forge-cluster-cli-design.md](docs/specs/2026-03-14-thunder-forge-cluster-cli-design.md)

## Status

This repository is under active development (see [LICENSE](LICENSE)).
