# -*- coding: utf-8 -*-
"""
knode CLI

Commands:
  knode index <path>              Index an Android project
  knode serve --project <path>    Launch the graph browser UI
  knode mcp   --project <path>    Start the MCP stdio server
  knode stats --project <path>    Print graph statistics
"""
import click
import uvicorn
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from knode import __version__

console = Console()


@click.group()
@click.version_option(__version__, prog_name="knode")
def cli():
    """[knode] Knowledge graph for Android codebases."""


# ──────────────────────────────────────────────────────────────────────────────
# index
# ──────────────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("project_path", default=".", type=click.Path(exists=True))
@click.option("--full", is_flag=True, help="Force full re-index.")
@click.option("--quiet", is_flag=True, help="Suppress output.")
def index(project_path: str, full: bool, quiet: bool):
    """Index an Android project at PROJECT_PATH."""
    import os
    project_path = os.path.abspath(project_path)

    if not quiet:
        console.print(Panel.fit(
            f"[bold cyan]knode[/bold cyan]  v{__version__}\n"
            f"[dim]Indexing:[/dim] [white]{project_path}[/white]",
            border_style="cyan",
        ))

    from knode.indexer.graph_builder import build_graph
    from knode.graph.storage import get_db_path
    storage = build_graph(project_path, incremental=not full, quiet=quiet)
    stats = storage.get_stats()
    storage.close()

    if not quiet:
        table = Table(title="Index complete", border_style="cyan", show_header=True)
        table.add_column("Metric", style="bold")
        table.add_column("Value", justify="right", style="green")
        table.add_row("Total nodes", str(stats["node_count"]))
        table.add_row("Total edges", str(stats["edge_count"]))
        for k, v in stats.get("nodes_by_type", {}).items():
            table.add_row(f"  {k}", str(v))

        console.print(table)

    # ── Register in global registry ───────────────────────────────────────────
    try:
        from knode.registry import register
        project_name = register(project_path, get_db_path(project_path))
        if not quiet:
            console.print(
                f"[bold green]OK[/bold green] Registered [cyan]{project_name}[/cyan] "
                "in global registry [dim](~/.knode/registry.json)[/dim]"
            )
    except Exception as exc:  # noqa: BLE001
        if not quiet:
            console.print(f"[yellow]WARNING Could not register project: {exc}[/yellow]")

    # ── Scaffold .agents/skills/knode/ and AGENTS.md ─────────────────────
    try:
        from knode.scaffold import scaffold_project
        scaffold_project(project_path)
        if not quiet:
            console.print(
                "[bold green]OK[/bold green] Scaffolded "
                "[cyan].agents/skills/knode/[/cyan] and updated "
                "[cyan]AGENTS.md[/cyan]"
            )
    except Exception as exc:  # noqa: BLE001
        if not quiet:
            console.print(f"[yellow]WARNING Could not scaffold agent files: {exc}[/yellow]")

    if not quiet:
        console.print(f"\n[bold green]Done![/bold green] Run [cyan]knode serve --project \"{project_path}\"[/cyan] to explore.")


# ──────────────────────────────────────────────────────────────────────────────
# serve
# ──────────────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--project", default=".", type=click.Path(exists=True), help="Path to indexed Android project.")
@click.option("--port", default=7070, show_default=True, help="HTTP port for the graph browser.")
@click.option("--host", default="127.0.0.1", show_default=True)
def serve(project: str, port: int, host: str):
    """Launch the graph browser UI in your browser."""
    import os, webbrowser, threading, time
    project = os.path.abspath(project)

    from knode.web.app import create_app
    try:
        web_app = create_app(project)
    except FileNotFoundError as e:
        console.print(f"[red]✗ {e}[/red]")
        raise SystemExit(1)

    url = f"http://{host}:{port}"
    console.print(Panel.fit(
        f"[bold cyan]knode Graph Browser[/bold cyan]\n"
        f"[dim]Project:[/dim] [white]{project}[/white]\n"
        f"[dim]URL:[/dim]     [link={url}]{url}[/link]",
        border_style="cyan",
    ))

    def _open():
        time.sleep(1.2)
        webbrowser.open(url)

    threading.Thread(target=_open, daemon=True).start()
    uvicorn.run(web_app, host=host, port=port, log_level="warning")


# ──────────────────────────────────────────────────────────────────────────────
# mcp
# ──────────────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--project", required=False, default=None, type=click.Path(exists=True),
              help="Path to indexed Android project (optional, tools can index on-demand).")
def mcp(project: str | None):
    """Start the MCP stdio server for AI agent integration."""
    import os
    if project:
        project = os.path.abspath(project)
    from knode.mcp_server.server import main as mcp_main
    mcp_main(project)


# ──────────────────────────────────────────────────────────────────────────────
# stats
# ──────────────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--project", default=".", type=click.Path(exists=True))
def stats(project: str):
    """Print statistics for an indexed project."""
    import os
    from pathlib import Path
    from knode.graph.storage import GraphStorage, get_db_path

    project = os.path.abspath(project)
    db_path = get_db_path(project)
    if not Path(db_path).exists():
        console.print(f"[red]✗ No index found. Run `knode index \"{project}\"` first.[/red]")
        raise SystemExit(1)

    with GraphStorage(db_path) as st:
        s = st.get_stats()
        name = st.get_project_info("name") or Path(project).name
        indexed_at = st.get_project_info("indexed_at") or "unknown"

    table = Table(title=f"Stats: {name}", border_style="cyan")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right", style="green")
    table.add_row("Indexed at", indexed_at)
    table.add_row("Total nodes", str(s["node_count"]))
    table.add_row("Total edges", str(s["edge_count"]))
    for k, v in s.get("nodes_by_type", {}).items():
        table.add_row(f"  {k}", str(v))
    for k, v in s.get("edges_by_type", {}).items():
        table.add_row(f"  ─ {k}", str(v))
    console.print(table)


