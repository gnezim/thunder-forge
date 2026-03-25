"""Tests for parse_cluster_config — raw dict parsing without file I/O."""

import pytest

from thunder_forge.cluster.config import parse_cluster_config


def test_parse_cluster_config_basic():
    """parse_cluster_config accepts a raw dict and returns a ClusterConfig."""
    raw = {
        "models": {
            "coder": {
                "source": {"type": "huggingface", "repo": "mlx-community/Qwen3-Coder-Next-4bit", "revision": "main"},
                "disk_gb": 44.8,
                "kv_per_32k_gb": 8,
                "max_context": 131072,
            }
        },
        "nodes": {
            "rock": {"ip": "192.168.1.61", "ram_gb": 32, "user": "infra_user", "role": "gateway"},
            "msm1": {"ip": "192.168.1.101", "ram_gb": 128, "user": "admin", "role": "node"},
        },
        "assignments": {"msm1": [{"model": "coder", "port": 8000}]},
    }
    config = parse_cluster_config(raw)
    assert "coder" in config.models
    assert config.models["coder"].source.type == "huggingface"
    assert config.models["coder"].disk_gb == 44.8
    assert config.nodes["msm1"].user == "admin"
    assert config.nodes["rock"].role == "gateway"
    assert config.assignments["msm1"][0].model == "coder"


def test_parse_cluster_config_user_stored_as_is():
    """User field is stored as-is — no env var resolution."""
    raw = {
        "models": {},
        "nodes": {"n1": {"ip": "1.2.3.4", "ram_gb": 64, "role": "node"}},
        "assignments": {},
    }
    config = parse_cluster_config(raw)
    assert config.nodes["n1"].user == ""


def test_parse_cluster_config_role_migration():
    """Deprecated role names are migrated."""
    raw = {
        "models": {},
        "nodes": {
            "n1": {"ip": "1.2.3.4", "ram_gb": 64, "role": "inference"},
            "gw": {"ip": "1.2.3.5", "ram_gb": 32, "role": "infra"},
        },
        "assignments": {},
    }
    with pytest.warns(DeprecationWarning, match="deprecated"):
        config = parse_cluster_config(raw)
    assert config.nodes["n1"].role == "node"
    assert config.nodes["gw"].role == "gateway"


def test_parse_cluster_config_external_endpoints():
    """External endpoints are parsed correctly."""
    raw = {
        "models": {},
        "nodes": {},
        "assignments": {},
        "external_endpoints": [
            {"model_name": "qwen3-30b", "api_base": "http://example.com/v1", "api_key_env": "MY_KEY"}
        ],
    }
    config = parse_cluster_config(raw)
    assert len(config.external_endpoints) == 1
    assert config.external_endpoints[0].model_name == "qwen3-30b"
    assert config.external_endpoints[0].api_key_env == "MY_KEY"
