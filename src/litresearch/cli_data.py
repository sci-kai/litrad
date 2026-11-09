"""CLI commands for backups, progress export/import, restore and checks."""

import json
from datetime import datetime
from pathlib import Path

import typer
from sqlmodel import Session, func, select

from litresearch import notes, profiles
from litresearch.backup import (
    BackupError,
    atomic_write_text,
    export_progress,
    import_progress,
    integrity_ok,
    latest_backup,
    profile_settings,
    restore,
    run_backup,
    save_profile_file,
    summarize,
)
from litresearch.cli import ConfigOpt, app, console, load_cfg
from litresearch.config import load_config
from litresearch.db import Paper, QueueItem, Reading, XpEvent, init_db
from litresearch.profile import ProfileError, get_profile, profile_rules

BACKUP_WARN_DAYS = 7


def _fail(message: str):
    console.print(f"[red]{message}[/red]")
    raise typer.Exit(code=1)


@app.command()
def backup(config: Path = ConfigOpt):
    """Snapshot the database and export your progress into backup.dir."""
    cfg = load_cfg(config)
    engine = init_db(cfg.paths.database)
    try:
        with Session(engine) as session:
            result = run_backup(cfg, session, settings=profile_settings(cfg))
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
    cfg = load_cfg(config)
    engine = init_db(cfg.paths.database)
    try:
        with Session(engine) as session:
            notes.refresh_quietly(session, cfg)
            data = export_progress(session, settings=profile_settings(cfg))
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
    cfg = load_cfg(config)
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
            else:
                notes.refresh_quietly(session, cfg)  # merged notes go into the note files
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
    cfg = load_cfg(config)
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
    cfg = load_cfg(config)
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


# --- profiles ---------------------------------------------------------------------------

profile_app = typer.Typer(
    help="Profiles: each has its own progress, queue, library, topics and locked rules."
)
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
        f"  penalty: -{rules.drop_penalty} for a queued paper left unread",
        f"  {len(rules.levels)} levels ({rules.levels[0].title} ... {rules.levels[-1].title}), "
        f"{len(rules.badges())} badges",
    ]


@profile_app.command("list")
def profile_list_cmd(config: Path = ConfigOpt):
    """List all profiles (* = the current one)."""
    base = load_config(config)
    current = profiles.get_current(base)
    infos = profiles.list_profiles(base)
    for info in infos:
        mark = "*" if info.slug == current else " "
        last = info.last_activity.date().isoformat() if info.last_activity else "never"
        console.print(
            f"{mark} [bold]{info.name}[/bold] [dim]({info.slug})[/dim]: {info.level.icon} Lv "
            f"{info.level.number} {info.level.title}, {info.xp} XP, {info.papers_read} papers "
            f"read, {info.library} in library, last active {last}"
        )
    if not infos:
        console.print("No profiles yet. Create one with 'litresearch profile create NAME'.")
    legacy = profiles.legacy_available(base)
    if legacy:
        console.print(
            f"Data from before profiles ({legacy['library']} library papers, "
            f"{legacy['readings']} readings) can become a profile: "
            "'litresearch profile adopt NAME'."
        )


def _confirm_rules(base, yes: bool) -> None:
    console.print(
        "These rules (from the 'game' section of config.yaml) will be locked:\n"
        + "\n".join(_rules_lines(base.game))
    )
    if not yes:
        typer.confirm("Create the profile with these rules?", abort=True)


def _created(base, slug: str) -> None:
    profiles.set_current(base, slug)
    info = profiles.describe(base, slug)
    console.print(
        f"Created '{info.name}' (fingerprint {info.fingerprint}) in "
        f"{profiles.folder(base, slug)}; it is now the current profile."
    )


