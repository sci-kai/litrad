"""CLI commands for backups, archives (export/import), restore and checks."""

from datetime import datetime
from pathlib import Path

import typer
from sqlmodel import Session, func, select

from litrad import notes, profiles
from litrad.backup import (
    BackupError,
    integrity_ok,
    latest_backup,
    profile_settings,
    restore,
    run_backup,
    save_profile_archive,
    settings_text,
    summarize_session,
)
from litrad.cli import ConfigOpt, app, console, load_cfg
from litrad.config import load_config
from litrad.db import Paper, QueueItem, Reading, XpEvent, init_db
from litrad.profile import ProfileError, get_profile, profile_rules

BACKUP_WARN_DAYS = 7


def _fail(message: str):
    console.print(f"[red]{message}[/red]")
    raise typer.Exit(code=1)


@app.command()
def backup(config: Path = ConfigOpt):
    """Snapshot the database and write an archive of everything into backup.dir."""
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
    console.print(f"Archive:  {result.archive}")
    if result.removed:
        console.print(f"Removed {len(result.removed)} old file(s) (keeping {cfg.backup.keep}).")


@app.command("export-progress")
def export_progress_cmd(
    target: Path = typer.Argument(..., help="Zip archive to write"), config: Path = ConfigOpt
):
    """Write your progress, notes, queue and XP to an archive (the same as export-all)."""
    export_all_cmd(target, config)


def save_profile_archive_to(session, cfg, target: Path, settings: dict) -> Path:
    from litrad.archive import export_archive

    return export_archive(session, cfg, target, settings_text(cfg, settings)).path


@app.command("import-progress")
def import_progress_cmd(
    source: Path = typer.Argument(..., help="Archive (.zip) from a backup or export"),
    dry_run: bool = typer.Option(False, help="Show what would change, write nothing"),
    config: Path = ConfigOpt,
):
    """Merge progress from another machine (idempotent; notes from both sides are kept)."""
    from litrad import archive

    cfg = load_cfg(config)
    try:  # the same as import-all --into this profile
        result = archive.import_into(cfg, source, dry_run=dry_run)
    except BackupError as error:
        _fail(str(error))
    for line in result.lines():
        console.print(line, markup=False, highlight=False)


@app.command("export-all")
def export_all_cmd(
    target: Path = typer.Argument(
        None, help="Zip file to write (default: a new file in backup.dir)"
    ),
    config: Path = ConfigOpt,
):
    """Export everything of the profile into one documented zip archive."""
    from litrad.archive import archive_name, export_archive
    from litrad.db import Profile

    cfg = load_cfg(config)
    settings = cfg.paths.database.parent / "settings.yaml"
    engine = init_db(cfg.paths.database)
    try:
        with Session(engine) as session:
            notes.refresh_quietly(session, cfg)
            if target is None:
                profile = session.exec(select(Profile)).first()
                target = cfg.backup.dir / archive_name(profile.name if profile else "profile")
            result = export_archive(
                session,
                cfg,
                target,
                settings.read_text("utf-8") if settings.is_file() else None,
            )
    finally:
        engine.dispose()
    counts = result.counts
    console.print(
        f"Exported {counts.get('papers', 0)} papers, {counts.get('readings', 0)} readings, "
        f"{counts.get('xp_ledger', 0)} XP transactions, {counts.get('libraries', 0)} libraries "
        f"and {counts.get('notes', 0)} note files to {result.path} "
        f"({result.size // 1024 + 1} KB)"
    )


@app.command("import-all")
def import_all_cmd(
    source: Path = typer.Argument(..., help="Zip archive written by export-all"),
    new: str = typer.Option(
        None, "--new", help="Create a new profile with this name (default: the archive's name)"
    ),
    into: str = typer.Option(None, "--into", help="Merge into this existing profile"),
    dry_run: bool = typer.Option(False, help="Check and show what would change, write nothing"),
    partial: bool = typer.Option(
        False, help="Import only the consistent records and list what was skipped"
    ),
    config: Path = ConfigOpt,
):
    """Import an archive from export-all: checked first, then all or nothing."""
    from litrad.archive import ArchiveError, import_into, import_new
    from litrad.migrations import SchemaTooNew

    base = load_config(config)
    if new and into:
        _fail("Use either --new or --into, not both.")
    try:
        if into:
            slug = profiles.find(base, into)
            result = import_into(
                profiles.profile_config(base, slug), source, dry_run=dry_run, partial=partial
            )
        else:
            result = import_new(base, source, new, dry_run=dry_run, partial=partial)
    except ArchiveError as error:
        lines = error.report.lines() if error.report.errors else []
        console.print(f"[red]{error if not lines else 'Not imported; nothing was changed.'}[/red]")
        for line in lines:
            console.print(line, markup=False, highlight=False)
        if lines:
            console.print(
                "Fix the files, or import only the consistent part with --partial.",
                markup=False,
            )
        raise typer.Exit(code=1) from None
    except (BackupError, profiles.ProfilesError, SchemaTooNew) as error:
        _fail(str(error))
    for line in result.lines():
        console.print(line, markup=False, highlight=False)
    if result.slug and not dry_run:
        console.print(f"Created profile '{profiles.describe(base, result.slug).name}'.")


