"""Deployment: plist generation, SSH deploy, launchctl management."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from thunder_forge.cluster.config import Assignment, ClusterConfig, Model, Node
from thunder_forge.cluster.ssh import scp_content, ssh_run


def generate_plist(
    model: Model,
    slot: Assignment,
    node: Node,
    *,
    embedding_model: Model | None = None,
) -> str:
    label = f"com.vllm-mlx-{slot.port}"
    user_home = f"/Users/{node.user}"
    vllm_path = f"{user_home}/.local/bin/vllm-mlx"

    program_args = [
        vllm_path,
        "serve",
        model.source.repo,
        "--port",
        str(slot.port),
        "--host",
        "0.0.0.0",
        "--continuous-batching",
    ]

    if slot.embedding and embedding_model:
        program_args.extend(["--embedding-model", embedding_model.source.repo])

    env_vars = {
        "PATH": f"{user_home}/.local/bin:/opt/homebrew/bin:/usr/bin:/bin",
        "HOME": user_home,
        "no_proxy": "*",
    }

    plist = ET.Element("plist", version="1.0")
    d = ET.SubElement(plist, "dict")

    def add_key_value(parent: ET.Element, key: str, value_elem: ET.Element) -> None:
        k = ET.SubElement(parent, "key")
        k.text = key
        parent.append(value_elem)

    def make_string(text: str) -> ET.Element:
        e = ET.Element("string")
        e.text = text
        return e

    def make_true() -> ET.Element:
        return ET.Element("true")

    def make_integer(val: int) -> ET.Element:
        e = ET.Element("integer")
        e.text = str(val)
        return e

    add_key_value(d, "Label", make_string(label))

    k = ET.SubElement(d, "key")
    k.text = "ProgramArguments"
    arr = ET.SubElement(d, "array")
    for arg in program_args:
        s = ET.SubElement(arr, "string")
        s.text = arg

    k = ET.SubElement(d, "key")
    k.text = "EnvironmentVariables"
    env_dict = ET.SubElement(d, "dict")
    for env_key, env_val in env_vars.items():
        ek = ET.SubElement(env_dict, "key")
        ek.text = env_key
        ev = ET.SubElement(env_dict, "string")
        ev.text = env_val

    add_key_value(d, "StandardOutPath", make_string(f"{user_home}/logs/vllm-mlx-{slot.port}.log"))
    add_key_value(d, "StandardErrorPath", make_string(f"{user_home}/logs/vllm-mlx-{slot.port}.err"))

    add_key_value(d, "RunAtLoad", make_true())
    add_key_value(d, "KeepAlive", make_true())
    add_key_value(d, "ThrottleInterval", make_integer(10))
    add_key_value(d, "ProcessType", make_string("Interactive"))

    ET.indent(plist, space="  ")
    xml_declaration = '<?xml version="1.0" encoding="UTF-8"?>\n'
    doctype = (
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"\n  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
    )
    body = ET.tostring(plist, encoding="unicode")
    return xml_declaration + doctype + body + "\n"


NEWSYSLOG_CONF = """\
# logfilename                             [owner:group] mode count size(KB) when  flags
/Users/{user}/logs/vllm-mlx-*.log            {user}:staff     644  7     102400   *     CNJ
/Users/{user}/logs/vllm-mlx-*.err            {user}:staff     644  7     102400   *     CNJ
"""


def upgrade_node_tools(node: Node) -> None:
    """Best-effort upgrade of uv-managed tools on a node."""
    result = ssh_run(node.user, node.ip, "uv tool upgrade --all", timeout=120)
    if result.returncode != 0:
        print(f"  Warning: uv tool upgrade failed on {node.ip} (continuing)")
    else:
        print("  Tools upgraded")


def deploy_node(
    node_name: str,
    config: ClusterConfig,
) -> list[str]:
    errors: list[str] = []
    node = config.nodes[node_name]
    slots = config.assignments.get(node_name, [])

    if not slots:
        return [f"{node_name}: no assignments found"]

    uid_result = ssh_run(node.user, node.ip, "mkdir -p ~/logs ~/Library/LaunchAgents && id -u")
    if uid_result.returncode != 0:
        return [f"{node_name}: failed to get UID"]
    uid = uid_result.stdout.strip()

    upgrade_node_tools(node)

    deployed_ports: set[int] = set()

    for slot in slots:
        model = config.models[slot.model]
        embedding_model = config.models.get("embedding") if slot.embedding else None

        plist_xml = generate_plist(model, slot, node, embedding_model=embedding_model)
        plist_name = f"com.vllm-mlx-{slot.port}.plist"
        remote_plist = f"~/Library/LaunchAgents/{plist_name}"

        result = scp_content(node.user, node.ip, plist_xml, remote_plist)
        if result.returncode != 0:
            errors.append(f"{node_name}: failed to upload {plist_name}")
            continue

        label = f"com.vllm-mlx-{slot.port}"
        domain = f"gui/{uid}"

        # Bootout + bootstrap in one call. If bootstrap fails (already registered), fall back to kickstart.
        plist_path = f"~/Library/LaunchAgents/{plist_name}"
        cmd = f"launchctl bootout {domain}/{label} 2>/dev/null; launchctl bootstrap {domain} {plist_path}"
        result = ssh_run(node.user, node.ip, cmd)
        if result.returncode != 0:
            result = ssh_run(node.user, node.ip, f"launchctl kickstart -kp {domain}/{label}")
            if result.returncode != 0:
                errors.append(f"{node_name}: failed to start service on port {slot.port}: {result.stderr.strip()}")

        deployed_ports.add(slot.port)

    newsyslog = NEWSYSLOG_CONF.format(user=node.user)
    scp_content(node.user, node.ip, newsyslog, "/tmp/vllm-mlx-newsyslog.conf")
    ssh_run(node.user, node.ip, "sudo mv /tmp/vllm-mlx-newsyslog.conf /etc/newsyslog.d/vllm-mlx.conf")

    ls_result = ssh_run(node.user, node.ip, "ls ~/Library/LaunchAgents/com.vllm-mlx-*.plist 2>/dev/null || true")
    if ls_result.stdout.strip():
        for line in ls_result.stdout.strip().splitlines():
            filename = line.strip().split("/")[-1]
            try:
                port = int(filename.replace("com.vllm-mlx-", "").replace(".plist", ""))
                if port not in deployed_ports:
                    print(f"  Removing stale plist for port {port}")
                    stale = f"com.vllm-mlx-{port}"
                    cmd = f"launchctl bootout gui/{uid}/{stale} 2>/dev/null; rm ~/Library/LaunchAgents/{stale}.plist"
                    ssh_run(node.user, node.ip, cmd)
            except ValueError:
                continue

    return errors


def restart_litellm(config: ClusterConfig) -> bool:
    from thunder_forge.cluster.config import find_repo_root

    rock = config.rock
    docker_dir = find_repo_root() / "docker"
    result = ssh_run(
        rock.user,
        rock.ip,
        f"cd {docker_dir} && docker compose restart litellm",
        timeout=60,
    )
    return result.returncode == 0


def health_poll(ip: str, port: int, *, timeout_secs: int = 180, interval: int = 5) -> bool:
    import time
    import urllib.request

    url = f"http://{ip}:{port}/v1/models"
    deadline = time.monotonic() + timeout_secs

    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5):
                return True
        except Exception:
            time.sleep(interval)

    return False


def run_deploy(config: ClusterConfig, *, target_node: str | None = None) -> bool:
    all_ok = True

    if target_node:
        if target_node not in config.assignments:
            print(f"Node '{target_node}' not found in assignments")
            return False
        deploy_nodes = [target_node]
    else:
        deploy_nodes = list(config.assignments.keys())

    for node_name in deploy_nodes:
        print(f"\nDeploying to {node_name}...")
        errors = deploy_node(node_name, config)
        if errors:
            all_ok = False
            for err in errors:
                print(f"  {err}")
        else:
            print("  Plists deployed")

    print("\nRestarting LiteLLM...")
    if restart_litellm(config):
        print("  LiteLLM restarted")
    else:
        if target_node:
            print("  Warning: LiteLLM restart failed (non-fatal with --node)")
        else:
            print("  LiteLLM restart failed")
            all_ok = False

    print("\nWaiting for services to become healthy...")
    for node_name in deploy_nodes:
        node = config.nodes[node_name]
        for slot in config.assignments[node_name]:
            healthy = health_poll(node.ip, slot.port)
            status = "healthy" if healthy else "timeout"
            print(f"  {node_name}:{slot.port} ({slot.model}): {status}")
            if not healthy:
                all_ok = False

    return all_ok
