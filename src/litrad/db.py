"""SQLite schema and session helpers."""

from datetime import date, datetime
from enum import StrEnum
from pathlib import Path

import numpy as np
from pydantic import NaiveDatetime
from sqlalchemy import JSON, Column, LargeBinary, UniqueConstraint, event
from sqlalchemy.engine import URL
from sqlmodel import Field, Session, SQLModel, create_engine, select


# Plain functions instead of ``default_factory=list``/``dict``: pydantic calls
# ``inspect.signature`` on builtin factories at every model construction (slow).
def empty_list() -> list:
    return []


def empty_dict() -> dict:
    return {}


def now() -> datetime:
    # ISO 8601, second precision, local system time
    return datetime.now().replace(microsecond=0)


class ReadStatus(StrEnum):
    """How far a paper has been read; ``dismissed`` = not relevant."""

    abstract = "abstract"
    skimmed = "skimmed"
    read = "read"
    thorough = "thorough"
    dismissed = "dismissed"


# reading depth in increasing order
READ_LEVELS = [ReadStatus.abstract, ReadStatus.skimmed, ReadStatus.read, ReadStatus.thorough]


def depth(status: ReadStatus | None) -> int:
    """0 = unread/dismissed, 1 = abstract ... 4 = thorough."""
    return READ_LEVELS.index(status) + 1 if status in READ_LEVELS else 0


class Category(StrEnum):
    # recommendations leave the reading depth to the reader
    open = "open"


