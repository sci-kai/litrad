"""CLI commands for the reading queue, reading log and score."""

from contextlib import contextmanager
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path

import typer
from sqlmodel import Session, select

from litresearch import history as read_history
from litresearch.cli import (
    ConfigOpt,
    app,
    console,
    load_cfg,
    make_http,
    paper_line,
)
from litresearch.db import Library, Paper, Reading, ReadStatus, StatusEvent, XpEvent, init_db
from litresearch.game import (
    Award,
    badge_progress,
    grade,
    level_for,
    next_read_streak,
    queue_top_waiting_days,
    reading_impact_factor,
    stats,
    streak_multiplier,
    total_xp,
    week_progress,
)
from litresearch.papers import paper_link
from litresearch.profile import active_rules
from litresearch.reading import (
    REF_HELP,
    ReadingError,
    add_note,
    drop_from_queue,
    fetch_paper,
    find_paper,
    queue_add,
    queue_items,
    queue_move,
    record_reading,
    source_of,
)
from litresearch.search import SORTS, SOURCES, SearchQuery, enabled_sources, search_library
from litresearch.search import search as literature_search

queue_app = typer.Typer(help="Your short, ordered reading queue.", invoke_without_command=True)
app.add_typer(queue_app, name="queue")

QUEUE_NUDGE_DAYS = 14


class Level(StrEnum):
    abstract = "abstract"
    skimmed = "skimmed"
    read = "read"
    thorough = "thorough"


@contextmanager
def open_db(config: Path):
    cfg = load_cfg(config)
    engine = init_db(cfg.paths.database)
    try:
        with Session(engine) as session:
            yield cfg, session
    except ReadingError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(code=1) from None
    finally:
        engine.dispose()


def bar(fraction: float, width: int = 20) -> str:
    filled = round(max(0.0, min(1.0, fraction)) * width)
    return "█" * filled + "░" * (width - filled)


def show_award(session: Session, award: Award) -> None:
    """Print the points of an action, level-ups and new badges."""
    for event in award.events:
        if event.kind != "badge":
            colour = "green" if event.points >= 0 else "red"
            console.print(f"  [{colour}]{event.points:>+4}[/{colour}]  {event.reason}")
    if award.profile_started:
        console.print(
            "  [bold cyan]🎮 Your game has started with the rules from config.yaml; they are "
            "now locked ('litresearch profile show').[/bold cyan]"
        )
    if award.points:
        level = award.level_after
        xp = total_xp(session)
        to_go = f"{level.next_floor - xp} XP to next level" if level.next_floor else "max level"
        console.print(
            f"  [bold]{award.points:+} XP[/bold]  ·  total {xp} XP  ·  "
            f"Level {level.number} {level.title} [{bar(level.progress(xp), 10)}] {to_go}"
        )
    for badge in award.badges:
        console.print(
            f"  [bold yellow]{badge.icon} Badge unlocked: {badge.name}[/bold yellow] "
            f"({badge.description})"
        )
    if award.level_before and award.level_after.number > award.level_before.number:
        console.print(
            f"  [bold magenta]⬆ LEVEL UP! You are now a {award.level_after.title}.[/bold magenta]"
        )


def show_next(session: Session) -> None:
    items = queue_items(session)
    if items:
        _, paper = items[0]
        console.print(f"Up next: [bold]q1[/bold] {paper.title}")
    else:
        console.print("Your queue is empty. Pick something: [bold]litresearch recommend[/bold]")


# --- queue -------------------------------------------------------------------


@queue_app.callback()
def queue_show(ctx: typer.Context, config: Path = ConfigOpt):
    """Show the queue (default when no subcommand is given)."""
    if ctx.invoked_subcommand:
        return
    with open_db(config) as (cfg, session):
        items = queue_items(session)
        console.print(f"[bold]Reading queue[/bold] ({len(items)}/{cfg.queue.max_size})")
        if not items:
            console.print("  empty. Pick papers from [bold]litresearch recommend[/bold].")
        for item, paper in items:
            marker = "[bold green]▶ UP NEXT[/bold green] " if item.position == 1 else ""
            waiting = (date.today() - item.added_at.date()).days
            console.print(
                f"[bold cyan]q{item.position}[/bold cyan]  {marker}[bold]{paper.title}[/bold]"
            )
            console.print(
                f"     {paper_line(paper)}  [dim]· {item.source}, waiting {waiting} d[/dim]"
            )
        days = queue_top_waiting_days(session, date.today())
        if days is not None and days >= QUEUE_NUDGE_DAYS:
            console.print(
                f"[yellow]Your top paper has waited {days} days. Read it, or drop it "
                "if it no longer matters.[/yellow]"
            )


