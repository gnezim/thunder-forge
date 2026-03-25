"""Tests for admin deploy checks."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

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


# --- check_ssh ---


def test_check_ssh_ok():
    from thunder_admin.checks import check_ssh

    from thunder_forge.cluster.config import Node

    node = Node(ip="10.0.0.1", ram_gb=64, user="admin")
    mock_client = MagicMock()
    mock_stdout = MagicMock()
    mock_stdout.read.return_value = b"ok\n"
    mock_client.exec_command.return_value = (MagicMock(), mock_stdout, MagicMock())

    with patch("thunder_admin.checks.paramiko.SSHClient", return_value=mock_client):
        with patch("thunder_admin.checks._resolve_ssh_key", return_value=MagicMock()):
            result, conn = check_ssh(node)

    assert result == ("ok", "")
    assert conn is mock_client


def test_check_ssh_timeout():
    from thunder_admin.checks import check_ssh

    from thunder_forge.cluster.config import Node

    node = Node(ip="10.0.0.1", ram_gb=64, user="admin")
    mock_client = MagicMock()
    mock_client.connect.side_effect = TimeoutError("timed out")

    with patch("thunder_admin.checks.paramiko.SSHClient", return_value=mock_client):
        with patch("thunder_admin.checks._resolve_ssh_key", return_value=MagicMock()):
            result, conn = check_ssh(node)

    assert result == ("error", "SSH timeout")
    assert conn is None


def test_check_ssh_unexpected_exception():
    from thunder_admin.checks import check_ssh

    from thunder_forge.cluster.config import Node

    node = Node(ip="10.0.0.1", ram_gb=64, user="admin")
    mock_client = MagicMock()
    mock_client.connect.side_effect = Exception("host key mismatch")

    with patch("thunder_admin.checks.paramiko.SSHClient", return_value=mock_client):
        with patch("thunder_admin.checks._resolve_ssh_key", return_value=MagicMock()):
            result, conn = check_ssh(node)

    assert result[0] == "error"
    assert "host key mismatch" in result[1]
    assert conn is None
