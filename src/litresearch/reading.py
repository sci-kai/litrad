"""Reading queue and reading log.

The queue is deliberately short (``queue.max_size``) and strictly ordered:
position 1 is *the* paper to read next. Reading a paper to at least
"skimmed" depth (or dismissing it) takes it off the queue.
"""

import re
from dataclasses import dataclass
from datetime import datetime

from sqlmodel import Session, select

from litresearch import libraries, notes
from litresearch.config import Config
from litresearch.db import (
    NoteFile,
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
from litresearch.papers import upsert_hit, upsert_work
from litresearch.profile import active_rules, ensure_profile, profile_rules
from litresearch.recommend.engine import latest_batch
from litresearch.sources.europepmc import EuropePMC
from litresearch.sources.hits import Hit
from litresearch.sources.http import HttpClient, HttpError
from litresearch.sources.openalex import OpenAlex
from litresearch.sources.pubmed import PubMed


class ReadingError(ValueError):
    """A user-facing problem, e.g. a full queue or an unknown reference."""


# --- references ------------------------------------------------------------

REF_HELP = (
    "r<N> (recommendation), q<N> (queue position), #<id>, a DOI, "
    "pmid:<N>, PMC<N>, a citation key from your library, or words from the title"
)


PMID_REF = re.compile(r"pmid:\s*(\d+)", re.IGNORECASE)
PMC_REF = re.compile(r"(PMC\d+)", re.IGNORECASE)


def find_paper(session: Session, ref: str) -> Paper | None:
    """Resolve a reference; None only for DOIs, PMIDs and PMC ids not stored yet."""
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
    if match := PMID_REF.fullmatch(ref):  # None: not stored yet, fetch_paper gets it
        return session.exec(select(Paper).where(Paper.pmid == match.group(1))).first()
    if match := PMC_REF.fullmatch(ref):
        return session.exec(select(Paper).where(Paper.pmcid == match.group(1).upper())).first()
    paper = session.exec(select(Paper).where(Paper.bibtex_key == ref)).first()
    if paper:
        return paper
    words = ref.lower().split()
    # match on titles only: loading whole papers is slow for large libraries
    matches = [
        (paper_id, title)
        for paper_id, title in session.exec(select(Paper.id, Paper.title)).all()
        if all(w in title.lower() for w in words)
    ]
    if len(matches) == 1:
        return session.get(Paper, matches[0][0])
    if not matches:
        raise ReadingError(f"Nothing matches '{ref}'. Use {REF_HELP}.")
    listing = "; ".join(f"#{paper_id} {title[:50]}" for paper_id, title in matches[:5])
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
    """Put a paper on the queue (at ``position``, default last); enforces the limit."""
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
    if source == "recommendation":
        # recommendations join the "Recommended" library only once you queue them
        libraries.add_recommended(session, paper)
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
    """Penalty for a paper you put on the queue but let go unread.

    Applies to every queued paper (from the recommendations, your library or
    added by hand) that leaves the queue, removed or marked not relevant,
    before it was at least skimmed: the queue is a commitment.
    """
    if item is None or read_depth >= 2:
        return None
    profile, _ = ensure_profile(session, cfg, at)
    penalty = profile_rules(profile).drop_penalty
    if not penalty:
        return None
    event = XpEvent(
        paper_id=paper.id,
        points=-penalty,
        kind="penalty",
        reason=f"Queued paper left the queue unread: {paper.title[:60]}",
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
    if item is None or deepest_depth(session, paper.id) >= 2:
        return 0
    return active_rules(session, cfg).drop_penalty


def fetch_paper(session: Session, cfg: Config, http: HttpClient, ref: str) -> Paper:
    """Store a paper that is not in the database yet: by DOI, ``pmid:N`` or ``PMCn``.

    OpenAlex supplies metrics for DOIs; Europe PMC the PMID, the PMC id and the
    abstract; PubMed is the fallback for PMIDs that Europe PMC does not know.
    A failing source is skipped as long as one of them knows the paper.
    """
    ref = ref.strip()
    doi = normalize_doi(ref)
    pmid = match.group(1) if (match := PMID_REF.fullmatch(ref)) else None
    pmcid = match.group(1).upper() if (match := PMC_REF.fullmatch(ref)) else None
    if not (doi or pmid or pmcid):
        raise ReadingError(
            "Not found. Enter a DOI, pmid:<N>, PMC<N>, r1–r5, a citation key or title words."
        )
    problems = []
    paper = None
    if not doi:  # PMID or PMC id: Europe PMC, then PubMed
        hit = _lookup(cfg, http, problems, pmid=pmid, pmcid=pmcid)
        if hit is None and pmid and cfg.sources.pubmed:
            try:
                hits = PubMed(http, cfg.api.ncbi_key).summaries([pmid])
                hit = hits[0] if hits else None
            except HttpError as error:
                problems.append(f"PubMed: {error}")
        if hit is None:
            raise ReadingError(f"Could not find {ref}" + _because(problems))
        paper = upsert_hit(session, hit)
        doi = hit.doi
    if doi:  # metrics from OpenAlex; identifiers and abstract from Europe PMC
        try:
            works = OpenAlex(http, cfg.api.openalex_key, cfg.api.contact_email).works_by_doi([doi])
        except HttpError as error:
            works = {}
            problems.append(f"OpenAlex: {error}")
        if doi in works:
            paper = upsert_work(session, works[doi])
        # Europe PMC adds PMID, PMC id (free full text) and abstract where they are missing
        if paper is None or not (paper.pmid and paper.pmcid and paper.abstract):
            hit = _lookup(cfg, http, problems, doi=doi)
            if hit is not None:
                paper = upsert_hit(session, hit)
    if paper is None:
        raise ReadingError(f"No database knows {doi}; add it by title instead" + _because(problems))
    session.flush()
    return paper


def _lookup(cfg: Config, http: HttpClient, problems: list[str], **ids) -> Hit | None:
    if not cfg.sources.europepmc:
        return None
    try:
        return EuropePMC(http).lookup(**ids)
    except HttpError as error:
        problems.append(f"Europe PMC: {error}")
        return None


def _because(problems: list[str]) -> str:
    return f" ({'; '.join(p[:120] for p in problems)})" if problems else ""


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
    """Where a queued paper came from (shown in the UI and used for badges).

    A paper picked from the recommendations counts as "recommendation" even
    when it is also in one of your libraries.
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


def _has_note(session: Session, paper: Paper) -> bool:
    reading = session.get(Reading, paper.id)
    return bool(reading and reading.notes) or session.get(NoteFile, paper.id) is not None


def _pull_note(session: Session, cfg: Config, paper: Paper, at: datetime, adding: bool) -> None:
    """Take over edits made in the note file before appending to the note."""
    if adding or _has_note(session, paper):
        notes.refresh_paper(session, cfg, paper, metadata=False, award=False, commit=False, at=at)


def _push_note(session: Session, cfg: Config, paper: Paper) -> None:
    """Write the note, status and rating into the note file (never fails the reading)."""
    if not _has_note(session, paper):
        return  # no note: nothing to write (a file made elsewhere is linked on sync)
    try:
        notes.refresh_paper(session, cfg, paper, award=False)
    except Exception:  # the reading is saved; the file follows at the next sync
        session.rollback()


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
    session.add(reading)
    session.flush()
    _pull_note(session, cfg, paper, at, bool(note))
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

    profile, started = ensure_profile(session, cfg, at)
    rules = profile_rules(profile)
    award = award_reading(
        session,
        rules,
        paper,
        previous_depth,
        status,
        was_top and depth(status) >= 2,
        rated,
        emptied,
        at,
    )
    award.profile_started = started
    if status == ReadStatus.dismissed:
        penalty = unread_drop_penalty(session, cfg, paper, removed, previous_depth, at)
        if penalty:
            award.events.append(penalty)
            session.flush()
            award.level_after = level_for(total_xp(session), rules)
    session.commit()
    _push_note(session, cfg, paper)
    session.refresh(reading)
    return ReadingOutcome(reading, previous, left_queue, award)


def add_note(session: Session, cfg: Config, paper: Paper, text: str, at: datetime | None = None):
    """Add a note without changing the reading status (may earn the notes bonus)."""
    at = at or now()
    reading = session.get(Reading, paper.id)
    if reading is None:
        raise ReadingError("Mark the paper first (e.g. 'read <ref> abstract'), then add notes")
    _pull_note(session, cfg, paper, at, True)
    append_note(reading, text, at)
    reading.updated_at = at
    session.add(reading)
    session.flush()
    profile, started = ensure_profile(session, cfg, at)
    award = award_reading(
        session,
        profile_rules(profile),
        paper,
        deepest_depth(session, paper.id),
        reading.status,
        False,
        False,
        False,
        at,
    )
    award.profile_started = started
    session.commit()
    _push_note(session, cfg, paper)
    return award
