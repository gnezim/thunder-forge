# Thunder Forge Setup Guide

End-to-end guide for deploying an MLX inference cluster with Thunder Forge.

## Cluster Architecture

Thunder Forge manages two types of nodes:

- **Infrastructure (gateway) node** (Linux) — runs LiteLLM proxy, Open WebUI, PostgreSQL via Docker Compose. Acts as the control plane: downloads models, deploys services, runs health checks.
- **Inference nodes** (macOS with Apple Silicon) — run vllm-mlx services via launchd. Each node serves one or more models on dedicated ports.

## Prerequisites

- All nodes on the same LAN with SSH access between them
- macOS on inference nodes, Linux on infrastructure node
- Internet access on the infra node for model downloads (proxy supported via `HTTP_PROXY`/`HTTPS_PROXY`)

## Step 1: Configure Environment (before bootstrap)

Create a `.env` file in the project root **before** running the setup script. The same `.env` is used by both `setup-node.sh` and all `thunder-forge` CLI commands.

```bash
# ~/thunder-forge/.env (infrastructure node)
TF_SSH_USER=admin
TF_SSH_KEY=~/.ssh/id_ed25519
HF_HOME=~/.cache/huggingface
# Required if infra node uses a proxy for outbound internet:
# HTTP_PROXY=socks5h://127.0.0.1:1080
# HTTPS_PROXY=socks5h://127.0.0.1:1080
```

| Variable | Default | Description |
|----------|---------|-------------|
| `TF_SSH_USER` | `admin` (inference) / current user (infra) | Default SSH user for nodes |
| `TF_SSH_KEY` | `~/.ssh/id_ed25519` | SSH key path |
| `TF_DIR` | `~/thunder-forge` | Clone location (set if using a non-default path) |
| `TF_LOG_DIR` | `~/logs` | Log directory on inference nodes |
| `TF_REPO_URL` | `https://github.com/shared-goals/thunder-forge.git` | Git clone URL |
| `HF_HOME` | `~/.cache/huggingface` | HuggingFace cache directory (set to external drive if root partition is small) |
| `TF_DISABLE_SLEEP` | `true` | Disable macOS sleep on inference nodes (set `false` for laptops) |

Per-node user overrides go in `node-assignments.yaml` (see Step 4).

## Step 2: Bootstrap the Infrastructure Node

SSH into your infrastructure node, clone the repo and run the setup script:

```bash
git clone https://github.com/shared-goals/thunder-forge.git ~/thunder-forge
cd ~/thunder-forge
# Create .env first (see Step 1), then:
bash scripts/setup-node.sh gateway
```

> **Note:** The script is POSIX-compatible — runs under both `bash` and `zsh`.

This will:
- Install Docker Engine, `uv`, and `hf` CLI (HuggingFace, with `socksio` for proxy support)
- Install Python dependencies (`uv sync`)
- Generate `docker/.env` with random secrets (LiteLLM master key, Postgres password, WebUI credentials)
- Start Docker Compose (LiteLLM, Open WebUI, PostgreSQL)
- Generate an SSH keypair for connecting to inference nodes

**Save the generated credentials** from `~/thunder-forge/docker/.env`.

> **Port conflict:** If port 8080 is already in use, add `WEBUI_PORT=<port>` to `docker/.env` and restart: `docker compose up -d`.

After setup, authenticate with HuggingFace (required for gated models):

```bash
hf auth login
```

## Step 3: Bootstrap Inference Nodes

SSH into each inference node, clone the repo and run the setup script:

```bash
git clone https://github.com/shared-goals/thunder-forge.git ~/thunder-forge
cd ~/thunder-forge
# Optionally create .env (e.g. TF_DISABLE_SLEEP=false for laptops), then:
zsh scripts/setup-node.sh node
```

This will:
- Install Homebrew, `uv`, and `vllm-mlx`
- Optionally disable macOS sleep (controlled by `TF_DISABLE_SLEEP`)
- Create log directory (`~/logs`) and LaunchAgents directory
- Upgrade all installed `uv` tools to latest versions

Repeat for all inference nodes.

## Step 4: Distribute SSH Keys

From the infrastructure node, copy its public key to each inference node:

