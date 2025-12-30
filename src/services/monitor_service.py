from __future__ import annotations

import socket
import time
from typing import Any

from pydantic import BaseModel

from services.config_service import Node, TFConfig, iter_nodes


class PortStatus(BaseModel):
    ssh: bool
    ollama: bool


class NodeStatus(BaseModel):
    name: str
    mgmt_ip: str
    fabric_ip: str | None
    mgmt: PortStatus
    fabric: PortStatus


class ClusterStatus(BaseModel):
    ts: float
    nodes: list[NodeStatus]


def _tcp_probe(host: str, port: int, timeout_seconds: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return True
    except OSError:
        return False


def _node_status(
    node: Node,
    *,
    fabric_ip: str | None,
    ssh_port: int,
    ollama_port: int,
    timeout_seconds: float,
) -> NodeStatus:
    mgmt = PortStatus(
        ssh=_tcp_probe(node.mgmt_ip, ssh_port, timeout_seconds),
        ollama=_tcp_probe(node.mgmt_ip, ollama_port, timeout_seconds),
    )
    if fabric_ip:
        fabric = PortStatus(
            ssh=_tcp_probe(fabric_ip, ssh_port, timeout_seconds),
            ollama=_tcp_probe(fabric_ip, ollama_port, timeout_seconds),
        )
    else:
        fabric = PortStatus(ssh=False, ollama=False)

    return NodeStatus(
        name=node.name,
        mgmt_ip=node.mgmt_ip,
        fabric_ip=fabric_ip,
        mgmt=mgmt,
        fabric=fabric,
    )


def get_cluster_status(inventory: TFConfig) -> ClusterStatus:
    ssh_port = inventory.settings.monitor.ssh_port
    ollama_port = inventory.settings.monitor.ollama_port
    timeout_seconds = inventory.settings.ssh.connect_timeout_seconds

    fabric_addr_by_name: dict[str, str] = {}
    if inventory.fabricnet is not None:
        fabric_addr_by_name = {n.name: n.address for n in inventory.fabricnet.nodes}

    nodes = [
        _node_status(
            node,
            fabric_ip=fabric_addr_by_name.get(node.name),
            ssh_port=ssh_port,
            ollama_port=ollama_port,
            timeout_seconds=timeout_seconds,
        )
        for node in iter_nodes(inventory)
    ]
    return ClusterStatus(ts=time.time(), nodes=nodes)


def cluster_status_as_dict(inventory: TFConfig) -> dict[str, Any]:
    return get_cluster_status(inventory).model_dump()
