#!/usr/bin/env python3
"""Command line interface."""

import logging
from collections import Counter
from pathlib import Path

import typer
from sqlmodel import Session, func, select

from litresearch import __version__
from litresearch.config import Config, load_config
from litresearch.db import Embedding, Paper, init_db
from litresearch.sources.http import HttpClient, ResponseCache

app = typer.Typer(help="Personal literature radar on top of a Paperpile library.")

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


@app.command()
def run(config: Path = ConfigOpt, dry_run: bool = False):
    """Build a new reading list."""
    _not_yet(3)


@app.command()
def serve(config: Path = ConfigOpt):
    """Start the web app."""
    _not_yet(4)


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


if __name__ == "__main__":
    app()
