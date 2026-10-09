#!/usr/bin/env python3
"""Command line interface."""

import logging
from collections import Counter
from datetime import date
from pathlib import Path

import click
import typer
from rich.console import Console
from sqlmodel import Session, func, select

from litresearch import __version__
from litresearch.config import Config, load_config
from litresearch.db import Embedding, Paper, init_db
from litresearch.sources.http import HttpClient, ResponseCache

app = typer.Typer(help="Personal literature radar on top of a Paperpile library.")

console = Console(highlight=False)

ConfigOpt = typer.Option(Path("config/config.yaml"), "--config", "-c", help="Path to config.yaml")


def make_http(cfg: Config) -> HttpClient:
    s2_interval = 1.1 if cfg.api.semanticscholar_key else 3.0
    return HttpClient(
        cache=ResponseCache(cfg.paths.cache_dir / "http_cache.sqlite"),
        contact_email=cfg.api.contact_email,
        min_interval={
            "api.semanticscholar.org": s2_interval,
            "api.crossref.org": 0.2,
            "api.openalex.org": 0.1,
        },
    )


def run() -> None:
    """Console entry point: expected problems become messages, not tracebacks."""
    from litresearch.config import ConfigError

    try:
        app()
    except ConfigError as error:
        console.print(f"[red]Configuration problem:[/red] {error}")
        raise SystemExit(2) from None


@app.callback()
def main(verbose: bool = typer.Option(False, "--verbose", "-v", help="Show HTTP retries etc.")):
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING, format="%(message)s")


def _not_yet(milestone: int):
    typer.echo(f"Not implemented yet (milestone {milestone}).", err=True)
    raise typer.Exit(code=1)


@app.command()
def version():
    """Print the version."""
    typer.echo(__version__)


@app.command("db-init")
def db_init(config: Path = ConfigOpt):
    """Create the SQLite database and tables."""
    cfg = load_config(config)
    init_db(cfg.paths.database).dispose()
    typer.echo(f"Database ready: {cfg.paths.database}")


@app.command()
def ingest(
    config: Path = ConfigOpt,
    s2: bool = typer.Option(True, help="Fetch embeddings/TLDRs from Semantic Scholar"),
):
    """Import the Paperpile BibTeX export and enrich it with metadata."""
    from litresearch.ingest.pipeline import ingest_library

    cfg = load_config(config)
    if not cfg.paths.bibtex.exists():
        console.print(
            f"[red]No BibTeX file at {cfg.paths.bibtex}.[/red] Export your Paperpile library "
            "(see docs/setup.md) or fix paths.bibtex."
        )
        raise typer.Exit(code=1)
    engine = init_db(cfg.paths.database)
    http = make_http(cfg)
    try:
        with Session(engine) as session:
            report = ingest_library(cfg, session, http, use_s2=s2, source="cli")
    finally:
        http.close()
        engine.dispose()
    typer.echo("\n".join(report.lines()))
    if report.check:
        for finding in report.check.findings:
            if finding.level in ("error", "warning"):
                typer.echo(f"CHECK: {finding.message}")


@app.command("check-bib")
def check_bib(
    bibtex: Path = typer.Argument(None, help="BibTeX file (default: paths.bibtex)"),
    config: Path = ConfigOpt,
):
    """Check a Paperpile BibTeX export: does it contain what litresearch needs?"""
    from litresearch.ingest.check import FIELDS, INSTRUCTIONS, check_bibtex

    cfg = load_config(config)
    path = bibtex or cfg.paths.bibtex
    result = check_bibtex(path, cfg.library.ignore_labels)
    console.print(f"[bold]{path}[/bold]: {result.entries} entries")
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
        console.print("\nHow to export from Paperpile:")
        for number, step in enumerate(INSTRUCTIONS, start=1):
            console.print(f"  {number}. {step}")
    if not result.ok:
        raise typer.Exit(code=1)


@app.command()
def status(config: Path = ConfigOpt, top: int = typer.Option(10, help="Number of folders to list")):
    """Summarise the library stored in the database."""
    cfg = load_config(config)
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        papers = session.exec(select(Paper).where(Paper.in_library)).all()
        from litresearch.ingest.pipeline import last_ingest

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


def short_authors(authors: list[str]) -> str:
    if not authors:
        return "unknown authors"
    first = authors[0].split(",")[0]
    return first if len(authors) == 1 else f"{first} et al."


def paper_line(paper: Paper) -> str:
    year = paper.pub_date.isoformat() if paper.pub_date else (paper.year or "n.d.")
    venue = f" · {paper.venue}" if paper.venue else ""
    return f"{short_authors(paper.authors)} ({year}){venue}"


def paper_link(paper: Paper) -> str:
    if paper.doi:
        return f"https://doi.org/{paper.doi}"
    if paper.pmid:
        return f"https://pubmed.ncbi.nlm.nih.gov/{paper.pmid}"
    return paper.url or ""


