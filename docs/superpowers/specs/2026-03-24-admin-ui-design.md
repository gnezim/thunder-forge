# Design: Thunder Forge Admin UI

**Date:** 2026-03-24
**Status:** Approved
**Sub-project:** 1 of 3 (Admin UI: config + deploy)

## Context

Thunder Forge manages an MLX inference cluster via a CLI tool (`thunder-forge`). The cluster config (`node-assignments.yaml`) is gitignored because the repo is public and all config data is private (IPs, users, model assignments). Currently config is edited manually on the gateway host.

This design adds a web-based admin UI for managing config, triggering deploys, and viewing cluster health. It replaces manual YAML editing and the GitHub Actions deploy pipeline.

**Related sub-projects (separate specs):**
- Sub-project 2: Monitoring integration in admin UI (depends on this)
- Sub-project 3: Repository sanitization (independent)

## Architecture

```
+--------------------------------------------------+
|  Gateway                                          |
|                                                   |
|  Docker Compose                                   |
|  +----------+  +----------+  +----------+        |
|  | Postgres |<-| LiteLLM  |  | OpenWebUI|        |
|  |  :5432   |  |  :4000   |  |  :8080   |        |
|  +----+-----+  +----------+  +----------+        |
|       |                                           |
|  +----+-----+                                     |
|  | Admin UI |--SSH--> gateway host                |
|  | Streamlit|         +-> thunder-forge deploy    |
|  |  :8501   |         +-> thunder-forge health    |
|  +----------+                                     |
|                                                   |
|  Gateway host (native)                            |
|  +-> thunder-forge CLI                            |
|  +-> SSH keys -> nodes                            |
+--------------------------------------------------+

         SSH                SSH
          |                  |
     +----+----+        +----+----+
     |  Node   |  ...   |  Node   |
     | mlx-lm  |        | mlx-lm  |
     |  :8000  |        |  :8000  |
     +---------+        +---------+
```

**Key decisions:**
- Admin UI runs in Docker, calls thunder-forge on gateway host via SSH
- Config stored in Postgres (separate database from LiteLLM), not on disk or in git
- Deploy flow: read config from DB -> generate YAML -> SSH copy to gateway -> SSH run thunder-forge
- Streamlit for rapid development, minimal frontend code

## Data Model

Separate database `thunder_admin` in the existing Postgres instance. Created via init script (`docker-entrypoint-initdb.d`).

```sql
-- Operators
CREATE TABLE users (
    id            SERIAL PRIMARY KEY,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    is_admin      BOOLEAN DEFAULT FALSE,
    created_at    TIMESTAMPTZ DEFAULT now()
);

-- Full snapshot of node-assignments config as JSONB (append-only)
CREATE TABLE config_versions (
    id            SERIAL PRIMARY KEY,
    config        JSONB NOT NULL,
    author_id     INTEGER REFERENCES users(id),
    comment       TEXT,
    created_at    TIMESTAMPTZ DEFAULT now()
);

-- Deploy log
CREATE TABLE deploys (
    id            SERIAL PRIMARY KEY,
    config_id     INTEGER REFERENCES config_versions(id),
    triggered_by  INTEGER REFERENCES users(id),
    status        TEXT NOT NULL DEFAULT 'running',  -- running, success, failed
    output        TEXT,
    started_at    TIMESTAMPTZ DEFAULT now(),
    finished_at   TIMESTAMPTZ
);
```

**JSONB schema:** The `config` column stores the exact structure of `node-assignments.yaml` parsed to JSON. This includes all top-level keys: `models` (with full `source` sub-object: `type`, `repo`, `revision`, `quantize`, `path`, `package`, `weight_repo`), `nodes`, `assignments`, and `external_endpoints`. All fields are preserved on roundtrip — the UI exposes common fields for editing, but the JSONB stores the complete structure including `extra_args`, `serving`, `notes`, etc.

Example JSONB:
```json
{
  "models": {
    "qwen3-30b": {
      "source": {"type": "huggingface", "repo": "mlx-community/Qwen3-30B-A3B-4bit", "revision": "main"},
      "disk_gb": 18, "ram_gb": 15, "kv_per_32k_gb": 0,
      "active_params": "3B of 30B", "max_context": 131072,
      "extra_args": null, "serving": "", "notes": "..."
    }
  },
  "nodes": {
    "msm1": {"ip": "192.168.1.101", "ram_gb": 128, "role": "node", "user": "admin"}
  },
  "assignments": {
    "msm1": [{"model": "qwen3-30b", "port": 8000, "embedding": false}]
  },
  "external_endpoints": [
    {"model_name": "qwen3-30b", "api_base": "http://...", "api_key_env": "FINN_LITELLM_KEY"}
  ]
}
```

