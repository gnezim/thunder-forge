from __future__ import annotations

import re


def parse_networksetup_getinfo_ipv4(*, getinfo_text: str) -> dict[str, str | None]:
    """Parse IPv4 details from `networksetup -getinfo <service>` output.

    Returns keys: ip_address, subnet_mask, router (values may be None).
    """

    text = (getinfo_text or "").strip()
    if not text:
        return {"ip_address": None, "subnet_mask": None, "router": None}

    def _pick(label: str) -> str | None:
        m = re.search(rf"^{re.escape(label)}:\s*(.+?)\s*$", text, flags=re.MULTILINE)
        if not m:
            return None
        value = (m.group(1) or "").strip()
        if not value or value.lower() in {"none", "(null)"}:
            return None
        return value

    return {
        "ip_address": _pick("IP address"),
        "subnet_mask": _pick("Subnet mask"),
        "router": _pick("Router"),
    }


def parse_ifconfig_status(*, ifconfig_text: str) -> str | None:
    """Extract link status from `ifconfig <iface>` output.

    Typical line: `status: active` or `status: inactive`.
    """
    text = (ifconfig_text or "")
    m = re.search(r"^\s*status:\s*(\w+)\s*$", text, flags=re.MULTILINE)
    if not m:
        return None
    value = (m.group(1) or "").strip().lower()
    return value or None


def parse_ifconfig_bridge_members(*, ifconfig_text: str) -> list[str]:
    """Return member interface names (e.g. en5) from `ifconfig bridge0` output."""
    text = (ifconfig_text or "")
    members = re.findall(r"^\s*member:\s*(\w+)\s+flags=", text, flags=re.MULTILINE)
    # Preserve order while removing duplicates.
    seen: set[str] = set()
    ordered: list[str] = []
    for m in members:
        if m not in seen:
            seen.add(m)
            ordered.append(m)
    return ordered


def parse_network_service_device(
    *, network_service_order_text: str, service_name: str
) -> str | None:
    """Return the device name (e.g. 'bridge0') for a given macOS network service.

    Input should be the output of: `networksetup -listnetworkserviceorder`.
    """
    lines = (network_service_order_text or "").splitlines()
    if not lines:
        return None

    # Example snippet:
    # (2) Thunderbolt Bridge
    # (Hardware Port: Thunderbolt Bridge, Device: bridge0)
    for idx, line in enumerate(lines):
        if service_name not in line:
            continue
        if idx + 1 >= len(lines):
            return None
        m = re.search(r"\(Hardware Port:\s*(.+?),\s*Device:\s*(.*?)\)", lines[idx + 1])
        if not m:
            return None
        device = (m.group(2) or "").strip()
        return device or None

    return None


def parse_thunderbolt_devices(*, hardware_ports_text: str) -> list[str]:
    """Return device names (enX) for Thunderbolt ports.

    Input should be the output of: `networksetup -listallhardwareports`.
    """
    lines = (hardware_ports_text or "").splitlines()
    devices: list[str] = []
    in_thunderbolt_block = False

    for raw in lines:
        line = raw.strip()
        if not line:
            in_thunderbolt_block = False
            continue

        if line.startswith("Hardware Port:"):
            port_name = line.removeprefix("Hardware Port:").strip()
            in_thunderbolt_block = port_name.startswith("Thunderbolt")
            continue

        if in_thunderbolt_block and line.startswith("Device:"):
            dev = line.removeprefix("Device:").strip()
            if dev:
                devices.append(dev)
            in_thunderbolt_block = False

    # Preserve stable ordering and avoid duplicates.
    seen: set[str] = set()
    ordered: list[str] = []
    for d in devices:
        if d not in seen:
            seen.add(d)
            ordered.append(d)
    return ordered
