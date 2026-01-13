from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from pathlib import Path

from services.hosts_service import build_hosts_block, upsert_managed_hosts_block
from services.config_service import iter_nodes, load_config
from services.macos_networksetup_service import (
    parse_network_service_device,
    parse_thunderbolt_devices,
)
from services.netprobe_service import tcp_probe
from services.ssh_service import run_ssh
from services.fabricnet_service import (
    configure_fabric_ipv4,
    require_macos_tahoe_26_2_plus,
)


def _resolve_fabric_ssh_host(
    *, inventory, node_name: str, fallback_hostname: str
) -> str:
    fabricnet = getattr(inventory, "fabricnet", None)
    if fabricnet is not None:
        for item in fabricnet.nodes:
            if item.name == node_name and item.address:
                return str(item.address)
    return fallback_hostname


def _node_for_fabric_ssh(*, inventory, node):
    host = _resolve_fabric_ssh_host(
        inventory=inventory,
        node_name=node.name,
        fallback_hostname=f"{node.name}-fabric",
    )
    return node.model_copy(update={"ssh_host": host})


def _run_remote_sh(*, node, ssh, shell_script: str, check: bool = True):
    return run_ssh(
        node=node,
        settings=ssh,
        remote_command=f"sh -lc {shlex.quote(shell_script)}",
        check=check,
    )


def _parse_listen_is_external(lsof_text: str, port: int) -> tuple[bool, str]:
    lines = [ln.strip() for ln in (lsof_text or "").splitlines() if ln.strip()]
    if not lines:
        return False, "not-listening"

    port_token = f":{int(port)}"
    joined = "\n".join(lines)

    # lsof examples:
    # - TCP *:11434 (LISTEN)
    # - TCP 0.0.0.0:11434 (LISTEN)
    # - TCP 127.0.0.1:11434 (LISTEN)
    if f"*{port_token}" in joined or f"0.0.0.0{port_token}" in joined:
        return True, "0.0.0.0"
    if f"127.0.0.1{port_token}" in joined:
        return False, "127.0.0.1"

    # If it's bound to a specific interface IP (e.g. 169.254.x.y), treat as external.
    if port_token in joined:
        return True, "iface-ip"

    return False, "unknown"


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _ensure_local_thunderbolt_bridge0(service_name: str) -> None:
    if sys.platform != "darwin":
        return

    try:
        order = subprocess.run(
            ["networksetup", "-listnetworkserviceorder"],
            check=True,
            text=True,
            capture_output=True,
        ).stdout
    except Exception:
        return

    device = parse_network_service_device(
        network_service_order_text=order or "", service_name=service_name
    )
    if device != "bridge0":
        return

    try:
        hp = subprocess.run(
            ["networksetup", "-listallhardwareports"],
            check=True,
            text=True,
            capture_output=True,
        ).stdout
    except Exception:
        return

    tb_devices = parse_thunderbolt_devices(hardware_ports_text=hp or "")
    if not tb_devices:
        return

    cmds = [
        "ifconfig bridge0 >/dev/null 2>&1 || ifconfig bridge0 create",
        *[f"ifconfig bridge0 addm {d} >/dev/null 2>&1 || true" for d in tb_devices],
        "ifconfig bridge0 up",
    ]
    subprocess.run(["sudo", "sh", "-lc", "; ".join(cmds)], check=False)