@app.command("restore")
def restore_cmd(
    snapshot_file: Path = typer.Argument(..., help="A litrad_*.sqlite snapshot"),
    yes: bool = typer.Option(False, "--yes", help="Do not ask for confirmation"),
    config: Path = ConfigOpt,
):
    """Replace the database with a snapshot (the current state is backed up first)."""
    cfg = load_cfg(config)
    if not yes:
        typer.confirm(
            f"Replace {cfg.paths.database} with {snapshot_file}? Stop 'litrad serve' first.",
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
        _fail("The database does not exist yet; run 'litrad ingest'.")
    healthy = integrity_ok(database)
    console.print(f"Integrity: {'[green]ok[/green]' if healthy else '[red]FAILED[/red]'}")
    if not healthy:
        last = latest_backup(cfg)
        hint = f" (latest from {last.isoformat()})" if last else ""
        _fail(
            f"The database is damaged. Restore a snapshot from {cfg.backup.dir}{hint} "
            "with 'litrad restore'."
        )
    from litrad.backup import check_database
    from litrad.migrations import SCHEMA_VERSION, read_version

    problems = check_database(database)
    if problems:
        _fail("The database has problems:\n  " + "\n  ".join(problems))
    version, _ = read_version(database)
    upgrade = " (upgraded when opened)" if version < SCHEMA_VERSION else ""
    console.print(f"Schema:    version {version}, this litrad: {SCHEMA_VERSION}{upgrade}")
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
        console.print("[yellow]No backup yet. Run 'litrad backup'.[/yellow]")
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
        f"thorough +{xp['thorough']}; read earlier {rules.backlog_share:.0%} of that",
        f"  bonuses: cheese (top paper first) +{rules.cheese_bonus:.0%} of base, "
        f"streak +{rules.streak_step:.0%}/week "
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
        console.print("No profiles yet. Create one with 'litrad profile create NAME'.")


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
        "and run 'litrad ingest'."
    )


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
            console.print("No profile yet. Create one with 'litrad profile create NAME'.")
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
                path = save_profile_archive(session, cfg, cfg.backup.dir, settings=settings)
            else:
                path = save_profile_archive_to(session, cfg, target, settings)
    finally:
        engine.dispose()
    console.print(f"Saved: {path}")


@profile_app.command("load")
def profile_load_cmd(
    source: Path = typer.Argument(..., help="A saved profile (.zip archive)"),
    name: str = typer.Option(None, help="Name of the new profile (default: the saved name)"),
    config: Path = ConfigOpt,
):
    """Load a saved profile as a new profile (existing profiles are not changed)."""
    from litrad import archive
    from litrad.migrations import SchemaTooNew

    base = load_config(config)
    try:
        result = archive.import_new(base, source, name)
    except (profiles.ProfilesError, BackupError, ProfileError, SchemaTooNew) as error:
        _fail(str(error))
    _created(base, result.slug)


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
            rows = [summarize_session(session, cfg, label="current")]
    finally:
        engine.dispose()
    from litrad import archive

    for path in files:
        try:
            rows.append(archive.summarize_file(path, cfg, path.name))
        except (OSError, ValueError, BackupError) as error:
            console.print(f"[yellow]{path}: {error}[/yellow]")
    reference = rows[0]["fingerprint"]
    for row in rows:
        same = "same rules" if row["fingerprint"] == reference else "DIFFERENT rules"
        console.print(
            f"[bold]{row['label']}[/bold] ({row['name']}): {row['level'].icon} Lv "
            f"{row['level'].number} {row['level'].title}, {row['xp']} XP, "
            f"{row['papers_read']} papers ({row['thorough']} thorough), "
            f"best streak {row['best_streak']}, {row['badges']} badges, "
            f"{row['quests']} quests · {same}"
        )
