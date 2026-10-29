"""SQLite schema and session helpers."""

from datetime import date, datetime
from enum import StrEnum
from pathlib import Path

import numpy as np
from pydantic import NaiveDatetime
from sqlalchemy import JSON, Column, LargeBinary, UniqueConstraint, event, inspect, text
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
    # "detail" and "skim" were used by batches built before v0.1.0; new
    # recommendations leave the reading depth to the reader ("open")
    detail = "detail"
    skim = "skim"
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


class StatusEvent(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    paper_id: int = Field(foreign_key="paper.id", index=True)
    status: ReadStatus
    # True if the paper was at the top of the reading queue at that moment
    was_queue_top: bool = False
    at: NaiveDatetime = Field(default_factory=now, index=True)


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
    litresearch keeps for you (see ``libraries.py``)."""

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    description: str = ""
    # imported: from a reference manager export · recommended: recommendations you queued
    kind: str = "imported"
    # bibtex | ris | csl-json (of the last import)
    format: str | None = None
    # the file a "replace" import or `litresearch ingest` reads (e.g. synced by rclone)
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


# seconds a writer waits for another writer (e.g. CLI while the web UI runs)
BUSY_TIMEOUT = 30


def get_engine(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{path}", connect_args={"timeout": BUSY_TIMEOUT})

    @event.listens_for(engine, "connect")
    def _durable(connection, _record):
        # every commit reaches the disk before it returns, also on SD cards
        connection.execute("PRAGMA synchronous=FULL")

    return engine


def add_missing_columns(engine) -> list[str]:
    """Minimal migration: add columns that exist in the models but not in the file.

    Only nullable columns or columns with a scalar default can be added this
    way, which covers additive schema changes. Returns ``table.column`` names.
    """
    inspector = inspect(engine)
    added = []
    models = {m.__tablename__: m for m in SQLModel.__subclasses__() if hasattr(m, "__table__")}
    with engine.begin() as connection:
        for table in SQLModel.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                kind = column.type.compile(engine.dialect)
                default = ""
                scalar = getattr(column.default, "arg", None)
                if isinstance(scalar, bool | int | float):
                    default = f" DEFAULT {int(scalar) if isinstance(scalar, bool) else scalar}"
                elif isinstance(scalar, str):
                    default = " DEFAULT '" + scalar.replace("'", "''") + "'"
                elif isinstance(column.type, JSON):
                    # existing rows would stay NULL, which the models do not expect
                    factory = models.get(table.name).model_fields[column.name].default_factory
                    if factory is empty_list:
                        default = " DEFAULT '[]'"
                    elif factory is empty_dict:
                        default = " DEFAULT '{}'"
                connection.execute(
                    text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {kind}{default}')
                )
                added.append(f"{table.name}.{column.name}")
    return added


def add_missing_indexes(engine) -> None:
    """Create indexes added to the model after a database was created.

    ``create_all`` only indexes new tables, so older databases get them here.
    """
    for table in SQLModel.metadata.sorted_tables:
        for index in table.indexes:
            index.create(engine, checkfirst=True)


def init_db(path: Path):
    engine = get_engine(path)
    SQLModel.metadata.create_all(engine)
    add_missing_columns(engine)
    add_missing_indexes(engine)
    return engine
