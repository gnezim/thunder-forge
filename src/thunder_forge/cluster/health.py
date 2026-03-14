"""Health checks for inference nodes and infrastructure services."""

from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request

from thunder_forge.cluster.config import ClusterConfig


def check_inference_node(ip: str, port: int, timeout: float = 5.0) -> bool:
    url = f"http://{ip}:{port}/v1/models"
    try:
        with urllib.request.urlopen(url, timeout=timeout):
            return True
    except Exception:
        return False


def check_docker_services(
    rock_ip: str,
    rock_user: str,
    expected_services: tuple[str, ...] = ("litellm", "openwebui", "postgres"),
) -> dict[str, bool]:
    results = {svc: False for svc in expected_services}
    try:
        cmd = [
            "ssh", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=no",
            f"{rock_user}@{rock_ip}",
            "cd ~/thunder-forge/docker && docker compose ps --format json",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if proc.returncode != 0:
            return results
        for line in proc.stdout.strip().splitlines():
            if not line.strip():
                continue
            try:
                svc = json.loads(line)
                name = svc.get("Name", svc.get("Service", ""))
                state = svc.get("State", "")
                health = svc.get("Health", "")
                for expected in expected_services:
                    if expected in name:
                        results[expected] = state == "running" and health in ("healthy", "")
            except json.JSONDecodeError:
                continue
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return results


def run_health_checks(config: ClusterConfig) -> bool:
    all_healthy = True
    print("=== Inference ===")
    for node_name, slots in sorted(config.assignments.items()):
        node = config.nodes[node_name]
        for slot in slots:
            healthy = check_inference_node(node.ip, slot.port)
            status = "\u2705" if healthy else "\u274c"
            print(f"  {node_name}:{slot.port} ({slot.model}): {status}")
            if not healthy:
                all_healthy = False
    print("\n=== Infrastructure ===")
    rock = config.rock
    docker_health = check_docker_services(rock.ip, rock.user)
    display_names = {"litellm": "LiteLLM", "openwebui": "Open WebUI", "postgres": "PostgreSQL"}
    for svc, healthy in docker_health.items():
        status = "\u2705" if healthy else "\u274c"
        name = display_names.get(svc, svc)
        print(f"  {name:12s} {status}")
        if not healthy:
            all_healthy = False
    print("\n=== Model Assignments ===")
    for node_name, slots in sorted(config.assignments.items()):
        slot_strs = [f"{s.model}:{s.port}" for s in slots]
        print(f"  {node_name}: {', '.join(slot_strs)}")
    return all_healthy
