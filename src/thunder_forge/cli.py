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
    typer.echo("generate-config: not implemented yet")
    raise typer.Exit(1)


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
