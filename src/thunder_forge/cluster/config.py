"""Config parsing, memory validation, and LiteLLM config generation."""

from __future__ import annotations

import os
import subprocess
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
    user: str = ""
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
            user=v.get("user") or os.environ.get("TF_SSH_USER") or "admin",
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


def generate_litellm_config(config: ClusterConfig) -> str:
    model_list: list[dict] = []
    for node_name, slots in sorted(config.assignments.items()):
        node = config.nodes[node_name]
        for slot in slots:
            model = config.models[slot.model]
            if model.serving in ("embedding", "cli"):
                continue
            if model.serving == "mlx-openai-server":
                provider = "openai"
            else:
                provider = "hosted_vllm"
            entry: dict = {
                "model_name": slot.model,
                "litellm_params": {
                    "model": f"{provider}/{model.source.repo}",
                    "api_base": f"http://{node.ip}:{slot.port}/v1",
                    "api_key": "none",
                },
            }
            if model.max_context > 0:
                entry["litellm_params"]["max_input_tokens"] = model.max_context
                entry["litellm_params"]["max_output_tokens"] = 16384
            model_list.append(entry)
            if slot.embedding:
                emb_model = config.models.get("embedding")
                if emb_model:
                    model_list.append({
                        "model_name": "embedding",
                        "litellm_params": {
                            "model": f"openai/{emb_model.source.repo}",
                            "api_base": f"http://{node.ip}:{slot.port}/v1",
                            "api_key": "none",
                        },
                    })
    output: dict = {
        "model_list": model_list,
        "litellm_settings": {
            "num_retries": 2,
            "timeout": 120,
            "allowed_fails": 3,
            "cooldown_time": 30,
            "callbacks": ["prometheus"],
        },
        "router_settings": {
            "routing_strategy": "least-busy",
            "model_group_retry_policy": {},
        },
        "general_settings": {
            "master_key": "os.environ/LITELLM_MASTER_KEY",
        },
    }
    header = (
        "# AUTO-GENERATED by thunder-forge generate-config\n"
        "# from configs/node-assignments.yaml\n"
        "# Do not edit manually — edit node-assignments.yaml instead.\n\n"
    )
    return header + yaml.dump(output, default_flow_style=False, sort_keys=False)


def find_repo_root() -> Path:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True,
        )
        return Path(result.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    current = Path.cwd()
    for parent in [current, *current.parents]:
        if (parent / "configs" / "node-assignments.yaml").exists():
            return parent
    msg = "Cannot find repo root (no git repo and no configs/node-assignments.yaml found)"
    raise FileNotFoundError(msg)


def check_config_sync(config: ClusterConfig, committed_path: Path) -> bool:
    generated = generate_litellm_config(config)
    if not committed_path.exists():
        return False
    committed = committed_path.read_text()
    return generated == committed
