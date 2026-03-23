"""Deployment: plist generation, SSH deploy, launchctl management."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from thunder_forge.cluster.config import Assignment, ClusterConfig, Model, Node
from thunder_forge.cluster.ssh import scp_content, ssh_run


def _require_resolved(node: Node, node_name: str) -> None:
    """Raise if resolved fields are missing (pre-flight not run)."""
    if node.home_dir is None:
        msg = f"{node_name}: node.home_dir is None — run pre-flight first (remove --skip-preflight)"
        raise ValueError(msg)


def generate_plist(
    model: Model,
    slot: Assignment,
    node: Node,
    *,
    embedding_model: Model | None = None,
) -> str:
    _require_resolved(node, f"port-{slot.port}")
    home = node.home_dir
    label = f"com.vllm-mlx-{slot.port}"
    vllm_path = f"{home}/.local/bin/vllm-mlx"

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

    path_parts = [f"{home}/.local/bin", "/usr/bin", "/bin"]
    if node.homebrew_prefix:
        path_parts.insert(1, f"{node.homebrew_prefix}/bin")

    env_vars = {
        "PATH": ":".join(path_parts),
        "HOME": home,
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

    add_key_value(d, "StandardOutPath", make_string(f"{home}/logs/vllm-mlx-{slot.port}.log"))
    add_key_value(d, "StandardErrorPath", make_string(f"{home}/logs/vllm-mlx-{slot.port}.err"))

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
{home}/logs/vllm-mlx-*.log            {user}:staff     644  7     102400   *     CNJ
{home}/logs/vllm-mlx-*.err            {user}:staff     644  7     102400   *     CNJ
"""


def upgrade_node_tools(node: Node) -> None:
    """Best-effort upgrade of uv-managed tools on a node."""
    result = ssh_run(node.user, node.ip, "uv tool upgrade --all", timeout=120, shell=node.shell)
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        if "nothing to upgrade" in stderr.lower() or "no tools installed" in stderr.lower():
            print("  No uv tools to upgrade (OK)")
        else:
            print(f"  Warning: uv tool upgrade failed on {node.ip}: {stderr or 'unknown error'} (continuing)")
    else:
        print("  Tools upgraded")


def deploy_node(
    node_name: str,
    config: ClusterConfig,
    *,
    dry_run: bool = False,
) -> list[str]:
    errors: list[str] = []
    node = config.nodes[node_name]
    slots = config.assignments.get(node_name, [])

    if not slots:
        return [f"{node_name}: no assignments found"]

    _require_resolved(node, node_name)

    if dry_run:
        for slot in slots:
            print(f"    [upload] com.vllm-mlx-{slot.port}.plist ({slot.model}, port {slot.port})")
        print(f"    [restart] {len(slots)} launchd services")
        print(f"    [health] poll /v1/models on ports {', '.join(str(s.port) for s in slots)}")
        return errors

    uid_result = ssh_run(node.user, node.ip, "mkdir -p ~/logs ~/Library/LaunchAgents && id -u", shell=node.shell)
    if uid_result.returncode != 0:
        return [f"{node_name}: failed to get UID — {(uid_result.stderr or '').strip()}"]
    uid = uid_result.stdout.strip()

    upgrade_node_tools(node)

    deployed_ports: set[int] = set()

    for slot in slots:
        model = config.models[slot.model]
        embedding_model = config.models.get("embedding") if slot.embedding else None

        plist_xml = generate_plist(model, slot, node, embedding_model=embedding_model)
        plist_name = f"com.vllm-mlx-{slot.port}.plist"
        remote_plist = f"~/Library/LaunchAgents/{plist_name}"

        result = scp_content(node.user, node.ip, plist_xml, remote_plist, shell=node.shell)
        if result.returncode != 0:
            errors.append(f"{node_name}: failed to upload {plist_name} — {(result.stderr or '').strip()}")
            continue

        label = f"com.vllm-mlx-{slot.port}"
        domain = f"gui/{uid}"

        # Note: 2>/dev/null on bootout is intentional — bootout errors when service isn't
        # loaded (expected on first deploy). Justified deviation from spec Section 4.1.
        plist_path = f"~/Library/LaunchAgents/{plist_name}"
        cmd = f"launchctl bootout {domain}/{label} 2>/dev/null; launchctl bootstrap {domain} {plist_path}"
        result = ssh_run(node.user, node.ip, cmd, shell=node.shell)
        if result.returncode != 0:
            result = ssh_run(node.user, node.ip, f"launchctl kickstart -kp {domain}/{label}", shell=node.shell)
            if result.returncode != 0:
                errors.append(
                    f"{node_name}: failed to start service on port {slot.port}\n"
                    f"  stderr: {(result.stderr or '').strip()}\n"
                    f"  → Try: thunder-forge deploy --node {node_name}"
                )

        deployed_ports.add(slot.port)

    newsyslog = NEWSYSLOG_CONF.format(user=node.user, home=node.home_dir)
    scp_content(node.user, node.ip, newsyslog, "/tmp/vllm-mlx-newsyslog.conf", shell=node.shell)
    ssh_run(node.user, node.ip, "sudo mv /tmp/vllm-mlx-newsyslog.conf /etc/newsyslog.d/vllm-mlx.conf", shell=node.shell)

    # Note: ls 2>/dev/null || true is intentional — no plists present is not an error.
    ls_result = ssh_run(
        node.user, node.ip, "ls ~/Library/LaunchAgents/com.vllm-mlx-*.plist 2>/dev/null || true", shell=node.shell
    )
    if ls_result.stdout.strip():
        for line in ls_result.stdout.strip().splitlines():
            filename = line.strip().split("/")[-1]
            try:
                port = int(filename.replace("com.vllm-mlx-", "").replace(".plist", ""))
                if port not in deployed_ports:
                    print(f"  Removing stale plist for port {port}")
                    stale = f"com.vllm-mlx-{port}"
                    # Note: 2>/dev/null on bootout is intentional — stale service may not be loaded.
                    cmd = f"launchctl bootout gui/{uid}/{stale} 2>/dev/null; rm ~/Library/LaunchAgents/{stale}.plist"
                    ssh_run(node.user, node.ip, cmd, shell=node.shell)
            except ValueError:
                continue

    return errors


