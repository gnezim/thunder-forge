# Stability Hardening Design

**Date:** 2026-03-23
**Status:** APPROVED

## Problem

The project has ~30 fix commits out of ~70 total — nearly half the history is debugging. Bugs cluster into 5 root causes: cross-platform assumptions, SOCKS proxy/HF download chain issues, hardcoded values, first-run failures, and test/code drift. The deployment workflow involves a remote operator relaying console output, so every bug costs a multi-person round-trip. The project needs to work on first try when deploying to 4× Mac Studio + 1× Radxa ROCK.

## Approach

Targeted hardening (Approach B): restructure the 3 modules that cause 90% of failures (ssh.py, models.py, deploy.py), add pre-flight validation, dry-run mode, and actionable errors. No full rewrite.

---

## Section 1: Centralized Node Configuration

### Problem
Platform details (shell, home dir, Homebrew path, user) are guessed independently in ssh.py, deploy.py, models.py, and config.py. When one guess is wrong, the fix patches that spot but the same assumption lives elsewhere.

### Design
Resolve all platform details once at config load time in config.py, store them on the Node dataclass, use them everywhere.

New resolved fields on Node (computed during pre-flight, not from YAML):

```python
@dataclass
class Node:
    # Existing
    ip: str
    ram_gb: int
    role: str       # "node" or "gateway" (see Section 5)
    user: str

    # Resolved during pre-flight
    platform: str           # "darwin" or "linux"
    shell: str              # "zsh" or "bash" — detected, not assumed
    home_dir: str           # "/Users/admin" or "/home/gnezim"
    homebrew_prefix: str | None  # "/opt/homebrew", "/usr/local", or None
```

**Population:** During pre-flight, SSH to each node once and run a batched probe: `uname -s`, `echo $SHELL`, `echo $HOME`, `brew --prefix 2>/dev/null`. Cache results on Node object.

**Downstream consumers stop guessing:**
- deploy.py reads `node.home_dir` and `node.homebrew_prefix` for plist generation
- ssh.py reads `node.shell` for command wrapping (no more `zsh ... || bash ...` fallback)
- models.py reads `node.home_dir` for rsync destination paths

**Eliminates:**
- Hardcoded `/Users/{user}` in deploy.py
- Hardcoded `/opt/homebrew` in deploy.py
- `_login_shell()` platform guessing in ssh.py
- `zsh -lc ... 2>/dev/null || bash -lc ...` fallback hack in ssh.py
- `os.getlogin()` fragility in config.py

---

## Section 2: Pre-flight Validation System

### Problem
Commands fail mid-execution with cryptic errors — SSH connection refused, uv not found, wrong user, no disk space. Discovered only after remote operator runs the command.

### Design
New file: `src/thunder_forge/cluster/preflight.py`. Runs automatically before deploy, ensure-models, health. Probes every target node, validates environment, populates Node resolved fields or fails with actionable checklist.

**Checks per node:**

| Check | How | Failure message |
|-------|-----|-----------------|
| SSH reachable | `ssh -o ConnectTimeout=10` | "Cannot reach msm1 (192.168.1.101) — check SSH key and network" |
| Correct user | `whoami` | "SSH user 'admin' doesn't exist on msm1 — set user field in YAML" |
| Shell detected | `echo $SHELL` | "Cannot detect shell on msm1" |
| Home dir exists | `echo $HOME && test -d $HOME` | "Home dir doesn't exist on msm1" |
| uv available | `which uv` | "uv not found on msm1 — run setup-node.sh first" |
| vllm-mlx available (nodes) | `uv tool list \| grep vllm` | "vllm-mlx not installed on msm1" |
| Homebrew (macOS only) | `brew --prefix` | "Homebrew not found on msm1" |
| Disk space | `df -g $HOME` | "msm1 has 4GB free — need ~50GB for models" |
| HF_HOME writable (gateway) | `test -w $HF_HOME` | "HF_HOME not writable on rock" |
| Docker running (gateway) | `docker info` | "Docker not running on rock" |

**Behavior:**
- All checks on all target nodes run in parallel (one SSH connection per node, batched probe commands)
- Collects all failures, prints numbered checklist, exits non-zero
- On success: populates Node resolved fields, continues to actual command
- Skippable with `--skip-preflight`

**Example failure output:**
```
Pre-flight checks failed:

  msm1 (192.168.1.101):
    ✗ uv not found — run: zsh scripts/setup-node.sh node
    ✗ 4GB free disk — need ~50GB for model weights

  rock (192.168.1.61):
    ✗ SSH connection refused — check sshd is running

Fix these issues and retry.
```

