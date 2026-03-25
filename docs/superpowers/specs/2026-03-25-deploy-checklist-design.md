# Deploy Checklist — Design Spec

**Date:** 2026-03-25
**Feature:** Pre-deploy status checklist on the Deploy page

## Overview

Add a "Check Status" button to the Deploy page that runs a 5-step pipeline check for each assignment slot and displays the results as a compact status row. The goal is to make it immediately visible what's deployable and what's blocked before pressing Deploy.

## UI Layout

On the Deploy page, between "Changes to Deploy" and the Deploy button:

```
[Check Status]

air / fast:8000   ✓ config  ✓ ssh  ✓ model  ✗ service  – port
                                      com.mlx-lm-8000 not found

[Deploy ▶]
```

- Each assignment slot renders as one row with 5 status columns
- Status icons: `✓` green (ok), `✗` red (error), `⚠` yellow (warn), `–` grey (skipped / upstream failed)
- Errors show a caption/detail line below the row
- Checks are invalidated when the config version changes

## Checks Pipeline

Checks run in parallel via `ThreadPoolExecutor`, one thread per slot.

| Step | Type | What it checks |
|------|------|----------------|
| **config** | static (no SSH) | RAM fits on node, port is unique per node, model and node exist in config |
| **ssh** | SSH via paramiko | `echo ok` on the target node — confirms connectivity |
| **model** | SSH | `ls ~/.cache/huggingface/hub/models--{org}--{name}/` exists on node |
| **service** | SSH | `launchctl list com.mlx-lm-{port}` on macOS nodes; `systemctl is-active thunder-forge-{port}` on Linux nodes |
| **port** | HTTP | `GET http://{node_ip}:{port}/v1/models` with 3s timeout |

Steps after a failed dependency return `–` (grey) to avoid misleading results:
- `model`, `service`, `port` are skipped if `ssh` fails
- `port` is also skipped if `service` is not running

## Data Model

```python
# Check result tuple
CheckResult = tuple[Literal["ok", "warn", "error", "skip"], str]  # (status, detail_message)

# Per-slot results dict
SlotChecks = dict[str, CheckResult]  # keys: "config", "ssh", "model", "service", "port"

# Session state key
# st.session_state["deploy_checks"]: dict[(node_name, model_name, port), SlotChecks]
# st.session_state["deploy_checks_config_id"]: int — invalidate on config version change
```

## Code Structure

### New file: `admin/thunder_admin/checks.py`

Five check functions, all returning `CheckResult`:

- `check_config(node_name, node, slot, config) -> SlotChecks` — static only, no I/O
- `check_ssh(node_name, node) -> CheckResult` — paramiko ping
- `check_model(node, slot, config) -> CheckResult` — SSH ls on HF cache path
- `check_service(node, slot) -> CheckResult` — SSH launchctl/systemctl
- `check_port(node, slot) -> CheckResult` — HTTP GET /v1/models

Entry point:

```python
def run_all_checks(config: dict) -> dict[tuple, SlotChecks]:
    """Run all checks for all assignment slots in parallel."""
    ...
```

### Modified: `admin/thunder_admin/pages/deploy.py`

- Add "Check Status" `st.button` above the Deploy button
- On click: call `run_all_checks`, store in `st.session_state["deploy_checks"]`
- After checks: render one row per slot using `st.columns([2, 1, 1, 1, 1, 1])`
- Invalidate cache when `current["id"] != st.session_state.get("deploy_checks_config_id")`

## SSH Reuse

`check_ssh`, `check_model`, and `check_service` reuse `ssh_exec` from `admin/thunder_admin/deploy.py`. Each check opens its own short-lived paramiko connection (acceptable for a manual "Check" action).

## Error Handling

- SSH timeout (10s per check) → `("error", "SSH timeout")`
- HTTP timeout (3s) → `("error", "timeout")`
- Unexpected exceptions → `("error", str(e)[:120])`
- No assignments in config → show info message instead of checklist

## Non-Goals

- No auto-refresh / polling — checks are on-demand only
- No blocking the Deploy button based on check results — user decides
- No checks for external endpoints (they have their own validation)
