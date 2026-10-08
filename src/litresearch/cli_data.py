"""CLI commands for backups, progress export/import, restore and checks."""

import json
from datetime import datetime
from pathlib import Path

import typer
from sqlmodel import Session, func, select

from litresearch.backup import (
    BackupError,
    atomic_write_text,
    export_progress,
    import_progress,
    integrity_ok,
    latest_backup,
    restore,
    run_backup,
)
from litresearch.cli import ConfigOpt, app, console
from litresearch.config import load_config
from litresearch.db import Paper, QueueItem, Reading, XpEvent, init_db

BACKUP_WARN_DAYS = 7


def _fail(message: str):
    console.print(f"[red]{message}[/red]")
    raise typer.Exit(code=1)


@app.command()
def backup(config: Path = ConfigOpt):
    """Snapshot the database and export your progress into backup.dir."""
    cfg = load_config(config)
    engine = init_db(cfg.paths.database)
    try:
        with Session(engine) as session:
            result = run_backup(cfg, session)
    except BackupError as error:
        _fail(str(error))
    finally:
        engine.dispose()
    console.print(f"Snapshot: {result.snapshot}")
    console.print(f"Progress: {result.progress}")
    if result.removed:
        console.print(f"Removed {len(result.removed)} old file(s) (keeping {cfg.backup.keep}).")


@app.command("export-progress")
def export_progress_cmd(
    target: Path = typer.Argument(..., help="JSON file to write"), config: Path = ConfigOpt
):
    """Write your reading progress, notes, queue and XP to a portable JSON file."""
    cfg = load_config(config)
    engine = init_db(cfg.paths.database)
    try:
        with Session(engine) as session:
            data = export_progress(session)
    finally:
        engine.dispose()
    atomic_write_text(target, json.dumps(data, indent=1))
    console.print(
        f"Exported {len(data['readings'])} readings, {len(data['xp_events'])} XP transactions "
        f"and {len(data['queue'])} queued papers to {target}"
    )


@app.command("import-progress")
def import_progress_cmd(
    source: Path = typer.Argument(..., help="JSON file from export-progress or backup"),
    dry_run: bool = typer.Option(False, help="Show what would change, write nothing"),
    config: Path = ConfigOpt,
):
    """Merge progress from another machine (idempotent; notes from both sides are kept)."""
    cfg = load_config(config)
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        _fail(f"Cannot read {source}: {error}")
    engine = init_db(cfg.paths.database)
    try:
        with Session(engine) as session:
            if not dry_run:
                run_backup(cfg, session)  # safety net before merging
            report = import_progress(session, cfg, data)
            if dry_run:
                session.rollback()
    except BackupError as error:
        _fail(str(error))
    finally:
        engine.dispose()
    if dry_run:
        console.print("[bold]Dry run[/bold]: nothing was written.")
    console.print("\n".join(report.lines()))


@app.command("restore")
def restore_cmd(
    snapshot_file: Path = typer.Argument(..., help="A litresearch_*.sqlite snapshot"),
    yes: bool = typer.Option(False, "--yes", help="Do not ask for confirmation"),
    config: Path = ConfigOpt,
):
    """Replace the database with a snapshot (the current state is backed up first)."""
    cfg = load_config(config)
    if not yes:
        typer.confirm(
            f"Replace {cfg.paths.database} with {snapshot_file}? Stop 'litresearch serve' first.",
            abort=True,
        )
    try:
        safety = restore(cfg, snapshot_file)
    except BackupError as error:
        _fail(str(error))
    console.print(f"Restored. The previous database was saved as {safety}")


@app.command()
def check(config: Path = ConfigOpt):
    """Check the database and show where your data and backups are."""
    cfg = load_config(config)
    database = cfg.paths.database
    console.print(f"Database:  {database.resolve()}")
    if not database.exists():
        _fail("The database does not exist yet; run 'litresearch ingest'.")
    healthy = integrity_ok(database)
    console.print(f"Integrity: {'[green]ok[/green]' if healthy else '[red]FAILED[/red]'}")
    engine = init_db(database)
    with Session(engine) as session:

        def count(model) -> int:
            return session.exec(select(func.count()).select_from(model)).one()

        console.print(
            f"Contents:  {count(Paper)} papers, {count(Reading)} readings, "
            f"{count(QueueItem)} queued, {count(XpEvent)} XP transactions"
        )
    engine.dispose()
    console.print(f"Backups:   {cfg.backup.dir.resolve()} (keeping {cfg.backup.keep})")
    last = latest_backup(cfg)
    if last is None:
        console.print("[yellow]No backup yet. Run 'litresearch backup'.[/yellow]")
    else:
        age = (datetime.now() - last).days
        colour = "yellow" if age >= BACKUP_WARN_DAYS else "green"
        console.print(f"Last backup: [{colour}]{last.isoformat()} ({age} days ago)[/{colour}]")
    if not healthy:
        _fail("Restore the latest snapshot with 'litresearch restore'.")
