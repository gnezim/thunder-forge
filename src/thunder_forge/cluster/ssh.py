"""Shared SSH and SCP helpers for remote operations."""

from __future__ import annotations

import socket
import subprocess


def _is_local(ip: str) -> bool:
    """Check if the given IP belongs to this machine."""
    try:
        local_ips = {addr[4][0] for addr in socket.getaddrinfo(socket.gethostname(), None)}
    except socket.gaierror:
        local_ips = set()
    local_ips.update({"127.0.0.1", "::1"})
    return ip in local_ips


def ssh_run(
    user: str,
    ip: str,
    cmd: str,
    *,
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    """Run a command on a remote node via SSH, or locally if the target is this machine."""
    if _is_local(ip):
        return subprocess.run(
            ["bash", "-c", cmd],
            capture_output=True, text=True, timeout=timeout,
        )
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
    """Write content to a remote file via SSH stdin pipe, or locally if target is this machine."""
    if _is_local(ip):
        return subprocess.run(
            ["bash", "-c", f"cat > {remote_path}"],
            input=content, capture_output=True, text=True, timeout=15,
        )
    return subprocess.run(
        ["ssh", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=no",
         f"{user}@{ip}", f"cat > {remote_path}"],
        input=content, capture_output=True, text=True, timeout=15,
    )