@queue_app.command("add")
def queue_add_cmd(
    ref: str = typer.Argument(None, help=REF_HELP),
    title: str = typer.Option(None, help="Add a paper by title only (no DOI)"),
    url: str = typer.Option(None, help="Link for a paper added by title"),
    top: bool = typer.Option(False, "--top", help="Put it at position 1"),
    at: int = typer.Option(None, "--at", help="Insert at this position"),
    config: Path = ConfigOpt,
):
    """Add a paper: a recommendation (r1), library paper, DOI or a title."""
    with open_db(config) as (cfg, session):
        if title:
            paper = Paper(title=title, url=url)
            session.add(paper)
            session.flush()
        elif ref:
            paper = find_paper(session, ref)
            if paper is None:  # an unknown DOI
                http = make_http(cfg)
                try:
                    paper = fetch_paper(session, cfg, http, ref)
                    session.commit()
                finally:
                    http.close()
        else:
            raise ReadingError("Give a reference or --title")
        item = queue_add(session, cfg, paper, source_of(session, paper, ref), 1 if top else at)
        console.print(f"Added to the queue at q{item.position}: [bold]{paper.title}[/bold]")
        show_next(session)


@queue_app.command("drop")
def queue_drop_cmd(ref: str = typer.Argument(..., help=REF_HELP), config: Path = ConfigOpt):
    """Remove a paper from the queue (costs XP if you did not at least skim it)."""
    with open_db(config) as (cfg, session):
        paper = find_paper(session, ref)
        if paper is None:
            raise ReadingError("Not on the queue")
        penalty = drop_from_queue(session, cfg, paper)
        console.print(f"Dropped: {paper.title}")
        if penalty:
            console.print(f"  [red]{penalty.points} XP[/red]  {penalty.reason}")
        show_next(session)


@queue_app.command("move")
def queue_move_cmd(
    ref: str = typer.Argument(..., help=REF_HELP),
    position: int = typer.Argument(..., help="New position (1 = up next)"),
    config: Path = ConfigOpt,
):
    """Change the order of the queue."""
    with open_db(config) as (_, session):
        paper = find_paper(session, ref)
        if paper is None:
            raise ReadingError("Unknown paper")
        queue_move(session, paper, position)
        show_next(session)


# --- reading ------------------------------------------------------------------


