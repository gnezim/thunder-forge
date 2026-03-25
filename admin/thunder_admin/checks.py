"""Pre-deploy status checks for each assignment slot."""
from __future__ import annotations

import os  # noqa: F401
from concurrent.futures import ThreadPoolExecutor, as_completed  # noqa: F401
from typing import Literal

import paramiko  # noqa: F401

from thunder_admin.config import validate_config
from thunder_forge.cluster.config import Assignment, ClusterConfig, Node, parse_cluster_config  # noqa: F401

CheckResult = tuple[Literal["ok", "warn", "error", "skip"], str]
SlotChecks = dict[str, CheckResult]

_SSH_TIMEOUT = 10


def check_config(config: dict) -> CheckResult:
    """Static config validation — no I/O. Returns all errors joined, capped at 120 chars."""
    errors = validate_config(config)
    if not errors:
        return ("ok", "")
    joined = "; ".join(errors)
    return ("error", joined[:120])
