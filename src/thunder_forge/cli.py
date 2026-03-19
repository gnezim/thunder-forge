"""Thunder Forge CLI — cluster management commands."""

import typer

app = typer.Typer(
    name="thunder-forge",
    help="CLI for managing a local MLX inference cluster.",
    no_args_is_help=True,
)


@app.command()
def generate_config(
    check: bool = typer.Option(
        False, "--check", help="Compare generated config with committed file, exit 1 on mismatch."
    ),
) -> None:
    """Generate litellm-config.yaml from node-assignments.yaml."""
    from thunder_forge.cluster.config import (
        check_config_sync,
        find_repo_root,
        generate_litellm_config,
        load_cluster_config,
        validate_memory,
    )

    repo_root = find_repo_root()
    assignments_path = repo_root / "configs" / "node-assignments.yaml"
    config_path = repo_root / "configs" / "litellm-config.yaml"

    if not assignments_path.exists():
        typer.echo(f"Error: {assignments_path} not found", err=True)
        raise typer.Exit(1)

    config = load_cluster_config(assignments_path)

    # Memory validation
    typer.echo("Validating memory budgets...")
    errors = validate_memory(config)
    for node_name, slots in sorted(config.assignments.items()):
        node = config.nodes[node_name]
        parts = []
        total = 8
        for slot in slots:
            model = config.models[slot.model]
            weight = model.ram_gb if model.ram_gb is not None else model.disk_gb
            kv = model.kv_per_32k_gb
            total += weight + kv
            parts.append(f"{slot.model}({weight}+{kv}kv)")
        budget = " + ".join(parts) + f" + 8 OS = {total:.1f} GB / {node.ram_gb} GB"
        status = "✅" if total <= node.ram_gb else "❌ EXCEEDS"
        typer.echo(f"  {node_name}: {budget} {status}")

    if errors:
        for err in errors:
            typer.echo(f"Error: {err}", err=True)
        raise typer.Exit(1)

    if check:
        if check_config_sync(config, config_path):
            typer.echo("✅ Config is in sync with assignments")
            raise typer.Exit(0)
        else:
            typer.echo("❌ Config mismatch — run 'thunder-forge generate-config' to update", err=True)
            raise typer.Exit(1)

    content = generate_litellm_config(config)
    config_path.write_text(content)
    typer.echo(f"✅ Generated {config_path}")


@app.command()
def ensure_models(
    dry_run: bool = typer.Option(False, "--dry-run", help="Print what would be downloaded without doing it."),
) -> None:
    """Download and sync models to assigned inference nodes."""
    from thunder_forge.cluster.config import find_repo_root, load_cluster_config
    from thunder_forge.cluster.models import run_ensure_models

    repo_root = find_repo_root()
    assignments_path = repo_root / "configs" / "node-assignments.yaml"
    config = load_cluster_config(assignments_path)

    success = run_ensure_models(config, dry_run=dry_run)
    raise typer.Exit(0 if success else 1)


@app.command()
def deploy(
    node: str | None = typer.Option(None, "--node", help="Deploy to a single node (e.g. msm1)."),
    skip_models: bool = typer.Option(False, "--skip-models", help="Skip model download/sync step."),
) -> None:
    """Deploy models, plists, and configs to the cluster."""
    from thunder_forge.cluster.config import (
        find_repo_root,
        generate_litellm_config,
        load_cluster_config,
        validate_memory,
    )
    from thunder_forge.cluster.deploy import run_deploy
    from thunder_forge.cluster.models import run_ensure_models

    repo_root = find_repo_root()
    assignments_path = repo_root / "configs" / "node-assignments.yaml"
    config_path = repo_root / "configs" / "litellm-config.yaml"
    config = load_cluster_config(assignments_path)

    if not skip_models:
        typer.echo("Ensuring models are present...")
        if not run_ensure_models(config, target_node=node):
            typer.echo("Model sync failed", err=True)
            raise typer.Exit(1)

    typer.echo("\nGenerating config...")
    errors = validate_memory(config)
    if errors:
        for err in errors:
            typer.echo(f"Error: {err}", err=True)
        raise typer.Exit(1)
    content = generate_litellm_config(config)
    config_path.write_text(content)
    typer.echo(f"  Generated {config_path}")

    success = run_deploy(config, target_node=node)
    raise typer.Exit(0 if success else 1)


@app.command()
def health() -> None:
    """Check health of all cluster services."""
    from thunder_forge.cluster.config import find_repo_root, load_cluster_config
    from thunder_forge.cluster.health import run_health_checks

    repo_root = find_repo_root()
    assignments_path = repo_root / "configs" / "node-assignments.yaml"
    config = load_cluster_config(assignments_path)

    all_healthy = run_health_checks(config)
    raise typer.Exit(0 if all_healthy else 1)
