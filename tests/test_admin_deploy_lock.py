"""Tests for gateway deploy lock file parsing and management."""

import time


def test_parse_lock_file_content():
    from thunder_forge.cluster.deploy import parse_lock_file

    content = f"PID:12345\nHEARTBEAT:{int(time.time())}"
    lock = parse_lock_file(content)
    assert lock["pid"] == 12345
    assert isinstance(lock["heartbeat"], int)


def test_parse_lock_file_empty():
    from thunder_forge.cluster.deploy import parse_lock_file

    assert parse_lock_file("") is None
    assert parse_lock_file(None) is None


def test_format_lock_file():
    from thunder_forge.cluster.deploy import format_lock_file

    content = format_lock_file(12345)
    assert "PID:12345" in content
    assert "HEARTBEAT:" in content
