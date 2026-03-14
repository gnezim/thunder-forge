"""Tests for deploy logic: plist generation, orchestration."""

import xml.etree.ElementTree as ET
from pathlib import Path
from textwrap import dedent

import pytest

from thunder_forge.cluster.config import load_cluster_config
from thunder_forge.cluster.deploy import generate_plist


@pytest.fixture()
def config_path(tmp_path: Path) -> Path:
    content = dedent("""\
        models:
          coder:
            source:
              type: huggingface
              repo: "mlx-community/Qwen3-Coder-Next-4bit"
            disk_gb: 44.8
            kv_per_32k_gb: 8
            max_context: 131072

        nodes:
          rock: { ip: "192.168.1.61", ram_gb: 32, user: "admin", role: infra }
          msm1: { ip: "192.168.1.101", ram_gb: 128, user: "admin", role: inference }

        assignments:
          msm1:
            - model: coder
              port: 8000
    """)
    p = tmp_path / "node-assignments.yaml"
    p.write_text(content)
    return p


def test_generate_plist_basic(config_path: Path) -> None:
    config = load_cluster_config(config_path)
    model = config.models["coder"]
    slot = config.assignments["msm1"][0]
    node = config.nodes["msm1"]

    xml_str = generate_plist(model, slot, node)

    root = ET.fromstring(xml_str)
    assert root.tag == "plist"

    assert "com.vllm-mlx-8000" in xml_str
    assert "mlx-community/Qwen3-Coder-Next-4bit" in xml_str
    assert "--port" in xml_str
    assert "8000" in xml_str
    assert "--continuous-batching" in xml_str
    assert "--max-model-len" in xml_str
    assert "131072" in xml_str
    assert "Interactive" in xml_str


def test_generate_plist_with_embedding(config_path: Path) -> None:
    config = load_cluster_config(config_path)

    from thunder_forge.cluster.config import Model, ModelSource, Assignment
    config.models["embedding"] = Model(
        source=ModelSource(type="huggingface", repo="mlx-community/Qwen3-Embedding-0.6B-4bit-DWQ"),
        disk_gb=0.5,
        serving="embedding",
    )

    model = config.models["coder"]
    slot = Assignment(model="coder", port=8000, embedding=True)
    node = config.nodes["msm1"]

    xml_str = generate_plist(model, slot, node, embedding_model=config.models.get("embedding"))
    assert "--embedding-model" in xml_str
    assert "Qwen3-Embedding-0.6B-4bit-DWQ" in xml_str
