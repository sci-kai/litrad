"""Reading history: every paper you marked, with depth, dates, rating, notes and XP.

Filtering, sorting and aggregation run in SQL over light rows (ids, dates,
numbers); full papers and notes are loaded for the shown page only. The text
search is the one exception: SQLite folds the case of ASCII letters only, so
title, authors and notes of the marked papers are compared in Python
(``str.casefold``, which handles umlauts).
"""

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from sqlalchemy import and_, case, exists, func
from sqlmodel import Session, select

from litrad.db import (
    READ_LEVELS,
    LibraryPaper,
    Paper,
    Reading,
    ReadStatus,
    Recommendation,
    StatusEvent,
    XpEvent,
)

PAGE_SIZE = 50
CHUNK = 400  # ids per IN (...) query

# sort key -> (label, natural direction)
SORTS = {
    "last": "last read",
    "first": "first read",
    "depth": "depth",
    "rating": "rating",
    "xp": "XP",
    "title": "title",
}
DEPTHS = [s.value for s in READ_LEVELS]
SOURCES = {
    "recommendation": "recommendation",
    "library": "library",
    "manual": "added by you",
}
TSV_COLUMNS = [
    "title",
    "authors",
    "year",
    "journal",
    "doi",
    "pmid",
    "deepest depth",
    "first read",
    "last read",
    "rating",
    "notes",
    "xp",
    "source",
]


@dataclass
class HistoryFilter:
    sort: str = "last"
    depths: list[str] = field(default_factory=list)
    min_rating: int = 0
    since: date | None = None
    until: date | None = None
    library: int | None = None
    has_notes: bool = False
    dismissed: bool = False
    q: str = ""


@dataclass
class HistoryRow:
    """One paper with what you did with it (light: no notes, no paper columns)."""

    paper_id: int
    deepest: int  # 0 = only dismissed, 1 = abstract ... 4 = thorough
    dismissed: bool
    first: datetime
    last: datetime
    rating: int | None
    xp: int
    title: str
    earlier: bool = False  # logged as read before litrad


@dataclass
class HistoryEntry:
    row: HistoryRow
    paper: Paper
    notes: str
    source: str


def parse_date(text: str | None) -> date | None:
    try:
        return date.fromisoformat((text or "").strip())
    except ValueError:
        return None


def _depth_case(column):
    return case(
        *[(column == level.value, i + 1) for i, level in enumerate(READ_LEVELS)],
        else_=0,
    )


def _event_summary():
    """Per paper: deepest depth, first and last status change (one grouped query)."""
    reading_levels = [s.value for s in READ_LEVELS]
    return (
        select(
            StatusEvent.paper_id.label("paper_id"),
            func.max(_depth_case(StatusEvent.status)).label("deepest"),
            func.min(case((StatusEvent.status.in_(reading_levels), StatusEvent.at))).label("first"),
            func.max(StatusEvent.at).label("last"),
            func.max(StatusEvent.earlier).label("earlier"),
        )
        .group_by(StatusEvent.paper_id)
        .subquery()
    )


def _xp_summary():
    return (
        select(XpEvent.paper_id.label("paper_id"), func.sum(XpEvent.points).label("xp"))
        .where(XpEvent.paper_id.is_not(None))
        .group_by(XpEvent.paper_id)
        .subquery()
    )


def _search_ids(session: Session, text: str) -> set[int]:
    words = text.casefold().split()
    found = set()
    for paper_id, title, authors, notes in session.exec(
        select(Paper.id, Paper.title, Paper.authors, Reading.notes).join(
            Reading, Reading.paper_id == Paper.id
        )
    ).all():
        haystack = f"{title} {' '.join(authors or [])} {notes}".casefold()
        if all(w in haystack for w in words):
            found.add(paper_id)
    return found