class Paper(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    # the citation key of the paper's first library (keys per library: LibraryPaper)
    bibtex_key: str | None = Field(default=None, index=True, unique=True)
    entry_type: str | None = None
    doi: str | None = Field(default=None, index=True, unique=True)
    pmid: str | None = Field(default=None, index=True)
    # PubMed Central id ("PMC1234567"): the paper is free to read there
    pmcid: str | None = None
    arxiv_id: str | None = None
    s2_id: str | None = Field(default=None, index=True)
    openalex_id: str | None = None
    title: str
    abstract: str | None = None
    authors: list[str] = Field(default_factory=empty_list, sa_column=Column(JSON))
    year: int | None = None
    pub_date: date | None = None
    venue: str | None = None
    venue_openalex_id: str | None = None
    url: str | None = None
    tldr: str | None = None
    citation_count: int | None = None
    influential_citation_count: int | None = None
    fwci: float | None = None
    citation_percentile: float | None = None
    venue_mean_citedness: float | None = None
    topics: list[str] = Field(default_factory=empty_list, sa_column=Column(JSON))
    # in at least one imported library (kept up to date by libraries.refresh_flags)
    in_library: bool = False
    # folders ("a/b/c") and labels of all its libraries together
    folders: list[str] = Field(default_factory=empty_list, sa_column=Column(JSON))
    tags: list[str] = Field(default_factory=empty_list, sa_column=Column(JSON))
    # where the record came from: bibtex | crossref | openalex | europepmc | pubmed
    resolved_via: str | None = None
    enriched_at: NaiveDatetime | None = None
    created_at: NaiveDatetime = Field(default_factory=now)
    updated_at: NaiveDatetime = Field(default_factory=now)


class Embedding(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("paper_id", "model"),)

    id: int | None = Field(default=None, primary_key=True)
    paper_id: int = Field(foreign_key="paper.id", index=True)
    # vectors from different models must never be compared
    model: str
    vector: bytes = Field(sa_column=Column(LargeBinary))


class RecommendationBatch(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    created_at: NaiveDatetime = Field(default_factory=now)
    # Monday of the ISO week the batch belongs to
    week_start: date = Field(index=True)
    n_candidates: int = 0
    warnings: list[str] = Field(default_factory=empty_list, sa_column=Column(JSON))
    # how many candidates each step removed (shown as a funnel)
    funnel: dict | None = Field(default=None, sa_column=Column(JSON))
    # snapshot of the selection settings used for this batch
    params: dict | None = Field(default=None, sa_column=Column(JSON))


class Recommendation(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    batch_id: int = Field(foreign_key="recommendationbatch.id", index=True)
    paper_id: int = Field(foreign_key="paper.id", index=True)
    rank: int
    category: Category
    score: float
    breakdown: dict = Field(default_factory=empty_dict, sa_column=Column(JSON))
    reason: str = ""


class Reading(SQLModel, table=True):
    paper_id: int = Field(foreign_key="paper.id", primary_key=True)
    status: ReadStatus
    rating: int | None = Field(default=None, ge=1, le=5)
    notes: str = ""
    updated_at: NaiveDatetime = Field(default_factory=now)


class NoteFile(SQLModel, table=True):
    """The Markdown file that holds a paper's notes (see ``notes.py``).

    ``Reading.notes`` is a copy of the file's body; ``sha256`` is the body
    both had at the last sync, so a change on either side can be told apart
    from a change on both (a conflict, which never overwrites anything).
    """

    paper_id: int = Field(foreign_key="paper.id", primary_key=True)
    # relative to the notes folder, "/" as separator
    path: str
    # SHA-256 of the body at the last sync ("" = unknown: differing sides are a conflict)
    sha256: str = ""
    mtime_ns: int = 0
    size: int = 0
    # hash of the front matter values litrad last wrote
    meta: str = ""
    conflict: bool = False
    synced_at: NaiveDatetime = Field(default_factory=now)


class StatusEvent(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    paper_id: int = Field(foreign_key="paper.id", index=True)
    status: ReadStatus
    # True if the paper was at the top of the reading queue at that moment
    was_queue_top: bool = False
    at: NaiveDatetime = Field(default_factory=now, index=True)
    # read before you used litrad ("read earlier", logged at ``at``): counts
    # for papers read, depth and levels, never for streaks, quests or pace
    earlier: bool = False
    # when it was read, if known (only for ``earlier``)
    read_on: date | None = None


class QueueItem(SQLModel, table=True):
    paper_id: int = Field(foreign_key="paper.id", primary_key=True)
    # 1 = read next
    position: int
    # recommendation | manual | library
    source: str = "manual"
    added_at: NaiveDatetime = Field(default_factory=now)


class XpEvent(SQLModel, table=True):
    """Every point earned, with its reason; the score is the sum."""

    id: int | None = Field(default=None, primary_key=True)
    paper_id: int | None = Field(default=None, foreign_key="paper.id", index=True)
    points: int
    kind: str = Field(index=True)
    reason: str
    at: NaiveDatetime = Field(default_factory=now, index=True)


class Library(SQLModel, table=True):
    """A collection of papers: an imported reference-manager library or a list
    litrad keeps for you (see ``libraries.py``)."""

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    description: str = ""
    # imported: from a reference manager export · recommended: recommendations you queued
    # · positive / negative: example papers for Semantic Scholar (kept by examples.py)
    kind: str = "imported"
    # bibtex | ris | csl-json (of the last import)
    format: str | None = None
    # the file a "replace" import or `litrad ingest` reads (e.g. synced by rclone)
    source_path: str | None = None
    # papers of this library are "your library" for the recommendation similarity
    use_for_recommendations: bool = True
    created_at: NaiveDatetime = Field(default_factory=now)
    updated_at: NaiveDatetime = Field(default_factory=now)


class LibraryPaper(SQLModel, table=True):
    """A paper in a library, with what the reference manager says about it there."""

    library_id: int = Field(foreign_key="library.id", primary_key=True)
    paper_id: int = Field(foreign_key="paper.id", primary_key=True)
    # the citation key in this library's export
    citation_key: str | None = Field(default=None, index=True)
    # folders ("a/b/c") and flat labels / keywords in this library
    folders: list[str] = Field(default_factory=empty_list, sa_column=Column(JSON))
    tags: list[str] = Field(default_factory=empty_list, sa_column=Column(JSON))
    added_at: NaiveDatetime = Field(default_factory=now)


class ExampleChoice(SQLModel, table=True):
    """Your choice about a paper as example for Semantic Scholar's recommendations.

    ``include`` puts it into the list of that kind (positive or negative),
    ``exclude`` keeps the rules from choosing it (see ``examples.py``).
    """

    paper_id: int = Field(foreign_key="paper.id", primary_key=True)
    # positive | negative
    kind: str = Field(primary_key=True)
    # include | exclude
    action: str
    at: NaiveDatetime = Field(default_factory=now)


class IngestRun(SQLModel, table=True):
    """One import of a reference-manager export into a library."""

    id: int | None = Field(default=None, primary_key=True)
    at: NaiveDatetime = Field(default_factory=now)
    library_id: int | None = Field(default=None, index=True)
    # new (a new library) | add (to an existing one) | replace (sync with the file)
    mode: str = "replace"
    format: str | None = None
    # upload | file (re-import of the library's file) | cli
    source: str = "file"
    file_name: str = ""
    sha256: str = ""
    size: int = 0
    entries: int = 0
    added: int = 0
    updated: int = 0
    removed: int = 0
    check: dict | None = Field(default=None, sa_column=Column(JSON))
    warnings: list[str] = Field(default_factory=empty_list, sa_column=Column(JSON))


class Profile(SQLModel, table=True):
    """The game profile. Its rules are a locked copy taken when the game started."""

    id: int | None = Field(default=None, primary_key=True)
    name: str
    created_at: NaiveDatetime = Field(default_factory=now)
    rules: dict = Field(default_factory=empty_dict, sa_column=Column(JSON))
    fingerprint: str = ""
    app_version: str = ""


class Achievement(SQLModel, table=True):
    code: str = Field(primary_key=True)
    unlocked_at: NaiveDatetime = Field(default_factory=now)


def store_embedding(session: Session, paper_id: int, model: str, vector) -> None:
    data = np.asarray(vector, dtype=np.float32).tobytes()
    existing = session.exec(
        select(Embedding).where(Embedding.paper_id == paper_id, Embedding.model == model)
    ).first()
    if existing:
        existing.vector = data
        session.add(existing)
    else:
        session.add(Embedding(paper_id=paper_id, model=model, vector=data))


def load_embedding(embedding: Embedding) -> np.ndarray:
    return np.frombuffer(embedding.vector, dtype=np.float32)


def sqlite_url(path: Path) -> URL:
    """An SQLAlchemy URL for a database file; ``#``, ``?`` or ``%`` in the path are no query."""
    return URL.create("sqlite", database=str(path))


def readonly_uri(path: Path) -> str:
    """A read-only ``file:`` URI for ``sqlite3.connect(..., uri=True)``, fully quoted."""
    return Path(path).absolute().as_uri() + "?mode=ro"


# seconds a writer waits for another writer (e.g. CLI while the web UI runs)
BUSY_TIMEOUT = 30


def get_engine(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(sqlite_url(path), connect_args={"timeout": BUSY_TIMEOUT})

    @event.listens_for(engine, "connect")
    def _durable(connection, _record):
        # every commit reaches the disk before it returns, also on SD cards
        connection.execute("PRAGMA synchronous=FULL")

    return engine


def init_db(path: Path):
    """Open a profile database; new files are created, older ones upgraded first.

    Raises ``migrations.SchemaTooNew`` for a database of a newer litrad.
    """
    from litrad.migrations import migrate

    migrate(path)
    return get_engine(path)
