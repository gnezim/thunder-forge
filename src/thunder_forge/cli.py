"""Thunder Forge CLI — cluster management commands."""

from typing import Optional

import typer

app = typer.Typer(
    name="thunder-forge",
    help="CLI for managing a local MLX inference cluster.",
    no_args_is_help=True,
)


@app.command()
def generate_config(
    check: bool = typer.Option(False, "--check", help="Compare generated config with committed file, exit 1 on mismatch."),
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
    typer.echo("ensure-models: not implemented yet")
    raise typer.Exit(1)


@app.command()
def deploy(
    node: Optional[str] = typer.Option(None, "--node", help="Deploy to a single node (e.g. msm1)."),
) -> None:
    """Deploy models, plists, and configs to the cluster."""
    typer.echo("deploy: not implemented yet")
    raise typer.Exit(1)


@app.command()
def health() -> None:
    """Check health of all cluster services."""
    typer.echo("health: not implemented yet")
    raise typer.Exit(1)