def rows(session: Session, f: HistoryFilter) -> list[HistoryRow]:
    """All papers matching the filter, sorted; light rows (two queries at most)."""
    events = _event_summary()
    xp = _xp_summary()
    reading_depth = _depth_case(Reading.status)
    deepest = func.max(func.coalesce(events.c.deepest, 0), reading_depth)
    last = func.coalesce(events.c.last, Reading.updated_at)
    first = func.coalesce(events.c.first, events.c.last, Reading.updated_at)
    points = func.coalesce(xp.c.xp, 0)
    dismissed = Reading.status == ReadStatus.dismissed.value

    query = (
        select(
            Reading.paper_id,
            deepest,
            dismissed,
            first,
            last,
            Reading.rating,
            points,
            Paper.title,
            func.coalesce(events.c.earlier, False),
        )
        .join(Paper, Paper.id == Reading.paper_id)
        .join(events, events.c.paper_id == Reading.paper_id, isouter=True)
        .join(xp, xp.c.paper_id == Reading.paper_id, isouter=True)
    )
    if not f.dismissed:
        query = query.where(~dismissed)
    if f.depths:
        query = query.where(deepest.in_([DEPTHS.index(d) + 1 for d in f.depths if d in DEPTHS]))
    if f.min_rating:
        query = query.where(Reading.rating >= f.min_rating)
    if f.since:
        query = query.where(last >= datetime.combine(f.since, time.min))
    if f.until:
        query = query.where(last < datetime.combine(f.until + timedelta(days=1), time.min))
    if f.library is not None:
        query = query.where(
            exists().where(
                and_(
                    LibraryPaper.paper_id == Reading.paper_id, LibraryPaper.library_id == f.library
                )
            )
        )
    if f.has_notes:
        query = query.where(Reading.notes != "")
    order = {
        "last": [last.desc()],
        "first": [first.asc()],
        "depth": [deepest.desc(), last.desc()],
        "rating": [func.coalesce(Reading.rating, 0).desc(), last.desc()],
        "xp": [points.desc(), last.desc()],
        "title": [func.lower(Paper.title)],
    }[f.sort if f.sort in SORTS else "last"]
    result = [
        HistoryRow(pid, d, bool(dis), fi, la, rating, int(pts), title, bool(early))
        for pid, d, dis, fi, la, rating, pts, title, early in session.exec(
            query.order_by(*order, Reading.paper_id)
        ).all()
    ]
    if f.q.strip():
        ids = _search_ids(session, f.q)
        result = [r for r in result if r.paper_id in ids]
    return result


def counts(history: list[HistoryRow]) -> dict[str, int]:
    """Papers per depth (and not relevant), over the rows given."""
    totals = {d: 0 for d in DEPTHS}
    totals["dismissed"] = 0
    for row in history:
        key = "dismissed" if row.dismissed and not row.deepest else DEPTHS[row.deepest - 1]
        totals[key] += 1
    return totals


def entries(session: Session, history: list[HistoryRow]) -> list[HistoryEntry]:
    """Full papers, notes and sources for the given rows (chunked, in order)."""
    out = []
    for start in range(0, len(history), CHUNK):
        part = history[start : start + CHUNK]
        ids = [r.paper_id for r in part]
        papers = {p.id: p for p in session.exec(select(Paper).where(Paper.id.in_(ids))).all()}
        notes = dict(
            session.exec(
                select(Reading.paper_id, Reading.notes).where(Reading.paper_id.in_(ids))
            ).all()
        )
        recommended = set(
            session.exec(
                select(Recommendation.paper_id).where(Recommendation.paper_id.in_(ids)).distinct()
            ).all()
        )
        for row in part:
            paper = papers[row.paper_id]
            source = (
                "recommendation"
                if row.paper_id in recommended
                else "library"
                if paper.in_library
                else "manual"
            )
            out.append(HistoryEntry(row, paper, notes.get(row.paper_id) or "", source))
    return out


def deepest_name(row: HistoryRow) -> str:
    return DEPTHS[row.deepest - 1] if row.deepest else "dismissed"


def _cell(text) -> str:
    return (
        str(text).replace("\\", "\\\\").replace("\t", "\\t").replace("\r", "").replace("\n", "\\n")
    )


def to_tsv(items: list[HistoryEntry]) -> str:
    """The rows as TSV; tabs and newlines inside a cell become ``\\t`` and ``\\n``."""
    buffer = io.StringIO()
    writer = csv.writer(
        buffer, delimiter="\t", lineterminator="\n", quoting=csv.QUOTE_NONE, escapechar=None
    )
    writer.writerow(TSV_COLUMNS)
    for item in items:
        paper, row = item.paper, item.row
        writer.writerow(
            _cell(value if value is not None and value != "" else "n/a")
            for value in (
                paper.title,
                "; ".join(paper.authors or []),
                paper.year,
                paper.venue,
                paper.doi,
                paper.pmid,
                deepest_name(row),
                row.first.isoformat(),
                row.last.isoformat(),
                row.rating,
                item.notes,
                row.xp,
                SOURCES[item.source],
            )
        )
    return buffer.getvalue()
