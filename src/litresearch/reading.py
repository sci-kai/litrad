"""Reading queue and reading log.

The queue is deliberately short (``queue.max_size``) and strictly ordered:
position 1 is *the* paper to read next. Reading a paper to at least
"skimmed" depth (or dismissing it) takes it off the queue.
"""

import re
from dataclasses import dataclass
from datetime import datetime

from sqlmodel import Session, select

from litresearch.config import Config
from litresearch.db import (
    Paper,
    QueueItem,
    Reading,
    ReadStatus,
    Recommendation,
    StatusEvent,
    XpEvent,
    depth,
    now,
)
from litresearch.game import Award, award_reading, level_for, total_xp
from litresearch.ingest.bibtex import normalize_doi
from litresearch.recommend.engine import latest_batch


class ReadingError(ValueError):
    """A user-facing problem, e.g. a full queue or an unknown reference."""


# --- references ------------------------------------------------------------

REF_HELP = (
    "r<N> (recommendation), q<N> (queue position), #<id>, a DOI, "
    "pmid:<N>, a Paperpile citation key, or words from the title"
)


def find_paper(session: Session, ref: str) -> Paper | None:
    """Resolve a reference; None only for DOIs that are not stored yet."""
    ref = ref.strip()
    if match := re.fullmatch(r"[rR](\d+)", ref):
        batch = latest_batch(session)
        rec = (
            batch
            and session.exec(
                select(Recommendation).where(
                    Recommendation.batch_id == batch.id, Recommendation.rank == int(match.group(1))
                )
            ).first()
        )
        if not rec:
            raise ReadingError(f"No recommendation {ref} in the latest batch")
        return session.get(Paper, rec.paper_id)
    if match := re.fullmatch(r"[qQ](\d+)", ref):
        item = session.exec(
            select(QueueItem).where(QueueItem.position == int(match.group(1)))
        ).first()
        if not item:
            raise ReadingError(f"Queue has no position {match.group(1)}")
        return session.get(Paper, item.paper_id)
    if match := re.fullmatch(r"#(\d+)", ref):
        paper = session.get(Paper, int(match.group(1)))
        if not paper:
            raise ReadingError(f"No paper with id {ref}")
        return paper
    if doi := normalize_doi(ref):
        return session.exec(select(Paper).where(Paper.doi == doi)).first()
    if match := re.fullmatch(r"pmid:(\d+)", ref, re.IGNORECASE):
        paper = session.exec(select(Paper).where(Paper.pmid == match.group(1))).first()
        if not paper:
            raise ReadingError(f"No stored paper with PMID {match.group(1)}")
        return paper
    paper = session.exec(select(Paper).where(Paper.bibtex_key == ref)).first()
    if paper:
        return paper
    words = ref.lower().split()
    matches = [
        p for p in session.exec(select(Paper)).all() if all(w in p.title.lower() for w in words)
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ReadingError(f"Nothing matches '{ref}'. Use {REF_HELP}.")
    listing = "; ".join(f"#{p.id} {p.title[:50]}" for p in matches[:5])
    raise ReadingError(f"'{ref}' matches {len(matches)} papers, be more specific: {listing}")


# --- queue -----------------------------------------------------------------


def queue_items(session: Session) -> list[tuple[QueueItem, Paper]]:
    return list(
        session.exec(
            select(QueueItem, Paper)
            .join(Paper, Paper.id == QueueItem.paper_id)
            .order_by(QueueItem.position)
        ).all()
    )


def _renumber(items: list[QueueItem]) -> None:
    for position, item in enumerate(items, start=1):
        item.position = position


def queue_add(
    session: Session,
    cfg: Config,
    paper: Paper,
    source: str = "manual",
    position: int | None = None,
) -> QueueItem:
    items = [item for item, _ in queue_items(session)]
    if any(item.paper_id == paper.id for item in items):
        raise ReadingError("Already on the queue")
    reading = session.get(Reading, paper.id)
    if reading and depth(reading.status) >= 2:
        raise ReadingError(f"Already read ({reading.status.value})")
    if len(items) >= cfg.queue.max_size:
        raise ReadingError(
            f"Queue is full ({len(items)}/{cfg.queue.max_size}). Read or drop a paper first: "
            "a short queue gets read, a long one gets ignored."
        )
    new = QueueItem(paper_id=paper.id, position=0, source=source)
    index = len(items) if position is None else max(0, min(position - 1, len(items)))
    items.insert(index, new)
    _renumber(items)
    for item in items:
        session.add(item)
    session.commit()
    return new


def queue_remove(session: Session, paper: Paper, commit: bool = True) -> QueueItem | None:
    """Take a paper off the queue; returns the removed item (None if not queued)."""
    items = [item for item, _ in queue_items(session)]
    remaining = [item for item in items if item.paper_id != paper.id]
    if len(remaining) == len(items):
        return None
    removed = next(item for item in items if item.paper_id == paper.id)
    session.delete(removed)
    session.flush()
    _renumber(remaining)
    for item in remaining:
        session.add(item)
    if commit:
        session.commit()
    else:
        session.flush()
    return removed


def unread_drop_penalty(
    session: Session,
    cfg: Config,
    paper: Paper,
    item: QueueItem | None,
    read_depth: int,
    at: datetime,
) -> XpEvent | None:
    """Penalty for a recommended paper you picked but let go unread.

    Applies only to papers added to the queue from a recommendation that were
    not at least skimmed. Manually added and library papers can be dropped
    freely.
    """
    if item is None or item.source != "recommendation" or read_depth >= 2:
        return None
    if not cfg.game.drop_penalty:
        return None
    event = XpEvent(
        paper_id=paper.id,
        points=-cfg.game.drop_penalty,
        kind="penalty",
        reason=f"Picked recommendation left the queue unread: {paper.title[:60]}",
        at=at,
    )
    session.add(event)
    return event


def drop_from_queue(
    session: Session, cfg: Config, paper: Paper, at: datetime | None = None
) -> XpEvent | None:
    """Remove a paper from the queue; returns the penalty, if any."""
    at = at or now()
    read_depth = deepest_depth(session, paper.id)
    item = queue_remove(session, paper, commit=False)
    if item is None:
        raise ReadingError("Not on the queue")
    penalty = unread_drop_penalty(session, cfg, paper, item, read_depth, at)
    session.commit()
    return penalty


def drop_would_cost(session: Session, cfg: Config, paper: Paper) -> int:
    """XP a drop would cost right now (for warnings in the UI)."""
    item = session.get(QueueItem, paper.id)
    if item is None or item.source != "recommendation" or deepest_depth(session, paper.id) >= 2:
        return 0
    return cfg.game.drop_penalty


def queue_move(session: Session, paper: Paper, position: int) -> None:
    items = [item for item, _ in queue_items(session)]
    item = next((i for i in items if i.paper_id == paper.id), None)
    if item is None:
        raise ReadingError("Not on the queue")
    items.remove(item)
    items.insert(max(0, min(position - 1, len(items))), item)
    _renumber(items)
    for entry in items:
        session.add(entry)
    session.commit()


def source_of(
    session: Session, paper: Paper, ref: str | None = None, hint: str | None = None
) -> str:
    """Where a queued paper came from; decides whether dropping it costs XP.

    A paper picked from the recommendations counts as "recommendation" even
    when it is also in the Paperpile library.
    """
    if hint in ("recommendation", "library", "manual"):
        return hint
    if ref and re.fullmatch(r"\s*[rR]\d+\s*", ref):
        return "recommendation"
    if paper.in_library:
        return "library"
    if session.exec(select(Recommendation).where(Recommendation.paper_id == paper.id)).first():
        return "recommendation"
    return "manual"


# --- reading log ------------------------------------------------------------


@dataclass
class ReadingOutcome:
    reading: Reading
    previous: ReadStatus | None
    left_queue: bool
    award: Award


def deepest_depth(session: Session, paper_id: int) -> int:
    events = session.exec(select(StatusEvent).where(StatusEvent.paper_id == paper_id)).all()
    return max((depth(e.status) for e in events), default=0)


def append_note(reading: Reading, text: str, at: datetime) -> None:
    entry = f"[{at.isoformat()}] {text.strip()}"
    reading.notes = f"{reading.notes}\n\n{entry}" if reading.notes else entry


def record_reading(
    session: Session,
    cfg: Config,
    paper: Paper,
    status: ReadStatus,
    rating: int | None = None,
    note: str | None = None,
    at: datetime | None = None,
) -> ReadingOutcome:
    """Store a reading status (plus rating/note) and award points."""
    if rating is not None and not 1 <= rating <= 5:
        raise ReadingError("Rating must be between 1 and 5")
    at = at or now()
    items = queue_items(session)
    was_top = bool(items) and items[0][0].paper_id == paper.id
    previous_depth = deepest_depth(session, paper.id)

    reading = session.get(Reading, paper.id)
    previous = reading.status if reading else None
    rated = rating is not None and (reading is None or reading.rating is None)
    if reading is None:
        reading = Reading(paper_id=paper.id, status=status)
    reading.status = status
    if rating is not None:
        reading.rating = rating
    if note:
        append_note(reading, note, at)
    reading.updated_at = at
    session.add(reading)
    session.add(StatusEvent(paper_id=paper.id, status=status, was_queue_top=was_top, at=at))
    session.flush()

    removed = None
    if depth(status) >= 2 or status == ReadStatus.dismissed:
        removed = queue_remove(session, paper, commit=False)
    left_queue = removed is not None
    emptied = left_queue and status != ReadStatus.dismissed and not queue_items(session)

    award = award_reading(
        session,
        cfg,
        paper,
        previous_depth,
        status,
        was_top and depth(status) >= 2,
        rated,
        emptied,
        at,
    )
    if status == ReadStatus.dismissed:
        penalty = unread_drop_penalty(session, cfg, paper, removed, previous_depth, at)
        if penalty:
            award.events.append(penalty)
            session.flush()
            award.level_after = level_for(total_xp(session))
    session.commit()
    session.refresh(reading)
    return ReadingOutcome(reading, previous, left_queue, award)


def add_note(session: Session, cfg: Config, paper: Paper, text: str, at: datetime | None = None):
    """Add a note without changing the reading status (may earn the notes bonus)."""
    at = at or now()
    reading = session.get(Reading, paper.id)
    if reading is None:
        raise ReadingError("Mark the paper first (e.g. 'read <ref> abstract'), then add notes")
    append_note(reading, text, at)
    reading.updated_at = at
    session.add(reading)
    session.flush()
    award = award_reading(
        session,
        cfg,
        paper,
        deepest_depth(session, paper.id),
        reading.status,
        False,
        False,
        False,
        at,
    )
    session.commit()
    return award
