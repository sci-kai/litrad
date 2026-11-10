#!/usr/bin/env python3
"""Command line interface."""

import logging
from collections import Counter
from datetime import date
from pathlib import Path

import typer
from rich.console import Console
from sqlmodel import Session, func, select

from litrad import __version__
from litrad.config import Config, load_config
from litrad.db import Embedding, Paper, init_db
from litrad.papers import paper_link
from litrad.sources.http import HttpClient, ResponseCache

app = typer.Typer(
    help="litrad, the Literature Radar Game: paper recommendations, a reading queue and "
    "a reading game on top of your reference-manager libraries."
)

console = Console(highlight=False)


def _config_path(value: Path | None) -> Path:
    """The config file to use (see ``litrad.locations``); created on the first run."""
    from litrad.locations import locate_config

    return locate_config(value, announce=lambda msg: typer.echo(msg, err=True)).path


ConfigOpt = typer.Option(
    None,
    "--config",
    "-c",
    callback=_config_path,
    help="Path to config.yaml (default: $LITRAD_CONFIG, config/config.yaml in the current "
    "folder, else your per-user config; see 'litrad where')",
    show_default=False,
)

# set by the global --profile option
state: dict = {"profile": None}


def load_cfg(config: Path) -> Config:
    """The configuration of the selected profile.

    The profile is the one named with ``--profile``, else the current one
    (``litrad profile use``, the web UI, or the only profile there is).
    """
    from litrad import profiles
    from litrad.config import ConfigError

    base = load_config(config)
    try:
        if state["profile"]:
            slug = profiles.find(base, state["profile"])
        else:
            slug = profiles.get_current(base)
            if slug is None:
                if profiles.list_profiles(base):
                    raise ConfigError(
                        "There are several profiles. Choose one with --profile NAME or "
                        "'litrad profile use NAME' (list them: 'litrad profile list')."
                    )
                raise ConfigError(
                    "There is no profile yet. Create one with 'litrad profile create NAME' "
                    "or in the web UI ('litrad serve')."
                )
        return profiles.profile_config(base, slug)
    except ValueError as error:  # ProfilesError, or invalid values in settings.yaml
        raise ConfigError(str(error)) from error


def make_http(cfg: Config) -> HttpClient:
    s2_interval = 1.1 if cfg.api.semanticscholar_key else 3.0
    return HttpClient(
        cache=ResponseCache(cfg.paths.cache_dir / "http_cache.sqlite"),
        min_interval={
            "api.semanticscholar.org": s2_interval,
            "api.crossref.org": 0.2,
            "api.openalex.org": 0.1,
            # NCBI: 3 requests/s without key, 10 with one
            "eutils.ncbi.nlm.nih.gov": 0.11 if cfg.api.ncbi_key else 0.34,
            "www.ebi.ac.uk": 0.1,
        },
    )


def run() -> None:
    """Console entry point: expected problems become messages, not tracebacks."""
    from litrad.config import ConfigError
    from litrad.migrations import MigrationError, SchemaTooNew

    try:
        app()
    except ConfigError as error:
        console.print(f"[red]Configuration problem:[/red] {error}")
        raise SystemExit(2) from None
    except (SchemaTooNew, MigrationError) as error:
        console.print(f"[red]Database problem:[/red] {error}")
        raise SystemExit(2) from None


@app.callback()
def main(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show HTTP retries etc."),
    profile: str = typer.Option(
        None, "--profile", "-p", help="Profile (name or id) to work with; default: the current one"
    ),
):
    state["profile"] = profile
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING, format="%(message)s")


@app.command()
def version():
    """Print the version."""
    typer.echo(__version__)


