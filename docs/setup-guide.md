# Thunder Forge Setup Guide

Get your MLX inference cluster running. This guide ends when you reach the Admin UI — the UI handles cluster configuration, model assignment, and deployment from there.

## Quick Start

### Prerequisites

- **Gateway**: Linux or macOS machine with Docker + Docker Compose installed
- **Compute nodes**: one or more macOS Apple Silicon machines
- All nodes on the same network with SSH access between them

Verify Docker is installed on the gateway:

```bash
docker info
```

### Step 1: Clone & Configure

On the gateway node:

```bash
git clone https://github.com/shared-goals/thunder-forge.git ~/thunder-forge
cd ~/thunder-forge
cp docker/.env.example docker/.env
```

Open `docker/.env` and fill in the **6 required fields**. Generate a value for each secret:

```bash
openssl rand -hex 32
```

```bash
LITELLM_MASTER_KEY=<generated>           # API key for LiteLLM proxy
POSTGRES_PASSWORD=<generated>            # PostgreSQL password
WEBUI_SECRET_KEY=<generated>             # Open WebUI session encryption
ADMIN_DB_PASSWORD=<generated>            # Thunder Admin database password
GATEWAY_SSH_USER=<your-username>         # SSH user on this gateway machine
THUNDER_FORGE_DIR=~/thunder-forge        # Absolute path to this repo
```

All other values in `docker/.env` have safe defaults.

### Step 2: Generate SSH Key for Node Access

```bash
bash scripts/setup-node.sh gateway
```

This generates the SSH keypair the Admin UI uses to reach compute nodes. The public key is printed at the end — copy it for the next step.

### Step 3: Bootstrap Compute Nodes

On **each macOS compute node**, run:

```bash
git clone https://github.com/shared-goals/thunder-forge.git ~/thunder-forge
cd ~/thunder-forge
zsh scripts/setup-node.sh node
```

Then add the gateway's public key (from Step 2) to the node's authorized keys:

```bash
echo "<public-key-from-step-2>" >> ~/.ssh/authorized_keys
```

Verify connectivity from the gateway:

```bash
ssh -i ~/.ssh/thunder_forge <user>@<node-ip> echo ok
```

### Step 4: Start the Docker Stack

On the gateway:

```bash
cd ~/thunder-forge/docker
docker compose up -d
docker compose ps   # all services should show "Up"
```

Four services start: PostgreSQL, LiteLLM proxy, Open WebUI, Admin UI.

### Step 5: Open the Admin UI

Navigate to `http://<gateway-ip>:8501`

First run creates the admin account and prompts for credentials. The Admin UI guides you through adding nodes, models, and deploying the cluster from here.

---

## Reference

### Environment Variables

#### `docker/.env` — Docker stack

The main config file. Most users only need this one.

**Core secrets** — all required. Generate each with `openssl rand -hex 32`:

| Variable | Description |
|---|---|
| `LITELLM_MASTER_KEY` | API key for the OpenAI-compatible LiteLLM proxy |
| `POSTGRES_PASSWORD` | PostgreSQL password |
| `WEBUI_SECRET_KEY` | Open WebUI session encryption key |
| `ADMIN_DB_PASSWORD` | Thunder Admin database password |

**Admin UI — SSH access:**

| Variable | Required | Default | Description |
|---|---|---|---|
| `GATEWAY_SSH_USER` | ✅ | — | SSH user on the gateway node |
| `GATEWAY_SSH_KEY` | ✅ | `~/.ssh/thunder_forge` | Path to SSH private key on the host (mounted read-only into the container) |
| `GATEWAY_SSH_HOST` | Optional | `localhost` | Gateway hostname or IP. Defaults to localhost since the Admin UI runs on the gateway. |
| `GATEWAY_SSH_PORT` | Optional | `22` | SSH port |

**Admin UI — Thunder Forge integration:**

| Variable | Required | Default | Description |
|---|---|---|---|
| `THUNDER_FORGE_DIR` | ✅ | — | Absolute path to the thunder-forge repo on the gateway |
| `HF_TOKEN` | Optional | — | HuggingFace token for private or gated models |
| `SESSION_TIMEOUT_HOURS` | Optional | `24` | Admin UI session timeout in hours |
| `DISPLAY_TZ` | Optional | `UTC` | Timezone for timestamps in the Admin UI (e.g. `Europe/Moscow`) |

**Open WebUI:**

| Variable | Required | Default | Description |
|---|---|---|---|
| `UI_USERNAME` | Optional | `admin` | Chat interface username |
| `UI_PASSWORD` | Optional | — | Chat interface password |
| `WEBUI_AUTH` | Optional | `true` | Require login |
| `WEBUI_PORT` | Optional | `8080` | Open WebUI port |
| `ENABLE_SIGNUP` | Optional | `false` | Allow new user registration |

**LiteLLM & monitoring:**

| Variable | Required | Default | Description |
|---|---|---|---|
| `LITELLM_WORKERS` | Optional | `4` | LiteLLM worker processes (increase for high request volume) |
| `PG_PORT` | Optional | `5434` | PostgreSQL port (non-standard to avoid conflicts with a local Postgres instance) |
| `GRAFANA_URL` | Optional | — | Grafana dashboard URL, shown as a link in the Admin UI |

