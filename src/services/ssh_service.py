from __future__ import annotations

import math
import subprocess
from dataclasses import dataclass

from services.config_service import Node, SSHSettings


@dataclass(frozen=True)
class SSHResult:
    returncode: int
    stdout: str
    stderr: str


_last_logged_node_name: str | None = None
_has_logged_command_in_current_host_block: bool = False


def _format_remote_command_for_log(remote_command: str) -> str:
    lines = [line.rstrip() for line in remote_command.strip().splitlines() if line.strip()]
    if not lines:
        return ""
    if len(lines) == 1:
        return lines[0]
    return f"{lines[0]} …"


def _log_remote_command(*, node_name: str, remote_command: str) -> None:
    global _has_logged_command_in_current_host_block
    global _last_logged_node_name

    formatted = _format_remote_command_for_log(remote_command)
    if not formatted:
        return

    if _last_logged_node_name != node_name:
        if _last_logged_node_name is not None:
            print()
        print(f"[{node_name}]:")
        print()
        _last_logged_node_name = node_name
        _has_logged_command_in_current_host_block = False
    elif _has_logged_command_in_current_host_block:
        # Separate subsequent commands/output within the same host block.
        print()

    print(f"$ {formatted}")
    print()
    _has_logged_command_in_current_host_block = True


def _ssh_base_args(settings: SSHSettings) -> list[str]:
    # OpenSSH expects an integer number of seconds here; it rejects floats
    # (e.g. "1.0" -> "invalid time value").
    connect_timeout_seconds = max(1, int(math.ceil(settings.connect_timeout_seconds)))
    args = [
        "ssh",
        "-o",
        f"ConnectTimeout={connect_timeout_seconds}",
        "-o",
        "ServerAliveInterval=5",
        "-o",
        "ServerAliveCountMax=1",
        "-o",
        "StrictHostKeyChecking=accept-new",
    ]
    if settings.batch_mode:
        args += ["-o", "BatchMode=yes"]
    return args


def run_ssh(
    *,
    node: Node,
    settings: SSHSettings,
    remote_command: str,
    check: bool = True,
    input_text: str | None = None,
    log_command: bool = True,
    log_output: bool = True,
    allocate_tty: bool = False,
    capture_output: bool = True,
) -> SSHResult:
    host = node.ssh_host or node.mgmt_ip
    target = f"{node.ssh_user}@{host}"
    cmd = _ssh_base_args(settings)
    if allocate_tty:
        # Insert right after the leading `ssh` binary.
        cmd.insert(1, "-tt")
    cmd += [target, remote_command]

    if log_command:
        _log_remote_command(node_name=node.name, remote_command=remote_command)

    if capture_output:
        proc = subprocess.run(cmd, capture_output=True, text=True, input=input_text)
        result = SSHResult(proc.returncode, proc.stdout, proc.stderr)
    else:
        proc = subprocess.run(cmd)
        result = SSHResult(proc.returncode, "", "")

    if log_output and capture_output:
        stdout_text = (result.stdout or "").rstrip("\n")
        stderr_text = (result.stderr or "").rstrip("\n")
        if stdout_text:
            for line in stdout_text.splitlines():
                print(f"  {line}")
        if stderr_text:
            for line in stderr_text.splitlines():
                print(f"  {line}")
        # No trailing newline here; spacing between sections is handled when the
        # next command is logged.
    if check and proc.returncode != 0:
        stderr_text = ""
        if getattr(proc, "stderr", None):
            stderr_text = str(proc.stderr).strip()
        if not stderr_text and not capture_output:
            stderr_text = "(no captured stderr; see command output above)"
        raise RuntimeError(
            f"SSH failed for {node.name} ({target}): rc={proc.returncode}\n{stderr_text}"
        )
    return result


def run_ssh_sudo(
    *,
    node: Node,
    settings: SSHSettings,
    remote_command: str,
    check: bool = True,
    sudo_password: str | None = None,
    interactive: bool = False,
) -> SSHResult:
    if interactive:
        # Allocate a TTY and let sudo prompt on the remote.
        # This is the most compatible mode (handles sudo policies requiring a tty).
        return run_ssh(
            node=node,
            settings=settings,
            remote_command=f"sudo {remote_command}",
            check=check,
            allocate_tty=True,
            capture_output=False,
        )

    if sudo_password is None:
        # -n: non-interactive; fails fast if sudo needs a password.
        return run_ssh(
            node=node,
            settings=settings,
            remote_command=f"sudo -n {remote_command}",
            check=check,
        )

    # -S: read password from stdin.
    # NOTE: the password is never included in the logged command, only sent via stdin.
    return run_ssh(
        node=node,
        settings=settings,
        remote_command=f"sudo -S -p '' {remote_command}",
        check=check,
        input_text=f"{sudo_password}\n",
    )
