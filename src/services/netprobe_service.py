from __future__ import annotations

import math
import shutil
import socket
import subprocess


def tcp_probe(host: str, port: int, timeout_seconds: float) -> bool:
    """Best-effort TCP probe.

    Prefers `nc` on macOS to avoid per-app VPN/NetworkExtension routing surprises
    that can affect Python socket calls.
    """
    nc = shutil.which("nc")
    timeout = max(1, int(math.ceil(float(timeout_seconds))))

    if nc:
        try:
            proc = subprocess.run(
                [nc, "-4", "-n", "-z", "-w", str(timeout), host, str(int(port))],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return proc.returncode == 0
        except Exception:
            pass

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(float(timeout_seconds))
        try:
            return sock.connect_ex((host, int(port))) == 0
        finally:
            sock.close()
    except OSError:
        return False
