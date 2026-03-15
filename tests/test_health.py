"""Tests for health check logic."""

from pathlib import Path
from textwrap import dedent
from unittest.mock import MagicMock, patch

import pytest

from thunder_forge.cluster.health import check_inference_node


@pytest.fixture()
def config_path(tmp_path: Path) -> Path:
    content = dedent("""\
        models:
          coder:
            source: { type: huggingface, repo: "test/coder" }
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


@patch("thunder_forge.cluster.health.urllib.request.urlopen")
def test_check_inference_node_healthy(mock_urlopen: MagicMock) -> None:
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)
    mock_urlopen.return_value = mock_response

    result = check_inference_node("192.168.1.101", 8000)
    assert result is True
    mock_urlopen.assert_called_once()


@patch("thunder_forge.cluster.health.urllib.request.urlopen")
def test_check_inference_node_unreachable(mock_urlopen: MagicMock) -> None:
    mock_urlopen.side_effect = Exception("Connection refused")

    result = check_inference_node("192.168.1.101", 8000)
    assert result is False
