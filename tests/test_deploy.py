"""Tests for deploy logic: plist generation, orchestration."""

import xml.etree.ElementTree as ET
from pathlib import Path
from textwrap import dedent

import pytest

from thunder_forge.cluster.config import Assignment, Model, ModelSource, Node, load_cluster_config
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
          rock: { ip: "192.168.1.61", ram_gb: 32, user: "infra_user", role: gateway }
          msm1: { ip: "192.168.1.101", ram_gb: 128, user: "admin", role: node }

        assignments:
          msm1:
            - model: coder
              port: 8000
    """)
    p = tmp_path / "node-assignments.yaml"
    p.write_text(content)
    return p


def test_generate_plist_uses_resolved_fields() -> None:
    """Plist uses node.home_dir and node.homebrew_prefix, not hardcoded paths."""
    node = Node(
        ip="192.168.1.101",
        ram_gb=128,
        user="admin",
        role="node",
        home_dir="/Users/admin",
        homebrew_prefix="/opt/homebrew",
    )
    model = Model(source=ModelSource(type="huggingface", repo="test/model"), disk_gb=10)
    slot = Assignment(model="test", port=8000)
    xml_str = generate_plist(model, slot, node)
    assert "/Users/admin/.local/bin/mlx_lm.server" in xml_str
    assert "/opt/homebrew/bin" in xml_str
    assert "/Users/admin/logs/" in xml_str


def test_generate_plist_non_default_homebrew() -> None:
    """Plist uses custom homebrew prefix (e.g. Intel Mac)."""
    node = Node(
        ip="192.168.1.101",
        ram_gb=128,
        user="admin",
        role="node",
        home_dir="/Users/admin",
        homebrew_prefix="/usr/local",
    )
    model = Model(source=ModelSource(type="huggingface", repo="test/model"), disk_gb=10)
    slot = Assignment(model="test", port=8000)
    xml_str = generate_plist(model, slot, node)
    assert "/usr/local/bin" in xml_str
    assert "/opt/homebrew" not in xml_str


def test_generate_plist_no_homebrew() -> None:
    """Plist works without homebrew (Linux node)."""
    node = Node(
        ip="192.168.1.101",
        ram_gb=128,
        user="admin",
        role="node",
        home_dir="/home/admin",
        homebrew_prefix=None,
    )
    model = Model(source=ModelSource(type="huggingface", repo="test/model"), disk_gb=10)
    slot = Assignment(model="test", port=8000)
    xml_str = generate_plist(model, slot, node)
    assert "/home/admin/.local/bin/mlx_lm.server" in xml_str
    assert "/opt/homebrew" not in xml_str


def test_generate_plist_requires_resolved_fields() -> None:
    """Plist raises error if resolved fields are missing."""
    node = Node(ip="192.168.1.101", ram_gb=128, user="admin", role="node")
    model = Model(source=ModelSource(type="huggingface", repo="test/model"), disk_gb=10)
    slot = Assignment(model="test", port=8000)
    with pytest.raises(ValueError, match="pre-flight"):
        generate_plist(model, slot, node)


def test_generate_plist_basic(config_path: Path) -> None:
    config = load_cluster_config(config_path)
    model = config.models["coder"]
    slot = config.assignments["msm1"][0]
    node = config.nodes["msm1"]
    # Simulate pre-flight populating resolved fields
    node.home_dir = "/Users/admin"
    node.homebrew_prefix = "/opt/homebrew"

    xml_str = generate_plist(model, slot, node)

    root = ET.fromstring(xml_str)
    assert root.tag == "plist"

    assert "com.mlx-lm-8000" in xml_str
    assert "mlx-community/Qwen3-Coder-Next-4bit" in xml_str
    assert "--model" in xml_str
    assert "--port" in xml_str
    assert "8000" in xml_str
    assert "--host" in xml_str
    assert "0.0.0.0" in xml_str
    assert "--continuous-batching" not in xml_str
    assert "--max-model-len" not in xml_str
    assert "HF_HUB_OFFLINE" in xml_str
    assert "Interactive" in xml_str


def test_generate_plist_with_extra_args(config_path: Path) -> None:
    config = load_cluster_config(config_path)
    model = config.models["coder"]
    model.extra_args = ["--trust-remote-code", "--log-level", "DEBUG"]
    slot = config.assignments["msm1"][0]
    node = config.nodes["msm1"]
    node.home_dir = "/Users/admin"
    node.homebrew_prefix = "/opt/homebrew"
    xml_str = generate_plist(model, slot, node)
    assert "--trust-remote-code" in xml_str
    assert "DEBUG" in xml_str


def test_generate_plist_enable_thinking_false(config_path: Path) -> None:
    config = load_cluster_config(config_path)
    model = config.models["coder"]
    model.enable_thinking = False
    slot = config.assignments["msm1"][0]
    node = config.nodes["msm1"]
    node.home_dir = "/Users/admin"
    node.homebrew_prefix = "/opt/homebrew"
    xml_str = generate_plist(model, slot, node)
    assert "--chat-template-args" in xml_str
    assert '"enable_thinking": false' in xml_str


def test_generate_plist_enable_thinking_none(config_path: Path) -> None:
    """enable_thinking=None (unset) should not add --chat-template-args."""
    config = load_cluster_config(config_path)
    model = config.models["coder"]
    slot = config.assignments["msm1"][0]
    node = config.nodes["msm1"]
    node.home_dir = "/Users/admin"
    node.homebrew_prefix = "/opt/homebrew"
    xml_str = generate_plist(model, slot, node)
    assert "--chat-template-args" not in xml_str


def test_generate_plist_log_paths() -> None:
    """Logs go to ~/logs/mlx-lm-{port}.log, not /tmp/."""
    node = Node(
        ip="192.168.1.101",
        ram_gb=128,
        user="admin",
        role="node",
        home_dir="/Users/admin",
        homebrew_prefix="/opt/homebrew",
    )
    model = Model(source=ModelSource(type="huggingface", repo="test/model"), disk_gb=10)
    slot = Assignment(model="test", port=8000)
    xml_str = generate_plist(model, slot, node)
    assert "/Users/admin/logs/mlx-lm-8000.log" in xml_str
    assert "/Users/admin/logs/mlx-lm-8000.err" in xml_str
    assert "vllm" not in xml_str
