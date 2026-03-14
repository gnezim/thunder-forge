"""Shared SSH and SCP helpers for remote operations."""

from __future__ import annotations

import subprocess


def ssh_run(
    user: str,
    ip: str,
    cmd: str,
    *,
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    """Run a command on a remote node via SSH."""
    return subprocess.run(
        ["ssh", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=no",
         f"{user}@{ip}", cmd],
        capture_output=True, text=True, timeout=timeout,
    )


def run_local(
    cmd: list[str],
    *,
    timeout: int = 600,
) -> subprocess.CompletedProcess[str]:
    """Run a command locally."""
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def scp_content(
    user: str,
    ip: str,
    content: str,
    remote_path: str,
) -> subprocess.CompletedProcess[str]:
    """Write content to a remote file via SSH stdin pipe."""
    return subprocess.run(
        ["ssh", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=no",
         f"{user}@{ip}", f"cat > {remote_path}"],
        input=content, capture_output=True, text=True, timeout=15,
    )
