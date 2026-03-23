# Thunder Forge Setup Guide

End-to-end guide for deploying an MLX inference cluster with Thunder Forge.

## Cluster Architecture

Thunder Forge manages two types of nodes:

- **Gateway node** (Linux) — runs LiteLLM proxy, Open WebUI, PostgreSQL via Docker Compose. Acts as the control plane: downloads models, deploys services, runs health checks.
- **Compute nodes** (macOS with Apple Silicon) — run vllm-mlx services via launchd. Each node serves one or more models on dedicated ports.

## Prerequisites

- All nodes on the same LAN with SSH access between them
- macOS on compute nodes, Linux on the gateway node
- Internet access on the gateway node for model downloads (proxy supported via `HTTP_PROXY`/`HTTPS_PROXY`)

## Step 1: Configure Environment

Create a `.env` file in the project root **before** running the setup script. The same `.env` is used by both `setup-node.sh` and all `thunder-forge` CLI commands.

```bash
# ~/thunder-forge/.env (gateway node)
TF_SSH_USER=admin
TF_SSH_KEY=~/.ssh/id_ed25519
HF_HOME=~/.cache/huggingface
# Required if gateway node uses a proxy for outbound internet:
# HTTP_PROXY=socks5h://127.0.0.1:1080
# HTTPS_PROXY=socks5h://127.0.0.1:1080
```

| Variable | Default | Description |
|----------|---------|-------------|
| `TF_SSH_USER` | `$USER` env var | Default SSH user for all nodes |
| `TF_SSH_KEY` | `~/.ssh/id_ed25519` | SSH key path |
| `TF_DIR` | `~/thunder-forge` | Clone location (set if using a non-default path) |
| `TF_LOG_DIR` | `~/logs` | Log directory on compute nodes |
| `TF_REPO_URL` | `https://github.com/shared-goals/thunder-forge.git` | Git clone URL |
| `HF_HOME` | `~/.cache/huggingface` | HuggingFace cache directory (set to external drive if root partition is small) |
| `TF_DISABLE_SLEEP` | `true` | Disable macOS sleep on compute nodes (set `false` for laptops) |

Per-node user overrides go in `node-assignments.yaml` (see Step 5).

## Step 2: Bootstrap the Gateway Node

SSH into your gateway node, clone the repo and run the setup script:

```bash
git clone https://github.com/shared-goals/thunder-forge.git ~/thunder-forge
cd ~/thunder-forge
# Create .env first (see Step 1), then:
bash scripts/setup-node.sh gateway
```

The script will:
1. Check prerequisites (not root, internet reachable, curl available)
2. Prompt for sudo password upfront
3. Install Docker Engine, `uv`, and `hf` CLI (with `socksio` for proxy support)
4. Clone thunder-forge and install Python dependencies (`uv sync`)
5. Generate `docker/.env` with random secrets (LiteLLM master key, Postgres password, WebUI credentials)
6. Start Docker Compose and wait for services to become healthy
7. Generate an SSH keypair for connecting to compute nodes
8. Verify all tools are installed correctly

**Save the generated credentials** from `~/thunder-forge/docker/.env`.

After setup, authenticate with HuggingFace (required for gated models):

```bash
hf auth login
```

Verify the setup anytime:

```bash
bash scripts/setup-node.sh gateway --check
```

> **Port conflict:** If port 8080 is already in use, add `WEBUI_PORT=<port>` to `docker/.env` and restart: `docker compose up -d`.

## Step 3: Bootstrap Compute Nodes

SSH into each compute node, clone the repo and run the setup script:

```bash
git clone https://github.com/shared-goals/thunder-forge.git ~/thunder-forge
cd ~/thunder-forge
# Optionally create .env (e.g. TF_DISABLE_SLEEP=false for laptops), then:
zsh scripts/setup-node.sh node
```

The script will:
1. Check prerequisites (not root, internet reachable, curl available)
2. Prompt for sudo password upfront (needed for sleep disable)
3. Install Homebrew, `uv`, and `vllm-mlx`
4. Configure PATH in `~/.zshenv` and `~/.zshrc`
5. Optionally disable macOS sleep (controlled by `TF_DISABLE_SLEEP`)
6. Create log directory (`~/logs`)
7. Verify all tools are installed correctly

Repeat for all compute nodes.

Verify any node's setup anytime:

```bash
zsh scripts/setup-node.sh node --check
```

## Step 4: Distribute SSH Keys

From the gateway node, copy its public key to each compute node:

```bash
ssh-copy-id -i ~/.ssh/id_ed25519 <user>@<node-ip>
```

Verify connectivity:

```bash
ssh -o BatchMode=yes <user>@<node-ip> "echo ok"
```

## Step 5: Configure the Cluster

All remaining steps run from the **gateway node** in `~/thunder-forge`.

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

