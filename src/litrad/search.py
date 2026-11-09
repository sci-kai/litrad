"""Find papers in Europe PMC and PubMed by keywords, title, author, journal and year.

One structured ``SearchQuery`` is translated into each database's own query
language; the results are merged by DOI/PMID and marked with what you already
did with them (in your library, on the queue, read). Adding a result goes
through ``reading.fetch_paper`` with its DOI or PMID, like any other reference.

Only what you type into the search form is sent, no personal data.
"""

import re
from dataclasses import dataclass, field

from sqlmodel import Session, select

from litrad.config import Config
from litrad.db import Paper, QueueItem, Reading
from litrad.sources.europepmc import EuropePMC
from litrad.sources.hits import Hit
from litrad.sources.http import HttpClient, HttpError
from litrad.sources.pubmed import PubMed

SOURCES = {"europepmc": "Europe PMC", "pubmed": "PubMed"}
SORTS = {"relevance": "relevance", "newest": "newest first", "cited": "most cited"}


def _plain(text: str) -> str:
    """A value for a quoted field: no quotes, single spaces."""
    return re.sub(r"\s+", " ", text.replace('"', " ")).strip()


def _split(text: str) -> list[str]:
    """Several authors or journals, separated by ";"."""
    return [_plain(part) for part in text.split(";") if _plain(part)]


@dataclass
class SearchQuery:
    keywords: str = ""  # anywhere (title, abstract, keywords); database syntax allowed
    title: str = ""  # words in the title
    author: str = ""  # "Swanton C" or "Swanton"; several separated by ";"
    journal: str = ""  # "Nature" or "Nat Genet"; several separated by ";"
    year_from: int | None = None
    year_to: int | None = None
    open_access: bool = False

    def is_empty(self) -> bool:
        return not any(
            [
                self.keywords.strip(),
                self.title.strip(),
                self.author.strip(),
                self.journal.strip(),
                self.year_from,
                self.year_to,
            ]
        )

    def describe(self) -> str:
        parts = []
        if self.keywords.strip():
            parts.append(f"“{self.keywords.strip()}”")
        if self.title.strip():
            parts.append(f"title: {self.title.strip()}")
        if self.author.strip():
            parts.append(f"author: {self.author.strip()}")
        if self.journal.strip():
            parts.append(f"journal: {self.journal.strip()}")
        if self.year_from or self.year_to:
            parts.append(f"years {self.year_from or '…'}–{self.year_to or '…'}")
        if self.open_access:
            parts.append("open access")
        return " · ".join(parts)

    def europepmc(self) -> str:
        """The query in Europe PMC syntax."""
        parts = []
        if keywords := self.keywords.strip():
            # Europe PMC searches full texts by default, which finds papers that only
            # mention the words; like PubMed, look in title and abstract unless the
            # keywords already use Europe PMC's field syntax (e.g. KW:, MESH:)
            fielded = re.search(r"\b[A-Z_]{2,}:", keywords)
            parts.append(f"({keywords})" if fielded else f"TITLE_ABS:({keywords})")
        if self.title.strip():
            parts.append(f'TITLE:"{_plain(self.title)}"')
        if names := _split(self.author):
            parts.append("(" + " OR ".join(f'AUTH:"{name}"' for name in names) + ")")
        if journals := _split(self.journal):
            parts.append("(" + " OR ".join(f'JOURNAL:"{name}"' for name in journals) + ")")
        if self.year_from or self.year_to:
            parts.append(f"PUB_YEAR:[{self.year_from or 1800} TO {self.year_to or 3000}]")
        if self.open_access:
            parts.append("OPEN_ACCESS:y")
        return " AND ".join(parts)

    def pubmed(self) -> str:
        """The query in PubMed syntax."""
        parts = []
        if self.keywords.strip():
            parts.append(f"({self.keywords.strip()})")
        if self.title.strip():
            parts.append(f'"{_plain(self.title)}"[ti]')
        if names := _split(self.author):
            parts.append("(" + " OR ".join(f'"{name}"[au]' for name in names) + ")")
        if journals := _split(self.journal):
            parts.append("(" + " OR ".join(f'"{name}"[journal]' for name in journals) + ")")
        if self.year_from or self.year_to:
            parts.append(f"{self.year_from or 1800}:{self.year_to or 3000}[dp]")
        if self.open_access:
            parts.append('"free full text"[sb]')
        return " AND ".join(parts)


@dataclass
class Result:
    hit: Hit
    paper_id: int | None = None  # already stored in litrad
    in_library: bool = False
    queued: bool = False
    status: str | None = None  # reading status, e.g. "read" or "dismissed"