@app.command()
def recommend(
    config: Path = ConfigOpt,
    refresh: bool = typer.Option(False, help="Build a new batch even if this week has one"),
    s2: bool = typer.Option(True, help="Use Semantic Scholar (recommendations, embeddings)"),
):
    """Show this week's top papers (built on first call each week)."""
    from litresearch.recommend.engine import batch_items, build_recommendations, latest_batch

    cfg = load_config(config)
    engine = init_db(cfg.paths.database)
    try:
        with Session(engine) as session:
            batch = None if refresh else latest_batch(session, date.today())
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
    console.print(
        f"[bold]Recommendations for the week of {batch.week_start.isoformat()}[/bold] "
        f"[dim]({batch.n_candidates} candidates checked)[/dim]"
    )
    if not items:
        console.print("No eligible papers found. Check your topics or try --refresh later.")
    for rec, paper in items:
        console.print(f"\n[bold cyan]r{rec.rank}[/bold cyan]  [bold]{paper.title}[/bold]")
        console.print(f"     {paper_line(paper)}")
        console.print(f"     [dim]{rec.reason}[/dim]")
        console.print(f"     [blue]{paper_link(paper)}[/blue]")
    for warning in batch.warnings:
        console.print(f"[yellow]WARNING: {warning}[/yellow]")
    if items:
        console.print(
            "\nPut one on your reading queue with: [bold]litresearch queue add r1[/bold]. "
            "How deeply you read it is up to you; deeper reads earn more XP."
        )


def choose_network(cfg: Config, config: Path) -> str:
    """Ask where to serve; optionally save the answer as the default."""
    from litresearch.web.network import interfaces
    from litresearch.web.settings import write_values

    options = [("local", "only this computer (127.0.0.1)"), ("all", "all networks")]
    options += [
        (i.name, f"{i.kind}: {i.address}") for i in interfaces() if i.kind != "this computer only"
    ]
    console.print("[bold]Where should the web UI be reachable?[/bold]")
    for number, (name, text) in enumerate(options, start=1):
        console.print(f"  {number}. {name:<8} {text}")
    picked = typer.prompt("Choose a number", type=click.IntRange(1, len(options)))
    spec = options[picked - 1][0]
    if typer.confirm(f"Save '{spec}' as the default (web.host in {config})?", default=True):
        write_values(config, {"web": {"host": spec}})
        console.print("Saved. Next time just run [bold]litresearch serve[/bold].")
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
        "Default: web.host from the config.",
    ),
    port: int = typer.Option(None, help="Port (default: web.port from the config)"),
    choose: bool = typer.Option(False, "--choose", help="Pick the network interactively"),
    list_networks: bool = typer.Option(
        False, "--list-networks", help="Show the available networks and exit"
    ),
    no_login: bool = typer.Option(
        False, "--no-login", help="Allow network access without a password (not recommended)"
    ),
):
    """Start the web server and print its address. No browser is opened."""
    import uvicorn

    from litresearch.web.app import create_app
    from litresearch.web.network import NetworkError, interfaces, resolve

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
    spec = choose_network(cfg, config) if choose else (network or cfg.web.host)
    try:
        binding = resolve(spec, port)
    except NetworkError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(code=1) from None
    if not binding.local_only and not cfg.web.password_hash and not no_login:
        console.print(
            f"[red]Refusing to serve on {binding.description} without a password:[/red] anyone "
            "in that network could read and change your data.\n"
            "Set one with [bold]litresearch set-password[/bold], or use --network local."
        )
        raise typer.Exit(code=1)
    if not binding.local_only and not cfg.web.password_hash:
        console.print("[yellow]WARNING: serving to the network without a login.[/yellow]")

    console.print(f"litresearch web server on [bold]{binding.description}[/bold]")
    for url in binding.urls:
        console.print(f"  open [bold]{url}[/bold]")
    if binding.host not in ("127.0.0.1", "0.0.0.0"):
        console.print(
            f"  [dim]only reachable via {binding.host}, also from this computer itself[/dim]"
        )
    elif binding.host == "0.0.0.0":
        console.print(f"  [dim]on this computer also http://127.0.0.1:{port}/[/dim]")
    if not binding.local_only and cfg.web.password_hash:
        console.print("  login required (password set with litresearch set-password)")
    if cfg.backup.interval_hours > 0:
        from litresearch.backup import start_backup_thread

        console.print(
            f"Backups every {cfg.backup.interval_hours:g} h to {cfg.backup.dir} "
            f"(keeping {cfg.backup.keep})"
        )
        start_backup_thread(config.resolve(), log=lambda msg: console.print(f"[dim]{msg}[/dim]"))
    console.print("Stop with Ctrl+C.")
    uvicorn.run(create_app(config.resolve()), host=binding.host, port=port, log_level="warning")


@app.command("set-password")
def set_password(
    config: Path = ConfigOpt,
    remove: bool = typer.Option(False, "--remove", help="Remove the password (no login)"),
):
    """Set the password for the web UI (stored as a salted hash in config.yaml)."""
    from litresearch.web.auth import hash_password
    from litresearch.web.settings import write_values

    if remove:
        write_values(config, {"web": {"password_hash": "n/a"}})
        console.print("Password removed. The UI now only starts on this computer (127.0.0.1).")
        return
    password = typer.prompt("New password", hide_input=True, confirmation_prompt=True)
    try:
        stored = hash_password(password)
    except ValueError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(code=1) from None
    write_values(config, {"web": {"password_hash": stored}})
    console.print(
        "Password set; existing logins are signed out. Start the UI for your network with "
        "[bold]litresearch serve --host 0.0.0.0[/bold] (or set web.host: 0.0.0.0)."
    )


@app.command()
def export(config: Path = ConfigOpt):
    """Export reading lists and notes to the Obsidian vault."""
    _not_yet(6)


@app.command("sync-notes")
def sync_notes(config: Path = ConfigOpt):
    """Import note and status edits from the Obsidian vault."""
    _not_yet(6)


@app.command("export-paperpile")
def export_paperpile(config: Path = ConfigOpt):
    """Write a BibTeX file of recommended/read papers for import into Paperpile."""
    _not_yet(6)


# reading queue, reading log and score commands register themselves on ``app``
from litresearch import cli_data, cli_reading  # noqa: E402, F401, I001


if __name__ == "__main__":
    run()