@app.command()
def where(
    config: Path = typer.Option(
        None, "--config", "-c", help="Path to config.yaml", show_default=False
    ),
):
    """Show which config file and data folders are used, and why."""
    from litrad import locations
    from litrad.config import config_base_dir
    from litrad.migrations import BACKUP_FOLDER, UPGRADE_BACKUPS_KEEP

    choice = locations.find_config(config)
    rows = [("Config file", choice.path, choice.description)]
    if choice.path.exists():
        import yaml

        try:
            raw = yaml.safe_load(choice.path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            raw = {}
        raw_paths = raw.get("paths") if isinstance(raw, dict) else None
        base, rule = config_base_dir(choice.path, raw_paths if isinstance(raw_paths, dict) else {})
        rows.append(("Data folder", base, f"base of relative paths: {locations.BASE_RULES[rule]}"))
        cfg = load_config(choice.path)
        rows += [
            ("Profiles", cfg.paths.profiles_dir, "paths.profiles_dir"),
            (
                "Upgrade copies",
                cfg.paths.profiles_dir / "<profile>" / BACKUP_FOLDER,
                f"copies from before a database upgrade, newest {UPGRADE_BACKUPS_KEEP} kept",
            ),
            ("Backups", cfg.backup.dir, "backup.dir"),
            ("Cache", cfg.paths.cache_dir, "paths.cache_dir"),
        ]
    else:
        rows.append(("Data folder", locations.user_data_dir(), "per-user data folder"))
    for label, path, why in rows:
        typer.echo(f"{label + ':':<22}{path}")
        typer.echo(f"{'':<22}({why})")
    from litrad.desktop import log_file

    rows = [("Desktop app log", log_file(), "litrad app and the desktop app")]
    for label, path, why in rows:
        typer.echo(f"{label + ':':<22}{path}")
        typer.echo(f"{'':<22}({why})")
    typer.echo(
        f"Per-user folders:     config {locations.user_config_dir()}, "
        f"data {locations.user_data_dir()}"
    )


@app.command()
def ingest(
    config: Path = ConfigOpt,
    s2: bool = typer.Option(True, help="Fetch embeddings/TLDRs from Semantic Scholar"),
):
    """Update every library from its file (e.g. Paperpile's auto-export synced by rclone)."""
    from litrad.ingest.pipeline import ingest_library, refresh_libraries

    cfg = load_cfg(config)

    def nothing_to_import():
        console.print(
            "[red]No library has a file to import yet.[/red] Import an export with "
            "[bold]litrad library import FILE --new NAME[/bold] "
            "(see docs/documentation.md, Setup)."
        )
        raise typer.Exit(code=1)

    if not cfg.paths.database.exists() and not cfg.paths.bibtex.exists():
        nothing_to_import()  # without creating an empty database
    engine = init_db(cfg.paths.database)
    http = make_http(cfg)
    try:
        with Session(engine) as session:
            results = refresh_libraries(cfg, session, http, use_s2=s2)
            if not results:
                if not cfg.paths.bibtex.exists():
                    nothing_to_import()
                # first run: paths.bibtex becomes "My library"
                library_report = ingest_library(cfg, session, http, use_s2=s2, source="cli")
                results = [(None, library_report)]
    finally:
        http.close()
        engine.dispose()
    failed = False
    for library, report in results:
        if isinstance(report, str):
            console.print(f"[red]{library.name}: {report}[/red]")
            failed = True
            continue
        typer.echo("\n".join(report.lines()))
        if report.check:
            for finding in report.check.findings:
                if finding.level in ("error", "warning"):
                    typer.echo(f"CHECK: {finding.message}")
        typer.echo("")
    if failed:
        raise typer.Exit(code=1)


@app.command("check-bib")
def check_bib(
    bibtex: Path = typer.Argument(None, help="BibTeX, RIS or CSL-JSON file"),
    config: Path = ConfigOpt,
):
    """Check an export (BibTeX, RIS, CSL-JSON): does it contain what litrad needs?"""
    from litrad.ingest.check import FIELDS, INSTRUCTIONS, check_file
    from litrad.ingest.formats import FORMATS

    cfg = load_cfg(config)
    path = bibtex or cfg.paths.bibtex
    result = check_file(path, cfg.library.ignore_labels)
    kind = f" ({FORMATS[result.format]})" if result.format else ""
    console.print(f"[bold]{path}[/bold]: {result.entries} entries{kind}")
    for rule in FIELDS:
        if rule.name in result.coverage:
            console.print(f"  {rule.label:<28} {result.coverage[rule.name]:>5.0%}")
    icons = {
        "error": "[red]ERROR[/red]",
        "warning": "[yellow]WARNING[/yellow]",
        "info": "INFO",
        "ok": "[green]OK[/green]",
    }
    for finding in result.findings:
        console.print(f"{icons[finding.level]}: {finding.message}")
    if not result.ok or any(f.level == "warning" for f in result.findings):
        console.print("\nHow to export:")
        for number, step in enumerate(INSTRUCTIONS, start=1):
            console.print(f"  {number}. {step}")
    if not result.ok:
        raise typer.Exit(code=1)


@app.command()
def status(config: Path = ConfigOpt, top: int = typer.Option(10, help="Number of folders to list")):
    """Summarise your libraries (all imported libraries together, then each)."""
    cfg = load_cfg(config)
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        papers = session.exec(select(Paper).where(Paper.in_library)).all()
        from litrad.ingest.pipeline import last_ingest

        last = last_ingest(session)
        n_embedded = session.exec(
            select(func.count(func.distinct(Embedding.paper_id)))
            .join(Paper, Paper.id == Embedding.paper_id)
            .where(Paper.in_library)
        ).one()
    engine.dispose()
    folders = Counter(folder for paper in papers for folder in paper.folders)
    typer.echo(f"Library papers:  {len(papers)}")
    if last is not None:
        age = (date.today() - last.at.date()).days
        typer.echo(f"  last import:   {last.at.isoformat()} ({age} days ago, {last.source})")
    typer.echo(f"  with DOI:      {sum(bool(p.doi) for p in papers)}")
    typer.echo(f"  with metrics:  {sum(p.enriched_at is not None for p in papers)}")
    typer.echo(f"  with abstract: {sum(bool(p.abstract) for p in papers)}")
    typer.echo(f"  embedded:      {n_embedded}")
    typer.echo("Top folders:")
    for folder, count in folders.most_common(top):
        typer.echo(f"  {count:>4}  {folder}")
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        from litrad import libraries

        rows = libraries.summaries(session)
    engine.dispose()
    if rows:
        typer.echo("Libraries:")
        for row in rows:
            mark = " (for recommendations)" if row.library.use_for_recommendations else ""
            typer.echo(f"  {row.papers:>4}  {row.library.name}{mark}")


def short_authors(authors: list[str]) -> str:
    if not authors:
        return "unknown authors"
    first = authors[0].split(",")[0]
    return first if len(authors) == 1 else f"{first} et al."


def paper_line(paper: Paper) -> str:
    year = paper.pub_date.isoformat() if paper.pub_date else (paper.year or "n.d.")
    venue = f" · {paper.venue}" if paper.venue else ""
    return f"{short_authors(paper.authors)} ({year}){venue}"


@app.command()
def recommend(
    config: Path = ConfigOpt,
    new: bool = typer.Option(False, "--new", "--refresh", help="Get new recommendations"),
    s2: bool = typer.Option(True, help="Use Semantic Scholar (recommendations, embeddings)"),
):
    """Show your latest recommendations (new ones with --new, or when there are none)."""
    from litrad.recommend.engine import (
        batch_items,
        build_recommendations,
        days_since_last_batch,
        latest_batch,
    )

    cfg = load_cfg(config)
    engine = init_db(cfg.paths.database)
    try:
        with Session(engine) as session:
            batch = None if new else latest_batch(session)
            if batch is not None:
                age = days_since_last_batch(session)
                if cfg.selection.reminder_days and age >= cfg.selection.reminder_days:
                    console.print(
                        f"[yellow]These recommendations are {age} days old; "
                        "get new ones with 'litrad recommend --new'.[/yellow]"
                    )
            if batch is None:
                http = make_http(cfg)
                try:
                    with console.status("Searching for candidates (about a minute)..."):
                        batch = build_recommendations(cfg, session, http, use_s2=s2)
                finally:
                    http.close()
            items = batch_items(session, batch)
            show_batch(batch, items)
    finally:
        engine.dispose()


def show_batch(batch, items) -> None:
    """Print a recommendation set with reasons and links."""
    console.print(
        f"[bold]Recommendations from {batch.created_at.date().isoformat()}[/bold] "
        f"[dim]({batch.n_candidates} candidates checked)[/dim]"
    )
    if not items:
        console.print("No eligible papers found. Check your topics or try --new later.")
    for rec, paper in items:
        console.print(f"\n[bold cyan]r{rec.rank}[/bold cyan]  [bold]{paper.title}[/bold]")
        console.print(f"     {paper_line(paper)}")
        console.print(f"     [dim]{rec.reason}[/dim]")
        console.print(f"     [blue]{paper_link(paper)}[/blue]")
    for warning in batch.warnings:
        console.print(f"[yellow]WARNING: {warning}[/yellow]")
    if items:
        console.print(
            "\nPut one on your reading queue with: [bold]litrad queue add r1[/bold]. "
            "How deeply you read it is up to you; deeper reads earn more XP."
        )


def interactive() -> bool:
    """True when a person can answer questions (not systemd, cron or a pipe)."""
    import sys

    return sys.stdin.isatty()


def choose_network(cfg: Config, config: Path) -> str:
    """Ask where to serve (Enter keeps web.host); optionally save the answer."""
    from litrad.web.network import interfaces
    from litrad.web.settings import write_values

    options = [("local", "only this computer (127.0.0.1)"), ("all", "all networks")]
    options += [
        (i.name, f"{i.kind}: {i.address}") for i in interfaces() if i.kind != "this computer only"
    ]
    current = cfg.web.host if cfg.web.host in [name for name, _ in options] else "local"
    if cfg.web.host in ("127.0.0.1", "localhost"):
        current = "local"
    elif cfg.web.host == "0.0.0.0":
        current = "all"
    console.print("[bold]Where should the web UI be reachable?[/bold]")
    for number, (name, text) in enumerate(options, start=1):
        note = "" if name == "local" or cfg.web.password_hash else " [dim](needs a password)[/dim]"
        mark = " [green]← default[/green]" if name == current else ""
        console.print(f"  {number}. {name:<8} {text}{note}{mark}")
    if not cfg.web.password_hash:
        console.print("  [dim]Network access needs a password: litrad set-password[/dim]")
    default = [name for name, _ in options].index(current) + 1
    # a plain int prompt: newer typer versions no longer accept click's IntRange
    while True:
        picked = typer.prompt("Choose a number", type=int, default=default)
        if 1 <= picked <= len(options):
            break
        console.print(f"[red]Choose a number from 1 to {len(options)}.[/red]")
    spec = options[picked - 1][0]
    if spec != current and typer.confirm(
        f"Make '{spec}' the default (web.host in {config})?", default=True
    ):
        write_values(config, {"web": {"host": spec}})
        console.print("Saved.")
    return spec


@app.command()
def serve(
    config: Path = ConfigOpt,
    network: str = typer.Option(
        None,
        "--network",
        "-n",
        "--host",
        help="Where to listen: local, all, an interface name (eth0, wlan0) or an IP address. "
        "Without it, serve asks (Enter keeps web.host from the config).",
    ),
    port: int = typer.Option(None, help="Port (default: web.port from the config)"),
    choose: bool = typer.Option(
        True,
        "--choose/--no-choose",
        help="Ask where to serve (default). --no-choose uses web.host without asking; "
        "serve never asks when it is not run in a terminal.",
    ),
    list_networks: bool = typer.Option(
        False, "--list-networks", help="Show the available networks and exit"
    ),
    no_login: bool = typer.Option(
        False, "--no-login", help="Allow network access without a password (not recommended)"
    ),
):
    """Start the web server and print its address. No browser is opened."""
    import uvicorn

    from litrad.web.app import create_app
    from litrad.web.network import NetworkError, interfaces, local_names, resolve

    cfg = load_config(config)
    port = port or cfg.web.port
    if list_networks:
        console.print("Networks of this computer (use the name with --network or web.host):")
        console.print("  local    this computer only (127.0.0.1)")
        console.print("  all      every network below")
        for interface in interfaces():
            if interface.kind != "this computer only":
                console.print(f"  {interface.name:<8} {interface.kind}: {interface.address}")
        return
    if network:
        spec = network
    elif choose and interactive():
        spec = choose_network(cfg, config)
    else:
        spec = cfg.web.host
    try:
        binding = resolve(spec, port)
    except NetworkError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(code=1) from None
    if not binding.local_only and not cfg.web.password_hash and not no_login:
        console.print(
            f"[red]Refusing to serve on {binding.description} without a password:[/red] anyone "
            "in that network could read and change your data.\n"
            "Set one with [bold]litrad set-password[/bold], or use --network local."
        )
        raise typer.Exit(code=1)
    if not binding.local_only and not cfg.web.password_hash:
        console.print("[yellow]WARNING: serving to the network without a login.[/yellow]")

    console.print(f"litrad web server on [bold]{binding.description}[/bold]")
    for url in binding.urls:
        console.print(f"  open [bold]{url}[/bold]")
    if binding.host not in ("127.0.0.1", "0.0.0.0"):
        console.print(
            f"  [dim]only reachable via {binding.host}, also from this computer itself[/dim]"
        )
    elif binding.host == "0.0.0.0":
        console.print(f"  [dim]on this computer also http://127.0.0.1:{port}/[/dim]")
    if not binding.local_only and cfg.web.password_hash:
        console.print("  login required (password set with litrad set-password)")
    if cfg.backup.interval_hours > 0:
        from litrad.backup import start_backup_thread

        console.print(
            f"Backups every {cfg.backup.interval_hours:g} h to {cfg.backup.dir} "
            f"(keeping {cfg.backup.keep})"
        )
        start_backup_thread(config.resolve(), log=lambda msg: console.print(f"[dim]{msg}[/dim]"))
    console.print("Stop with Ctrl+C.")
    uvicorn.run(
        create_app(config.resolve(), trusted_names=local_names()),
        host=binding.host,
        port=port,
        log_level="warning",
    )


@app.command("app")
def desktop_app(
    config: Path = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to config.yaml (default: as for every command)",
        show_default=False,
    ),
    port: int = typer.Option(None, help="Port on 127.0.0.1 (default: 47290 if free, else any)"),
    browser: bool = typer.Option(
        True, "--browser/--no-browser", help="Open the browser (also off: LITRAD_NO_BROWSER=1)"
    ),
):
    """Desktop mode: start litrad on this computer only and open it in your browser.

    Quit with Quit in the sidebar or Ctrl+C. A second start opens the running one.
    """
    from litrad import desktop

    raise typer.Exit(code=desktop.run(config, port, browser))