@app.command("search")
def search_cmd(
    keywords: str = typer.Argument("", help="Words anywhere in title, abstract or keywords"),
    title: str = typer.Option("", "--title", "-t", help="Words in the title"),
    author: str = typer.Option("", "--author", "-a", help='e.g. "Swanton C"; several with ";"'),
    journal: str = typer.Option("", "--journal", "-j", help='e.g. "Nature"; several with ";"'),
    year_from: int = typer.Option(None, "--from", help="Published from this year"),
    year_to: int = typer.Option(None, "--to", help="Published up to this year"),
    open_access: bool = typer.Option(False, "--oa", help="Only papers free to read"),
    source: str = typer.Option(
        "all", "--source", "-s", help="all (enabled databases), europepmc, pubmed or library"
    ),
    sort: str = typer.Option("relevance", help="relevance, newest or cited"),
    limit: int = typer.Option(10, help="Results per database (max 100)"),
    add: int = typer.Option(None, "--add", help="Put result number N on your queue"),
    config: Path = ConfigOpt,
):
    """Search Europe PMC, PubMed or your library by keywords, title, author, journal, year."""
    query = SearchQuery(
        keywords=keywords,
        title=title,
        author=author,
        journal=journal,
        year_from=year_from,
        year_to=year_to,
        open_access=open_access,
    )
    if query.is_empty():
        raise typer.BadParameter(
            "Give keywords or at least one of --title/--author/--journal/--from"
        )
    if sort not in SORTS:
        raise typer.BadParameter(f"--sort must be one of {', '.join(SORTS)}")
    with open_db(config) as (cfg, session):
        if source == "library":
            papers = search_library(session, query)
            console.print(f"{len(papers)} paper(s) in your library: {query.describe()}")
            for number, paper in enumerate(papers[:limit], start=1):
                console.print(f"[bold cyan]{number:>2}[/bold cyan]  {paper.title}")
                console.print(f"     {paper_line(paper)} · #{paper.id}")
            if add:
                if not 1 <= add <= min(len(papers), limit):
                    raise ReadingError(f"There is no result {add}")
                item = queue_add(session, cfg, papers[add - 1], source_of(session, papers[add - 1]))
                console.print(f"Added to the queue at q{item.position}.")
            return
        if source == "all":
            sources = enabled_sources(cfg)
        elif source in SOURCES:
            sources = [source]
        else:
            raise ReadingError("--source must be all, europepmc, pubmed or library")
        http = make_http(cfg)
        try:
            found = literature_search(query, cfg, http, session, sources, limit, sort)
            totals = ", ".join(f"{SOURCES[s]} {n:,}" for s, n in found.totals.items())
            console.print(f"{query.describe()}  [dim]({totals or 'no answer'})[/dim]")
            for name, problem in found.errors.items():
                console.print(f"[yellow]{SOURCES[name]} did not answer: {problem}[/yellow]")
            for number, result in enumerate(found.results, start=1):
                hit = result.hit
                marks = [SOURCES[s] for s in hit.found_in]
                if hit.open_access:
                    marks.append("free full text")
                if result.queued:
                    marks.append("ON YOUR QUEUE")
                elif result.status:
                    marks.append(result.status.upper())
                elif result.in_library:
                    marks.append("in your library")
                authors = ", ".join(hit.authors[:3]) + (" et al." if len(hit.authors) > 3 else "")
                console.print(f"\n[bold cyan]{number:>2}[/bold cyan]  [bold]{hit.title}[/bold]")
                console.print(
                    f"     {authors} ({hit.year or 'n.d.'}) · {hit.journal or '?'}"
                    + (f" · {hit.cited_by} citations" if hit.cited_by is not None else "")
                )
                console.print(f"     [dim]{hit.ref or ''} · {' · '.join(marks)}[/dim]")
            if not found.results:
                console.print("Nothing found. Try fewer fields or another spelling.")
            if add:
                if not 1 <= add <= len(found.results):
                    raise ReadingError(f"There is no result {add}")
                hit = found.results[add - 1].hit
                paper = find_paper(session, hit.ref) or fetch_paper(session, cfg, http, hit.ref)
                item = queue_add(session, cfg, paper, "manual")
                console.print(
                    f"\nAdded to the queue at q{item.position}: [bold]{paper.title}[/bold]"
                )
            elif found.results:
                console.print(
                    "\nAdd one with [bold]--add N[/bold] or "
                    "[bold]litresearch queue add <DOI|pmid:N>[/bold]."
                )
        finally:
            http.close()


@app.command("next")
def next_cmd(config: Path = ConfigOpt):
    """Show the paper at the top of your queue."""
    with open_db(config) as (_, session):
        items = queue_items(session)
        if not items:
            show_next(session)
            return
        show_paper(session, items[0][1])
        console.print(
            "\nWhen done: [bold]litresearch read q1 skimmed|read|thorough "
            "[--rating 1-5] [--note TEXT][/bold]"
        )


def show_paper(session: Session, paper: Paper) -> None:
    """Print a paper with metadata, status, notes and link."""
    console.print(f"[bold]{paper.title}[/bold]  [dim]#{paper.id}[/dim]")
    console.print(paper_line(paper))
    if paper.citation_count is not None:
        pct = (
            f", top {100 - paper.citation_percentile:.2g}% cited"
            if paper.citation_percentile
            else ""
        )
        console.print(f"[dim]{paper.citation_count:,} citations{pct}[/dim]")
    console.print(f"[blue]{paper_link(paper)}[/blue]")
    if paper.tldr:
        console.print(f"\n[italic]TL;DR: {paper.tldr}[/italic]")
    if paper.abstract:
        console.print(f"\n{paper.abstract}")
    reading = session.get(Reading, paper.id)
    if reading:
        rating = f", rated {'★' * reading.rating}" if reading.rating else ""
        console.print(f"\n[bold]Status:[/bold] {reading.status.value}{rating}")
        if reading.notes:
            console.print(f"[bold]Notes:[/bold]\n{reading.notes}")


@app.command()
def show(ref: str = typer.Argument(..., help=REF_HELP), config: Path = ConfigOpt):
    """Show details, status and notes of a paper."""
    with open_db(config) as (_, session):
        paper = find_paper(session, ref)
        if paper is None:
            raise ReadingError("Unknown paper")
        show_paper(session, paper)


