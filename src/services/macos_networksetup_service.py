from __future__ import annotations

import re


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
