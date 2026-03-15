# Thunder Forge Setup Guide

End-to-end guide for deploying the MLX inference cluster.

## Cluster Overview

| Node | IP | Role | OS | RAM |
|------|-----|------|----|-----|
| rock | 192.168.1.61 | Infrastructure (Docker) | Linux ARM64 | 32 GB |
| msm1 | 192.168.1.101 | Inference (vllm-mlx) | macOS | 128 GB |
| msm2 | 192.168.1.102 | Inference (vllm-mlx) | macOS | 128 GB |
| msm3 | 192.168.1.103 | Inference (vllm-mlx) | macOS | 128 GB |
| msm4 | 192.168.1.104 | Inference (vllm-mlx) | macOS | 128 GB |

**Services on rock:** LiteLLM proxy (:4000), Open WebUI (:8080), PostgreSQL

## Prerequisites

- All nodes on the same LAN (192.168.1.0/24)
- SSH access to each node (`admin` on inference nodes; on rock, the current OS user is used by default)
- macOS on inference nodes, Linux on rock

## Step 1: Bootstrap the Infrastructure Node (rock)

SSH into rock and run the setup script:

```bash
ssh infra_user@192.168.1.61

# Option A: clone the repo and run from there
git clone https://github.com/shared-goals/thunder-forge.git ~/thunder-forge
cd ~/thunder-forge
bash scripts/setup-node.sh infra
```

This will:
- Install Docker Engine and `uv`
- Install `huggingface-cli` (for model downloads)
- Check HuggingFace auth (warns if not logged in)
- Check proxy env vars (warns if `HTTP_PROXY`/`HTTPS_PROXY` not set)
- Clone thunder-forge (if not already cloned)
- Install Python dependencies (`uv sync`)
- Generate `docker/.env` with random secrets (LiteLLM master key, Postgres password, WebUI credentials)
- Start Docker Compose (LiteLLM, Open WebUI, PostgreSQL)
- Generate an SSH keypair at `~/.ssh/id_ed25519`

**Before running**, ensure your proxy is configured if outbound internet is filtered:

```bash
export HTTP_PROXY=http://your-proxy:port
export HTTPS_PROXY=http://your-proxy:port
```

**Save the generated credentials** from `~/thunder-forge/docker/.env` — you'll need the WebUI password to log in.

### Custom paths

Override defaults via environment variables or a `.env` file:

```bash
# Via env vars
TF_DIR=/opt/thunder-forge bash scripts/setup-node.sh infra

# Or create scripts/.env or ~/.thunder-forge.env
cat > ~/.thunder-forge.env <<EOF
TF_DIR=/opt/thunder-forge
TF_SSH_KEY=/home/infra_user/.ssh/cluster_key
TF_REPO_URL=git@github.com:shared-goals/thunder-forge.git
EOF
```

| Variable | Default | Description |
|----------|---------|-------------|
| `TF_DIR` | `~/thunder-forge` | Clone location |
| `TF_LOG_DIR` | `~/logs` | Log directory (inference nodes) |
| `TF_SSH_KEY` | `~/.ssh/id_ed25519` | SSH key path |
| `TF_SSH_USER` | `admin` (inference) / current user (infra) | SSH user for node connections |
| `TF_REPO_URL` | `https://github.com/shared-goals/thunder-forge.git` | Git clone URL |

## Step 2: Bootstrap Inference Nodes (msm1–msm4)

SSH into each Mac Studio and run:

```bash
bash setup-node.sh inference
```

This will:
- Install Homebrew, `uv`, and `vllm-mlx`
- Disable macOS sleep
- Create log directory

Repeat for all four inference nodes.

## Step 3: Distribute SSH Keys

From rock, copy its public key to each inference node so thunder-forge can manage them remotely:

```bash
for ip in 192.168.1.{101,102,103,104}; do
  ssh-copy-id -i ~/.ssh/id_ed25519 admin@$ip
done
```

Verify connectivity:

```bash
for ip in 192.168.1.{101,102,103,104}; do
  ssh -o BatchMode=yes admin@$ip "echo ok" && echo "$ip reachable" || echo "$ip FAILED"
done
```

