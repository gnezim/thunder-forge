"""Shared SSH and SCP helpers for remote operations."""

from __future__ import annotations

import platform
import shlex
import socket
import subprocess


def _login_shell() -> str:
    """Return the login shell to use: zsh on macOS, bash on Linux."""
    return "zsh" if platform.system() == "Darwin" else "bash"


def _is_local(ip: str) -> bool:
    """Check if the given IP belongs to this machine by trying to bind to it."""
    if ip in ("127.0.0.1", "::1"):
        return True
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind((ip, 0))
        s.close()
        return True
    except OSError:
        return False


def ssh_run(
    user: str,
    ip: str,
    cmd: str,
    *,
    timeout: int = 30,
    stream: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a command on a remote node via SSH, or locally if the target is this machine."""
    capture = not stream
    shell = _login_shell()
    if _is_local(ip):
        return subprocess.run(
            [shell, "-lc", cmd],
            capture_output=capture,
            text=True,
            timeout=timeout,
        )
    # Wrap in login shell on the remote side so PATH is sourced.
    # Remote inference nodes are macOS (zsh); use bash -l as fallback.
    wrapped = f"zsh -lc {shlex.quote(cmd)} 2>/dev/null || bash -lc {shlex.quote(cmd)}"
    return subprocess.run(
        ["ssh", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=no", f"{user}@{ip}", wrapped],
        capture_output=capture,
        text=True,
        timeout=timeout,
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
    shell = _login_shell()
    if _is_local(ip):
        return subprocess.run(
            [shell, "-lc", f"cat > {remote_path}"],
            input=content,
            capture_output=True,
            text=True,
            timeout=15,
        )
    return subprocess.run(
        ["ssh", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=no", f"{user}@{ip}", f"cat > {remote_path}"],
        input=content,
        capture_output=True,
        text=True,
        timeout=15,
    )