@app.command()
def read(
    ref: str = typer.Argument(..., help=REF_HELP),
    level: Level = typer.Argument(..., help="How deeply you read it"),
    rating: int = typer.Option(None, "--rating", "-r", min=1, max=5, help="1-5 stars"),
    note: str = typer.Option(None, "--note", "-n", help="A note to keep"),
    config: Path = ConfigOpt,
):
    """Log that you read a paper and earn points."""
    with open_db(config) as (cfg, session):
        paper = find_paper(session, ref)
        if paper is None:
            raise ReadingError("Unknown paper; add it to the queue first")
        outcome = record_reading(session, cfg, paper, ReadStatus(level.value), rating, note)
        left = " (left the queue)" if outcome.left_queue else ""
        console.print(f"[bold]✔ {level.value}[/bold]: {paper.title}{left}")
        show_award(session, outcome.award)
        show_next(session)


@app.command()
def dismiss(ref: str = typer.Argument(..., help=REF_HELP), config: Path = ConfigOpt):
    """Mark a paper as not relevant; it also teaches the recommender."""
    with open_db(config) as (cfg, session):
        paper = find_paper(session, ref)
        if paper is None:
            raise ReadingError("Unknown paper")
        outcome = record_reading(session, cfg, paper, ReadStatus.dismissed)
        console.print(f"Dismissed: {paper.title}")
        show_award(session, outcome.award)


@app.command()
def note(
    ref: str = typer.Argument(..., help=REF_HELP),
    text: str = typer.Argument(..., help="Note text"),
    config: Path = ConfigOpt,
):
    """Add a timestamped note to a paper you have marked."""
    with open_db(config) as (cfg, session):
        paper = find_paper(session, ref)
        if paper is None:
            raise ReadingError("Unknown paper")
        award = add_note(session, cfg, paper, text)
        console.print(f"Note saved for: {paper.title}")
        show_award(session, award)


# --- score ----------------------------------------------------------------------


@app.command()
def score(config: Path = ConfigOpt):
    """Your reading dashboard: level, streak, weekly report card, badges."""
    today = date.today()
    with open_db(config) as (cfg, session):
        rules = active_rules(session, cfg)
        s = stats(session, today, rules)
        level = level_for(s["xp"], rules)
        to_go = (
            f"{level.next_floor - s['xp']} XP to next level" if level.next_floor else "max level"
        )
        console.print(
            f"[bold]{level.icon} Level {level.number} · {level.title}[/bold]   {s['xp']} XP  "
            f"[{bar(level.progress(s['xp']))}] {to_go}"
        )
        flame = " 🔥" if s["streak"] >= 2 else ""
        console.print(
            f"Streak: {s['streak']} week(s){flame} (best {s['best_streak']}) · "
            f"next reads earn x{streak_multiplier(next_read_streak(session, today), rules):.2f}"
        )

        week = week_progress(session, rules, today)
        letter, comment = grade(week, week_over=False)
        console.print(
            f"\n[bold]This week[/bold] (from {week['week_start'].isoformat()}): "
            f"grade [bold]{letter}[/bold], {comment}"
        )
        deep = min(week["detail"], week["goal_detail"])
        goal_total = week["goal_detail"] + week["goal_skim"]
        console.print(
            f"  Quest [{bar(week['fraction'], 10)}]  deep reads {deep}/{week['goal_detail']}, "
            f"papers {week['total']}/{goal_total}  ·  {week['xp']} XP this week"
        )
        last_week = week_progress(session, rules, date.fromordinal(today.toordinal() - 7))
        console.print(
            f"  Last week: grade {grade(last_week, week_over=True)[0]}, {last_week['xp']} XP"
        )

        rif = reading_impact_factor(session, today)
        console.print(
            f"\nReading Impact Factor (avg XP/week, last 4 weeks): [bold]{rif:.1f}[/bold]"
        )
        if s["papers_read"]:
            discipline = s["frogs"] / s["papers_read"]
            depth_share = s["deep_reads"] / s["papers_read"]
            console.print(
                f"Discipline (top paper read first): {discipline:.0%} · "
                f"Depth (read or deeper): {depth_share:.0%} · papers read: {s['papers_read']}"
            )

        progress = badge_progress(session, today, rules)
        owned = [b for b, _, unlocked in progress if unlocked]
        console.print(
            f"\n[bold]Badges {len(owned)}/{len(progress)}[/bold]: "
            + (", ".join(f"{b.icon} {b.name}" for b in owned) or "none yet")
        )
        upcoming = sorted(
            ((b, v) for b, v, unlocked in progress if not unlocked),
            key=lambda bv: bv[1] / bv[0].target,
            reverse=True,
        )[:3]
        for badge, value in upcoming:
            console.print(
                f"  next: {badge.name} [{bar(value / badge.target, 10)}] {value}/{badge.target}"
                f" [dim]· {badge.description}[/dim]"
            )
        days = queue_top_waiting_days(session, today)
        if days is not None and days >= QUEUE_NUDGE_DAYS:
            console.print(f"\n[yellow]Your top paper has waited {days} days.[/yellow]")


