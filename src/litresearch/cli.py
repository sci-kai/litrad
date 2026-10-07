#!/usr/bin/env python3
"""Command line interface."""

from pathlib import Path

import typer

from litresearch import __version__
from litresearch.config import load_config
from litresearch.db import init_db

app = typer.Typer(help="Personal literature radar on top of a Paperpile library.")

ConfigOpt = typer.Option(Path("config/config.yaml"), "--config", "-c", help="Path to config.yaml")


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
    init_db(cfg.paths.database)
    typer.echo(f"Database ready: {cfg.paths.database}")


@app.command()
def ingest(config: Path = ConfigOpt):
    """Import the Paperpile BibTeX export and enrich it with metadata."""
    _not_yet(2)


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