def _cmd_configure_fabric(args: argparse.Namespace) -> int:
    inventory = load_config(args.config)
    ssh = inventory.settings.ssh
    nodes = iter_nodes(inventory)
    ssh_port = inventory.settings.monitor.ssh_port
    # Reachability checks should not reuse the SSH connect timeout. Users often
    # tune SSH timeout very low (e.g. 0.05s) which would create false negatives.
    probe_timeout_seconds = max(
        1.0, float(inventory.settings.ssh.connect_timeout_seconds)
    )

    only: set[str] | None = None
    if getattr(args, "only", None):
        raw = str(args.only)
        only = {p.strip() for p in raw.split(",") if p.strip()}
        nodes = [n for n in nodes if n.name in only]
        if not nodes:
            print(f"[error] no nodes matched --only={raw!r}")
            return 2

    fabricnet = inventory.fabricnet
    if fabricnet is None:
        print("Missing top-level 'fabricnet' section in tf.yml.")
        print(
            "\n".join(
                [
                    "What to do:",
                    "- Add something like:",
                    "  fabricnet:",
                    '    service_name: "Thunderbolt Bridge"',
                    "    ipv4_defaults:",
                    "      netmask: 255.255.255.252",
                    '      router: ""',
                    "    nodes:",
                    "      - name: msm1",
                    "        address: 172.16.10.2",
                ]
            )
        )
        return 2

    fabric_addr_by_name = {n.name: n.address for n in fabricnet.nodes}
    total = len(nodes)

    # Quick local validation: ensure every node has a fabricnet address configured.
    for node in nodes:
        if node.name not in fabric_addr_by_name:
            name = node.name
            print(
                "\n".join(
                    [
                        f"[error] {name}: missing fabricnet.nodes entry in tf.yml (fabricnet.nodes[].name={name})",
                        "What to do:",
                        "- Edit tf.yml and add an entry under fabricnet.nodes:",
                        f"    - name: {name}",
                        "      address: 169.254.10.X",
                        "Notes:",
                        "- This runs macOS 'networksetup' over SSH, so it requires macOS on the node.",
                        "- Supported macOS: Tahoe 26.2+ only.",
                        "- This runs via 'sudo' on the node. If sudo requires a password, you'll be prompted in your terminal.",
                    ]
                )
            )
            return 2

    attempted: list[str] = []
    for idx, node in enumerate(nodes):
        if idx > 0:
            print()
        address = fabric_addr_by_name[node.name]

        if node.service_manager != "brew":
            print(
                f"[error] {node.name}: fabricnet automation only supports macOS/brew nodes for now"
            )
            return 2

        # One pass per node:
        # 1) version check
        require_macos_tahoe_26_2_plus(node=node, ssh=ssh)

        # 2) validate the macOS network service exists on the node
        # Suppress the full services list output unless there is an error.
        services_out = run_ssh(
            node=node,
            settings=ssh,
            remote_command="networksetup -listallnetworkservices",
            log_command=False,
            log_output=False,
        ).stdout
        services = [
            line.strip().lstrip("*").strip()
            for line in services_out.splitlines()
            if line.strip()
        ]
        if services and services[0].lower().startswith("an asterisk"):
            services = services[1:]

        if fabricnet.service_name not in services:
            available = "\n".join(f"- {s}" for s in services) or "(none detected)"
            print(
                "\n".join(
                    [
                        f"[error] {node.name}: {fabricnet.service_name!r} is not a recognized network service on this node",
                        "What to do:",
                        "- SSH to the node and run: networksetup -listallnetworkservices",
                        "- Pick the exact name and set it in tf.yml under fabricnet.service_name",
                        "Available services on this node:",
                        available,
                    ]
                )
            )
            return 2

        # 3) apply config
        print()
        print(f"[fabricnet] {node.name}: setting {address} ({fabricnet.service_name})")

        # Always allocate a TTY and let sudo prompt on the remote.
        # This keeps output clean (no failed `sudo -n ...` first) and is the
        # most compatible mode across sudo policies.
        try:
            configure_fabric_ipv4(
                node=node,
                ssh=ssh,
                service_name=fabricnet.service_name,
                address=address,
                ipv4_defaults=fabricnet.ipv4_defaults,
                ipv4_mode=fabricnet.ipv4_mode,
                enforce_macos_version_check=False,
                sudo_interactive=True,
            )
        except RuntimeError as e:
            msg = str(e)
            print()
            print(f"[error] {node.name}: failed to configure fabricnet")
            if "sudo: a password is required" in msg:
                print(
                    "\n".join(
                        [
                            "Cause: sudo did not accept a password (wrong password, or user not permitted).",
                            "What to do:",
                            f"- SSH to the node on mgmt: ssh {node.ssh_user}@{node.ssh_host or node.mgmt_ip}",
                            "- Run: sudo -v (ensure it succeeds)",
                            "- Then run the networksetup command manually:",
                            f"  sudo networksetup -setmanualwithdhcprouter {fabricnet.service_name!r} {address} {fabricnet.ipv4_defaults.netmask} {(fabricnet.ipv4_defaults.router or '0.0.0.0')}",
                            f"- Re-run: make setup-env (or: uv run python scripts/setup_env.py fabricnet --only {node.name})",
                        ]
                    )
                )
            else:
                print(msg)
            return 2
        attempted.append(node.name)

    print(
        f"Configured fabricnet on {len(attempted)}/{total} nodes: {', '.join(attempted)}"
    )

    print()
    print("[fabricnet] verifying reachability from hub")
    _ensure_local_thunderbolt_bridge0(fabricnet.service_name)
    failures: list[str] = []
    for node in nodes:
        addr = fabric_addr_by_name.get(node.name)
        if not addr:
            continue
        ok = tcp_probe(addr, ssh_port, probe_timeout_seconds)
        status = "ok" if ok else "unreachable"
        print(f"- {node.name}: {addr}:{ssh_port} -> {status}")
        if not ok:
            failures.append(node.name)

    if failures:
        print()
        print(
            "\n".join(
                [
                    f"[error] fabricnet reachability failed for {len(failures)} node(s): {', '.join(failures)}",
                    "What to do:",
                    "- Update the hub's /etc/hosts with <name>-fabric entries (see: make local-hosts)",
                    "- Verify cabling/topology and that the Thunderbolt service is up",
                    "- On a node, run: networksetup -getinfo <service>",
                ]
            )
        )
        return 2

    return 0


