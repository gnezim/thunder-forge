# Thunder Forge

Cluster management CLI for a self-hosted MLX inference cluster: **4x Mac Studio M4 Max** (128 GB each) + **1x Radxa ROCK 5 ITX+** (ARM64 Linux, infrastructure hub).

Thunder Forge manages model deployment, service orchestration, health monitoring, and configuration generation across the cluster from a single command-line tool running on the Radxa ROCK hub node.

## Quick Start

```bash
# Install dependencies
uv sync

# See available commands
uv run thunder-forge --help

# Generate LiteLLM proxy config from cluster definition
uv run thunder-forge generate-config

# Check cluster health
uv run thunder-forge health
```

## Commands

| Command            | Description                                                    |
| ------------------ | -------------------------------------------------------------- |
| `generate-config`  | Generate LiteLLM `proxy_config.yaml` from `configs/*.yml`      |
| `ensure-models`    | Download/sync models to inference nodes via SSH                 |
| `deploy`           | Deploy vllm-mlx services to inference nodes (launchd plist)    |
| `health`           | Run health checks across all cluster nodes                     |

Use `uv run thunder-forge <command> --help` for detailed usage of each command.

### generate-config

Reads `configs/cluster.yml` (node inventory) and `configs/models.yml` (model registry), then writes a LiteLLM-compatible `proxy_config.yaml`. Use `--check` to validate without writing.

### ensure-models

Connects to each inference node via SSH and ensures the required models are downloaded. Compares the desired model set from `configs/models.yml` against what is already present on each node.

### deploy

Generates macOS `launchd` plist files for vllm-mlx on each inference node, copies them over SSH, and starts the services. Handles graceful restart of running services.

### health

Probes all cluster nodes for SSH reachability, vllm-mlx service status, and model availability. Reports a summary table to the terminal.

## Node Bootstrap

New nodes can be set up with the bootstrap script:

```bash
# On a Mac Studio (inference node)
bash scripts/setup-node.sh inference

# On the Radxa ROCK (infrastructure hub)
bash scripts/setup-node.sh infra
```

The `inference` role installs Homebrew, uv, and vllm-mlx, disables macOS sleep, and creates the logs directory. The `infra` role installs Docker, uv, clones this repo, generates secrets for the Docker Compose stack, starts the services, and generates an SSH key for connecting to inference nodes.

## Configuration

All cluster configuration lives in `configs/`:

- `configs/cluster.yml` -- Node inventory (hostnames, IPs, roles, hardware specs)
- `configs/models.yml` -- Model registry (which models on which nodes, memory budgets)

See `configs/cluster.example.yml` and `configs/models.example.yml` for structure.

## Infrastructure Stack (Docker)

The Radxa ROCK hub runs these services via Docker Compose (`docker/`):

- **LiteLLM** -- OpenAI-compatible proxy that routes requests to inference nodes
- **Open WebUI** -- Chat interface
- **PostgreSQL** -- LiteLLM backend
- **Grafana + Prometheus** -- Monitoring

## CI/CD

Pushes to `main` that touch `configs/`, `src/thunder_forge/`, or `docker/` trigger the deploy workflow (`.github/workflows/deploy.yml`) on a self-hosted runner on the Radxa ROCK.

## Design

See the full design spec: [docs/specs/2026-03-14-thunder-forge-cluster-cli-design.md](docs/specs/2026-03-14-thunder-forge-cluster-cli-design.md)
