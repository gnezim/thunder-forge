# Session Updates — 2026-03-15

Tasks completed during this session, covering model registry sync, setup script improvements, documentation, and config corrections.

---

## Task 1: Sync model registry with knowledge base

**Commits:** `8f83167`

**Problem:** `configs/node-assignments.yaml` only contained the `coder` model with incorrect KV cache and context values. The Obsidian knowledge base had been updated with new models and corrected specs.

**Changes:**
- [x] Fix `coder` model: `kv_per_32k_gb` 8 → 0.75 (Gated DeltaNet hybrid attention), `max_context` 131072 → 262144
- [x] Add `general` model (Qwen3.5-35B-A3B-4bit, 20.4 GB, multimodal MoE)
- [x] Add `fast` model (Qwen3.5-9B-MLX-4bit, 5.6 GB, dense)
- [x] Add `embedding` model (Qwen3-Embedding-0.6B-4bit-DWQ, serving: embedding)
- [x] Add `image-gen` model (Z-Image-Turbo via mflux, serving: mlx-openai-server)
- [x] Add `video-gen` model (LTX-2 distilled via mlx-video, serving: cli)
- [x] Add retired model comments (gpt-oss, Qwen3-Next-80B-A3B)
- [x] Add source type comments to registry header

**Files:** `configs/node-assignments.yaml`

---

## Task 2: Make setup-node.sh paths configurable

**Commits:** `de47fb4`, `2db6645`

**Problem:** All paths in `scripts/setup-node.sh` were hardcoded (`~/thunder-forge`, `~/logs`, `~/.ssh/id_ed25519`, clone URL), making it inflexible for non-default deployments.

**Changes:**
- [x] Extract hardcoded paths into env vars with defaults: `TF_DIR`, `TF_LOG_DIR`, `TF_SSH_KEY`, `TF_REPO_URL`
- [x] Add `.env` file loading (checks `scripts/.env` then `~/.thunder-forge.env`)
- [x] Env vars take precedence over `.env` file values
- [x] Document all configurable variables in script header

**Files:** `scripts/setup-node.sh`

---

## Task 3: Create cluster setup guide

**Commits:** `1eadec3`

**Problem:** No user-facing documentation for end-to-end cluster deployment.

**Changes:**
- [x] Write `docs/setup-guide.md` covering full deployment flow
- [x] Document cluster overview (nodes, IPs, roles)
- [x] Step-by-step: bootstrap infra → bootstrap inference → SSH keys → config → models → generate-config → Docker → deploy → health
- [x] Include custom path configuration examples
- [x] Add troubleshooting section (logs, Docker, memory, re-deploy)
- [x] Document service access (LiteLLM API, Open WebUI) with curl example

**Files:** `docs/setup-guide.md`

---

## Task 4: Fix rock user and shell defaults

**Commits:** `59c3fc9`

**Problem:** Rock's SSH user was incorrectly set to `admin` (actual: `infra_user`). The setup script had a `.bashrc` fallback despite zsh being the default shell on all nodes.

**Changes:**
- [x] Update rock user from `admin` to `infra_user` in `configs/node-assignments.yaml`
- [x] Update all test fixtures (4 test files) to use `infra_user` for rock
- [x] Update `docs/setup-guide.md` SSH examples to use `infra_user@192.168.1.61`
- [x] Update `scripts/setup-node.sh` ssh-copy-id output to use `$USER` instead of hardcoded `admin`
- [x] Remove `.bashrc` fallback in infra setup — always write to `~/.zshrc`

**Files:** `configs/node-assignments.yaml`, `scripts/setup-node.sh`, `docs/setup-guide.md`, `tests/test_config.py`, `tests/test_health.py`, `tests/test_deploy.py`, `tests/test_models.py`

---

## Other commits (not from this session)

- `dd659cf` — `.env` added to `.gitignore` (pushed externally between our commits)
