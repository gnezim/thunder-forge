from __future__ import annotations

import re
from typing import Literal

from services.config_service import FabricIPv4Defaults, Node, SSHSettings
from services.ssh_service import run_ssh, run_ssh_sudo


def require_macos_tahoe_26_2_plus(*, node: Node, ssh: SSHSettings) -> None:
    # Policy: we support macOS Tahoe 26.2+ only for fabricnet automation.
    # This is a pragmatic guardrail around `networksetup` behavior.
    out = run_ssh(node=node, settings=ssh, remote_command="sw_vers -productVersion").stdout
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


def _get_service_ipv4_address(*, node: Node, ssh: SSHSettings, service_name: str) -> str | None:
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


def configure_fabric_ipv4(
    *,
    node: Node,
    ssh: SSHSettings,
    service_name: str,
    address: str,
    ipv4_defaults: FabricIPv4Defaults,
    ipv4_mode: Literal["dhcp_with_manual_address", "manual"] = "dhcp_with_manual_address",
    enforce_macos_version_check: bool = True,
    sudo_password: str | None = None,
    sudo_interactive: bool = False,
) -> None:
    if enforce_macos_version_check:
        require_macos_tahoe_26_2_plus(node=node, ssh=ssh)

    netmask = ipv4_defaults.netmask
    router = ipv4_defaults.router or "0.0.0.0"

    # macOS: configure a named network service (e.g., "Thunderbolt Bridge").
    if ipv4_mode == "dhcp_with_manual_address":
        cmd = (
            f"networksetup -setmanualwithdhcprouter {service_name!r} {address} {netmask} {router}"
        )
    elif ipv4_mode == "manual":
        cmd = f"networksetup -setmanual {service_name!r} {address} {netmask} {router}"
    else:
        raise ValueError(f"Unsupported fabricnet ipv4_mode: {ipv4_mode!r}")
    run_ssh_sudo(
        node=node,
        settings=ssh,
        remote_command=cmd,
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
