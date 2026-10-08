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
    load_profile,
    reset_progress,
    restore,
    run_backup,
    save_profile_file,
    summarize,
)
from litresearch.cli import ConfigOpt, app, console
from litresearch.config import load_config
from litresearch.db import Paper, QueueItem, Reading, XpEvent, init_db
from litresearch.profile import ProfileError, get_profile, profile_rules, start_profile

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
    if not healthy:
        last = latest_backup(cfg)
        hint = f" (latest from {last.isoformat()})" if last else ""
        _fail(
            f"The database is damaged. Restore a snapshot from {cfg.backup.dir}{hint} "
            "with 'litresearch restore'."
        )
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


# --- game profile ---------------------------------------------------------------------------

profile_app = typer.Typer(help="Your game profile: locked rules, save, load, compare.")
app.add_typer(profile_app, name="profile")


def _rules_lines(rules) -> list[str]:
    xp = rules.base_xp()
    return [
        f"  reading: abstract +{xp['abstract']}, skimmed +{xp['skimmed']}, read +{xp['read']}, "
        f"thorough +{xp['thorough']}",
        f"  bonuses: frog +{rules.frog_bonus:.0%} of base, streak +{rules.streak_step:.0%}/week "
        f"(max {rules.streak_max_steps} steps), landmark +{rules.classic_xp}, fresh "
        f"+{rules.hot_xp} ({rules.hot_days} d), recommendation +{rules.recommendation_xp}, "
        f"rating +{rules.rating_xp}, notes +{rules.reflection_xp} ({rules.reflection_chars} "
        f"chars), inbox zero +{rules.inbox_zero_xp}, badge +{rules.badge_xp}",
        f"  weekly quest: {rules.weekly_detail_goal} deep + {rules.weekly_skim_goal} skims "
        f"= +{rules.quest_xp}",
        f"  penalty: -{rules.drop_penalty} for a picked recommendation left unread",
        f"  {len(rules.levels)} levels ({rules.levels[0].title} ... {rules.levels[-1].title}), "
        f"{len(rules.badges())} badges",
    ]


@profile_app.command("show")
def profile_show(config: Path = ConfigOpt):
    """Show the profile and its locked rules."""
    cfg = load_config(config)
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        profile = get_profile(session)
        if profile is None:
            console.print("No profile yet. Defaults from config.yaml (locked when you start):")
            lines = _rules_lines(cfg.game)
        else:
            console.print(
                f"[bold]{profile.name}[/bold], started {profile.created_at.date().isoformat()}, "
                f"rules locked (fingerprint {profile.fingerprint}):"
            )
            lines = _rules_lines(profile_rules(profile))
    engine.dispose()
    console.print("\n".join(lines))


@profile_app.command("start")
def profile_start_cmd(
    name: str = typer.Option("My reading journey", help="Profile name"),
    yes: bool = typer.Option(False, "--yes", help="Do not ask for confirmation"),
    config: Path = ConfigOpt,
):
    """Start the game with the rules from the 'game' section of config.yaml."""
    cfg = load_config(config)
    console.print("These rules will be locked:\n" + "\n".join(_rules_lines(cfg.game)))
    if not yes:
        typer.confirm("Start the game with these rules?", abort=True)
    engine = init_db(cfg.paths.database)
    try:
        with Session(engine) as session:
            profile = start_profile(session, cfg, name)
    except ProfileError as error:
        _fail(str(error))
    finally:
        engine.dispose()
    console.print(f"Started '{profile.name}' (fingerprint {profile.fingerprint}). Happy reading!")


@profile_app.command("save")
def profile_save_cmd(
    target: Path = typer.Argument(None, help="File (default: a new file in backup.dir)"),
    config: Path = ConfigOpt,
):
    """Save the profile with all progress and recommendations."""
    cfg = load_config(config)
    engine = init_db(cfg.paths.database)
    try:
        with Session(engine) as session:
            if target is None:
                path = save_profile_file(session, cfg.backup.dir)
            else:
                atomic_write_text(target, json.dumps(export_progress(session), indent=1))
                path = target
    finally:
        engine.dispose()
    console.print(f"Saved: {path}")


@profile_app.command("load")
def profile_load_cmd(
    source: Path = typer.Argument(..., help="A saved profile (JSON)"),
    yes: bool = typer.Option(False, "--yes", help="Do not ask for confirmation"),
    config: Path = ConfigOpt,
):
    """Replace your progress with a saved profile (the current state is saved first)."""
    cfg = load_config(config)
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        _fail(f"Cannot read {source}: {error}")
    if not yes:
        typer.confirm("Replace your current progress with this profile?", abort=True)
    engine = init_db(cfg.paths.database)
    try:
        with Session(engine) as session:
            archive = save_profile_file(session, cfg.backup.dir)
            report = load_profile(session, cfg, data)
    except BackupError as error:
        _fail(str(error))
    finally:
        engine.dispose()
    console.print(f"Loaded {source}. Your previous state was saved as {archive}.")
    console.print("\n".join(report.lines()))


@profile_app.command("compare")
def profile_compare_cmd(
    files: list[Path] = typer.Argument(..., help="Saved profiles to compare with yours"),
    config: Path = ConfigOpt,
):
    """Compare your profile with saved ones (nothing is changed)."""
    cfg = load_config(config)
    engine = init_db(cfg.paths.database)
    try:
        with Session(engine) as session:
            rows = [summarize(export_progress(session), cfg, "current")]
    finally:
        engine.dispose()
    for path in files:
        try:
            rows.append(summarize(json.loads(path.read_text(encoding="utf-8")), cfg, path.name))
        except (OSError, json.JSONDecodeError, BackupError) as error:
            console.print(f"[yellow]{path}: {error}[/yellow]")
    reference = rows[0]["fingerprint"]
    for row in rows:
        same = "same rules" if row["fingerprint"] == reference else "DIFFERENT rules"
        integrity = {True: "unmodified", False: "EDITED", None: "no checksum"}[row["checksum_ok"]]
        console.print(
            f"[bold]{row['label']}[/bold] ({row['name']}): {row['level'].icon} Lv "
            f"{row['level'].number} {row['level'].title}, {row['xp']} XP, "
            f"{row['papers_read']} papers ({row['thorough']} thorough), "
            f"best streak {row['best_streak']}, {row['badges']} badges, "
            f"{row['quests']} quests · {same} · {integrity}"
        )


@profile_app.command("reset")
def profile_reset_cmd(
    yes: bool = typer.Option(False, "--yes", help="Do not ask for confirmation"),
    config: Path = ConfigOpt,
):
    """Save the profile, then delete all progress to start a new game."""
    cfg = load_config(config)
    if not yes:
        typer.confirm("Archive this profile and delete all progress?", abort=True)
    engine = init_db(cfg.paths.database)
    try:
        with Session(engine) as session:
            archive = save_profile_file(session, cfg.backup.dir)
            reset_progress(session)
    finally:
        engine.dispose()
    console.print(f"Archived as {archive}. Start a new game with 'litresearch profile start'.")
