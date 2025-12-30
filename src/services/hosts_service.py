from __future__ import annotations

from dataclasses import dataclass

from services.config_service import HostsSyncSettings, TFConfig, iter_nodes


@dataclass(frozen=True)
class HostsArtifacts:
    block: str


def build_hosts_block(inventory: TFConfig) -> HostsArtifacts:
    start = inventory.settings.hosts_sync.managed_block_start
    end = inventory.settings.hosts_sync.managed_block_end

    fabric_addr_by_name: dict[str, str] = {}
    if inventory.fabricnet is not None:
        fabric_addr_by_name = {n.name: n.address for n in inventory.fabricnet.nodes}

    lines: list[str] = [start]
    for node in iter_nodes(inventory):
        # DRY/KISS: only one stable hostname per node.
        # - <name>-mgmt maps to the management interface.
        lines.append(f"{node.mgmt_ip} {node.name}-mgmt")

        fabric_ip = fabric_addr_by_name.get(node.name)
        if fabric_ip:
            # Stable fabric hostname for direct Thunderbolt networking.
            lines.append(f"{fabric_ip} {node.name}-fabric")
    lines.append(end)

    return HostsArtifacts(block="\n".join(lines) + "\n")


def upsert_managed_hosts_block(
    *,
    hosts_file_text: str,
    managed_block: str,
    settings: HostsSyncSettings,
) -> str:
    start = settings.managed_block_start
    end = settings.managed_block_end

    start_idx = hosts_file_text.find(start)
    end_idx = hosts_file_text.find(end)

    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        end_idx_inclusive = end_idx + len(end)
        before = hosts_file_text[:start_idx].rstrip("\n")
        after = hosts_file_text[end_idx_inclusive:].lstrip("\n")
        parts = []
        if before:
            parts.append(before)
        parts.append(managed_block.rstrip("\n"))
        if after:
            parts.append(after)
        return "\n".join(parts).rstrip("\n") + "\n"

    # No existing block; append.
    text = hosts_file_text.rstrip("\n")
    if text:
        text += "\n\n"
    text += managed_block.rstrip("\n") + "\n"
    return text