```bash
ssh-copy-id -i ~/.ssh/id_ed25519 <user>@<inference-node-ip>
```

Verify connectivity:

```bash
ssh -o BatchMode=yes <user>@<inference-node-ip> "echo ok"
```

## Step 5: Configure the Cluster

All remaining steps run from the **infrastructure node** in `~/thunder-forge`.

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

# Node inventory — one infra node, one or more inference nodes
nodes:
  rock: { ip: "192.168.1.61", ram_gb: 32, role: infra }
  msm1: { ip: "192.168.1.101", ram_gb: 128, role: inference }
  msm2: { ip: "192.168.1.102", ram_gb: 128, role: inference }

# What runs where — one vllm-mlx process per entry
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

### Generate LiteLLM config

```bash
uv run thunder-forge generate-config
```

Validates memory budgets and generates `configs/litellm-config.yaml`. Validate without writing:

```bash
uv run thunder-forge generate-config --check
```

## Step 6: Deploy

```bash
uv run thunder-forge deploy
```

This will:
1. Download models on the infra node and rsync to inference nodes (`ensure-models`)
2. Validate memory budgets
3. Generate LiteLLM proxy config
4. Deploy launchd plists to inference nodes via SSH
5. Restart LiteLLM proxy
6. Health-poll each service (up to 180s timeout)

Deploy to a single node:

```bash
uv run thunder-forge deploy --node msm1
```

Skip model sync (useful when models are already present):

```bash
uv run thunder-forge deploy --skip-models
```

## Step 7: Verify Cluster Health

```bash
uv run thunder-forge health
```

Example output:

```
=== Inference ===
  msm1:8000 (coder): ✅
  msm2:8000 (coder): ✅

=== Infrastructure ===
  LiteLLM      ✅
  Open WebUI   ✅
  PostgreSQL   ✅

=== Model Assignments ===
  msm1: coder:8000
  msm2: coder:8000
```

## Accessing Services

- **LiteLLM API**: `http://<infra-ip>:4000` — OpenAI-compatible endpoint
- **Open WebUI**: `http://<infra-ip>:8080` (or custom `WEBUI_PORT`) — chat interface

Credentials are in `~/thunder-forge/docker/.env`.

Example API call:

```bash
curl http://<infra-ip>:4000/v1/chat/completions \
  -H "Authorization: Bearer $(grep LITELLM_MASTER_KEY ~/thunder-forge/docker/.env | cut -d= -f2)" \
  -H "Content-Type: application/json" \
  -d '{"model": "coder", "messages": [{"role": "user", "content": "Hello"}]}'
```

## Troubleshooting

**Model won't load (health check fails):**
```bash
ssh <user>@<node-ip> "tail -50 ~/logs/vllm-mlx-8000.err"
```

**Manually restart a service on a node:**
```bash
ssh <user>@<node-ip> 'launchctl bootout gui/$(id -u)/com.vllm-mlx-8000 2>/dev/null; launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.vllm-mlx-8000.plist'
```

**Docker services unhealthy:**
```bash
cd ~/thunder-forge/docker && docker compose ps && docker compose logs --tail=50
```

**LiteLLM can't reach inference node:**
```bash
# Test connectivity from infra node to inference node
curl -s http://<inference-ip>:8000/v1/models
```

**Port conflict on Open WebUI:**
```bash
# Add to docker/.env:
echo 'WEBUI_PORT=8081' >> ~/thunder-forge/docker/.env
cd ~/thunder-forge/docker && docker compose up -d
```

**Memory budget exceeded:**
```bash
uv run thunder-forge generate-config
# Shows per-node memory breakdown with ✅/❌
```

**Re-deploy after config change:**
```bash
# Edit node-assignments.yaml, then:
uv run thunder-forge deploy
```

**LiteLLM fails with `IsADirectoryError`:**
This happens when `configs/litellm-config.yaml` doesn't exist — Docker creates a directory instead of mounting a file. Fix:
```bash
cd ~/thunder-forge/docker && docker compose down
rm -rf ~/thunder-forge/configs/litellm-config.yaml
uv run thunder-forge generate-config
docker compose up -d
```