**Node user field:** Stored explicitly in JSONB. If empty/null, thunder-forge falls back to `TF_SSH_USER` at deploy time (existing behavior). Admin UI shows the field as optional with placeholder showing the env var fallback.

**Logic:**
- Each config save = new row in `config_versions` (immutable, append-only)
- "Current config" = `SELECT * FROM config_versions ORDER BY id DESC LIMIT 1`
- Rollback = create new version copying JSONB from an older version
- Each deploy references a specific config version
- "Last deployed config" for diff = `config_id` from most recent `deploys` row where `status = 'success'`
- `users.is_admin` controls access to user management page

## UI Pages

### 1. Dashboard

- Cluster health status (calls `thunder-forge health --skip-preflight`)
- Summary: node count, model count, last deploy timestamp + status
- Quick links: Grafana, Open WebUI, LiteLLM UI (configurable URLs)

### 2. Config: Models

- Table of models from current config (name, repo, disk_gb, max_context, active_params)
- Add model: enter HF repo name, auto-fill from HuggingFace API (see Model Add Flow below)
- Edit / delete model
- Save = new config version in Postgres

### 3. Config: Nodes

- Table of nodes (name, ip, ram_gb, role, user)
- Add / edit / delete node
- Save = new config version

### 4. Config: Assignments

- Which model on which node, port
- Memory budget validation in real-time (same formula as `thunder-forge generate-config`)
- Visual indicator: green (fits), red (exceeds RAM)
- Save = new config version

### 4b. Config: External Endpoints

- Table of external LiteLLM/OpenAI-compatible endpoints (model_name, api_base, api_key_env)
- Add / edit / delete
- Save = new config version

### 5. Deploy

- Shows diff: current config vs last deployed config version
- "Deploy" button triggers the deploy flow
- Streaming stdout/stderr output in real-time
- Deploy history table (from `deploys` table)
- Status badges: running (spinner), success (green), failed (red)

### 6. Users (admin only)

- List users, create new user, delete user
- Reset password (generates new random password, displays once)

## Model Add Flow

When adding a model, the admin UI fetches metadata from HuggingFace API to auto-fill fields.

**Auto-filled from HF API (`GET https://huggingface.co/api/models/{repo}`):**
- `disk_gb` — sum of safetensors file sizes from repo siblings
- `max_context` — from config.json (`max_position_embeddings` or model-specific field)
- `active_params` — parsed from model name (e.g. `Qwen3-30B-A3B-4bit` -> "3B of 30B")
- `kv_per_32k_gb` — computed from config.json: `num_kv_heads * head_dim * num_layers * 2 * 2 * 32768 / 1e9`
- `revision` — current main commit hash

**Manual / optional fields:**
- `ram_gb` — override if runtime RAM differs from disk_gb
- `notes` — free text
- `serving` — embedding, cli, mlx-openai-server, or blank (default: served by mlx_lm.server)

**Validation before save:**
- HF repo exists and is accessible
- Contains safetensors files (not GGUF, not empty)
- Contains `tokenizer_config.json` (required by mlx_lm.server)
- disk_gb fits on at least one node in the cluster

**Fallback:** If gateway has no internet access to HF, all fields are manual input.

## Deploy Flow (detailed)

1. User clicks "Deploy" in UI
2. Admin UI reads current config from Postgres
3. Generates `node-assignments.yaml` from JSONB
4. SSH copies YAML to gateway host: `configs/node-assignments.yaml`
5. SSH runs on gateway host:
   ```bash
   cd /path/to/thunder-forge && \
   uv run thunder-forge generate-config && \
   uv run thunder-forge ensure-models && \
   uv run thunder-forge deploy
   ```
6. Deploy runs in a background thread. SSH channel reads stdout/stderr in chunks, appends to `deploys.output` column incrementally
7. UI polls `deploys.output` using `st.empty()` + `time.sleep(1)` loop with `st.rerun()`, showing growing log
8. On completion: thread updates `deploys.status` and `deploys.finished_at`
9. UI detects status change, shows final result with link to deploy details

## SSH Configuration

Admin UI container connects to gateway host via SSH:

```yaml
# docker-compose.yml
admin-ui:
  environment:
    GATEWAY_SSH_HOST: host.docker.internal
    GATEWAY_SSH_USER: ${TF_SSH_USER}
    GATEWAY_SSH_KEY: /ssh/id_ed25519
    THUNDER_FORGE_DIR: /path/to/thunder-forge
  volumes:
    - ${TF_SSH_KEY:-~/.ssh/id_ed25519}:/ssh/id_ed25519:ro
  extra_hosts:
    - "host.docker.internal:host-gateway"
```