#### `.env` (repo root) — CLI only

Only needed when running `thunder-forge` CLI commands directly. If you use the Admin UI exclusively, you can skip this file.

| Variable | Required | Description |
|---|---|---|
| `TF_SSH_USER` | Yes | Default SSH user for compute nodes |
| `TF_SSH_KEY` | Yes | Path to SSH key for node access |
| `HF_HOME` | Optional | HuggingFace cache directory |
| `HF_TOKEN` | Optional | Private model access |
| `TF_DIR` | Optional | Path to thunder-forge repo |
| `TF_DISABLE_SLEEP` | Optional | Disable macOS sleep on nodes (`true`/`false`) |

---

### Updating a Running Cluster

#### Code update (no config changes)

```bash
git pull
cd docker && docker compose up -d --build
```

Docker volumes (database, config history) are preserved. The Admin UI retains all config versions across updates.

#### Env var changes

Edit `docker/.env`, then restart:

```bash
cd docker && docker compose up -d
```

Only containers whose environment changed will restart. Data is preserved.

**Rotating secrets** (`LITELLM_MASTER_KEY`, `WEBUI_SECRET_KEY`, `ADMIN_DB_PASSWORD`):
1. Generate a new value: `openssl rand -hex 32`
2. Update `docker/.env`
3. `docker compose up -d`
4. Update any API clients that used the old `LITELLM_MASTER_KEY`

**⚠️ Special case: `POSTGRES_PASSWORD`**

The Postgres password is written into the database volume on first run. Changing `.env` alone after that will break the connection — the value in the database must be updated first:

```bash
# Update the password in the running database
docker compose exec postgres psql -U litellm -c "ALTER USER litellm PASSWORD 'new-password';"
docker compose exec postgres psql -U postgres -c "ALTER USER thunder_admin PASSWORD 'new-password';"

# Then update .env and restart
docker compose up -d
```

#### Database schema migrations

Migrations run automatically on every container startup. A `git pull` + `docker compose up -d --build` is always sufficient — no manual migration commands needed.

#### Model cache

Model weights live on compute nodes, not in Docker volumes. Adding new models is always safe via the Admin UI.

To free disk space after removing a model from the config:

```bash
ssh <user>@<node-ip> "rm -rf ~/.cache/huggingface/hub/<model-repo-name>"
```

---

### Troubleshooting

**Check stack health:**

```bash
cd ~/thunder-forge/docker
docker compose ps                        # all services should show "Up"
docker compose logs --tail=50 <service>  # logs for a specific service
```

**Admin UI not reachable on port 8501:**
- Admin UI runs in host network mode — check firewall rules on the gateway
- Check logs: `docker compose logs --tail=50 admin-ui`

**LiteLLM not responding on port 4000:**
- PostgreSQL must be healthy first: `docker compose logs postgres`
- LiteLLM depends on postgres — check `docker compose ps`

**All containers exit immediately after `docker compose up`:**
Missing required `.env` values — check logs for the specific variable name: `docker compose logs`

**Admin UI can't reach gateway via SSH:**

```bash
# Check the SSH key is accessible inside the container
docker compose exec admin-ui ls -la $GATEWAY_SSH_KEY

# Test SSH from inside the container
docker compose exec admin-ui ssh -i $GATEWAY_SSH_KEY $GATEWAY_SSH_USER@$GATEWAY_SSH_HOST echo ok
```

Also verify `THUNDER_FORGE_DIR` exists on the gateway.

**Compute node unreachable:**
- Verify the public key is in `~/.ssh/authorized_keys` on the node
- Test from the gateway: `ssh -i ~/.ssh/thunder_forge <user>@<node-ip> echo ok`

**mlx-lm service not starting on a compute node:**

```bash
ssh <user>@<node-ip> "tail -50 ~/logs/mlx-lm-<port>.err"
```

**HuggingFace offline error / model not loading:**
Model cache is incomplete. Trigger model sync from the Admin UI deploy page, or run directly:

```bash
cd ~/thunder-forge && uv run thunder-forge ensure-models
```

**`POSTGRES_PASSWORD` changed after first run:**
Containers fail to connect. Follow the rotation procedure under [Env var changes](#env-var-changes).

**LiteLLM `IsADirectoryError`:**
`configs/litellm-config.yaml` doesn't exist — Docker created a directory instead. Fix:

```bash
cd ~/thunder-forge/docker && docker compose down
rm -rf ~/thunder-forge/configs/litellm-config.yaml
uv run thunder-forge generate-config
docker compose up -d
```

**Port conflict on Open WebUI (port 8080 already in use):**

```bash
echo 'WEBUI_PORT=8081' >> ~/thunder-forge/docker/.env
cd ~/thunder-forge/docker && docker compose up -d
```

---

## Appendix: Local Test Setup

For a minimal local test on a single machine with a Linux VM, see [`docs/local-test-guide.md`](local-test-guide.md).
