"""Tests for config parsing, validation, and generation."""

from pathlib import Path
from textwrap import dedent

import pytest

from thunder_forge.cluster.config import load_cluster_config


@pytest.fixture()
def assignments_yaml(tmp_path: Path) -> Path:
    """Create a minimal node-assignments.yaml for testing."""
    content = dedent("""\
        models:
          coder:
            source:
              type: huggingface
              repo: "mlx-community/Qwen3-Coder-Next-4bit"
              revision: "main"
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


def test_load_cluster_config(assignments_yaml: Path) -> None:
    config = load_cluster_config(assignments_yaml)
    assert "coder" in config.models
    assert config.models["coder"].source.type == "huggingface"
    assert config.models["coder"].disk_gb == 44.8
    assert "msm1" in config.nodes
    assert config.nodes["msm1"].ip == "192.168.1.101"
    assert config.nodes["msm1"].role == "inference"
    assert "rock" in config.nodes
    assert config.nodes["rock"].role == "infra"
    assert len(config.assignments["msm1"]) == 1
    assert config.assignments["msm1"][0].model == "coder"
    assert config.assignments["msm1"][0].port == 8000