# Node inventory — one gateway node, one or more compute nodes
nodes:
  rock: { ip: "192.168.1.61", ram_gb: 32, role: gateway }
  msm1: { ip: "192.168.1.101", ram_gb: 128, role: node }
  msm2: { ip: "192.168.1.102", ram_gb: 128, role: node }

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
  mynode: { ip: "192.168.1.50", ram_gb: 64, role: node, user: myuser }
```

### Generate LiteLLM config

```bash
uv run thunder-forge generate-config
```

Validates memory budgets and generates `configs/litellm-config.yaml`. Validate without writing:

```bash
uv run thunder-forge generate-config --check
```

## Step 6: Pre-flight Check (recommended)

Before deploying, verify all nodes are reachable and correctly set up:

```bash
uv run thunder-forge health --skip-preflight
```

Or run a dry-run deploy to see exactly what will happen:

```bash
uv run thunder-forge deploy --dry-run
```

This runs pre-flight checks on all nodes (SSH, tools, disk space) then shows the deployment plan without executing. Example output:

```
Pre-flight: 2 nodes OK (msm1, msm2), 1 gateway OK (rock)

Deployment plan:

  msm1 (192.168.1.101) — 1 services:
    [upload] com.vllm-mlx-8000.plist (coder, port 8000)
    [restart] 1 launchd services
    [health] poll /v1/models on ports 8000

  rock (192.168.1.61) — gateway:
    [restart] LiteLLM proxy (docker compose restart litellm)

Run without --dry-run to execute.
```

Review the plan, then proceed to deploy.

## Step 7: Deploy

```bash
uv run thunder-forge deploy
```

This will:
1. Run pre-flight checks on all target nodes (SSH connectivity, tools, disk space)
2. Download models on the gateway and rsync to compute nodes (`ensure-models`)
3. Validate memory budgets
4. Generate LiteLLM proxy config
5. Deploy launchd plists to compute nodes via SSH
6. Restart LiteLLM proxy
7. Health-poll each service (up to 180s timeout)
8. Print a summary showing which nodes succeeded/failed

If a node fails, deployment continues to remaining nodes. Example summary:

```
Deploy complete: 2/2 nodes succeeded
  ✓ msm1 — 1 services running
  ✓ msm2 — 1 services running
```

Deploy to a single node:

```bash
uv run thunder-forge deploy --node msm1
```

Skip model sync (useful when models are already present):

```bash
uv run thunder-forge deploy --skip-models
```

Skip pre-flight checks (when you know the environment is correct):

```bash
uv run thunder-forge deploy --skip-preflight
```

## Step 8: Verify Cluster Health

```bash
uv run thunder-forge health
```

Example output:

```
Pre-flight: 2 nodes OK (msm1, msm2), 1 gateway OK (rock)

=== Nodes ===
  ✓ msm1:8000 (coder)
  ✓ msm2:8000 (coder)

=== Gateway ===
  ✓ LiteLLM
  ✓ Open WebUI
  ✓ PostgreSQL

=== Assignments ===
  msm1: coder:8000
  msm2: coder:8000
```

## Accessing Services

- **LiteLLM API**: `http://<gateway-ip>:4000` — OpenAI-compatible endpoint
- **Open WebUI**: `http://<gateway-ip>:8080` (or custom `WEBUI_PORT`) — chat interface

Credentials are in `~/thunder-forge/docker/.env`.

Example API call:

```bash
curl http://<gateway-ip>:4000/v1/chat/completions \
  -H "Authorization: Bearer $(grep LITELLM_MASTER_KEY ~/thunder-forge/docker/.env | cut -d= -f2)" \
  -H "Content-Type: application/json" \
  -d '{"model": "coder", "messages": [{"role": "user", "content": "Hello"}]}'
```

## Troubleshooting

**Pre-flight fails:**
Pre-flight checks run automatically before deploy, ensure-models, and health commands. If a check fails, the output tells you exactly what's wrong and how to fix it:
```
Pre-flight checks failed:

  msm1 (192.168.1.101):
    ✗ uv not found — run: setup-node.sh node

Fix these issues and retry.
```

**Verify a node's setup without reinstalling:**
```bash
# On a compute node:
zsh scripts/setup-node.sh node --check

# On the gateway:
bash scripts/setup-node.sh gateway --check
```

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

**LiteLLM can't reach a compute node:**
```bash
# Test connectivity from gateway to compute node
curl -s http://<node-ip>:8000/v1/models
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
# Shows per-node memory breakdown with ✓/✗
```

**Re-deploy after config change:**
```bash
# Edit node-assignments.yaml, then:
uv run thunder-forge deploy
```

**Deploy failed on one node — fix and retry just that node:**
```bash
uv run thunder-forge deploy --node msm1
```

**LiteLLM fails with `IsADirectoryError`:**
This happens when `configs/litellm-config.yaml` doesn't exist — Docker creates a directory instead of mounting a file. Fix:
```bash
cd ~/thunder-forge/docker && docker compose down
rm -rf ~/thunder-forge/configs/litellm-config.yaml
uv run thunder-forge generate-config
docker compose up -d
```
