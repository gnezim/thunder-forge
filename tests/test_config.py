"""Tests for config parsing, validation, and generation."""

from pathlib import Path
from textwrap import dedent

import pytest
import yaml as yaml_lib

from thunder_forge.cluster.config import (
    check_config_sync,
    generate_litellm_config,
    load_cluster_config,
    validate_memory,
)


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
          rock: { ip: "192.168.1.61", ram_gb: 32, user: "infra_user", role: infra }
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


def test_validate_memory_single_model_passes(assignments_yaml: Path) -> None:
    config = load_cluster_config(assignments_yaml)
    errors = validate_memory(config)
    assert errors == []


@pytest.fixture()
def overloaded_yaml(tmp_path: Path) -> Path:
    content = dedent("""\
        models:
          big_model:
            source: { type: huggingface, repo: "test/big" }
            disk_gb: 100
            kv_per_32k_gb: 30
            max_context: 32768

        nodes:
          msm1: { ip: "192.168.1.101", ram_gb: 128, user: "admin", role: inference }
          rock: { ip: "192.168.1.61", ram_gb: 32, user: "infra_user", role: infra }

        assignments:
          msm1:
            - model: big_model
              port: 8000
    """)
    p = tmp_path / "node-assignments.yaml"
    p.write_text(content)
    return p


def test_validate_memory_overloaded_fails(overloaded_yaml: Path) -> None:
    config = load_cluster_config(overloaded_yaml)
    errors = validate_memory(config)
    assert len(errors) == 1
    assert "msm1" in errors[0]


@pytest.fixture()
def multi_model_yaml(tmp_path: Path) -> Path:
    content = dedent("""\
        models:
          coder:
            source: { type: huggingface, repo: "test/coder" }
            disk_gb: 44.8
            kv_per_32k_gb: 8
            max_context: 131072
          general:
            source: { type: huggingface, repo: "test/general" }
            disk_gb: 44.8
            kv_per_32k_gb: 8
            max_context: 131072

        nodes:
          msm1: { ip: "192.168.1.101", ram_gb: 128, user: "admin", role: inference }
          rock: { ip: "192.168.1.61", ram_gb: 32, user: "infra_user", role: infra }

        assignments:
          msm1:
            - model: coder
              port: 8000
            - model: general
              port: 8001
    """)
    p = tmp_path / "node-assignments.yaml"
    p.write_text(content)
    return p


def test_validate_memory_multi_model_passes(multi_model_yaml: Path) -> None:
    config = load_cluster_config(multi_model_yaml)
    errors = validate_memory(config)
    assert errors == []


def test_validate_memory_uses_ram_gb_override(tmp_path: Path) -> None:
    content = dedent("""\
        models:
          video:
            source: { type: pip, package: "mlx-video" }
            disk_gb: 5
            ram_gb: 120
            max_context: 0
            serving: cli

        nodes:
          msm1: { ip: "192.168.1.101", ram_gb: 128, user: "admin", role: inference }
          rock: { ip: "192.168.1.61", ram_gb: 32, user: "infra_user", role: infra }

        assignments:
          msm1:
            - model: video
              port: 8000
    """)
    p = tmp_path / "node-assignments.yaml"
    p.write_text(content)
    config = load_cluster_config(p)
    errors = validate_memory(config)
    assert errors == []


def test_generate_litellm_config_basic(assignments_yaml: Path) -> None:
    config = load_cluster_config(assignments_yaml)
    result = generate_litellm_config(config)
    parsed = yaml_lib.safe_load(result)
    assert result.startswith("# AUTO-GENERATED")
    assert len(parsed["model_list"]) == 1
    entry = parsed["model_list"][0]
    assert entry["model_name"] == "coder"
    assert entry["litellm_params"]["model"] == "hosted_vllm/mlx-community/Qwen3-Coder-Next-4bit"
    assert entry["litellm_params"]["api_base"] == "http://192.168.1.101:8000/v1"
    assert entry["litellm_params"]["api_key"] == "none"
    assert entry["litellm_params"]["max_input_tokens"] == 131072
    assert entry["litellm_params"]["max_output_tokens"] == 16384
    assert parsed["litellm_settings"]["callbacks"] == ["prometheus"]
    assert parsed["router_settings"]["routing_strategy"] == "least-busy"
    assert parsed["general_settings"]["master_key"] == "os.environ/LITELLM_MASTER_KEY"


def test_generate_litellm_config_multi_node(multi_model_yaml: Path) -> None:
    config = load_cluster_config(multi_model_yaml)
    result = generate_litellm_config(config)
    parsed = yaml_lib.safe_load(result)
    assert len(parsed["model_list"]) == 2
    names = {e["model_name"] for e in parsed["model_list"]}
    assert names == {"coder", "general"}


def test_generate_litellm_config_embedding_slot(tmp_path: Path) -> None:
    content = dedent("""\
        models:
          coder:
            source: { type: huggingface, repo: "test/coder" }
            disk_gb: 44.8
            kv_per_32k_gb: 8
            max_context: 131072
          embedding:
            source: { type: huggingface, repo: "test/embedding-model" }
            disk_gb: 0.5
            serving: embedding

        nodes:
          msm1: { ip: "192.168.1.101", ram_gb: 128, user: "admin", role: inference }
          rock: { ip: "192.168.1.61", ram_gb: 32, user: "infra_user", role: infra }

        assignments:
          msm1:
            - model: coder
              port: 8000
              embedding: true
    """)
    p = tmp_path / "node-assignments.yaml"
    p.write_text(content)
    config = load_cluster_config(p)
    result = generate_litellm_config(config)
    parsed = yaml_lib.safe_load(result)
    assert len(parsed["model_list"]) == 2
    names = [e["model_name"] for e in parsed["model_list"]]
    assert "coder" in names
    assert "embedding" in names
    emb_entry = next(e for e in parsed["model_list"] if e["model_name"] == "embedding")
    assert emb_entry["litellm_params"]["model"] == "openai/test/embedding-model"
    assert emb_entry["litellm_params"]["api_base"] == "http://192.168.1.101:8000/v1"


def test_generate_litellm_config_skips_cli_serving(tmp_path: Path) -> None:
    content = dedent("""\
        models:
          video:
            source: { type: pip, package: "mlx-video" }
            disk_gb: 5
            ram_gb: 20
            max_context: 0
            serving: cli

        nodes:
          msm1: { ip: "192.168.1.101", ram_gb: 128, user: "admin", role: inference }
          rock: { ip: "192.168.1.61", ram_gb: 32, user: "infra_user", role: infra }

        assignments:
          msm1:
            - model: video
              port: 8000
    """)
    p = tmp_path / "node-assignments.yaml"
    p.write_text(content)
    config = load_cluster_config(p)
    result = generate_litellm_config(config)
    parsed = yaml_lib.safe_load(result)
    assert len(parsed["model_list"]) == 0


def test_check_config_sync_matches(assignments_yaml: Path, tmp_path: Path) -> None:
    config = load_cluster_config(assignments_yaml)
    generated = generate_litellm_config(config)
    committed = tmp_path / "litellm-config.yaml"
    committed.write_text(generated)
    assert check_config_sync(config, committed) is True


def test_check_config_sync_mismatch(assignments_yaml: Path, tmp_path: Path) -> None:
    config = load_cluster_config(assignments_yaml)
    committed = tmp_path / "litellm-config.yaml"
    committed.write_text("stale content")
    assert check_config_sync(config, committed) is False
