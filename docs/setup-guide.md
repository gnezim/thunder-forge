# Thunder Forge Setup Guide

End-to-end guide for deploying an MLX inference cluster with Thunder Forge.

## Cluster Architecture

Thunder Forge manages two types of nodes:

- **Infrastructure node** (Linux ARM64) — runs LiteLLM proxy, Open WebUI, PostgreSQL via Docker Compose. Acts as the control plane: downloads models, deploys services, runs health checks.
- **Inference nodes** (macOS with Apple Silicon) — run vllm-mlx services via launchd. Each node serves one or more models on dedicated ports.

## Prerequisites

- All nodes on the same LAN with SSH access between them
- macOS on inference nodes, Linux on infrastructure node
- Internet access for initial setup (package installs, model downloads)

## Step 1: Bootstrap the Infrastructure Node

SSH into your infrastructure node, clone the repo and run the setup script:

```bash
git clone https://github.com/shared-goals/thunder-forge.git ~/thunder-forge
cd ~/thunder-forge
zsh scripts/setup-node.sh infra
```

> **Note:** `git clone` is always the first step on any node. The setup script and all configuration live in the repo.

This will:
- Install Docker Engine, `uv`, and `hf` CLI (HuggingFace)
- Install Python dependencies (`uv sync`)
- Generate `docker/.env` with random secrets (LiteLLM master key, Postgres password, WebUI credentials)
- Start Docker Compose (LiteLLM, Open WebUI, PostgreSQL)
- Generate an SSH keypair for connecting to inference nodes
- Upgrade all installed tools to latest versions

### Configure environment variables (before running)

Before running the setup script, create a `.env` file in the project root:

```bash
cp ~/thunder-forge/.env.example ~/thunder-forge/.env
vi ~/thunder-forge/.env
```

```bash
# ~/thunder-forge/.env
TF_SSH_USER=admin
TF_SSH_KEY=~/.ssh/id_ed25519
HF_HOME=~/.cache/huggingface
# Uncomment if outbound internet goes through a proxy:
# HTTP_PROXY=socks5h://127.0.0.1:1080
# HTTPS_PROXY=socks5h://127.0.0.1:1080
```

This `.env` file is used by both `setup-node.sh` and `thunder-forge` CLI commands. Existing environment variables take precedence.

If outbound internet is filtered through a proxy, uncomment `HTTP_PROXY`/`HTTPS_PROXY` in the `.env` file.

After setup, authenticate with HuggingFace (required for gated models):

```bash
hf auth login
```

**Save the generated credentials** from `~/thunder-forge/docker/.env`.

## Step 2: Bootstrap Inference Nodes

SSH into each inference node, clone the repo and run the setup script:

```bash
git clone https://github.com/shared-goals/thunder-forge.git ~/thunder-forge
cd ~/thunder-forge
zsh scripts/setup-node.sh inference
```

> **Note:** Same pattern as the infra node — always clone the repo first.

### Configure environment variables (optional)

Create a `.env` in the project root before running (or edit the existing one):

```bash
cp ~/thunder-forge/.env.example ~/thunder-forge/.env
vi ~/thunder-forge/.env
```

To skip disabling macOS sleep, add `TF_DISABLE_SLEEP=false` to `.env`.

This will:
- Install Homebrew, `uv`, and `vllm-mlx`
- Optionally disable macOS sleep
- Create log directory (`~/logs`)
- Add `~/.local/bin` to PATH in `~/.zshenv` and `~/.zshrc`
- Upgrade all installed tools to latest versions

Repeat for all inference nodes.

## Step 3: Distribute SSH Keys

From the infrastructure node, copy its public key to each inference node:

```bash
ssh-copy-id -i ~/.ssh/id_ed25519 <user>@<inference-node-ip>
```

Verify connectivity:

```bash
ssh -o BatchMode=yes <user>@<inference-node-ip> "echo ok"
```

## Step 4: Configure the Project

All remaining steps run from the **infrastructure node** in `~/thunder-forge`.

### .env (operational config)

The project root `.env` is loaded by the CLI automatically. Copy the template and adjust:

```bash
cd ~/thunder-forge
cp .env.example .env
vi .env
```

If you already created `.env` during Step 1, it's already in place. Otherwise:

```bash
cp .env.example .env
vi .env
```

This single `.env` file is used by both `thunder-forge` CLI commands and `setup-node.sh`.

| Variable | Default | Description |
|----------|---------|-------------|
| `TF_SSH_USER` | `admin` (inference) / current user (infra) | Default SSH user for nodes |
| `TF_SSH_KEY` | `~/.ssh/id_ed25519` | SSH key path |
| `HF_HOME` | `~/.cache/huggingface` | HuggingFace cache directory |
| `TF_DISABLE_SLEEP` | `true` | Disable macOS sleep on inference nodes |

Per-node user overrides go in `node-assignments.yaml` (see below).

### node-assignments.yaml (cluster topology)

Copy the template and edit it for your cluster:

