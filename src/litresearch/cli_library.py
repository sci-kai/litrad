"""CLI commands for libraries (list, import, export, edit, use, delete) and for the
example papers of Semantic Scholar's recommendations (`litresearch examples`)."""

from datetime import date
from pathlib import Path

import typer
from sqlmodel import Session

from litresearch import examples, export, libraries
from litresearch.cli import ConfigOpt, app, console, load_cfg, make_http, paper_line
from litresearch.config import load_config as load_cfg_base
from litresearch.db import init_db
from litresearch.ingest.check import check_file
from litresearch.ingest.formats import FORMATS
from litresearch.ingest.pipeline import import_into
from litresearch.reading import REF_HELP, ReadingError, fetch_paper, find_paper, queue_items

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
            if library.kind in libraries.EXAMPLE_KINDS:
                examples.refresh(session, cfg)
                session.commit()
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


@library_app.command("use")
def library_use(
    names: list[str] = typer.Argument(..., help="Library names or ids"),
    off: bool = typer.Option(False, "--off", help="Stop using these libraries"),
    config: Path = ConfigOpt,
):
    """Use libraries for recommendations (similarity and positive-example rules), or not."""
    cfg, engine, session = _open(config)
    try:
        try:
            chosen = [libraries.find(session, name) for name in names]
            for library in chosen:
                libraries.update(session, library, use_for_recommendations=not off)
        except libraries.LibraryError as error:
            _fail(str(error))
        examples.refresh(session, cfg)
        session.commit()
        used = [
            lib.name
            for lib in libraries.all_libraries(session)
            if lib.use_for_recommendations and lib.kind not in libraries.EXAMPLE_KINDS
        ]
        console.print("Used for recommendations: " + (", ".join(used) or "none"))
        if not used:
            console.print(
                "[yellow]No library is used: the similarity to your library is 0 and the "
                "library rules of the positive examples find nothing.[/yellow]"
            )
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
        try:
            count = libraries.delete(session, library)
        except libraries.LibraryError as error:
            _fail(str(error))
        examples.refresh(session, cfg)
        session.commit()
        console.print(f"Deleted '{name}' ({count} papers).")
    finally:
        session.close()
        engine.dispose()


# --- example papers for Semantic Scholar ---------------------------------------------

examples_app = typer.Typer(
    help="Example papers for Semantic Scholar's recommendations: list, add, remove, rules.",
    invoke_without_command=True,
)
app.add_typer(examples_app, name="examples")

KIND_HELP = "positive or negative"


def _kind(kind: str) -> str:
    kind = kind.strip().lower()
    if kind not in examples.KINDS:
        _fail(f"Choose 'positive' or 'negative', not '{kind}'")
    return kind


def _show(cfg, session, kind: str) -> None:
    lists = examples.refresh(session, cfg)
    session.commit()
    current = lists[kind]
    rules = " → ".join(current.rules) or "none"
    console.print(
        f"[bold]{current.library.name}[/bold]: {len(current.papers)}/{current.count} papers "
        f"[dim](rules: {rules}; {current.manual} added by you, {len(current.removed)} removed "
        f"by you, {current.without_doi} skipped without DOI)[/dim]"
    )
    for number, paper in enumerate(current.papers, start=1):
        console.print(
            f"  {number:>3}  [bold]{paper.title}[/bold] [dim](#{paper.id})[/dim]  "
            f"[cyan]{current.origins[paper.id]}[/cyan]"
        )
        console.print(f"       {paper_line(paper)}")
    for paper, choice in current.removed:
        console.print(
            f"  [dim]removed by you {choice.at.date().isoformat()}: {paper.title} "
            f"(#{paper.id})[/dim]"
        )
    for warning in current.warnings:
        console.print(f"[yellow]{warning}[/yellow]")


def _paper(cfg, session, ref: str, fetch: bool = False):
    try:
        paper = find_paper(session, ref)
        if paper is None and fetch:
            http = make_http(cfg)
            try:
                paper = fetch_paper(session, cfg, http, ref)
            finally:
                http.close()
    except ReadingError as error:
        _fail(str(error))
    if paper is None:
        _fail(f"No paper '{ref}' in your database")
    return paper


@examples_app.callback()
def examples_show(ctx: typer.Context, config: Path = ConfigOpt):
    """Show both lists (default when no subcommand is given)."""
    if ctx.invoked_subcommand:
        return
    cfg, engine, session = _open(config)
    try:
        for kind in examples.KINDS:
            _show(cfg, session, kind)
    finally:
        session.close()
        engine.dispose()


@examples_app.command("positive")
def examples_positive(config: Path = ConfigOpt):
    """The positive examples in order, with their origin."""
    cfg, engine, session = _open(config)
    try:
        _show(cfg, session, examples.POSITIVE)
    finally:
        session.close()
        engine.dispose()