@dataclass
class SearchResults:
    query: SearchQuery
    results: list[Result] = field(default_factory=list)
    totals: dict[str, int] = field(default_factory=dict)  # matches per database
    errors: dict[str, str] = field(default_factory=dict)  # database → problem


def search(
    query: SearchQuery,
    cfg: Config,
    http: HttpClient,
    session: Session,
    sources: list[str] | None = None,
    limit: int = 25,
    sort: str = "relevance",
) -> SearchResults:
    """Search the chosen databases (default: all enabled) and merge the results.

    A database that fails (outage, rate limit) is reported in ``errors``; the
    others still return their results.
    """
    sources = sources or enabled_sources(cfg)
    found = SearchResults(query=query)
    merged: dict[str, Hit] = {}
    for source in sources:
        try:
            if source == "europepmc":
                total, hits = EuropePMC(http).search(query.europepmc(), limit, sort)
            elif source == "pubmed":
                total, hits = PubMed(http, cfg.api.ncbi_key).search(query.pubmed(), limit, sort)
            else:
                continue
        except HttpError as error:
            found.errors[source] = str(error)[:300]
            continue
        found.totals[source] = total
        for hit in hits:
            if hit.key in merged:
                merged[hit.key].merge(hit)
            else:
                merged[hit.key] = hit
    hits = list(merged.values())
    # each database sorted its own list; keep that order but sort the merged list
    if sort == "newest":
        hits.sort(key=lambda h: (h.pub_date is not None, h.pub_date), reverse=True)
    elif sort == "cited":
        hits.sort(key=lambda h: h.cited_by or 0, reverse=True)
    found.results = [_status(session, hit) for hit in hits]
    return found


def enabled_sources(cfg: Config) -> list[str]:
    chosen = [s for s in SOURCES if getattr(cfg.sources, s, False)]
    return chosen or ["europepmc"]


def _status(session: Session, hit: Hit) -> Result:
    """What you already did with this paper."""
    paper = None
    if hit.doi:
        paper = session.exec(select(Paper).where(Paper.doi == hit.doi)).first()
    if paper is None and hit.pmid:
        paper = session.exec(select(Paper).where(Paper.pmid == hit.pmid)).first()
    if paper is None:
        return Result(hit)
    reading = session.get(Reading, paper.id)
    return Result(
        hit,
        paper_id=paper.id,
        in_library=paper.in_library,
        queued=session.get(QueueItem, paper.id) is not None,
        status=reading.status.value if reading else None,
    )


def search_library(session: Session, query: SearchQuery) -> list[Paper]:
    """The same fields applied to the papers litrad already knows (offline)."""

    def has_all(text: str, words: str) -> bool:
        return all(w in text for w in words.lower().split())

    def has_any(text: str, names: str) -> bool:
        return any(has_all(text, name) for name in _split(names)) if _split(names) else True

    # only the searched columns are read for every paper (full papers with their
    # abstracts were slow for large libraries); the matches are loaded afterwards
    columns = (
        Paper.id,
        Paper.title,
        Paper.authors,
        Paper.bibtex_key,
        Paper.doi,
        Paper.pmid,
        Paper.venue,
        Paper.folders,
        Paper.tags,
        Paper.year,
        Paper.pub_date,
        Paper.pmcid,
    )
    if query.open_access:
        rows = session.exec(select(*columns).where(Paper.pmcid.is_not(None)))
    else:
        rows = session.exec(select(*columns))
    found = []
    for row in rows.all():
        authors = " ".join(row.authors or [])
        everything = " ".join(
            [
                row.title,
                authors,
                row.bibtex_key or "",
                row.doi or "",
                row.pmid or "",
                row.venue or "",
                " ".join(row.folders or []),
                " ".join(row.tags or []),
            ]
        ).lower()
        year = row.year or (row.pub_date.year if row.pub_date else None)
        if (
            has_all(everything, query.keywords)
            and has_all(row.title.lower(), query.title)
            and has_any(authors.lower(), query.author)
            and has_any((row.venue or "").lower(), query.journal)
            and (not query.year_from or (year and year >= query.year_from))
            and (not query.year_to or (year and year <= query.year_to))
            and (not query.open_access or row.pmcid)
        ):
            found.append(row.id)
    papers = {}
    for start in range(0, len(found), 500):
        chunk = found[start : start + 500]
        papers.update(
            (p.id, p) for p in session.exec(select(Paper).where(Paper.id.in_(chunk))).all()
        )
    return [papers[paper_id] for paper_id in found]