**Example success output:**
```
Pre-flight: 4 nodes OK (msm1, msm2, msm3, msm4), 1 gateway OK (rock)
```

---

## Section 3: Dry-Run Mode

### Problem
No way to review what a command will do before executing. When something breaks halfway through 20 SSH operations, unclear what already ran and whether it's safe to retry.

### Design
`--dry-run` flag on deploy, ensure-models, and generate-config. Runs pre-flight (real SSH to validate), then prints the execution plan without executing.

**Implementation:** Each operation function (deploy_node, ensure_huggingface, rsync_to_node, etc.) takes a `dry_run: bool` parameter. In dry-run mode, builds the same plan internally but appends to a step list instead of executing. The plan output is the actual plan the code would follow — not a separate approximation.

**Example: `thunder-forge deploy --dry-run`**
```
Pre-flight: 4 nodes OK, 1 gateway OK

Deployment plan:

  msm1 (192.168.1.101) — 3 services:
    [1] Upload plist: com.vllm-mlx-8000.plist (coder, port 8000)
    [2] Upload plist: com.vllm-mlx-8001.plist (general, port 8001)
    [3] Upload plist: com.vllm-mlx-8002.plist (fast, port 8002)
    [4] Remove stale: com.vllm-mlx-8003.plist (not in assignments)
    [5] Restart 3 launchd services
    [6] Health-poll /v1/models on ports 8000, 8001, 8002

  rock (192.168.1.61) — gateway:
    [1] Restart LiteLLM proxy (docker compose restart litellm)

Run without --dry-run to execute.
```

**Example: `thunder-forge ensure-models --dry-run`**
```
Pre-flight: 4 nodes OK, 1 gateway OK

Model sync plan:

  coder (mlx-community/Qwen2.5-Coder-32B-Instruct-4bit):
    [1] Download on rock via hf CLI -> /mnt/samsung/huggingface_cache/hub/
    [2] Rsync to msm1:~/.cache/huggingface/hub/ (estimated 18GB)
    [3] Rsync to msm3:~/.cache/huggingface/hub/ (estimated 18GB)
    Already cached on: msm2

Run without --dry-run to execute.
```

**Workflow:** Remote operator runs `--dry-run`, sends output. Review plan, confirm. Operator runs without `--dry-run`.

---

## Section 4: Actionable Error Messages

### Problem
Errors are swallowed (`except Exception: pass`), lose context (stderr suppressed with `2>/dev/null`), or print one line with no guidance.

### Design

**4.1 — Remove all `2>/dev/null`** from SSH command wrapping. Capture stderr, include in errors.

**4.2 — Remove all bare `except Exception: pass`** in health.py and models.py. Catch specific exceptions, report them.

**4.3 — Node context on every SSH failure.** ssh_run automatically includes node name and IP in error output. Callers don't format errors independently.

**4.4 — Completion summary after every command:**
```
Deploy complete: 3/4 nodes succeeded

  ✓ msm1 — 3 services running
  ✓ msm2 — 2 services running
  ✓ msm3 — 2 services running
  ✗ msm4 — launchctl bootstrap failed (see error above)
  ✓ rock — LiteLLM restarted

1 node failed. Fix msm4 and re-run: thunder-forge deploy --node msm4
```

**4.5 — Continue on partial failure.** Don't abort on first node failure. Deploy remaining nodes, collect all failures, print summary. 3/4 nodes working is better than 0/4 because msm1 failed first.

**Error format — every failure includes three things:**
```
SSH error on msm1 (192.168.1.101):
  Command: launchctl bootstrap gui/501 /path/to/plist
  Exit code: 1
  stderr: Could not find domain for
  → Service may already be loaded. Try: thunder-forge deploy --node msm1
```

---

## Section 5: Consistent Naming & setup-node.sh Hardening

### 5a: Naming Alignment

Standardize on `node` and `gateway` everywhere (matches setup-node.sh, more intuitive).

| Current | New |
|---------|-----|
| `role: inference` in YAML | `role: node` |
| `role: infra` in YAML | `role: gateway` |
| `config.rock` property | `config.gateway` property |
| `infra_name` property | `gateway_name` property |
| "infra node" in logs | "gateway" in logs |
| "inference node" in logs | "node" in logs |
| `check_inference_node()` | `check_node()` |
| `check_docker_services()` | `check_gateway_services()` |

