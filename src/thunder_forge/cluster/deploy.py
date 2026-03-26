"""Deployment: plist generation, SSH deploy, launchctl management."""

from __future__ import annotations

import atexit
import os
import threading
import time as time_mod
import xml.etree.ElementTree as ET
from pathlib import Path

from thunder_forge.cluster.config import Assignment, ClusterConfig, Model, Node, ServerArgs
from thunder_forge.cluster.ssh import scp_content, ssh_run

LOCK_FILE = "/tmp/thunder-forge-deploy.lock"
HEARTBEAT_INTERVAL = 30


def parse_lock_file(content: str | None) -> dict | None:
    """Parse lock file content. Returns dict with pid/heartbeat or None."""
    if not content or not content.strip():
        return None
    result = {}
    for line in content.strip().split("\n"):
        if ":" in line:
            key, val = line.split(":", 1)
            key = key.strip().lower()
            try:
                result[key] = int(val.strip())
            except ValueError:
                result[key] = val.strip()
    if "pid" not in result:
        return None
    return result


def format_lock_file(pid: int) -> str:
    """Format lock file content with PID and current timestamp."""
    return f"PID:{pid}\nHEARTBEAT:{int(time_mod.time())}"


def _acquire_lock() -> bool:
    """Acquire the gateway deploy lock. Returns True if acquired."""
    lock_path = Path(LOCK_FILE)
    if lock_path.exists():
        content = lock_path.read_text()
        lock = parse_lock_file(content)
        if lock:
            pid = lock.get("pid")
            if pid:
                try:
                    os.kill(pid, 0)
                except OSError:
                    pass  # PID dead, stale lock
                else:
                    heartbeat = lock.get("heartbeat", 0)
                    if isinstance(heartbeat, int) and time_mod.time() - heartbeat < 300:
                        return False
    lock_path.write_text(format_lock_file(os.getpid()))
    return True


def _release_lock() -> None:
    """Release the gateway deploy lock."""
    lock_path = Path(LOCK_FILE)
    if lock_path.exists():
        content = lock_path.read_text()
        lock = parse_lock_file(content)
        if lock and lock.get("pid") == os.getpid():
            lock_path.unlink(missing_ok=True)


def _heartbeat_loop(stop_event: threading.Event) -> None:
    """Update lock file heartbeat periodically."""
    while not stop_event.is_set():
        lock_path = Path(LOCK_FILE)
        if lock_path.exists():
            lock_path.write_text(format_lock_file(os.getpid()))
        stop_event.wait(HEARTBEAT_INTERVAL)


def _require_resolved(node: Node, node_name: str) -> None:
    """Raise if resolved fields are missing (pre-flight not run)."""
    if node.home_dir is None:
        msg = f"{node_name}: node.home_dir is None — run pre-flight first (remove --skip-preflight)"
        raise ValueError(msg)


