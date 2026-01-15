from __future__ import annotations

import re
import shlex
from typing import Literal

from services.config_service import FabricIPv4Defaults, Node, SSHSettings
from services.macos_networksetup_service import (
    parse_network_service_device,
    parse_thunderbolt_devices,
)
from services.ssh_service import run_ssh, run_ssh_sudo, run_ssh_sudo_shell


def require_macos_tahoe_26_2_plus(*, node: Node, ssh: SSHSettings) -> None:
    # Policy: we support macOS Tahoe 26.2+ only for fabricnet automation.
    # This is a pragmatic guardrail around `networksetup` behavior.
    out = run_ssh(
        node=node, settings=ssh, remote_command="sw_vers -productVersion"
    ).stdout
    version_text = (out or "").strip()
    if not version_text:
        raise RuntimeError(
            f"{node.name}: failed to detect macOS version (empty sw_vers output); "
            "fabricnet requires macOS Tahoe 26.2+"
        )

    parts = version_text.split(".")
    try:
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
    except ValueError as exc:
        raise RuntimeError(
            f"{node.name}: unexpected macOS version string from sw_vers: {version_text!r}; "
            "fabricnet requires macOS Tahoe 26.2+"
        ) from exc

    if (major, minor) < (26, 2):
        raise RuntimeError(
            f"{node.name}: unsupported macOS {version_text}; fabricnet requires macOS Tahoe 26.2+"
        )


def _get_service_ipv4_address(
    *, node: Node, ssh: SSHSettings, service_name: str
) -> str | None:
    out = run_ssh(
        node=node,
        settings=ssh,
        remote_command=f"networksetup -getinfo {service_name!r}",
    ).stdout
    text = (out or "").strip()
    if not text:
        return None

    # Example line: "IP address: 169.254.10.1"
    m = re.search(r"^IP address:\s*(.+?)\s*$", text, flags=re.MULTILINE)
    if not m:
        return None
    value = m.group(1).strip()
    if not value or value.lower() in {"none"}:
        return None
    return value


def _get_network_service_device(
    *, node: Node, ssh: SSHSettings, service_name: str
) -> str | None:
    out = run_ssh(
        node=node,
        settings=ssh,
        remote_command="networksetup -listnetworkserviceorder",
        log_command=False,
        log_output=False,
    ).stdout
    return parse_network_service_device(
        network_service_order_text=out or "", service_name=service_name
    )


def _discover_thunderbolt_devices(*, node: Node, ssh: SSHSettings) -> list[str]:
    out = run_ssh(
        node=node,
        settings=ssh,
        remote_command="networksetup -listallhardwareports",
        log_command=False,
        log_output=False,
    ).stdout
    return parse_thunderbolt_devices(hardware_ports_text=out or "")


def ensure_bridge0_for_thunderbolt(
    *,
    node: Node,
    ssh: SSHSettings,
    sudo_password: str | None = None,
    sudo_interactive: bool = False,
) -> list[str]:
    devices = _discover_thunderbolt_devices(node=node, ssh=ssh)
    if not devices:
        raise RuntimeError(
            f"{node.name}: no Thunderbolt hardware ports detected (networksetup -listallhardwareports)"
        )

    # Idempotent: create if missing, then try to add all thunderbolt en* to bridge0.
    # Some commands may fail if the member already exists; ignore those.
    parts: list[str] = [
        "ifconfig bridge0 >/dev/null 2>&1 || ifconfig bridge0 create",
    ]
    parts.extend(
        [f"ifconfig bridge0 addm {d} >/dev/null 2>&1 || true" for d in devices]
    )
    parts.append("ifconfig bridge0 up")
    cmd = "; ".join(parts)

    run_ssh_sudo_shell(
        node=node,
        settings=ssh,
        shell_script=cmd,
        sudo_password=sudo_password,
        interactive=sudo_interactive,
    )

    return devices