@profile_app.command("create")
def profile_create_cmd(
    name: str = typer.Argument(..., help="Profile name"),
    yes: bool = typer.Option(False, "--yes", help="Do not ask for confirmation"),
    config: Path = ConfigOpt,
):
    """Create a new, empty profile; its rules are locked from the start."""
    base = load_config(config)
    _confirm_rules(base, yes)
    try:
        slug = profiles.create_profile(base, name)
    except (profiles.ProfilesError, ProfileError) as error:
        _fail(str(error))
    _created(base, slug)
    console.print(
        f"Put your Paperpile export at {profiles.profile_config(base, slug).paths.bibtex} "
        "and run 'litresearch ingest'."
    )


@profile_app.command("adopt")
def profile_adopt_cmd(
    name: str = typer.Argument(..., help="Profile name"),
    yes: bool = typer.Option(False, "--yes", help="Do not ask for confirmation"),
    config: Path = ConfigOpt,
):
    """Turn the data from before profiles existed into a profile (a copy)."""
    base = load_config(config)
    if profiles.legacy_available(base) is None:
        _fail("There is no existing data to take over.")
    _confirm_rules(base, yes)
    try:
        slug = profiles.adopt_legacy(base, name)
    except (profiles.ProfilesError, ProfileError, BackupError) as error:
        _fail(str(error))
    _created(base, slug)


@profile_app.command("use")
def profile_use_cmd(
    name: str = typer.Argument(..., help="Profile name or id"), config: Path = ConfigOpt
):
    """Make a profile the current one (for the command line)."""
    base = load_config(config)
    try:
        slug = profiles.find(base, name)
        profiles.set_current(base, slug)
    except profiles.ProfilesError as error:
        _fail(str(error))
    console.print(f"Current profile: {profiles.describe(base, slug).name}")


@profile_app.command("show")
def profile_show(config: Path = ConfigOpt):
    """Show the profile and its locked rules."""
    cfg = load_cfg(config)
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        profile = get_profile(session)
        if profile is None:
            console.print("No profile yet. Create one with 'litresearch profile create NAME'.")
            lines = _rules_lines(cfg.game)
        else:
            console.print(
                f"[bold]{profile.name}[/bold], started {profile.created_at.date().isoformat()}, "
                f"rules locked (fingerprint {profile.fingerprint}):"
            )
            lines = _rules_lines(profile_rules(profile))
    engine.dispose()
    console.print(f"Data: {cfg.paths.database.parent.resolve()}")
    console.print("\n".join(lines))


@profile_app.command("save")
def profile_save_cmd(
    target: Path = typer.Argument(None, help="File (default: a new file in backup.dir)"),
    config: Path = ConfigOpt,
):
    """Save the profile with all progress, recommendations and settings."""
    cfg = load_cfg(config)
    settings = profile_settings(cfg)
    engine = init_db(cfg.paths.database)
    try:
        with Session(engine) as session:
            notes.refresh_quietly(session, cfg)
            if target is None:
                path = save_profile_file(session, cfg.backup.dir, settings=settings)
            else:
                data = export_progress(session, settings=settings)
                atomic_write_text(target, json.dumps(data, indent=1))
                path = target
    finally:
        engine.dispose()
    console.print(f"Saved: {path}")


@profile_app.command("load")
def profile_load_cmd(
    source: Path = typer.Argument(..., help="A saved profile (JSON)"),
    name: str = typer.Option(None, help="Name of the new profile (default: the saved name)"),
    config: Path = ConfigOpt,
):
    """Load a saved profile as a new profile (existing profiles are not changed)."""
    base = load_config(config)
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        _fail(f"Cannot read {source}: {error}")
    try:
        slug = profiles.import_profile_file(base, data, name)
    except (profiles.ProfilesError, BackupError, ProfileError) as error:
        _fail(str(error))
    _created(base, slug)


@profile_app.command("compare")
def profile_compare_cmd(
    files: list[Path] = typer.Argument(..., help="Saved profiles to compare with yours"),
    config: Path = ConfigOpt,
):
    """Compare your profile with saved ones (nothing is changed)."""
    cfg = load_cfg(config)
    engine = init_db(cfg.paths.database)
    try:
        with Session(engine) as session:
            notes.refresh_quietly(session, cfg)
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
