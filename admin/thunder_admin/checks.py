"""Pre-deploy status checks for each assignment slot."""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed  # noqa: F401
from typing import Literal

import paramiko

from thunder_admin.config import validate_config
from thunder_forge.cluster.config import (
    Assignment,
    ClusterConfig,
    Node,
    parse_cluster_config,  # noqa: F401
)

CheckResult = tuple[Literal["ok", "warn", "error", "skip"], str]
SlotChecks = dict[str, CheckResult]

_SSH_TIMEOUT = 10


def check_config(config: dict) -> CheckResult:
    """Static config validation — no I/O. Returns all errors joined, capped at 120 chars."""
    errors = validate_config(config)
    if not errors:
        return ("ok", "")
    joined = "; ".join(errors)
    return ("error", joined[:120])


def _resolve_ssh_key() -> paramiko.PKey:
    """Resolve SSH private key from container paths, in priority order."""
    local_key = "/tmp/ssh_key"
    if os.path.exists(local_key):
        return paramiko.PKey.from_path(local_key)
    env_key = os.environ.get("GATEWAY_SSH_KEY")
    if env_key and os.path.exists(env_key):
        return paramiko.PKey.from_path(env_key)
    default = os.path.expanduser("~/.ssh/id_ed25519")
    return paramiko.PKey.from_path(default)


def check_ssh(node: Node) -> tuple[CheckResult, paramiko.SSHClient | None]:
    """Open a paramiko SSH connection to the compute node. Returns (result, client).

    The returned SSHClient should be passed to check_model and check_service for
    connection reuse. Caller is responsible for closing the client.
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        pkey = _resolve_ssh_key()
        client.connect(
            hostname=node.ip,
            username=node.user,
            pkey=pkey,
            timeout=_SSH_TIMEOUT,
            look_for_keys=False,
            allow_agent=False,
        )
        _, stdout, _ = client.exec_command("echo ok", timeout=_SSH_TIMEOUT)
        out = stdout.read().decode().strip()
        if out == "ok":
            return ("ok", ""), client
        client.close()
        return ("error", f"unexpected response: {out[:60]}"), None
    except (TimeoutError, OSError) as e:
        client.close()
        if "timed out" in str(e).lower():
            return ("error", "SSH timeout"), None
        return ("error", str(e)[:120]), None
    except Exception as e:
        client.close()
        return ("error", str(e)[:120]), None


def check_model(ssh_conn: paramiko.SSHClient, node: Node, slot: Assignment, cluster: ClusterConfig) -> CheckResult:
    """Check HF model cache presence via SSH. Skips for non-HF source types."""
    model = cluster.models.get(slot.model)
    if model is None:
        return ("error", f"model '{slot.model}' not in config")

    if model.source.type != "huggingface":
        return ("warn", "non-HF source; skipping model check")

    slug = model.source.repo.replace("/", "--")
    path = f"~/.cache/huggingface/hub/models--{slug}"
    try:
        _, stdout, _ = ssh_conn.exec_command(f"ls {path}", timeout=_SSH_TIMEOUT)
        exit_code = stdout.channel.recv_exit_status()
        if exit_code == 0:
            return ("ok", "")
        return ("error", f"not found: {path}")
    except Exception as e:
        return ("error", str(e)[:120])


def check_service(ssh_conn: paramiko.SSHClient, node: Node, slot: Assignment) -> CheckResult:
    """Check if the mlx_lm.server service is running on the node."""
    try:
        _, uname_out, _ = ssh_conn.exec_command("uname -s", timeout=_SSH_TIMEOUT)
        platform = uname_out.read().decode().strip()

        if platform == "Darwin":
            label = f"com.mlx-lm-{slot.port}"
            _, stdout, _ = ssh_conn.exec_command(f"launchctl list {label}", timeout=_SSH_TIMEOUT)
            output = stdout.read().decode()
            exit_code = stdout.channel.recv_exit_status()
            if exit_code != 0 or '"PID"' not in output:
                return ("error", f"{label} not found or not running")
            return ("ok", "")
        else:
            svc = f"thunder-forge-{slot.port}"
            _, stdout, _ = ssh_conn.exec_command(f"systemctl is-active {svc}", timeout=_SSH_TIMEOUT)
            output = stdout.read().decode().strip()
            exit_code = stdout.channel.recv_exit_status()
            if output == "active" and exit_code == 0:
                return ("ok", "")
            return ("error", f"{svc} is {output or 'not active'}")
    except Exception as e:
        return ("error", str(e)[:120])
