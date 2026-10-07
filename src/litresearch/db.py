"""SQLite schema and session helpers."""

from datetime import date, datetime
from enum import Enum
from pathlib import Path

from pydantic import NaiveDatetime
from sqlalchemy import JSON, Column, LargeBinary, UniqueConstraint
from sqlmodel import Field, Session, SQLModel, create_engine


def now() -> datetime:
    # ISO 8601, second precision, local system time
    return datetime.now().replace(microsecond=0)


class ReadStatus(str, Enum):
    new = "new"
    abstract = "abstract"
    skimmed = "skimmed"
    read = "read"
    thorough = "thorough"
    later = "later"
    dismissed = "dismissed"


class Category(str, Enum):
    detail = "detail"
    skim = "skim"


class Paper(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    doi: str | None = Field(default=None, index=True, unique=True)
    pmid: str | None = Field(default=None, index=True)
    arxiv_id: str | None = None
    s2_id: str | None = Field(default=None, index=True)
    openalex_id: str | None = None
    title: str
    abstract: str | None = None
    authors: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    year: int | None = None
    pub_date: date | None = None
    venue: str | None = None
    tldr: str | None = None
    citation_count: int | None = None
    influential_citation_count: int | None = None
    fwci: float | None = None
    citation_percentile: float | None = None
    venue_mean_citedness: float | None = None
    in_library: bool = False
    library_added: date | None = None
    labels: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: NaiveDatetime = Field(default_factory=now)
    updated_at: NaiveDatetime = Field(default_factory=now)


class Embedding(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("paper_id", "model"),)

    id: int | None = Field(default=None, primary_key=True)
    paper_id: int = Field(foreign_key="paper.id", index=True)
    # vectors from different models must never be compared
    model: str
    vector: bytes = Field(sa_column=Column(LargeBinary))


class ReadingList(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    created_at: NaiveDatetime = Field(default_factory=now)


class ListItem(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    list_id: int = Field(foreign_key="readinglist.id", index=True)
    paper_id: int = Field(foreign_key="paper.id")
    rank: int
    category: Category
    score: float
    breakdown: dict = Field(default_factory=dict, sa_column=Column(JSON))
    why: str | None = None
    summary: str | None = None


class Reading(SQLModel, table=True):
    paper_id: int = Field(foreign_key="paper.id", primary_key=True)
    status: ReadStatus = ReadStatus.new
    rating: int | None = Field(default=None, ge=1, le=5)
    tags: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    notes: str = ""
    updated_at: NaiveDatetime = Field(default_factory=now)


class StatusEvent(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    paper_id: int = Field(foreign_key="paper.id", index=True)
    status: ReadStatus
    at: NaiveDatetime = Field(default_factory=now)


def get_engine(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{path}")


def init_db(path: Path):
    engine = get_engine(path)
    SQLModel.metadata.create_all(engine)
    return engine


def set_status(session: Session, paper_id: int, status: ReadStatus) -> Reading:
    """Set the reading status of a paper and record the change."""
    reading = session.get(Reading, paper_id) or Reading(paper_id=paper_id)
    reading.status = status
    reading.updated_at = now()
    session.add(reading)
    session.add(StatusEvent(paper_id=paper_id, status=status))
    session.commit()
    session.refresh(reading)
    return reading
