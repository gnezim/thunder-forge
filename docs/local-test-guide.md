# Thunder Forge — Local Test Guide

Minimal end-to-end test of the full pipeline on two machines using the setup scripts.

| Role | Machine | IP | What it does |
|------|--------|----|-------------|
| inference | MacBook Air 24GB | 192.168.88.19 | vllm-mlx serving Llama-3.2-3B |
| infra | Linux VM | 192.168.88.167 (SSH port 2298) | Docker Compose: LiteLLM + Open WebUI + PostgreSQL |

---

## Step 1: Bootstrap the inference node (MacBook Air)

SSH into the Air:

```bash
ssh gnezim@192.168.88.19
```

On the Air:

```bash
# Clone the repo (skip if already cloned)
git clone https://github.com/shared-goals/thunder-forge.git ~/thunder-forge

# Create .env to skip sleep disable (it's a laptop, not a headless server)
cat > ~/thunder-forge/.env <<'EOF'
TF_DISABLE_SLEEP=false
EOF

# Run the setup script
cd ~/thunder-forge
zsh scripts/setup-node.sh node
```

Expected output:
```
=== Thunder Forge Node Bootstrap ===
Role: node
...
=== Node setup complete ===
  Homebrew: ...
  uv:       ...
  vllm-mlx: ...
  Logs:     /Users/gnezim/logs
```

Exit back to your workstation:

```bash
exit
```

## Step 2: Bootstrap the infra node (Linux VM)

SSH into the Linux VM:

```bash
ssh gnezim@192.168.88.167
```

On the Linux VM:

```bash
# Clone the repo (skip if already cloned)
git clone https://github.com/shared-goals/thunder-forge.git ~/thunder-forge

# Create .env
cat > ~/thunder-forge/.env <<'EOF'
TF_SSH_USER=gnezim
EOF

# Run the setup script
cd ~/thunder-forge
bash scripts/setup-node.sh gateway
```

Expected output:
```
=== Thunder Forge Node Bootstrap ===
Role: gateway
...
=== Gateway setup complete ===
  Docker:       ...
  uv:           ...
  Compose:      running
```

> **Note:** The script generates `docker/.env` with random secrets and starts Docker Compose automatically. Save the credentials from `~/thunder-forge/docker/.env`.

Exit back to your workstation:

```bash
exit
```

## Step 3: Generate and copy LiteLLM config

On your workstation:

```bash
# Generate litellm-config.yaml from node-assignments.yaml
uv run thunder-forge generate-config
```

Expected:
```
Validating memory budgets...
  air: test(1.8+0.0kv) + 8 OS = 9.8 GB / 24 GB ✅
✅ Generated .../configs/litellm-config.yaml
```

Copy the config to the Linux VM and restart LiteLLM:

```bash
scp -P 2298 configs/litellm-config.yaml gnezim@192.168.88.167:~/thunder-forge/configs/
ssh gnezim@192.168.88.167 "cd ~/thunder-forge/docker && docker compose restart litellm"
```

## Step 4: Download the test model

On your workstation:

```bash
ssh gnezim@192.168.88.19 'zsh -lc "hf download mlx-community/Llama-3.2-3B-Instruct-4bit --revision main"'
```

Verify:

```bash
ssh gnezim@192.168.88.19 "ls ~/.cache/huggingface/hub/models--mlx-community--Llama-3.2-3B-Instruct-4bit/snapshots/"
```

## Step 5: Deploy vllm-mlx service

On your workstation:

```bash
uv run thunder-forge deploy --node air --skip-models
```

This will:
1. Upload a launchd plist to the Air
2. Start vllm-mlx via launchctl
3. Restart LiteLLM on the infra node
4. Health-poll `http://192.168.88.19:8000/v1/models` (up to 180s)

## Step 6: Verify cluster health

```bash
uv run thunder-forge health
```

Expected:

```
=== Inference ===
  air:8000 (test): ✅

=== Infrastructure ===
  LiteLLM      ✅
  Open WebUI   ✅
  PostgreSQL   ✅
```

## Step 7: Test end-to-end inference

Get the LiteLLM key from the Linux VM:

```bash
ssh gnezim@192.168.88.167 "grep LITELLM_MASTER_KEY ~/thunder-forge/docker/.env"
```

Send a request through the proxy:

```bash
curl http://192.168.88.167:4000/v1/chat/completions \
  -H "Authorization: Bearer <LITELLM_MASTER_KEY from above>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "test",
    "messages": [{"role": "user", "content": "Say hello in 5 words"}],
    "max_tokens": 50
  }'
```

## Step 8: Open WebUI

Open in browser: **http://192.168.88.167:8080**

Get credentials:

```bash
ssh gnezim@192.168.88.167 "grep -E 'UI_USERNAME|UI_PASSWORD' ~/thunder-forge/docker/.env"
```

Select the `test` model and send a message.

---

## Cleanup

Stop vllm-mlx on the Air:

```bash
ssh gnezim@192.168.88.19 'launchctl bootout gui/$(id -u)/com.vllm-mlx-8000 2>/dev/null; rm ~/Library/LaunchAgents/com.vllm-mlx-8000.plist'
```

Stop Docker on the Linux VM:

```bash
ssh gnezim@192.168.88.167 "cd ~/thunder-forge/docker && docker compose down"
```

## Troubleshooting

**vllm-mlx won't start:**
```bash
ssh gnezim@192.168.88.19 "tail -50 ~/logs/vllm-mlx-8000.err"
```

**LiteLLM can't reach inference node:**
```bash
ssh gnezim@192.168.88.167 "curl -s http://192.168.88.19:8000/v1/models"
```

**Docker services unhealthy:**
```bash
ssh gnezim@192.168.88.167 "cd ~/thunder-forge/docker && docker compose logs --tail=30"
```
