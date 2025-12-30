from __future__ import annotations

import os
from functools import lru_cache
from typing import Any, Literal, Optional

import yaml
from pydantic import BaseModel, Field
from pydantic import field_validator


class ServerConfig(BaseModel):
    bind: str = "127.0.0.1"
    port: int = 8000
    reload: bool = True


class TelegramConfig(BaseModel):
    bot_token: str


class SSHSettings(BaseModel):
    connect_timeout_seconds: float = 1.0
    batch_mode: bool = True


class MonitorSettings(BaseModel):
    ssh_port: int = 22
    ollama_port: int = 11434


class HostsSyncSettings(BaseModel):
    managed_block_start: str = "# BEGIN thunder-forge"
    managed_block_end: str = "# END thunder-forge"


class AccessSettings(BaseModel):
    admin_telegram_ids: list[int] = Field(default_factory=list)


class FabricIPv4Defaults(BaseModel):
    netmask: str = "255.255.255.252"
    router: str = ""


class FabricNetNode(BaseModel):
    name: str
    address: str


class FabricNetConfig(BaseModel):
    # macOS network service name as shown by:
    #   networksetup -listallnetworkservices
    service_name: str = "Thunderbolt Bridge"

    # IPv4 configuration mode.
    # - manual: macOS UI "Manually".
    # - dhcp_with_manual_address: macOS UI "Using DHCP with Manual Address".
    # Both can be used with 169.254/16 link-local addressing.
    ipv4_mode: Literal["dhcp_with_manual_address", "manual"] = "manual"
    ipv4_defaults: FabricIPv4Defaults = Field(default_factory=FabricIPv4Defaults)
    nodes: list[FabricNetNode] = Field(default_factory=list)

    @field_validator("nodes", mode="before")
    @classmethod
    def _coerce_nodes_null_to_empty_list(cls, v: Any):
        # YAML edge case: `nodes:` with only comments becomes `null`.
        return [] if v is None else v


class NodeDefaults(BaseModel):
    ssh_user: str | None = None
    service_manager: Literal["brew", "systemd"] | None = None

    # Optional overrides
    ssh_host: str | None = None

    ollama_service: str | None = None
    models: list[str] | None = None


class NodeItem(BaseModel):
    name: str
    mgmt_ip: str

    ssh_user: str | None = None
    service_manager: Literal["brew", "systemd"] | None = None
    ssh_host: str | None = None

    ollama_service: str | None = None
    models: list[str] | None = None


class NodesConfig(BaseModel):
    defaults: NodeDefaults = Field(default_factory=NodeDefaults)
    items: list[NodeItem] = Field(default_factory=list)

    @field_validator("items", mode="before")
    @classmethod
    def _coerce_items_null_to_empty_list(cls, v: Any):
        return [] if v is None else v


class Node(BaseModel):
    name: str
    ssh_user: str
    mgmt_ip: str
    service_manager: Literal["brew", "systemd"]

    # Optional overrides
    ssh_host: Optional[str] = None

    ollama_service: str = "ollama"
    models: list[str] = Field(default_factory=list)


def _resolve_nodes(nodes: NodesConfig) -> list[Node]:
    defaults = nodes.defaults.model_dump(exclude_none=True)
    resolved: list[Node] = []
    for item in nodes.items:
        merged = {
            **defaults,
            **item.model_dump(exclude_none=True),
        }
        resolved.append(Node.model_validate(merged))
    return resolved


class FleetSettings(BaseModel):
    ssh: SSHSettings = Field(default_factory=SSHSettings)
    monitor: MonitorSettings = Field(default_factory=MonitorSettings)
    hosts_sync: HostsSyncSettings = Field(default_factory=HostsSyncSettings)


class TFConfig(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    telegram: TelegramConfig
    access: AccessSettings = Field(default_factory=AccessSettings)
    settings: FleetSettings = Field(default_factory=FleetSettings)
    nodes: NodesConfig

    # Optional: shared fabric networking config (typically Thunderbolt Bridge).
    fabricnet: Optional[FabricNetConfig] = None

    mini_app_url: str = "http://127.0.0.1:8000/mini-app/"

    # Security: Telegram initData max age
    tma_max_age_seconds: int = 86400


def get_config_path() -> str:
    # Only env we keep: which single config file to use.
    return os.environ.get("TF_CONFIG_PATH", "tf.yml")


@lru_cache(maxsize=1)
def load_config(path: Optional[str] = None) -> TFConfig:
    path = path or get_config_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Missing config file: {path}. Create tf.yml (or set TF_CONFIG_PATH)."
        ) from exc
    return TFConfig.model_validate(data)


def iter_nodes(cfg: TFConfig) -> list[Node]:
    # Single place for consumers to get resolved nodes.
    return _resolve_nodes(cfg.nodes)
