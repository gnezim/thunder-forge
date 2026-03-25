"""Tests for admin deploy checks."""
from __future__ import annotations

from unittest.mock import MagicMock, patch  # noqa: F401

# --- check_config ---

def _valid_config() -> dict:
    return {
        "models": {
            "llama": {
                "source": {"type": "huggingface", "repo": "mlx-community/Llama-3.2-3B-Instruct-4bit"},
                "disk_gb": 2.0,
                "ram_gb": None,
            }
        },
        "nodes": {"msm1": {"ip": "10.0.0.1", "ram_gb": 64, "role": "node", "user": "admin"}},
        "assignments": {"msm1": [{"model": "llama", "port": 8000, "embedding": False}]},
        "external_endpoints": [],
    }


def test_check_config_ok():
    from thunder_admin.checks import check_config

    status, detail = check_config(_valid_config())
    assert status == "ok"
    assert detail == ""


def test_check_config_error_missing_model():
    from thunder_admin.checks import check_config

    config = _valid_config()
    config["assignments"]["msm1"][0]["model"] = "nonexistent"
    status, detail = check_config(config)
    assert status == "error"
    assert "nonexistent" in detail


def test_check_config_error_message_capped_at_120_chars():
    from thunder_admin.checks import check_config

    config = _valid_config()
    # Create many errors: reference non-existent models on many ports
    config["assignments"]["msm1"] = [{"model": f"missing_{i}", "port": 8000 + i} for i in range(10)]
    status, detail = check_config(config)
    assert status == "error"
    assert len(detail) <= 120
