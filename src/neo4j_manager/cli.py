"""neo4j-manager: manage local Colima/Docker Neo4j instances and sync their data via git."""

from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from neo4j_manager import instance as inst
from neo4j_manager import sync as sync_mod
from neo4j_manager.docker_ops import DockerOpsError
from neo4j_manager.sync import SyncError

app = typer.Typer(help="Manage local Colima/Docker Neo4j instances and sync their data via git.")
sync_app = typer.Typer(help="Push/pull a database's data to/from its linked GitHub repo.")
app.add_typer(sync_app, name="sync")

console = Console()


def _fail(exc: Exception) -> None:
    console.print(f"[red]Error:[/red] {exc}")
    raise typer.Exit(1)


@app.command()
def create(
    name: str,
    http_port: Optional[int] = typer.Option(None, help="Host port for the Neo4j browser/HTTP API."),
    bolt_port: Optional[int] = typer.Option(None, help="Host port for the Bolt protocol."),
    data_dir: Optional[str] = typer.Option(None, help="Base dir for data/logs/import/plugins (default: ~/neo4j-data/<name>)."),
    image: str = typer.Option("neo4j:latest", help="Neo4j docker image tag."),
    plugins: str = typer.Option("apoc", help="Comma-separated plugin list."),
    repo: str = typer.Option("", help="GitHub repo URL to link for sync (optional, can be set later via `sync init`)."),
    start: bool = typer.Option(True, help="Start the container immediately after creating it."),
):
    """Register and (by default) start a new named Neo4j instance."""
    try:
        instance = inst.create(
            name,
            http_port=http_port,
            bolt_port=bolt_port,
            data_dir=data_dir,
            plugins=[p.strip() for p in plugins.split(",") if p.strip()],
            image=image,
            repo=repo,
            start=start,
        )
    except (DockerOpsError, SyncError) as e:
        _fail(e)
        return
    console.print(
        f"[green]Created[/green] {name!r}: http://127.0.0.1:{instance.http_port} "
        f"bolt://127.0.0.1:{instance.bolt_port}  user=neo4j password={instance.auth_password}"
    )


@app.command()
def start(name: str):
    """Start a stopped (or not-yet-created) instance's container."""
    try:
        inst.start(name)
    except DockerOpsError as e:
        _fail(e)
        return
    console.print(f"[green]Started[/green] {name!r}")


@app.command()
def stop(name: str):
    """Stop an instance's container."""
    try:
        inst.stop(name)
    except DockerOpsError as e:
        _fail(e)
        return
    console.print(f"[yellow]Stopped[/yellow] {name!r}")


@app.command()
def restart(name: str):
    """Stop then start an instance's container."""
    try:
        inst.restart(name)
    except DockerOpsError as e:
        _fail(e)
        return
    console.print(f"[green]Restarted[/green] {name!r}")


@app.command(name="list")
def list_cmd():
    """List all known instances and their status."""
    _print_status(inst.list_status())


@app.command()
def status(name: Optional[str] = typer.Argument(None)):
    """Show status for one instance, or all instances if no name is given."""
    rows = inst.list_status()
    if name:
        rows = [r for r in rows if r["name"] == name]
        if not rows:
            _fail(SystemExit(f"No such instance: {name!r}"))
            return
    _print_status(rows)


def _print_status(rows: list[dict]) -> None:
    table = Table()
    for col in ("name", "state", "http_port", "bolt_port", "image", "data_dir"):
        table.add_column(col)
    for r in rows:
        table.add_row(*(str(r[c]) for c in ("name", "state", "http_port", "bolt_port", "image", "data_dir")))
    console.print(table)


@app.command()
def remove(
    name: str,
    purge_data: bool = typer.Option(False, "--purge-data", help="Also delete local data/logs/import/plugins folders."),
):
    """Stop and remove an instance's container (and optionally its local data)."""
    if purge_data:
        confirm = typer.confirm(f"This will permanently delete local data for {name!r}. Continue?")
        if not confirm:
            raise typer.Abort()
    try:
        inst.remove(name, purge_data=purge_data)
    except DockerOpsError as e:
        _fail(e)
        return
    console.print(f"[yellow]Removed[/yellow] {name!r}")


@app.command()
def shell(name: str):
    """Open an interactive cypher-shell session in the instance's container."""
    inst.shell(name)


@app.command()
def tui():
    """Launch the interactive TUI dashboard (browse/create/edit instances, sync push/pull)."""
    from neo4j_manager import tui as tui_mod

    tui_mod.run()


@sync_app.command("init")
def sync_init(
    name: str,
    repo: str = typer.Option(..., "--repo", help="GitHub repo URL to link for this instance's data."),
    create: bool = typer.Option(False, "--create", help="Create the GitHub repo via `gh repo create` first."),
):
    """Link an instance to a GitHub repo for data sync (clones or initializes the local working copy)."""
    if create:
        confirm = typer.confirm(f"This will create a new GitHub repo at {repo!r}. Continue?")
        if not confirm:
            raise typer.Abort()
    try:
        sync_mod.init_repo(name, repo, create=create)
    except SyncError as e:
        _fail(e)
        return
    console.print(f"[green]Linked[/green] {name!r} to {repo}")


@sync_app.command("push")
def sync_push(
    name: str,
    message: str = typer.Option("", "-m", "--message", help="Commit message (default: 'Sync <name>')."),
):
    """Export the instance's full graph and push it to its linked GitHub repo."""
    try:
        sync_mod.push(name, message=message)
    except SyncError as e:
        _fail(e)
        return
    console.print(f"[green]Pushed[/green] {name!r} data to its linked repo")


@sync_app.command("pull")
def sync_pull(
    name: str,
    repo: Optional[str] = typer.Option(None, "--repo", help="Repo URL (needed the first time on a new machine)."),
    force: bool = typer.Option(False, "--force", help="Discard any unpushed local changes before pulling."),
):
    """Pull the latest snapshot from the linked repo and replace the instance's data with it."""
    try:
        sync_mod.pull(name, repo_url=repo, force=force)
    except SyncError as e:
        _fail(e)
        return
    console.print(f"[green]Pulled[/green] latest data into {name!r}")


@sync_app.command("status")
def sync_status(name: str):
    """Show whether the instance's data repo has unpushed or unpulled changes."""
    st = sync_mod.status(name)
    if not st.get("configured"):
        console.print(f"[yellow]{name!r} has no data repo configured yet.[/yellow] Run `sync init` first.")
        return
    console.print(
        f"ahead={st['ahead']} behind={st['behind']} dirty={'yes' if st['dirty'] else 'no'}"
    )
    if st["dirty"]:
        console.print(st["dirty_files"])


if __name__ == "__main__":
    app()
