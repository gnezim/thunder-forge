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


# --- check_model ---


def _make_ssh_conn(stdout_output: bytes, exit_code: int = 0) -> MagicMock:
    """Return a mock SSHClient whose exec_command returns the given stdout."""
    mock_client = MagicMock()
    mock_stdout = MagicMock()
    mock_stdout.read.return_value = stdout_output
    mock_channel = MagicMock()
    mock_channel.recv_exit_status.return_value = exit_code
    mock_stdout.channel = mock_channel
    mock_client.exec_command.return_value = (MagicMock(), mock_stdout, MagicMock())
    return mock_client


def test_check_model_hf_found():
    from thunder_admin.checks import check_model

    from thunder_forge.cluster.config import Assignment, ClusterConfig, Model, ModelSource, Node

    node = Node(ip="10.0.0.1", ram_gb=64, user="admin")
    slot = Assignment(model="llama", port=8000)
    cluster = ClusterConfig(
        models={"llama": Model(source=ModelSource(type="huggingface", repo="mlx-community/Llama-3.2-3B"))},
        nodes={"msm1": node},
        assignments={"msm1": [slot]},
    )
    conn = _make_ssh_conn(b"snapshots\nrefs\n", exit_code=0)

    result = check_model(conn, node, slot, cluster)
    assert result == ("ok", "")
    # Verify the correct path was checked
    call_args = conn.exec_command.call_args[0][0]
    assert "models--mlx-community--Llama-3.2-3B" in call_args


def test_check_model_hf_not_found():
    from thunder_admin.checks import check_model

    from thunder_forge.cluster.config import Assignment, ClusterConfig, Model, ModelSource, Node

    node = Node(ip="10.0.0.1", ram_gb=64, user="admin")
    slot = Assignment(model="llama", port=8000)
    cluster = ClusterConfig(
        models={"llama": Model(source=ModelSource(type="huggingface", repo="mlx-community/Llama-3.2-3B"))},
        nodes={"msm1": node},
        assignments={"msm1": [slot]},
    )
    conn = _make_ssh_conn(b"", exit_code=2)  # ls returns 2 = no such file

    result = check_model(conn, node, slot, cluster)
    assert result[0] == "error"
    assert "not found" in result[1]


def test_check_model_non_hf_source_returns_warn():
    from thunder_admin.checks import check_model

    from thunder_forge.cluster.config import Assignment, ClusterConfig, Model, ModelSource, Node

    node = Node(ip="10.0.0.1", ram_gb=64, user="admin")
    slot = Assignment(model="local_m", port=8000)
    cluster = ClusterConfig(
        models={"local_m": Model(source=ModelSource(type="local", path="/models/llama"))},
        nodes={"msm1": node},
        assignments={"msm1": [slot]},
    )
    conn = MagicMock()

    for source_type in ("local", "pip", "convert"):
        cluster.models["local_m"].source.type = source_type
        result = check_model(conn, node, slot, cluster)
        assert result == ("warn", "non-HF source; skipping model check")
    conn.exec_command.assert_not_called()


def test_check_model_ssh_exception():
    from thunder_admin.checks import check_model

    from thunder_forge.cluster.config import Assignment, ClusterConfig, Model, ModelSource, Node

    node = Node(ip="10.0.0.1", ram_gb=64, user="admin")
    slot = Assignment(model="llama", port=8000)
    cluster = ClusterConfig(
        models={"llama": Model(source=ModelSource(type="huggingface", repo="mlx-community/Llama"))},
        nodes={"msm1": node},
        assignments={"msm1": [slot]},
    )
    conn = MagicMock()
    conn.exec_command.side_effect = Exception("channel closed")

    result = check_model(conn, node, slot, cluster)
    assert result[0] == "error"
    assert "channel closed" in result[1]