```bash
cp configs/node-assignments.yaml.example configs/node-assignments.yaml
vi configs/node-assignments.yaml
```

This is the single source of truth for your cluster. Define models, nodes, and assignments:

```yaml
# Model registry — add models here
models:
  coder:
    source:
      type: huggingface
      repo: "mlx-community/Qwen3-Coder-Next-4bit"
    disk_gb: 44.8
    kv_per_32k_gb: 0.75
    max_context: 262144

  fast:
    source:
      type: huggingface
      repo: "mlx-community/Qwen3.5-9B-MLX-4bit"
    disk_gb: 5.6
    active_params: "9B dense"
    max_context: 262144

# Node inventory
nodes:
  rock: { ip: "192.168.1.61", ram_gb: 32, role: infra }
  msm1: { ip: "192.168.1.101", ram_gb: 128, role: inference }
  msm2: { ip: "192.168.1.102", ram_gb: 128, role: inference }

# What runs where
assignments:
  msm1:
    - model: coder
      port: 8000
  msm2:
    - model: coder
      port: 8000
```

Use per-node `user` field if SSH user differs from `TF_SSH_USER`:

```yaml
nodes:
  mynode: { ip: "192.168.1.50", ram_gb: 64, role: inference, user: myuser }
```

## Step 5: Download Models

```bash
uv run thunder-forge ensure-models
```

Downloads models from HuggingFace on the infra node and syncs them to assigned inference nodes via rsync. Large models can take 20+ minutes. Progress is shown, and downloads are resumable.

If the infra node has limited disk space, set `HF_HOME` to an external drive in `.env`.

Preview without downloading:

```bash
uv run thunder-forge ensure-models --dry-run
```

## Step 6: Deploy

```bash
uv run thunder-forge deploy
```

This will:
1. Run `ensure-models` (idempotent, skip with `--skip-models`)
2. Validate memory budgets
3. Generate LiteLLM proxy config
4. Upgrade all `uv` tools on each node to latest versions
5. Deploy launchd plists to inference nodes
6. Restart LiteLLM proxy
7. Health-poll each service (up to 180s timeout)

Deploy to a single node (LiteLLM failure is a warning, not an error):

```bash
uv run thunder-forge deploy --node msm1
```

Skip model sync (useful when models are already present):

```bash
uv run thunder-forge deploy --skip-models
```

## Step 7: Generate LiteLLM Config (standalone)

If you only need to regenerate the proxy config without a full deploy:

```bash
uv run thunder-forge generate-config
```

Validate without writing:

```bash
uv run thunder-forge generate-config --check
```

## Step 8: Verify Cluster Health

```bash
uv run thunder-forge health
```

Example output:

```
=== Inference ===
  msm1:8000 (coder): healthy
  msm2:8000 (coder): healthy

=== Infrastructure ===
  LiteLLM      healthy
  Open WebUI   healthy
  PostgreSQL   healthy
```

## Accessing Services

- **LiteLLM API**: `http://<infra-node-ip>:4000` — OpenAI-compatible endpoint
- **Open WebUI**: `http://<infra-node-ip>:8080` — chat interface (credentials in `docker/.env`)

Example API call:

```bash
curl http://<infra-node-ip>:4000/v1/chat/completions \
  -H "Authorization: Bearer $(grep LITELLM_MASTER_KEY ~/thunder-forge/docker/.env | cut -d= -f2)" \
  -H "Content-Type: application/json" \
  -d '{"model": "coder", "messages": [{"role": "user", "content": "Hello"}]}'
```

## Troubleshooting

**Model won't load (health check fails):**
```bash
# Check vllm-mlx logs on the inference node
ssh <user>@<node-ip> "tail -50 ~/logs/vllm-mlx-8000.err"
```

**Manually restart a service on a node:**
```bash
ssh <user>@<node-ip> 'launchctl bootout gui/$(id -u)/com.vllm-mlx-8000 2>/dev/null; launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.vllm-mlx-8000.plist'
```

**Docker services unhealthy:**
```bash
ssh <user>@<infra-ip> "cd ~/thunder-forge/docker && docker compose ps && docker compose logs --tail=50"
```

**Memory budget exceeded:**
```bash
uv run thunder-forge generate-config --check
# Shows per-node memory breakdown
```

**Re-deploy after config change:**
```bash
# Edit node-assignments.yaml, then:
uv run thunder-forge deploy
```

## Setup Script Variables

These variables configure `scripts/setup-node.sh`. Set them in the project root `.env` file or export as environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `TF_DIR` | `~/thunder-forge` | Clone location |
| `TF_LOG_DIR` | `~/logs` | Log directory (inference nodes) |
| `TF_SSH_KEY` | `~/.ssh/id_ed25519` | SSH key path |
| `TF_REPO_URL` | `https://github.com/shared-goals/thunder-forge.git` | Git clone URL |
| `HF_HOME` | `~/.cache/huggingface` | HuggingFace cache directory |
| `TF_DISABLE_SLEEP` | `true` | Disable macOS sleep on inference nodes |