def _cmd_check_fabric_reachability(args: argparse.Namespace) -> int:
    inventory = load_config(args.config)
    nodes = iter_nodes(inventory)
    ssh_port = inventory.settings.monitor.ssh_port
    probe_timeout_seconds = max(
        1.0, float(inventory.settings.ssh.connect_timeout_seconds)
    )

    only: set[str] | None = None
    if getattr(args, "only", None):
        raw = str(args.only)
        only = {p.strip() for p in raw.split(",") if p.strip()}
        nodes = [n for n in nodes if n.name in only]
        if not nodes:
            print(f"[error] no nodes matched --only={raw!r}")
            return 2

    fabricnet = inventory.fabricnet
    if fabricnet is None:
        print("Missing top-level 'fabricnet' section in tf.yml.")
        return 2

    fabric_addr_by_name = {n.name: n.address for n in fabricnet.nodes}

    print("[fabricnet] reachability check (no changes)")
    failures: list[str] = []
    for node in nodes:
        addr = fabric_addr_by_name.get(node.name)
        if not addr:
            print(f"- {node.name}: (no fabric address in tf.yml) -> skipped")
            continue
        ok = tcp_probe(addr, ssh_port, probe_timeout_seconds)
        status = "ok" if ok else "unreachable"
        print(f"- {node.name}: {addr}:{ssh_port} -> {status}")
        if not ok:
            failures.append(node.name)

    if failures:
        print()
        print(
            f"[error] fabricnet reachability failed for {len(failures)} node(s): {', '.join(failures)}"
        )
        return 2

    return 0


