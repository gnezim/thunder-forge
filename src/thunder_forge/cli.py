"""Thunder Forge CLI — cluster management commands."""

import typer

app = typer.Typer(
    name="thunder-forge",
    help="CLI for managing a local MLX inference cluster.",
    no_args_is_help=True,
)


def _load_config() -> tuple:
    """Load cluster config from node-assignments.yaml. Returns (ClusterConfig, repo_root Path)."""
    from thunder_forge.cluster.config import find_repo_root, load_cluster_config

    repo_root = find_repo_root()
    assignments_path = repo_root / "configs" / "node-assignments.yaml"
    if not assignments_path.exists():
        typer.echo(f"Error: {assignments_path} not found", err=True)
        raise typer.Exit(1)
    return load_cluster_config(assignments_path), repo_root


def _run_preflight(config: object, *, target_node: str | None = None) -> None:
    """Run pre-flight checks. Exit on failure."""
    from thunder_forge.cluster.preflight import print_preflight_result, run_preflight

    errors = run_preflight(config, target_node=target_node)
    print_preflight_result(errors, config)
    if errors:
        raise typer.Exit(1)


@app.command()
def generate_config(
    check: bool = typer.Option(
        False, "--check", help="Compare generated config with committed file, exit 1 on mismatch."
    ),
) -> None:
    """Generate litellm-config.yaml from node-assignments.yaml."""
    from thunder_forge.cluster.config import (
        OS_OVERHEAD_GB,
        check_config_sync,
        generate_litellm_config,
        validate_memory,
    )

    config, repo_root = _load_config()
    config_path = repo_root / "configs" / "litellm-config.yaml"

    typer.echo("Validating memory budgets...")
    errors = validate_memory(config)
    for node_name, slots in sorted(config.assignments.items()):
        node = config.nodes[node_name]
        parts = []
        total = OS_OVERHEAD_GB
        for slot in slots:
            model = config.models[slot.model]
            weight = model.ram_gb if model.ram_gb is not None else model.disk_gb
            kv = model.kv_per_32k_gb
            total += weight + kv
            parts.append(f"{slot.model}({weight}+{kv}kv)")
        budget = " + ".join(parts) + f" + {OS_OVERHEAD_GB} OS = {total:.1f} GB / {node.ram_gb} GB"
        status = "✓" if total <= node.ram_gb else "✗ EXCEEDS"
        typer.echo(f"  {node_name}: {budget} {status}")

    if errors:
        for err in errors:
            typer.echo(f"Error: {err}", err=True)
        raise typer.Exit(1)

    if check:
        if check_config_sync(config, config_path):
            typer.echo("✓ Config is in sync with assignments")
            raise typer.Exit(0)
        else:
            typer.echo("✗ Config mismatch — run 'thunder-forge generate-config' to update", err=True)
            raise typer.Exit(1)

    content = generate_litellm_config(config)
    config_path.write_text(content)
    typer.echo(f"✓ Generated {config_path}")


@app.command()
def ensure_models(
    dry_run: bool = typer.Option(False, "--dry-run", help="Print what would be downloaded without doing it."),
    skip_preflight: bool = typer.Option(False, "--skip-preflight", help="Skip pre-flight node checks."),
) -> None:
    """Download and sync models to assigned nodes."""
    from thunder_forge.cluster.models import run_ensure_models

    config, _ = _load_config()

    if not skip_preflight:
        _run_preflight(config)

    success = run_ensure_models(config, dry_run=dry_run)
    raise typer.Exit(0 if success else 1)


@app.command()
def deploy(
    node: str | None = typer.Option(None, "--node", help="Deploy to a single node (e.g. msm1)."),
    skip_models: bool = typer.Option(False, "--skip-models", help="Skip model download/sync step."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show deployment plan without executing."),
    skip_preflight: bool = typer.Option(False, "--skip-preflight", help="Skip pre-flight node checks."),
) -> None:
    """Deploy models, plists, and configs to the cluster."""
    from thunder_forge.cluster.config import generate_litellm_config, validate_memory
    from thunder_forge.cluster.deploy import run_deploy
    from thunder_forge.cluster.models import run_ensure_models

    config, repo_root = _load_config()
    config_path = repo_root / "configs" / "litellm-config.yaml"

    if not skip_preflight:
        _run_preflight(config, target_node=node)

    if not skip_models and not dry_run:
        typer.echo("Ensuring models are present...")
        if not run_ensure_models(config, target_node=node):
            typer.echo("Model sync failed", err=True)
            raise typer.Exit(1)

    if not dry_run:
        typer.echo("\nGenerating config...")
        errors = validate_memory(config)
        if errors:
            for err in errors:
                typer.echo(f"Error: {err}", err=True)
            raise typer.Exit(1)
        content = generate_litellm_config(config)
        config_path.write_text(content)
        typer.echo(f"  Generated {config_path}")

    success = run_deploy(config, target_node=node, dry_run=dry_run)
    raise typer.Exit(0 if success else 1)


@app.command()
def health(
    skip_preflight: bool = typer.Option(False, "--skip-preflight", help="Skip pre-flight node checks."),
) -> None:
    """Check health of all cluster services."""
    from thunder_forge.cluster.health import run_health_checks

    config, _ = _load_config()

    if not skip_preflight:
        _run_preflight(config)

    all_healthy = run_health_checks(config)
    raise typer.Exit(0 if all_healthy else 1)
