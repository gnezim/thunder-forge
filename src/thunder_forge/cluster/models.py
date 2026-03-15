"""Model download and sync to inference nodes."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from thunder_forge.cluster.config import ClusterConfig
from thunder_forge.cluster.ssh import _is_local, run_local, ssh_run

# HF cache dir, respects HF_HOME env var (same as huggingface_hub library)
HF_CACHE = os.environ.get("HF_HOME", "~/.cache/huggingface") + "/hub"


@dataclass
class ModelTask:
    model_name: str
    source_type: str
    repo: str = ""
    revision: str = "main"
    quantize: str = ""
    path: str = ""
    package: str = ""
    weight_repo: str = ""
    target_nodes: list[str] = field(default_factory=list)


def resolve_model_tasks(
    config: ClusterConfig,
    *,
    target_node: str | None = None,
) -> list[ModelTask]:
    task_map: dict[str, ModelTask] = {}
    for node_name, slots in config.assignments.items():
        if target_node and node_name != target_node:
            continue
        for slot in slots:
            model = config.models[slot.model]
            src = model.source
            if slot.model not in task_map:
                task_map[slot.model] = ModelTask(
                    model_name=slot.model,
                    source_type=src.type,
                    repo=src.repo,
                    revision=src.revision,
                    quantize=src.quantize,
                    path=src.path,
                    package=src.package,
                    weight_repo=src.weight_repo,
                )
            task_map[slot.model].target_nodes.append(node_name)
    return list(task_map.values())


def _check_hf_cached(user: str, ip: str, repo: str) -> bool:
    hf_path = repo.replace("/", "--")
    result = ssh_run(user, ip, f"test -d {HF_CACHE}/models--{hf_path}/snapshots")
    return result.returncode == 0


def ensure_huggingface(task: ModelTask, config: ClusterConfig, *, dry_run: bool = False) -> list[str]:
    errors: list[str] = []
    rock = config.rock
    if dry_run:
        print(f"  [dry-run] Would download {task.repo} (rev: {task.revision}) on rock")
        for node_name in task.target_nodes:
            print(f"  [dry-run] Would rsync to {node_name}")
        return errors
    print(f"  Downloading {task.repo} on rock...")
    hf_env = f"HF_HOME={os.environ.get('HF_HOME', '~/.cache/huggingface')}"
    dl_cmd = f"{hf_env} hf download {task.repo} --revision {task.revision}"
    result = ssh_run(rock.user, rock.ip, dl_cmd, timeout=600, stream=True)
    if result.returncode != 0:
        errors.append(f"Download failed for {task.repo}: {result.stderr.strip()}")
        return errors
    hf_cache_path = task.repo.replace("/", "--")
    if _is_local(rock.ip):
        src_path = f"{HF_CACHE}/models--{hf_cache_path}/"
    else:
        src_path = f"{rock.user}@{rock.ip}:{HF_CACHE}/models--{hf_cache_path}/"
    for node_name in task.target_nodes:
        node = config.nodes[node_name]
        if _check_hf_cached(node.user, node.ip, task.repo):
            print(f"  {task.model_name} already cached on {node_name}")
            continue
        dest_path = f"{node.user}@{node.ip}:{HF_CACHE}/models--{hf_cache_path}/"
        print(f"  Syncing {task.model_name} to {node_name}...")
        rsync_result = run_local(
            ["rsync", "-az", "--progress", "-e", "ssh -o StrictHostKeyChecking=no",
             src_path, dest_path],
            timeout=600,
        )
        if rsync_result.returncode != 0:
            errors.append(f"Rsync to {node_name} failed: {rsync_result.stderr.strip()}")
    return errors


def ensure_convert(task: ModelTask, config: ClusterConfig, *, dry_run: bool = False) -> list[str]:
    errors: list[str] = []
    rock = config.rock
    if dry_run:
        print(f"  [dry-run] Would download {task.repo}, convert (q={task.quantize}), sync to {task.target_nodes}")
        return errors
    print(f"  Downloading source {task.repo} on rock...")
    hf_env = f"HF_HOME={os.environ.get('HF_HOME', '~/.cache/huggingface')}"
    dl_result = ssh_run(rock.user, rock.ip, f"{hf_env} hf download {task.repo}", timeout=600, stream=True)
    if dl_result.returncode != 0:
        errors.append(f"Download failed for {task.repo}: {dl_result.stderr.strip()}")
        return errors
    convert_node_name = task.target_nodes[0]
    convert_node = config.nodes[convert_node_name]
    output_dir = f"~/.cache/mlx-models/{task.model_name}/"
    check = ssh_run(convert_node.user, convert_node.ip, f"test -d {output_dir}")
    if check.returncode == 0:
        print(f"  Already converted on {convert_node_name}")
    else:
        print(f"  Converting on {convert_node_name} (quantize={task.quantize})...")
        convert_cmd = (
            f"python -m mlx_lm.convert --hf-path {task.repo} "
            f"-q --q-bits {task.quantize} --upload-repo '' --mlx-path {output_dir}"
        )
        conv_result = ssh_run(convert_node.user, convert_node.ip, convert_cmd, timeout=1800)
        if conv_result.returncode != 0:
            errors.append(f"Conversion failed on {convert_node_name}: {conv_result.stderr.strip()}")
            return errors
    for node_name in task.target_nodes[1:]:
        node = config.nodes[node_name]
        check = ssh_run(node.user, node.ip, f"test -d {output_dir}")
        if check.returncode == 0:
            print(f"  Already on {node_name}")
            continue
        print(f"  Syncing converted model to {node_name}...")
        src = f"{convert_node.user}@{convert_node.ip}:{output_dir}"
        dest = f"{node.user}@{node.ip}:{output_dir}"
        rsync_result = run_local(
            ["rsync", "-az", "-e", "ssh -o StrictHostKeyChecking=no", src, dest],
            timeout=600,
        )
        if rsync_result.returncode != 0:
            errors.append(f"Rsync to {node_name} failed: {rsync_result.stderr.strip()}")
    return errors


def ensure_local(task: ModelTask, config: ClusterConfig, *, dry_run: bool = False) -> list[str]:
    errors: list[str] = []
    for node_name in task.target_nodes:
        node = config.nodes[node_name]
        if dry_run:
            print(f"  [dry-run] Would verify {task.path} exists on {node_name}")
            continue
        result = ssh_run(node.user, node.ip, f"test -d {task.path}")
        if result.returncode != 0:
            errors.append(f"{node_name}: path {task.path} does not exist")
    return errors


def ensure_pip(task: ModelTask, config: ClusterConfig, *, dry_run: bool = False) -> list[str]:
    errors: list[str] = []
    rock = config.rock
    # Download weights on rock first, then rsync to nodes (same pattern as HF models)
    if task.weight_repo:
        if dry_run:
            print(f"  [dry-run] Would download weights {task.weight_repo} on rock")
        else:
            print(f"  Downloading weights {task.weight_repo} on rock...")
            hf_env = f"HF_HOME={os.environ.get('HF_HOME', '~/.cache/huggingface')}"
            dl_cmd = f"{hf_env} hf download {task.weight_repo}"
            dl_result = ssh_run(rock.user, rock.ip, dl_cmd, timeout=600, stream=True)
            if dl_result.returncode != 0:
                errors.append(f"Weight download failed for {task.weight_repo}: {dl_result.stderr.strip()}")
                return errors
    for node_name in task.target_nodes:
        node = config.nodes[node_name]
        if dry_run:
            print(f"  [dry-run] Would install {task.package} on {node_name}")
            if task.weight_repo:
                print(f"  [dry-run] Would rsync weights {task.weight_repo} to {node_name}")
            continue
        check = ssh_run(node.user, node.ip, f"uv tool list 2>/dev/null | grep -q {task.package}")
        if check.returncode == 0:
            print(f"  {task.package} already installed on {node_name}")
        else:
            print(f"  Installing {task.package} on {node_name}...")
            result = ssh_run(node.user, node.ip, f"uv tool install {task.package}", timeout=120)
            if result.returncode != 0:
                errors.append(f"{node_name}: install of {task.package} failed: {result.stderr.strip()}")
        if task.weight_repo:
            hf_cache_path = task.weight_repo.replace("/", "--")
            cache_dir = f"{HF_CACHE}/models--{hf_cache_path}/snapshots"
            check_cached = ssh_run(node.user, node.ip, f"test -d {cache_dir}")
            if check_cached.returncode == 0:
                print(f"  Weights {task.weight_repo} already cached on {node_name}")
                continue
            if _is_local(rock.ip):
                src_path = f"{HF_CACHE}/models--{hf_cache_path}/"
            else:
                src_path = f"{rock.user}@{rock.ip}:{HF_CACHE}/models--{hf_cache_path}/"
            dest_path = f"{node.user}@{node.ip}:{HF_CACHE}/models--{hf_cache_path}/"
            print(f"  Syncing weights {task.weight_repo} to {node_name}...")
            rsync_result = run_local(
                ["rsync", "-az", "--progress", "-e", "ssh -o StrictHostKeyChecking=no",
                 src_path, dest_path],
                timeout=600,
            )
            if rsync_result.returncode != 0:
                errors.append(f"{node_name}: weight rsync failed: {rsync_result.stderr.strip()}")
    return errors


def run_ensure_models(
    config: ClusterConfig,
    *,
    dry_run: bool = False,
    target_node: str | None = None,
) -> bool:
    tasks = resolve_model_tasks(config, target_node=target_node)
    all_ok = True
    for task in tasks:
        print(f"\n{task.model_name} ({task.source_type})")
        handler = {
            "huggingface": ensure_huggingface,
            "convert": ensure_convert,
            "local": ensure_local,
            "pip": ensure_pip,
        }.get(task.source_type)
        if handler is None:
            print(f"  Source type '{task.source_type}' not yet implemented (skipping)")
            continue
        errors = handler(task, config, dry_run=dry_run)
        if errors:
            all_ok = False
            for err in errors:
                print(f"  {err}")
        elif not dry_run:
            print("  Done")
    return all_ok