def generate_plist(
    model: Model,
    slot: Assignment,
    node: Node,
) -> str:
    _require_resolved(node, f"port-{slot.port}")
    home = node.home_dir
    label = f"com.mlx-lm-{slot.port}"
    server_path = f"{home}/.local/bin/mlx_lm.server"

    program_args = [
        server_path,
        "--model",
        model.source.repo,
        "--port",
        str(slot.port),
        "--host",
        "0.0.0.0",
    ]

    if model.enable_thinking is not None:
        import json

        program_args.extend(["--chat-template-args", json.dumps({"enable_thinking": model.enable_thinking})])

    if model.server_args:
        sa = model.server_args
        for flag, value in [
            ("--decode-concurrency", sa.decode_concurrency),
            ("--prompt-concurrency", sa.prompt_concurrency),
            ("--prefill-step-size", sa.prefill_step_size),
            ("--prompt-cache-size", sa.prompt_cache_size),
            ("--prompt-cache-bytes", sa.prompt_cache_bytes),
            ("--max-tokens", sa.max_tokens),
            ("--temp", sa.temp),
            ("--top-p", sa.top_p),
            ("--top-k", sa.top_k),
            ("--min-p", sa.min_p),
            ("--draft-model", sa.draft_model),
            ("--num-draft-tokens", sa.num_draft_tokens),
        ]:
            if value is not None:
                program_args.extend([flag, str(value)])

    if model.extra_args:
        program_args.extend(model.extra_args)

    path_parts = [f"{home}/.local/bin", "/usr/bin", "/bin"]
    if node.homebrew_prefix:
        path_parts.insert(1, f"{node.homebrew_prefix}/bin")

    env_vars = {
        "PATH": ":".join(path_parts),
        "HOME": home,
        "HF_HUB_OFFLINE": "1",
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

    add_key_value(d, "StandardOutPath", make_string(f"{home}/logs/mlx-lm-{slot.port}.log"))
    add_key_value(d, "StandardErrorPath", make_string(f"{home}/logs/mlx-lm-{slot.port}.err"))

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
{home}/logs/mlx-lm-*.log              {user}:staff     644  7     102400   *     CNJ
{home}/logs/mlx-lm-*.err              {user}:staff     644  7     102400   *     CNJ
"""


def install_node_tools(node: Node) -> None:
    """Install mlx-lm (with socks proxy support) and remove legacy vllm-mlx."""
    ssh_run(node.user, node.ip, "uv tool uninstall vllm-mlx 2>/dev/null || true", timeout=30, shell=node.shell)
    result = ssh_run(
        node.user,
        node.ip,
        "uv tool install --force mlx-lm --with 'httpx[socks]'",
        timeout=120,
        shell=node.shell,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        print(f"  Warning: mlx-lm install failed: {stderr or 'unknown error'} (continuing)")
    else:
        print("  mlx-lm installed")


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
            print(f"    [upload] com.mlx-lm-{slot.port}.plist ({slot.model}, port {slot.port})")
        print(f"    [restart] {len(slots)} launchd services")
        print(f"    [health] poll /v1/models on ports {', '.join(str(s.port) for s in slots)}")
        return errors

    uid_result = ssh_run(node.user, node.ip, "mkdir -p ~/logs ~/Library/LaunchAgents && id -u", shell=node.shell)
    if uid_result.returncode != 0:
        return [f"{node_name}: failed to get UID — {(uid_result.stderr or '').strip()}"]
    uid = uid_result.stdout.strip()

    # Clean up legacy vllm-mlx plists
    legacy_ls = ssh_run(
        node.user,
        node.ip,
        "ls ~/Library/LaunchAgents/com.vllm-mlx-*.plist 2>/dev/null || true",
        shell=node.shell,
    )
    if legacy_ls.stdout.strip():
        print("  Removing legacy vllm-mlx services...")
        for line in legacy_ls.stdout.strip().splitlines():
            filename = line.strip().split("/")[-1]
            try:
                port = int(filename.replace("com.vllm-mlx-", "").replace(".plist", ""))
                stale_label = f"com.vllm-mlx-{port}"
                bootout = f"launchctl bootout gui/{uid}/{stale_label} 2>/dev/null"
                cmd = f"{bootout}; rm ~/Library/LaunchAgents/{stale_label}.plist"
                ssh_run(node.user, node.ip, cmd, shell=node.shell)
            except ValueError:
                continue
        ssh_run(node.user, node.ip, "sudo rm -f /etc/newsyslog.d/vllm-mlx.conf", shell=node.shell)

    install_node_tools(node)

    deployed_ports: set[int] = set()

    for slot in slots:
        model = config.models[slot.model]
        plist_xml = generate_plist(model, slot, node)
        plist_name = f"com.mlx-lm-{slot.port}.plist"
        remote_plist = f"~/Library/LaunchAgents/{plist_name}"

        result = scp_content(node.user, node.ip, plist_xml, remote_plist, shell=node.shell)
        if result.returncode != 0:
            errors.append(f"{node_name}: failed to upload {plist_name} — {(result.stderr or '').strip()}")
            continue

        label = f"com.mlx-lm-{slot.port}"
        domain = f"gui/{uid}"
        plist_path = f"~/Library/LaunchAgents/{plist_name}"

        # Try kickstart first (works if service is already registered — just restarts it)
        result = ssh_run(node.user, node.ip, f"launchctl kickstart -kp {domain}/{label}", shell=node.shell)
        if result.returncode != 0:
            # Service not registered yet — bootout (cleanup) + sleep + bootstrap (register fresh)
            cmd = f"launchctl bootout {domain}/{label} 2>/dev/null; sleep 2; launchctl bootstrap {domain} {plist_path}"
            result = ssh_run(node.user, node.ip, cmd, shell=node.shell)
            if result.returncode != 0:
                err = (result.stderr or "").strip() + " " + (result.stdout or "").strip()
                errors.append(
                    f"{node_name}: failed to start service on port {slot.port}\n"
                    f"  error: {err.strip()}\n"
                    f"  → Try: thunder-forge deploy --node {node_name}"
                )

        deployed_ports.add(slot.port)

    newsyslog = NEWSYSLOG_CONF.format(user=node.user, home=node.home_dir)
    scp_content(node.user, node.ip, newsyslog, "/tmp/mlx-lm-newsyslog.conf", shell=node.shell)
    ssh_run(node.user, node.ip, "sudo mv /tmp/mlx-lm-newsyslog.conf /etc/newsyslog.d/mlx-lm.conf", shell=node.shell)

    # Note: ls 2>/dev/null || true is intentional — no plists present is not an error.
    ls_result = ssh_run(
        node.user, node.ip, "ls ~/Library/LaunchAgents/com.mlx-lm-*.plist 2>/dev/null || true", shell=node.shell
    )
    if ls_result.stdout.strip():
        for line in ls_result.stdout.strip().splitlines():
            filename = line.strip().split("/")[-1]
            try:
                port = int(filename.replace("com.mlx-lm-", "").replace(".plist", ""))
                if port not in deployed_ports:
                    print(f"  Removing stale plist for port {port}")
                    stale = f"com.mlx-lm-{port}"
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
        f"cd {docker_dir} && docker compose up -d litellm",
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

    # Acquire deploy lock
    if not _acquire_lock():
        print("Error: another deploy is already running (lock file exists and process is alive)")
        return False

    stop_event = threading.Event()
    heartbeat_thread = threading.Thread(target=_heartbeat_loop, args=(stop_event,), daemon=True)
    heartbeat_thread.start()
    atexit.register(_release_lock)

    try:
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
    finally:
        stop_event.set()
        _release_lock()