## Step 4: Configure Model Assignments

Edit `configs/node-assignments.yaml` on rock. The default assigns all nodes to the `coder` model:

```yaml
assignments:
  msm1:
    - model: coder
      port: 8000
  msm2:
    - model: coder
      port: 8000
  msm3:
    - model: coder
      port: 8000
  msm4:
    - model: coder
      port: 8000
```

For a multi-model setup (triple-stack):

```yaml
assignments:
  msm1:
    - model: coder
      port: 8000
    - model: general
      port: 8001
    - model: fast
      port: 8002
  msm2:
    - model: coder
      port: 8000
    - model: general
      port: 8001
    - model: fast
      port: 8002
  # ...
```

See the model registry section at the top of `node-assignments.yaml` for all available models and their memory requirements.

## Step 5: Download Models

From rock:

```bash
cd ~/thunder-forge
uv run thunder-forge ensure-models
```

This downloads models from HuggingFace and syncs them to the assigned inference nodes via rsync. First run can take 30+ minutes depending on model sizes and network speed.

Preview without downloading:

```bash
uv run thunder-forge ensure-models --dry-run
```

## Step 6: Generate LiteLLM Config

```bash
uv run thunder-forge generate-config
```

This reads `node-assignments.yaml`, validates memory budgets, and writes `configs/litellm-config.yaml` with routing rules for the LiteLLM proxy.

Validate without writing:

```bash
uv run thunder-forge generate-config --check
```

## Step 7: Restart Docker Services

After generating the config, restart LiteLLM so it picks up the new routing config:

```bash
cd ~/thunder-forge/docker
docker compose restart litellm
```

Or restart everything:

```bash
docker compose down && docker compose up -d
```

## Step 8: Deploy vllm-mlx Services

```bash
uv run thunder-forge deploy
```

This will:
1. Run `ensure-models` (idempotent)
2. Validate memory budgets
3. Generate and deploy launchd plists to each inference node
4. Start vllm-mlx processes via `launchctl`
5. Set up log rotation
6. Health-poll each service for up to 180s

Deploy to a single node:

```bash
uv run thunder-forge deploy --node msm1
```

## Step 9: Verify Cluster Health

```bash
uv run thunder-forge health
```

Expected output when everything is running:

```
Inference nodes:
  msm1 (192.168.1.101:8000) ✅ coder
  msm2 (192.168.1.102:8000) ✅ coder
  msm3 (192.168.1.103:8000) ✅ coder
  msm4 (192.168.1.104:8000) ✅ coder

Infrastructure (rock):
  litellm    ✅ running
  openwebui  ✅ running
  postgres   ✅ running
```

## Accessing Services

Once deployed:

- **LiteLLM API**: `http://192.168.1.61:4000` — OpenAI-compatible endpoint
- **Open WebUI**: `http://192.168.1.61:8080` — chat interface (credentials in `docker/.env`)

Example API call:

```bash
curl http://192.168.1.61:4000/v1/chat/completions \
  -H "Authorization: Bearer $(grep LITELLM_MASTER_KEY ~/thunder-forge/docker/.env | cut -d= -f2)" \
  -H "Content-Type: application/json" \
  -d '{"model": "coder", "messages": [{"role": "user", "content": "Hello"}]}'
```

## Troubleshooting

**Model won't load (health check fails):**
```bash
# Check vllm-mlx logs on the inference node
ssh admin@192.168.1.101 "cat ~/logs/vllm-mlx-8000.log"
```

**Docker services unhealthy:**
```bash
ssh infra_user@192.168.1.61 "cd ~/thunder-forge/docker && docker compose ps && docker compose logs --tail=50"
```

**Memory budget exceeded:**
```bash
uv run thunder-forge generate-config --check
# Shows per-node memory breakdown with ✅/❌
```

**Re-deploy after config change:**
```bash
# Edit node-assignments.yaml, then:
uv run thunder-forge generate-config
uv run thunder-forge deploy
```