def ensure_bridge0_ipv4(
    *,
    node: Node,
    ssh: SSHSettings,
    address: str,
    netmask: str,
    sudo_password: str | None = None,
    sudo_interactive: bool = False,
) -> None:
    # `networksetup` can report the manual config while `bridge0` is missing or
    # doesn't actually have the IPv4 assigned; ensure it is present on the interface.
    cmd = f"ifconfig bridge0 | grep -q 'inet {address} ' || ifconfig bridge0 inet {address} netmask {netmask}"
    run_ssh_sudo_shell(
        node=node,
        settings=ssh,
        shell_script=cmd,
        sudo_password=sudo_password,
        interactive=sudo_interactive,
    )


def configure_fabric_ipv4(
    *,
    node: Node,
    ssh: SSHSettings,
    service_name: str,
    address: str,
    ipv4_defaults: FabricIPv4Defaults,
    ipv4_mode: Literal[
        "dhcp_with_manual_address", "manual"
    ] = "dhcp_with_manual_address",
    enforce_macos_version_check: bool = True,
    sudo_password: str | None = None,
    sudo_interactive: bool = False,
) -> None:
    if enforce_macos_version_check:
        require_macos_tahoe_26_2_plus(node=node, ssh=ssh)

    netmask = ipv4_defaults.netmask
    router = ipv4_defaults.router or "0.0.0.0"

    svc = _get_network_service_device(node=node, ssh=ssh, service_name=service_name)
    is_bridge0_service = bool(svc == "bridge0")

    # Configure the service under sudo. When the service maps to bridge0 we run
    # all bridge operations + networksetup in a single sudo shell. This avoids
    # multiple password prompts on hosts with sudo tty tickets.
    if ipv4_mode == "dhcp_with_manual_address":
        networksetup_cmd = (
            f"networksetup -setmanualwithdhcprouter {shlex.quote(service_name)} "
            f"{shlex.quote(address)} {shlex.quote(netmask)} {shlex.quote(router)}"
        )
    elif ipv4_mode == "manual":
        networksetup_cmd = (
            f"networksetup -setmanual {shlex.quote(service_name)} "
            f"{shlex.quote(address)} {shlex.quote(netmask)} {shlex.quote(router)}"
        )
    else:
        raise ValueError(f"Unsupported fabricnet ipv4_mode: {ipv4_mode!r}")

    if is_bridge0_service:
        devices = _discover_thunderbolt_devices(node=node, ssh=ssh)
        if not devices:
            raise RuntimeError(
                f"{node.name}: no Thunderbolt hardware ports detected (networksetup -listallhardwareports)"
            )

        parts: list[str] = [
            "ifconfig bridge0 >/dev/null 2>&1 || ifconfig bridge0 create",
            *[f"ifconfig bridge0 addm {d} >/dev/null 2>&1 || true" for d in devices],
            "ifconfig bridge0 up",
            networksetup_cmd,
            # Ensure the IPv4 is actually on bridge0 (networksetup can report success while bridge0 is missing).
            f"ifconfig bridge0 | grep -q 'inet {address} ' || ifconfig bridge0 inet {address} netmask {netmask}",
        ]

        run_ssh_sudo_shell(
            node=node,
            settings=ssh,
            shell_script="; ".join(parts),
            sudo_password=sudo_password,
            interactive=sudo_interactive,
        )
    else:
        run_ssh_sudo(
            node=node,
            settings=ssh,
            remote_command=networksetup_cmd,
            sudo_password=sudo_password,
            interactive=sudo_interactive,
        )

    # Read-back verification: `networksetup` sometimes returns success but the
    # service may still show a self-assigned IP if the change didn't stick.
    applied = _get_service_ipv4_address(node=node, ssh=ssh, service_name=service_name)
    if applied != address:
        raise RuntimeError(
            "\n".join(
                [
                    f"{node.name}: fabricnet IP did not apply for service {service_name!r}",
                    f"Expected: {address}",
                    f"Observed: {applied or '(unknown)'}",
                    "What to check:",
                    "- Verify the exact service name: networksetup -listallnetworkservices",
                    "- Inspect service state: networksetup -getinfo <service>",
                    "- Ensure the Thunderbolt link is up and no bridging is enabled",
                ]
            )
        )
