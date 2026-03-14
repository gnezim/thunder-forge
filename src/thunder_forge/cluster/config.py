"""Config parsing, memory validation, and LiteLLM config generation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class ModelSource:
    type: str  # huggingface, convert, local, pip
    repo: str = ""
    revision: str = "main"
    quantize: str = ""
    path: str = ""
    package: str = ""
    weight_repo: str = ""


@dataclass
class Model:
    source: ModelSource
    disk_gb: float = 0.0
    kv_per_32k_gb: float = 0.0
    ram_gb: float | None = None
    active_params: str = ""
    max_context: int = 0
    serving: str = ""
    notes: str = ""


@dataclass
class Node:
    ip: str
    ram_gb: int
    user: str = "admin"
    role: str = "inference"


@dataclass
class Assignment:
    model: str
    port: int = 0
    embedding: bool = False


@dataclass
class ClusterConfig:
    models: dict[str, Model] = field(default_factory=dict)
    nodes: dict[str, Node] = field(default_factory=dict)
    assignments: dict[str, list[Assignment]] = field(default_factory=dict)

    @property
    def inference_nodes(self) -> dict[str, Node]:
        return {k: v for k, v in self.nodes.items() if v.role == "inference"}

    @property
    def rock(self) -> Node:
        for node in self.nodes.values():
            if node.role == "infra":
                return node
        msg = "No infra node found in config"
        raise ValueError(msg)


def _parse_model_source(raw: dict) -> ModelSource:
    return ModelSource(
        type=raw["type"],
        repo=raw.get("repo", ""),
        revision=raw.get("revision", "main"),
        quantize=raw.get("quantize", ""),
        path=raw.get("path", ""),
        package=raw.get("package", ""),
        weight_repo=raw.get("weight_repo", ""),
    )


def _parse_model(raw: dict) -> Model:
    return Model(
        source=_parse_model_source(raw["source"]),
        disk_gb=raw.get("disk_gb", 0.0),
        kv_per_32k_gb=raw.get("kv_per_32k_gb", 0.0),
        ram_gb=raw.get("ram_gb"),
        active_params=raw.get("active_params", ""),
        max_context=raw.get("max_context", 0),
        serving=raw.get("serving", ""),
        notes=raw.get("notes", ""),
    )


def load_cluster_config(path: Path) -> ClusterConfig:
    """Load and parse node-assignments.yaml into a ClusterConfig."""
    with path.open() as f:
        raw = yaml.safe_load(f)

    models = {k: _parse_model(v) for k, v in raw.get("models", {}).items()}

    nodes = {}
    for k, v in raw.get("nodes", {}).items():
        nodes[k] = Node(
            ip=v["ip"],
            ram_gb=v["ram_gb"],
            user=v.get("user", "admin"),
            role=v.get("role", "inference"),
        )

    assignments: dict[str, list[Assignment]] = {}
    for node_name, slots in raw.get("assignments", {}).items():
        assignments[node_name] = [
            Assignment(
                model=s["model"],
                port=s.get("port", 0),
                embedding=s.get("embedding", False),
            )
            for s in slots
        ]

    return ClusterConfig(models=models, nodes=nodes, assignments=assignments)


OS_OVERHEAD_GB = 8


def validate_memory(config: ClusterConfig) -> list[str]:
    errors: list[str] = []
    for node_name, slots in config.assignments.items():
        node = config.nodes.get(node_name)
        if node is None:
            errors.append(f"{node_name}: node not found in config")
            continue
        parts: list[str] = []
        total = OS_OVERHEAD_GB
        for slot in slots:
            model = config.models.get(slot.model)
            if model is None:
                errors.append(f"{node_name}: model '{slot.model}' not found in registry")
                continue
            weight_gb = model.ram_gb if model.ram_gb is not None else model.disk_gb
            kv_gb = model.kv_per_32k_gb
            slot_total = weight_gb + kv_gb
            total += slot_total
            parts.append(f"{slot.model}({weight_gb}+{kv_gb}kv)")
        budget_str = " + ".join(parts) + f" + {OS_OVERHEAD_GB} OS = {total:.1f} GB / {node.ram_gb} GB"
        if total > node.ram_gb:
            errors.append(f"{node_name}: {budget_str} ❌ EXCEEDS")
    return errors