**Migration:** Accept both old (`inference`/`infra`) and new (`node`/`gateway`) at parse time. Map old to new. Log deprecation warning if old names used.

### 5b: setup-node.sh Hardening

**Pre-checks at script start:**
```
Checking prerequisites...
  ✓ Running as gnezim (not root)
  ✓ Internet reachable
  ✓ sudo available
  ✗ curl not found — install: xcode-select --install
```

Fail fast if `sudo -n true` fails — tell user to run `sudo -v` first. Don't hang mid-script waiting for password.

**Step-by-step progress:**
```
[1/6] Installing Homebrew... done
[2/6] Installing uv... done
[3/6] Installing vllm-mlx... done
[4/6] Configuring PATH in ~/.zshenv and ~/.zshrc... done
[5/6] Disabling sleep (pmset)... done
[6/6] Verifying tools:
  ✓ brew 4.2.0 at /opt/homebrew/bin/brew
  ✓ uv 0.6.5 at /Users/admin/.local/bin/uv
  ✓ vllm-mlx 0.1.2

Node setup complete. Next: deploy from your workstation.
```

**Fix .env parser:** Replace custom sed pipeline with simpler line-by-line reader handling `KEY=value`, `KEY="value"`, inline `# comments`, blank lines. Reject unparseable lines with warning.

**Idempotent:** Every step checks if already done. Re-running completes in seconds.

**Gateway Docker validation:**
```
[7/8] Starting Docker Compose... done
[8/8] Waiting for services:
  ✓ litellm (healthy, port 4000)
  ✓ postgres (healthy, port 5432)
  ✓ open-webui (healthy, port 8080)
```

**`--check` mode:** `setup-node.sh node --check` runs only verification (tool versions, PATH, Docker health). Operator can confirm setup worked without re-running everything.

---

## Section 6: Test Coverage

### 6a: Fix Existing Failures
`test_load_cluster_config_user_defaults` — monkeypatch `os.getlogin()` for deterministic result. Update assertion to match actual user resolution logic.

### 6b: New Test Coverage

**Pre-flight:**
- Node reachable / unreachable — correct checklist output
- Missing tools — correct guidance message
- Partial failures (2/4 nodes fail) — continues checking, reports all

**Dry-run:**
- `deploy --dry-run` produces correct plan for multi-node config
- `ensure-models --dry-run` shows cached vs. needs-download
- Dry-run does NOT execute SSH (mock verifies zero calls)

**SSH helpers:**
- Node config fields (shell, home_dir) used in command wrapping
- stderr captured and returned (no `2>/dev/null`)
- Failure on one node doesn't abort remaining

**Deploy:**
- Plist uses `node.home_dir` and `node.homebrew_prefix`
- Stale plist cleanup regex logic
- Completion summary pass/fail counts

**Config:**
- New role names (`node`/`gateway`)
- Deprecation warnings for old names
- Resolved node fields

### 6c: Strategy
- All SSH mocked at `subprocess.run` boundary — no real SSH in tests
- Pre-flight and dry-run tests are highest value (validate what operator sees)
- setup-node.sh: `--check` mode serves as runtime verification (not unit-testable)

---

## Files Affected

| File | Change |
|------|--------|
| `src/thunder_forge/cluster/config.py` | Node dataclass fields, role naming, deprecation, remove os.getlogin |
| `src/thunder_forge/cluster/ssh.py` | Use node.shell, remove fallback hack, remove 2>/dev/null, node context in errors |
| `src/thunder_forge/cluster/deploy.py` | Use node.home_dir/homebrew_prefix, dry-run, continue-on-failure, summary |
| `src/thunder_forge/cluster/models.py` | Use node.home_dir, dry-run, actionable errors, continue-on-failure |
| `src/thunder_forge/cluster/health.py` | Rename functions, remove bare except, actionable errors |
| `src/thunder_forge/cluster/preflight.py` | **NEW** — pre-flight validation system |
| `src/thunder_forge/cli.py` | Wire preflight, --dry-run, --skip-preflight flags, role naming |
| `scripts/setup-node.sh` | Pre-checks, progress output, .env parser fix, --check mode, Docker validation |
| `configs/node-assignments.yaml.example` | Update role names |
| `tests/test_config.py` | Fix failing test, add role naming tests |
| `tests/test_preflight.py` | **NEW** — pre-flight tests |
| `tests/test_deploy.py` | Plist fields, dry-run, summary |
| `tests/test_models.py` | Dry-run, error handling |
| `tests/test_health.py` | Renamed functions, error handling |