@app.command()
def history(
    limit: int = typer.Option(15, help="Number of entries"),
    config: Path = ConfigOpt,
):
    """Recently logged papers and the points they earned."""
    with open_db(config) as (_, session):
        events = session.exec(
            select(StatusEvent, Paper)
            .join(Paper, Paper.id == StatusEvent.paper_id)
            .order_by(StatusEvent.at.desc())
            .limit(limit)
        ).all()
        if not events:
            console.print("Nothing logged yet.")
        for event, paper in events:
            points = sum(
                e.points
                for e in session.exec(
                    select(XpEvent).where(XpEvent.paper_id == paper.id, XpEvent.at == event.at)
                ).all()
            )
            when = datetime.isoformat(event.at)
            colour = "green" if points >= 0 else "red"
            console.print(
                f"{when}  {event.status.value:<9} [{colour}]{points:>+4}[/{colour}]  "
                f"{paper.title[:70]}"
            )


@app.command("read-papers")
def read_papers(
    depth: list[Level] = typer.Option([], "--depth", help="Only these depths (repeatable)"),
    rating: int = typer.Option(0, "--rating", min=0, max=5, help="Minimum rating"),
    since: str = typer.Option("", "--since", help="Last read on or after YYYY-MM-DD"),
    until: str = typer.Option("", "--until", help="Last read on or before YYYY-MM-DD"),
    library: str = typer.Option("", "--library", help="Only papers of this library (name)"),
    notes: bool = typer.Option(False, "--notes", help="Only papers with notes"),
    dismissed: bool = typer.Option(False, "--dismissed", help="Include not relevant papers"),
    search: str = typer.Option("", "--search", help="Words in title, authors or notes"),
    sort: str = typer.Option("last", "--sort", help="last, first, depth, rating, xp or title"),
    limit: int = typer.Option(25, help="Rows shown (the TSV file gets all)"),
    tsv: Path | None = typer.Option(None, "--tsv", help="Write all matching rows to this file"),
    config: Path = ConfigOpt,
):
    """Every paper you marked, with depth, dates, rating, notes and XP."""
    if sort not in read_history.SORTS:
        raise typer.BadParameter(f"one of {', '.join(read_history.SORTS)}", param_hint="--sort")
    for name, text in (("--since", since), ("--until", until)):
        if text and read_history.parse_date(text) is None:
            raise typer.BadParameter("use YYYY-MM-DD", param_hint=name)
    with open_db(config) as (_, session):
        library_id = None
        if library:
            found = session.exec(select(Library).where(Library.name == library)).first()
            if found is None:
                names = ", ".join(lib.name for lib in session.exec(select(Library)).all())
                console.print(f"[red]No library '{library}'. Libraries: {names or 'none'}[/red]")
                raise typer.Exit(code=1)
            library_id = found.id
        found_rows = read_history.rows(
            session,
            read_history.HistoryFilter(
                sort=sort,
                depths=[d.value for d in depth],
                min_rating=rating,
                since=read_history.parse_date(since),
                until=read_history.parse_date(until),
                library=library_id,
                has_notes=notes,
                dismissed=dismissed,
                q=search,
            ),
        )
        totals = read_history.counts(found_rows)
        console.print(
            "  ".join(f"{k} {v}" for k, v in totals.items()) + f"  total {len(found_rows)}"
        )
        if tsv:
            items = read_history.entries(session, found_rows)
            tsv.write_text(read_history.to_tsv(items), encoding="utf-8")
            console.print(f"Wrote {len(items)} rows to {tsv}")
            return
        for item in read_history.entries(session, found_rows[:limit]):
            row = item.row
            stars = "*" * row.rating if row.rating else "-"
            console.print(
                f"{row.last.date().isoformat()}  {read_history.deepest_name(row):<9} "
                f"{stars:<5} {row.xp:>+5}  {item.paper.title[:70]}",
                highlight=False,
                markup=False,
            )
