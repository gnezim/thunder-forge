from __future__ import annotations

import argparse
import socket
import subprocess
from pathlib import Path

from services.hosts_service import build_hosts_block, upsert_managed_hosts_block
from services.config_service import iter_nodes, load_config
from services.ssh_service import run_ssh
from services.fabricnet_service import configure_fabric_ipv4, require_macos_tahoe_26_2_plus


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _cmd_configure_fabric(args: argparse.Namespace) -> int:
    inventory = load_config(args.config)
    ssh = inventory.settings.ssh
    nodes = iter_nodes(inventory)
    ssh_port = inventory.settings.monitor.ssh_port
    # Reachability checks should not reuse the SSH connect timeout. Users often
    # tune SSH timeout very low (e.g. 0.05s) which would create false negatives.
    probe_timeout_seconds = max(1.0, float(inventory.settings.ssh.connect_timeout_seconds))

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
                    "    service_name: \"Thunderbolt Bridge\"",
                    "    ipv4_defaults:",
                    "      netmask: 255.255.255.252",
                    "      router: \"\"",
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

    print(f"Configured fabricnet on {len(attempted)}/{total} nodes: {', '.join(attempted)}")

    def _tcp_probe(host: str, port: int, timeout: float) -> bool:
        try:
            # Force IPv4 + explicit timeout; avoids surprises with dual-stack.
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            try:
                return sock.connect_ex((host, port)) == 0
            finally:
                sock.close()
        except OSError:
            return False

    print()
    print("[fabricnet] verifying reachability from hub")
    failures: list[str] = []
    for node in nodes:
        addr = fabric_addr_by_name.get(node.name)
        if not addr:
            continue
        ok = _tcp_probe(addr, ssh_port, probe_timeout_seconds)
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
        raise RuntimeError(f"Failed to update /etc/hosts locally: rc={proc.returncode}\n{err}")

    print(f"wrote {local_out} and updated local /etc/hosts")
    return 0


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

    lh = sub.add_parser(
        "local-hosts",
        help="Generate and apply managed /etc/hosts block on this machine (hub)",
    )
    lh.add_argument("--out", default="artifacts/hosts.block")
    lh.set_defaults(func=_cmd_generate_hosts)

    args = p.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