@examples_app.command("negative")
def examples_negative(config: Path = ConfigOpt):
    """The negative examples in order, with their origin."""
    cfg, engine, session = _open(config)
    try:
        _show(cfg, session, examples.NEGATIVE)
    finally:
        session.close()
        engine.dispose()


@examples_app.command("add")
def examples_add(
    kind: str = typer.Argument(..., help=KIND_HELP),
    ref: str = typer.Argument(..., help=REF_HELP),
    config: Path = ConfigOpt,
):
    """Use a paper as example (it leaves the other list)."""
    kind = _kind(kind)
    cfg, engine, session = _open(config)
    try:
        paper = _paper(cfg, session, ref, fetch=True)
        examples.include(session, paper, kind)
        lists = examples.refresh(session, cfg)
        session.commit()
        console.print(f"Added to the {kind} examples: {paper.title}")
        if not paper.doi:
            console.print("[yellow]It has no DOI, so Semantic Scholar cannot use it.[/yellow]")
        elif paper.id not in lists[kind].origins:
            console.print("[yellow]Not in the list: you added more papers than it holds.[/yellow]")
    finally:
        session.close()
        engine.dispose()


@examples_app.command("remove")
def examples_remove(
    kind: str = typer.Argument(..., help=KIND_HELP),
    ref: str = typer.Argument(..., help=REF_HELP),
    config: Path = ConfigOpt,
):
    """Take a paper out of a list; the rules will not choose it again."""
    kind = _kind(kind)
    cfg, engine, session = _open(config)
    try:
        paper = _paper(cfg, session, ref)
        if not examples.remove(session, cfg, paper.id, kind):
            _fail(f"'{paper.title}' is not a {kind} example")
        examples.refresh(session, cfg)
        session.commit()
        console.print(f"Removed from the {kind} examples: {paper.title}")
    finally:
        session.close()
        engine.dispose()


@examples_app.command("restore")
def examples_restore(
    kind: str = typer.Argument(..., help=KIND_HELP),
    ref: str = typer.Argument(..., help=REF_HELP),
    config: Path = ConfigOpt,
):
    """Let the rules choose a paper you removed again."""
    kind = _kind(kind)
    cfg, engine, session = _open(config)
    try:
        paper = _paper(cfg, session, ref)
        if not examples.restore(session, paper.id, kind):
            _fail(f"You did not remove '{paper.title}' from the {kind} examples")
        examples.refresh(session, cfg)
        session.commit()
        console.print(f"Restored: {paper.title}")
    finally:
        session.close()
        engine.dispose()


@examples_app.command("reset")
def examples_reset(
    kind: str = typer.Argument(..., help=KIND_HELP),
    yes: bool = typer.Option(False, "--yes", help="Do not ask for confirmation"),
    config: Path = ConfigOpt,
):
    """Undo all your additions and removals of a list."""
    kind = _kind(kind)
    cfg, engine, session = _open(config)
    try:
        if not yes:
            typer.confirm(f"Undo all your changes of the {kind} examples?", abort=True)
        undone = examples.reset(session, kind)
        examples.refresh(session, cfg)
        session.commit()
        console.print(f"Undid {undone} changes; the {kind} examples follow the rules again.")
    finally:
        session.close()
        engine.dispose()


@examples_app.command("rules")
def examples_rules(
    kind: str = typer.Argument(..., help=KIND_HELP),
    rules: list[str] = typer.Argument(
        None,
        help="Rules in order. positive: liked, read, recent, impact, fwci, cited; "
        "negative: dismissed, low_rated. None: show the current ones.",
    ),
    none: bool = typer.Option(False, "--none", help="Use no rules (only papers you add)"),
    count: int = typer.Option(None, "--count", help="Number of examples"),
    config: Path = ConfigOpt,
):
    """Show or set the rules and the size of a list (saved in the profile's settings)."""
    from litresearch import profiles
    from litresearch.cli import state

    kind = _kind(kind)
    cfg = load_cfg(config)
    if rules or none or count is not None:
        base = load_cfg_base(config)
        slug = profiles.find(base, state["profile"]) if state["profile"] else None
        slug = slug or profiles.get_current(base)
        if slug is None:
            _fail(f"No profile selected: set selection.s2_{kind}_rules in {config}")
        updates = {}
        if rules or none:
            updates[f"s2_{kind}_rules"] = [] if none else list(rules)
        if count is not None:
            updates[f"s2_{kind}_count"] = count
        try:
            cfg = profiles.write_overlay(base, slug, {"selection": updates})
        except ValueError as error:
            _fail(f"Not saved: {error}")
    for number, name in enumerate(examples.rules_of(cfg, kind), start=1):
        console.print(f"  {number}. {name}: {examples.RULES[kind][name][1]}")
    if not examples.rules_of(cfg, kind):
        console.print("  no rules: only papers you add")
    console.print(f"  up to {examples.count_of(cfg, kind)} papers")
