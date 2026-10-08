#!/usr/bin/env python3
"""Command line interface."""

import logging
from collections import Counter
from datetime import date
from pathlib import Path

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
            report = ingest_library(cfg, session, http, use_s2=s2)
    finally:
        http.close()
        engine.dispose()
    typer.echo("\n".join(report.lines()))


@app.command()
def status(config: Path = ConfigOpt, top: int = typer.Option(10, help="Number of folders to list")):
    """Summarise the library stored in the database."""
    cfg = load_config(config)
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        papers = session.exec(select(Paper).where(Paper.in_library)).all()
        n_embedded = session.exec(
            select(func.count(func.distinct(Embedding.paper_id)))
            .join(Paper, Paper.id == Embedding.paper_id)
            .where(Paper.in_library)
        ).one()
    engine.dispose()
    folders = Counter(folder for paper in papers for folder in paper.folders)
    typer.echo(f"Library papers:  {len(papers)}")
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
    from litresearch.db import Category

    console.print(
        f"[bold]Recommendations for the week of {batch.week_start.isoformat()}[/bold] "
        f"[dim]({batch.n_candidates} candidates checked)[/dim]"
    )
    if not items:
        console.print("No eligible papers found. Check your topics or try --refresh later.")
    for category, label in ((Category.detail, "Read in detail"), (Category.skim, "Skim")):
        group = [(r, p) for r, p in items if r.category == category]
        if group:
            console.print(f"\n[bold underline]{label}[/bold underline]")
        for rec, paper in group:
            console.print(f"[bold cyan]r{rec.rank}[/bold cyan]  [bold]{paper.title}[/bold]")
            console.print(f"     {paper_line(paper)}")
            console.print(f"     [dim]{rec.reason}[/dim]")
            console.print(f"     [blue]{paper_link(paper)}[/blue]")
    for warning in batch.warnings:
        console.print(f"[yellow]WARNING: {warning}[/yellow]")
    if items:
        console.print("\nPut one on your reading queue with: [bold]litresearch queue add r1[/bold]")


LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


def lan_address() -> str | None:
    """This machine's address in the local network (no packets are sent)."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("192.0.2.1", 9))  # TEST-NET address, never routed
            return probe.getsockname()[0]
        except OSError:
            return None


@app.command()
def serve(
    config: Path = ConfigOpt,
    host: str = typer.Option(
        None,
        help="Interface to listen on (default: web.host from the config). "
        "127.0.0.1 = this computer only, 0.0.0.0 = whole local network.",
    ),
    port: int = typer.Option(None, help="Port (default: web.port from the config)"),
    browser: bool = typer.Option(True, help="Open the UI in your browser"),
    no_login: bool = typer.Option(
        False, "--no-login", help="Allow network access without a password (not recommended)"
    ),
):
    """Start the web UI (on this computer or for your local network)."""
    import threading
    import webbrowser

    import uvicorn

    from litresearch.web.app import create_app

    cfg = load_config(config)
    host = host or cfg.web.host
    port = port or cfg.web.port
    local_only = host in LOCAL_HOSTS
    if not local_only and not cfg.web.password_hash and not no_login:
        console.print(
            f"[red]Refusing to listen on {host} without a password:[/red] anyone in your "
            "network could read and change your data.\n"
            "Set one with [bold]litresearch set-password[/bold], or use --host 127.0.0.1."
        )
        raise typer.Exit(code=1)
    if not local_only and not cfg.web.password_hash:
        console.print(f"[yellow]WARNING: listening on {host} without a login.[/yellow]")
    url = (
        f"http://127.0.0.1:{port}/"
        if host in ("0.0.0.0", "::") or local_only
        else (f"http://{host}:{port}/")
    )
    console.print(f"litresearch UI on this computer: [bold]{url}[/bold]")
    if host in ("0.0.0.0", "::"):
        address = lan_address()
        if address:
            console.print(
                f"  from other devices in your network: [bold]http://{address}:{port}/[/bold]"
            )
    if not local_only and cfg.web.password_hash:
        console.print("  login required (password set with litresearch set-password)")
    console.print("Stop with Ctrl+C.")
    if browser:
        threading.Timer(1.2, webbrowser.open, args=(url,)).start()
    if cfg.backup.interval_hours > 0:
        from litresearch.backup import start_backup_thread

        console.print(
            f"Backups every {cfg.backup.interval_hours:g} h to {cfg.backup.dir} "
            f"(keeping {cfg.backup.keep})"
        )
        start_backup_thread(config.resolve(), log=lambda msg: console.print(f"[dim]{msg}[/dim]"))
    uvicorn.run(create_app(config.resolve()), host=host, port=port, log_level="warning")


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
