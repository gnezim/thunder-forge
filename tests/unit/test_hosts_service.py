from __future__ import annotations

from services.config_service import HostsSyncSettings, TFConfig
from services.hosts_service import build_hosts_block, upsert_managed_hosts_block


def test_build_hosts_block_contains_mgmt_line():
    cfg = TFConfig.model_validate(
        {
            "telegram": {"bot_token": "test-token"},
            "settings": {
                "hosts_sync": {
                    "managed_block_start": "# BEGIN thunder-forge",
                    "managed_block_end": "# END thunder-forge",
                }
            },
            "nodes": {
                "defaults": {"ssh_user": "u", "service_manager": "brew"},
                "items": [
                    {
                        "name": "msm1",
                        "ssh_host": "1.2.3.4",
                        "mgmt_ip": "192.168.1.101",
                    }
                ],
            },
        }
    )

    block = build_hosts_block(cfg).block
    assert "# BEGIN thunder-forge" in block
    assert "192.168.1.101 msm1-mgmt" in block
    assert "# END thunder-forge" in block


def test_upsert_replaces_existing_block():
    hosts = "127.0.0.1 localhost\n# BEGIN thunder-forge\nold\n# END thunder-forge\n"
    managed = "# BEGIN thunder-forge\nnew\n# END thunder-forge\n"
    out = upsert_managed_hosts_block(
        hosts_file_text=hosts,
        managed_block=managed,
        settings=inv_settings(),
    )
    assert "old" not in out
    assert "new" in out


def inv_settings():
    return HostsSyncSettings(
        managed_block_start="# BEGIN thunder-forge",
        managed_block_end="# END thunder-forge",
    )