**SSH requirements:**
- Gateway host must be running `sshd`
- SSH key mounted read-only. Container entrypoint must `cp /ssh/id_ed25519 /tmp/ssh_key && chmod 400 /tmp/ssh_key` (bind-mounted files may have wrong permissions)
- SSH connections use `StrictHostKeyChecking=no` and `UserKnownHostsFile=/dev/null` (internal network, no host key verification needed)
- Gateway host user must have: access to thunder-forge repo directory, `uv` installed, SSH keys to all nodes

**THUNDER_FORGE_DIR:** Required env var, no default. Container startup validates it is set and fails loudly if missing.

## Bootstrap and First Run

On first container start:

1. Connect to Postgres, create `thunder_admin` database if not exists
2. Run migrations (create tables)
3. If `users` table is empty, create admin account:
   ```
   First run detected. Admin account created:
     Username: admin
     Password: <random-generated>
   Save this password!
   ```
4. Password printed to container stdout (visible in `docker logs admin-ui`)

## CLI Commands

Run inside the admin-ui container:

```bash
# Reset a user's password (prints new password to stdout)
docker exec admin-ui python -m thunder_admin reset-password <username>

# Create a new user
docker exec admin-ui python -m thunder_admin create-user <username>

# Import existing node-assignments.yaml as first config version
docker exec admin-ui python -m thunder_admin import-config /path/to/node-assignments.yaml

# List users
docker exec admin-ui python -m thunder_admin list-users
```

## Docker Compose Addition

```yaml
admin-ui:
  build: ./admin
  container_name: admin-ui
  restart: unless-stopped
  ports:
    - "${ADMIN_PORT:-8501}:8501"
  environment:
    DATABASE_URL: postgresql://thunder_admin:${ADMIN_DB_PASSWORD:-admin-local}@postgres:5432/thunder_admin
    GATEWAY_SSH_HOST: host.docker.internal
    GATEWAY_SSH_USER: ${TF_SSH_USER:-serpo}
    GATEWAY_SSH_KEY: /ssh/id_ed25519
    THUNDER_FORGE_DIR: ${THUNDER_FORGE_DIR:?THUNDER_FORGE_DIR must be set}
  volumes:
    - ${TF_SSH_KEY:-~/.ssh/id_ed25519}:/ssh/id_ed25519:ro
  extra_hosts:
    - "host.docker.internal:host-gateway"
  depends_on:
    postgres: { condition: service_healthy }
  healthcheck:
    test: ["CMD", "curl", "-sf", "http://localhost:8501/_stcore/health"]
    interval: 15s
    timeout: 5s
    retries: 3
  networks: [infra]
```

Postgres init script addition:

```bash
# docker/init-db.sh
#!/bin/bash
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<-EOSQL
    CREATE USER thunder_admin WITH PASSWORD '${ADMIN_DB_PASSWORD:-admin-local}';
    CREATE DATABASE thunder_admin OWNER thunder_admin;
EOSQL
```

Setting `OWNER thunder_admin` on the database means all tables created by the thunder_admin user will be fully accessible without additional grants.

## Authentication

- Per-user credentials stored in `users` table (bcrypt-hashed passwords)
- Streamlit session state for login (no external auth provider)
- First user auto-created at bootstrap
- Password reset via CLI only (no "forgot password" in UI)
- `is_admin` flag controls access to Users page

## What This Does NOT Include

- **Monitoring dashboards** — Grafana already handles this. Admin UI links to Grafana.
- **Log viewing** — VictoriaLogs + Grafana handles this.
- **Model serving configuration** (batching params, etc.) — thunder-forge defaults are sufficient.
- **Multi-cluster support** — one admin UI per gateway. Future consideration.
- **HTTPS/TLS** — assumed internal network. Add reverse proxy if needed.
- **GitHub Actions pipeline** — replaced by admin UI deploy. deploy.yml can be removed.

## Migration Path

For existing clusters:

1. Add admin-ui service to docker-compose.yml
2. `docker compose up -d` (starts admin-ui, creates database)
3. Note admin password from `docker logs admin-ui`
4. Import existing config: `docker cp configs/node-assignments.yaml admin-ui:/tmp/ && docker exec admin-ui python -m thunder_admin import-config /tmp/node-assignments.yaml`
5. Verify config in UI, test deploy
6. Remove GitHub Actions workflow (deploy.yml) if no longer needed

## File Structure

```
admin/
  Dockerfile
  requirements.txt          # streamlit, psycopg, paramiko, bcrypt, pyyaml
  thunder_admin/
    __init__.py
    __main__.py             # CLI entry point (reset-password, create-user, import-config)
    app.py                  # Streamlit app entry point
    db.py                   # Database connection, migrations, queries
    auth.py                 # Login, session management, password hashing
    config.py               # Config CRUD, YAML generation, HF API integration
    deploy.py               # SSH execution, deploy orchestration
    pages/
      dashboard.py
      models.py
      nodes.py
      assignments.py
      external_endpoints.py
      deploy.py
      users.py
```