# ──────────────────────────────────────────────────────────────────────────────
# list
# ──────────────────────────────────────────────────────────────────────────────

@cli.command(name="list")
def list_projects():
    """List all indexed projects in the global registry."""
    from pathlib import Path as _Path
    from knode.registry import list_projects as _list

    projects = _list()
    if not projects:
        console.print("[yellow]No projects registered. Run `knode index <path>` first.[/yellow]")
        return

    table = Table(title="Registered Projects", border_style="cyan", show_header=True)
    table.add_column("Name", style="bold cyan")
    table.add_column("Path", style="white")
    table.add_column("Indexed At", style="dim")
    table.add_column("DB", style="green")

    for name, info in projects.items():
        db_exists = _Path(info["db"]).exists()
        db_status = "✓" if db_exists else "[red]✗ missing[/red]"
        table.add_row(
            name,
            info["path"],
            info.get("indexed_at", "unknown"),
            db_status,
        )

    console.print(table)


# ──────────────────────────────────────────────────────────────────────────────
# clean
# ──────────────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--all", "all_projects", is_flag=True, help="Delete all indexes in the registry.")
@click.option("--force", is_flag=True, help="Force deletion without asking for confirmation.")
@click.argument("project_path", default=".", type=click.Path())
def clean(all_projects: bool, force: bool, project_path: str):
    """Delete the index for a project or all projects."""
    import os
    import shutil
    from pathlib import Path
    from knode.registry import list_projects as _list, unregister, _REGISTRY_FILE

    if all_projects:
        if not force:
            click.confirm("This will delete ALL graph databases and the global registry. Continue?", abort=True)
        
        projects = _list()
        for name, info in projects.items():
            db_path = Path(info["db"])
            knode_dir = db_path.parent
            if knode_dir.exists() and knode_dir.name == ".knode":
                try:
                    shutil.rmtree(knode_dir)
                    console.print(f"[dim]Removed directory: {knode_dir}[/dim]")
                except Exception as e:
                    console.print(f"[yellow]Could not remove {knode_dir}: {e}[/yellow]")
            unregister(info["path"])

        if _REGISTRY_FILE.exists():
            try:
                _REGISTRY_FILE.unlink()
            except Exception:
                pass

        console.print("[bold green]OK[/bold green] Deleted all indexes and cleared registry.")
    else:
        project_path = os.path.abspath(project_path)
        from knode.graph.storage import get_db_path
        db_path = Path(get_db_path(project_path))
        knode_dir = db_path.parent

        if not knode_dir.exists():
            console.print(f"[yellow]No index found for project at {project_path}[/yellow]")
            return

        if not force:
            click.confirm(f"This will delete the index at {knode_dir}. Continue?", abort=True)

        try:
            shutil.rmtree(knode_dir)
            console.print(f"[dim]Removed directory: {knode_dir}[/dim]")
        except Exception as e:
            console.print(f"[red]Could not remove {knode_dir}: {e}[/red]")
            return

        unregister(project_path)
        console.print(f"[bold green]OK[/bold green] Deleted index for {project_path}.")


# ──────────────────────────────────────────────────────────────────────────────
# hooks
# ──────────────────────────────────────────────────────────────────────────────

@cli.group()
def hooks():
    """Manage Git hooks for auto re-indexing."""
    pass

@hooks.command(name="install")
@click.argument("project_path", default=".", type=click.Path(exists=True))
def hooks_install(project_path: str):
    """Install git hooks to auto re-index after commit/checkout/merge."""
    import os
    from knode.hooks import install_hooks
    project_path = os.path.abspath(project_path)
    try:
        install_hooks(project_path)
        console.print("[bold green]OK[/bold green] Git hooks installed successfully.")
    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise click.Abort()

@hooks.command(name="uninstall")
@click.argument("project_path", default=".", type=click.Path(exists=True))
def hooks_uninstall(project_path: str):
    """Uninstall git hooks."""
    import os
    from knode.hooks import uninstall_hooks
    project_path = os.path.abspath(project_path)
    try:
        uninstall_hooks(project_path)
        console.print("[bold green]OK[/bold green] Git hooks uninstalled successfully.")
    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise click.Abort()

@hooks.command(name="status")
@click.argument("project_path", default=".", type=click.Path(exists=True))
def hooks_status(project_path: str):
    """Check the status of git hooks."""
    import os
    from knode.hooks import get_hooks_status
    project_path = os.path.abspath(project_path)
    try:
        status_dict = get_hooks_status(project_path)
        status_str = ", ".join(f"{k}: {v}" for k, v in status_dict.items())
        console.print(f"Git hooks status for [cyan]{project_path}[/cyan]: [bold]{status_str}[/bold]")
    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise click.Abort()