def _cmd_generate_hosts(args: argparse.Namespace) -> int:
    inventory = load_config(args.config)
    artifacts = build_hosts_block(inventory)
    local_out = Path(args.out)
    _write_text(local_out, artifacts.block)

    # Apply to local /etc/hosts (hub). This usually requires sudo.
    hosts_path = Path("/etc/hosts")
    current = hosts_path.read_text(encoding="utf-8")
    updated = upsert_managed_hosts_block(
        hosts_file_text=current,
        managed_block=artifacts.block,
        settings=inventory.settings.hosts_sync,
    )

    proc = subprocess.run(
        ["sudo", "tee", "/etc/hosts"],
        input=updated,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        err = (proc.stderr or "").strip() or "(no stderr)"
        raise RuntimeError(
            f"Failed to update /etc/hosts locally: rc={proc.returncode}\n{err}"
        )

    print(f"wrote {local_out} and updated local /etc/hosts")
    return 0


def _cmd_ollama_check(args: argparse.Namespace) -> int:
    if sys.platform != "darwin":
        print("[error] ollama automation is macOS-only")
        return 2

    inventory = load_config(args.config)
    ssh = inventory.settings.ssh
    nodes = iter_nodes(inventory)
    port = int(inventory.settings.monitor.ollama_port)
    timeout_seconds = max(1.0, float(inventory.settings.ssh.connect_timeout_seconds))

    only: set[str] | None = None
    if getattr(args, "only", None):
        raw = str(args.only)
        only = {p.strip() for p in raw.split(",") if p.strip()}
        nodes = [n for n in nodes if n.name in only]
        if not nodes:
            print(f"[error] no nodes matched --only={raw!r}")
            return 2

    print("[ollama] status check (no changes)")
    failures: list[str] = []

    for node in nodes:
        if node.service_manager != "brew":
            print(
                f"- {node.name}: unsupported (service_manager={node.service_manager})"
            )
            failures.append(node.name)
            continue

        remote = _node_for_fabric_ssh(inventory=inventory, node=node)

        installed = False
        try:
            r = _run_remote_sh(
                node=remote, ssh=ssh, shell_script="command -v ollama >/dev/null 2>&1"
            )
            installed = r.returncode == 0
        except RuntimeError:
            installed = False

        lsof_out = ""
        if installed:
            lsof_out = _run_remote_sh(
                node=remote,
                ssh=ssh,
                shell_script=f"lsof -nP -iTCP:{port} -sTCP:LISTEN || true",
                check=False,
            ).stdout

        is_external, listen_mode = _parse_listen_is_external(lsof_out, port)

        http_local_ok = False
        if installed:
            http_local_ok = (
                _run_remote_sh(
                    node=remote,
                    ssh=ssh,
                    shell_script=f"curl -fsS --max-time 2 http://127.0.0.1:{port}/api/tags >/dev/null",
                    check=False,
                ).returncode
                == 0
            )

        reachable = tcp_probe(
            str(remote.ssh_host or remote.mgmt_ip), port, timeout_seconds
        )

        status_bits = []
        status_bits.append("installed" if installed else "missing")
        status_bits.append(f"listen={listen_mode}")
        status_bits.append("http=ok" if http_local_ok else "http=fail")
        status_bits.append("reach=ok" if reachable else "reach=fail")
        status = ", ".join(status_bits)

        ok = installed and is_external and http_local_ok and reachable
        print(f"- {node.name}: {status} -> {'ok' if ok else 'needs-fix'}")
        if not ok:
            failures.append(node.name)

    if failures:
        print()
        print(
            f"[error] ollama not ready on {len(failures)} node(s): {', '.join(failures)}"
        )
        return 2
    return 0


def _cmd_ollama_ensure(args: argparse.Namespace) -> int:
    if sys.platform != "darwin":
        print("[error] ollama automation is macOS-only")
        return 2

    inventory = load_config(args.config)
    ssh = inventory.settings.ssh
    nodes = iter_nodes(inventory)
    port = int(inventory.settings.monitor.ollama_port)

    only: set[str] | None = None
    if getattr(args, "only", None):
        raw = str(args.only)
        only = {p.strip() for p in raw.split(",") if p.strip()}
        nodes = [n for n in nodes if n.name in only]
        if not nodes:
            print(f"[error] no nodes matched --only={raw!r}")
            return 2

    print("[ollama] ensure installed/configured/running")
    failures: list[str] = []
    for idx, node in enumerate(nodes):
        if idx > 0:
            print()

        if node.service_manager != "brew":
            print(
                f"[error] {node.name}: unsupported (service_manager={node.service_manager})"
            )
            failures.append(node.name)
            continue

        remote = _node_for_fabric_ssh(inventory=inventory, node=node)

        print(f"[ollama] {node.name}: ensuring brew+ollama")
        # On macOS/Homebrew, the most reliable way to configure external bind for a
        # brew-managed LaunchAgent is to persist EnvironmentVariables in the
        # ~/Library/LaunchAgents/homebrew.mxcl.ollama.plist and restart the job in
        # the gui/<uid> domain.
        script = "\n".join(
            [
                "set -e",
                # Find brew even when PATH is minimal (common over non-interactive SSH).
                "BREW=",
                "if command -v brew >/dev/null 2>&1; then BREW=brew; fi",
                "if [ -z \"$BREW\" ] && [ -x /opt/homebrew/bin/brew ]; then BREW=/opt/homebrew/bin/brew; fi",
                "if [ -z \"$BREW\" ] && [ -x /usr/local/bin/brew ]; then BREW=/usr/local/bin/brew; fi",
                "[ -n \"$BREW\" ] || { echo 'brew not found'; exit 2; }",
                # Install if missing.
                "command -v ollama >/dev/null 2>&1 || $BREW install ollama",
                # Ensure the LaunchAgent plist exists by starting via brew services.
                "$BREW services start ollama >/dev/null 2>&1 || true",
                "PL=\"$HOME/Library/LaunchAgents/homebrew.mxcl.ollama.plist\"",
                "[ -f \"$PL\" ] || { echo \"missing launchagent plist: $PL\"; exit 2; }",
                # Persist env vars.
                f"/usr/libexec/PlistBuddy -c \"Add :EnvironmentVariables:OLLAMA_HOST string 0.0.0.0:{port}\" \"$PL\" 2>/dev/null || \\",
                f"  /usr/libexec/PlistBuddy -c \"Set :EnvironmentVariables:OLLAMA_HOST 0.0.0.0:{port}\" \"$PL\"",
                "/usr/libexec/PlistBuddy -c \"Add :EnvironmentVariables:OLLAMA_ORIGINS string *\" \"$PL\" 2>/dev/null || \\",
                "  /usr/libexec/PlistBuddy -c \"Set :EnvironmentVariables:OLLAMA_ORIGINS *\" \"$PL\"",
                # Restart via launchctl so the job picks up the updated plist env.
                "USER_UID=$(id -u)",
                "launchctl bootout gui/$USER_UID/homebrew.mxcl.ollama >/dev/null 2>&1 || true",
                "launchctl bootstrap gui/$USER_UID \"$PL\" >/dev/null 2>&1",
                "launchctl enable gui/$USER_UID/homebrew.mxcl.ollama >/dev/null 2>&1 || true",
                "launchctl kickstart -k gui/$USER_UID/homebrew.mxcl.ollama >/dev/null 2>&1 || true",
                "sleep 1",
                # Local health check and external bind verification.
                f"curl -fsS --max-time 3 http://127.0.0.1:{port}/api/tags >/dev/null",
                f"lsof -nP -iTCP:{port} -sTCP:LISTEN | grep -E 'TCP (\\*|0\\.0\\.0\\.0):{port} .*LISTEN' >/dev/null",
            ]
        )

        try:
            _run_remote_sh(node=remote, ssh=ssh, shell_script=script, check=True)
        except RuntimeError as e:
            print(f"[error] {node.name}: ensure failed")
            print(str(e))
            failures.append(node.name)
            continue

    if failures:
        print()
        print(
            f"[error] ollama ensure failed on {len(failures)} node(s): {', '.join(failures)}"
        )
        return 2

    # Final verification in one pass.
    print()
    args_check = argparse.Namespace(
        config=args.config, only=getattr(args, "only", None)
    )
    return _cmd_ollama_check(args_check)


def main() -> int:
    p = argparse.ArgumentParser(prog="setup-env")
    p.add_argument(
        "--config",
        default=None,
        help="Config path (default: TF_CONFIG_PATH or tf.yml)",
    )

    sub = p.add_subparsers(dest="cmd", required=True)

    fn = sub.add_parser(
        "fabricnet", help="Configure fabric network IPv4 on nodes (macOS networksetup)"
    )
    fn.add_argument(
        "--only",
        default=None,
        help="Configure only these node names (comma-separated), e.g. --only msm2",
    )
    fn.set_defaults(func=_cmd_configure_fabric)

    fc = sub.add_parser(
        "fabricnet-check",
        help="Check fabric reachability from this machine (no network changes)",
    )
    fc.add_argument(
        "--only",
        default=None,
        help="Check only these node names (comma-separated), e.g. --only msm2",
    )
    fc.set_defaults(func=_cmd_check_fabric_reachability)

    lh = sub.add_parser(
        "local-hosts",
        help="Generate and apply managed /etc/hosts block on this machine (hub)",
    )
    lh.add_argument("--out", default="artifacts/hosts.block")
    lh.set_defaults(func=_cmd_generate_hosts)

    oc = sub.add_parser(
        "ollama-check",
        help="Check Ollama installation/config/status on nodes over fabric SSH (no changes)",
    )
    oc.add_argument(
        "--only",
        default=None,
        help="Check only these node names (comma-separated), e.g. --only msm2",
    )
    oc.set_defaults(func=_cmd_ollama_check)

    oe = sub.add_parser(
        "ollama-ensure",
        help="Install/configure/start Ollama on nodes over fabric SSH (macOS/brew)",
    )
    oe.add_argument(
        "--only",
        default=None,
        help="Ensure only these node names (comma-separated), e.g. --only msm2",
    )
    oe.set_defaults(func=_cmd_ollama_ensure)

    args = p.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