@app.command("set-password")
def set_password(
    config: Path = ConfigOpt,
    remove: bool = typer.Option(False, "--remove", help="Remove the password (no login)"),
    password_file: Path = typer.Option(
        None,
        "--password-file",
        help="Read the new password from the first line of this file instead of asking "
        "(for scripts and Docker secrets)",
    ),
):
    """Set the password for the web UI (stored as a salted hash in config.yaml)."""
    from litrad.web.auth import hash_password
    from litrad.web.settings import write_values

    if remove:
        write_values(config, {"web": {"password_hash": "n/a"}})
        console.print("Password removed. The UI now only starts on this computer (127.0.0.1).")
        return
    if password_file is not None:
        try:
            lines = password_file.read_text(encoding="utf-8").splitlines()
        except OSError as error:
            console.print(f"[red]Cannot read {password_file}: {error.strerror}[/red]")
            raise typer.Exit(code=1) from None
        password = lines[0] if lines else ""
    else:
        password = typer.prompt("New password", hide_input=True, confirmation_prompt=True)
    try:
        stored = hash_password(password)
    except ValueError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(code=1) from None
    write_values(config, {"web": {"password_hash": stored}})
    console.print(
        "Password set; existing logins are signed out. Start [bold]litrad serve[/bold] "
        "and choose the network to serve on."
    )


# reading queue, reading log and score commands register themselves on ``app``
from litrad import cli_data, cli_library, cli_reading  # noqa: E402, F401, I001


if __name__ == "__main__":
    run()