def restart_litellm(config: ClusterConfig) -> bool:
    from thunder_forge.cluster.config import find_repo_root

    gw = config.gateway
    docker_dir = find_repo_root() / "docker"
    result = ssh_run(
        gw.user,
        gw.ip,
        f"cd {docker_dir} && docker compose restart litellm",
        timeout=60,
        shell=gw.shell,
    )
    return result.returncode == 0


def health_poll(ip: str, port: int, *, timeout_secs: int = 180, interval: int = 5) -> bool:
    import time
    import urllib.error
    import urllib.request

    url = f"http://{ip}:{port}/v1/models"
    handler = urllib.request.ProxyHandler({})
    opener = urllib.request.build_opener(handler)
    deadline = time.monotonic() + timeout_secs

    while time.monotonic() < deadline:
        try:
            with opener.open(url, timeout=5):
                return True
        except (urllib.error.URLError, OSError, TimeoutError):
            time.sleep(interval)

    return False


def run_deploy(config: ClusterConfig, *, target_node: str | None = None, dry_run: bool = False) -> bool:
    if target_node:
        if target_node not in config.assignments:
            print(f"Node '{target_node}' not found in assignments")
            return False
        deploy_nodes = [target_node]
    else:
        deploy_nodes = [n for n in config.assignments if config.nodes[n].role == "node"]

    if dry_run:
        print("\nDeployment plan:\n")
        for node_name in deploy_nodes:
            node = config.nodes[node_name]
            slots = config.assignments[node_name]
            print(f"  {node_name} ({node.ip}) — {len(slots)} services:")
            deploy_node(node_name, config, dry_run=True)
        try:
            gw = config.gateway
            print(f"\n  {config.gateway_name} ({gw.ip}) — gateway:")
            print("    [restart] LiteLLM proxy (docker compose restart litellm)")
        except ValueError:
            pass
        print("\nRun without --dry-run to execute.")
        return True

    # Real deploy — continue on partial failure
    succeeded: list[str] = []
    failed: dict[str, str] = {}

    for node_name in deploy_nodes:
        print(f"\nDeploying to {node_name}...")
        errors = deploy_node(node_name, config)
        if errors:
            failed[node_name] = "; ".join(errors)
            for err in errors:
                print(f"  ✗ {err}")
        else:
            succeeded.append(node_name)
            print("  ✓ Plists deployed")

    # LiteLLM restart — only if at least one node succeeded
    litellm_ok = False
    if succeeded:
        print("\nRestarting LiteLLM...")
        if restart_litellm(config):
            print("  ✓ LiteLLM restarted")
            litellm_ok = True
        else:
            if target_node:
                print("  Warning: LiteLLM restart failed (non-fatal with --node)")
                litellm_ok = True  # non-fatal
            else:
                print("  ✗ LiteLLM restart failed")
    elif not target_node:
        print("\nSkipping LiteLLM restart — no nodes deployed successfully")

    # Health poll — only successfully deployed nodes
    if succeeded:
        print("\nWaiting for services to become healthy...")
        for node_name in succeeded:
            node = config.nodes[node_name]
            for slot in config.assignments[node_name]:
                healthy = health_poll(node.ip, slot.port)
                status = "✓ healthy" if healthy else "✗ timeout"
                print(f"  {status} — {node_name}:{slot.port} ({slot.model})")
                if not healthy:
                    failed[node_name] = failed.get(node_name, "") + f" port {slot.port} unhealthy"

    # Summary
    total = len(deploy_nodes)
    ok_count = len(succeeded) - len([n for n in succeeded if n in failed])
    print(f"\nDeploy complete: {ok_count}/{total} nodes succeeded")
    for node_name in deploy_nodes:
        if node_name in failed:
            print(f"  ✗ {node_name} — {failed[node_name]}")
        else:
            slots = config.assignments[node_name]
            print(f"  ✓ {node_name} — {len(slots)} services running")

    if failed:
        print("\nFix failed nodes and re-run: thunder-forge deploy --node <name>")

    return len(failed) == 0 and litellm_ok
