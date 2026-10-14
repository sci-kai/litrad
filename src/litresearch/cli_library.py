"""CLI commands for libraries: list, create, import, export, edit, delete."""

from datetime import date
from pathlib import Path

import typer
from sqlmodel import Session

from litresearch import export, libraries
from litresearch.cli import ConfigOpt, app, console, load_cfg, make_http
from litresearch.db import init_db
from litresearch.ingest.check import check_file
from litresearch.ingest.formats import FORMATS
from litresearch.ingest.pipeline import import_into
from litresearch.reading import queue_items

library_app = typer.Typer(
    help="Libraries: import exports from reference managers, list, export, edit, delete."
)
app.add_typer(library_app, name="library")


def _fail(message: str):
    console.print(f"[red]{message}[/red]")
    raise typer.Exit(code=1)


def _open(config: Path):
    cfg = load_cfg(config)
    engine = init_db(cfg.paths.database)
    session = Session(engine)
    libraries.migrate(session, cfg)
    return cfg, engine, session


@library_app.command("list")
def library_list(config: Path = ConfigOpt):
    """All libraries with size, reading progress and file."""
    cfg, engine, session = _open(config)
    try:
        rows = libraries.summaries(session)
        for row in rows:
            lib = row.library
            marks = ["🎯 recommendations" if lib.use_for_recommendations else "", lib.kind]
            details = ", ".join(m for m in marks if m)
            console.print(
                f"[bold]{lib.name}[/bold] [dim](#{lib.id}, {details})[/dim]: "
                f"{row.papers} papers, {row.read} read"
                + (f", updated {row.last_run.at.date().isoformat()}" if row.last_run else "")
            )
            if lib.description:
                console.print(f"   {lib.description}")
            if lib.source_path:
                console.print(f"   [dim]file: {lib.source_path}[/dim]")
        queue = queue_items(session)
        console.print(
            f"[bold]{libraries.QUEUE_NAME}[/bold] [dim](always current)[/dim]: "
            f"{len(queue)}/{cfg.queue.max_size} papers"
        )
        if not rows:
            console.print(
                "No libraries yet. Import one: litresearch library import FILE --new NAME"
            )
    finally:
        session.close()
        engine.dispose()


@library_app.command("import")
def library_import(
    file: Path = typer.Argument(..., help="BibTeX, RIS or CSL-JSON export", exists=True),
    new: str = typer.Option(None, "--new", help="Create a new library with this name"),
    to: str = typer.Option(None, "--to", help="Import into this existing library (name or id)"),
    replace: bool = typer.Option(
        False, "--replace", help="With --to: the file is the complete library (default: add)"
    ),
    description: str = typer.Option("", help="Description of a new library"),
    check: bool = typer.Option(False, "--check", help="Only check the file, change nothing"),
    s2: bool = typer.Option(True, help="Fetch embeddings/TLDRs from Semantic Scholar"),
    config: Path = ConfigOpt,
):
    """Import an export into a new library (--new NAME) or an existing one (--to NAME).

    The file stays where it is: `litresearch ingest` reads it again for libraries
    created with --new or updated with --replace (e.g. a file synced by rclone).
    """
    cfg, engine, session = _open(config)
    try:
        result = check_file(file, cfg.library.ignore_labels)
        kind = FORMATS.get(result.format or "", "?")
        console.print(f"[bold]{file}[/bold]: {result.entries} entries ({kind})")
        for finding in result.findings:
            console.print(f"  {finding.level.upper()}: {finding.message}")
        if not result.ok:
            _fail("Not imported. What an export must contain: docs/documentation.md, Setup.")
        if check:
            return
        if bool(new) == bool(to):
            _fail("Choose either --new NAME or --to LIBRARY")
        try:
            if new:
                library = libraries.create(session, new, description)
                mode = "new"
            else:
                library = libraries.find(session, to)
                if library.kind != libraries.IMPORTED:
                    _fail(f"'{library.name}' is kept by litresearch; import into another library")
                mode = "replace" if replace else "add"
        except libraries.LibraryError as error:
            _fail(str(error))
        http = make_http(cfg)
        try:
            report = import_into(cfg, session, http, library, file, mode, "cli", s2, result.format)
        finally:
            http.close()
        typer.echo("\n".join(report.lines()))
    finally:
        session.close()
        engine.dispose()


@library_app.command("export")
def library_export(
    name: str = typer.Argument(..., help='Library name or id, or "queue" for the reading queue'),
    target: Path = typer.Argument(None, help="File to write (default: <name>_<date>.<ext>)"),
    fmt: str = typer.Option("bibtex", "--format", "-f", help="bibtex or ris"),
    status: bool = typer.Option(
        False, "--status", help="Add reading status and rating as keywords"
    ),
    config: Path = ConfigOpt,
):
    """Export a library (or the reading queue) as BibTeX or RIS."""
    if fmt not in export.FORMATS:
        _fail(f"--format must be one of {', '.join(export.FORMATS)}")
    cfg, engine, session = _open(config)
    try:
        if name.lower() in ("queue", libraries.QUEUE_NAME.lower()):
            label = libraries.QUEUE_NAME
            items = export.paper_items(session, [paper for _, paper in queue_items(session)])
        else:
            try:
                library = libraries.find(session, name)
            except libraries.LibraryError as error:
                _fail(str(error))
            label = library.name
            items = export.library_items(session, library)
        target = target or Path(export.file_name(label, fmt, date.today()))
        target.write_text(export.render(items, fmt, status), encoding="utf-8")
        console.print(f"Exported {len(items)} papers of '{label}' to {target}")
    finally:
        session.close()
        engine.dispose()


@library_app.command("edit")
def library_edit(
    name: str = typer.Argument(..., help="Library name or id"),
    rename: str = typer.Option(None, "--name", help="New name"),
    description: str = typer.Option(None, help="New description"),
    recommendations: bool = typer.Option(
        None,
        "--recommendations/--no-recommendations",
        help="Use (or not) this library's papers for the recommendation similarity",
    ),
    config: Path = ConfigOpt,
):
    """Rename a library, describe it, or choose whether it feeds the recommendations."""
    cfg, engine, session = _open(config)
    try:
        try:
            library = libraries.find(session, name)
            libraries.update(session, library, rename, description, recommendations)
        except libraries.LibraryError as error:
            _fail(str(error))
        session.commit()
        console.print(f"Saved '{library.name}'.")
    finally:
        session.close()
        engine.dispose()


@library_app.command("delete")
def library_delete(
    name: str = typer.Argument(..., help="Library name or id"),
    yes: bool = typer.Option(False, "--yes", help="Do not ask for confirmation"),
    config: Path = ConfigOpt,
):
    """Delete a library; its papers stay in your other libraries and your history."""
    cfg, engine, session = _open(config)
    try:
        try:
            library = libraries.find(session, name)
        except libraries.LibraryError as error:
            _fail(str(error))
        if not yes:
            typer.confirm(f"Delete the library '{library.name}'?", abort=True)
        count = libraries.delete(session, library)
        session.commit()
        console.print(f"Deleted '{name}' ({count} papers).")
    finally:
        session.close()
        engine.dispose()
