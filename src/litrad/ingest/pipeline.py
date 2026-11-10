"""Importing a reference-manager export into a library, then enriching its papers.

``import_into`` reads a BibTeX, RIS or CSL-JSON file into one library, either
adding to it or replacing its content (the file is the complete library, e.g.
Paperpile's auto-export). Papers are matched to stored ones by the library's
citation key, then DOI, PMID and title + year, so a paper that is in several
libraries (or was recommended earlier) exists only once. Then missing DOIs are
resolved (Crossref) and metrics and embeddings fetched (OpenAlex, Semantic
Scholar) for the imported papers.
"""

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path

from sqlmodel import Session, select

from litrad import libraries
from litrad.config import Config
from litrad.db import IngestRun, Library, LibraryPaper, Paper, now, store_embedding
from litrad.ingest.bibtex import BibEntry
from litrad.ingest.check import BibCheck, check_entries
from litrad.ingest.formats import FORMATS, read_references
from litrad.sources import crossref
from litrad.sources.http import HttpClient, HttpError
from litrad.sources.openalex import OpenAlex
from litrad.sources.semanticscholar import EMBEDDING_MODEL, SemanticScholar

log = logging.getLogger(__name__)


@dataclass
class IngestReport:
    library: str = ""
    format: str = "bibtex"
    entries: int = 0
    added: int = 0
    updated: int = 0
    removed: int = 0
    doi_from_bibtex: int = 0
    doi_from_crossref: int = 0
    doi_from_openalex: int = 0
    unresolved: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    openalex_enriched: int = 0
    s2_enriched: int = 0
    embeddings: int = 0
    errors: list[str] = field(default_factory=list)
    check: BibCheck | None = None

    def lines(self) -> list[str]:
        out = [
            f"Library:               {self.library} ({FORMATS.get(self.format, self.format)})",
            f"Entries:               {self.entries}",
            f"  added / updated:     {self.added} / {self.updated}",
            f"  removed from library:{self.removed:>3}",
            f"DOI from the file:     {self.doi_from_bibtex}",
            f"New DOI via Crossref:  {self.doi_from_crossref}",
            f"New DOI via PMID:      {self.doi_from_openalex}",
            f"Not found anywhere:    {len(self.unresolved)} {', '.join(self.unresolved)}".rstrip(),
            f"Enriched (OpenAlex):   {self.openalex_enriched}",
            f"Enriched (S2):         {self.s2_enriched}",
            f"SPECTER2 embeddings:   {self.embeddings}",
        ]
        if self.duplicates:
            out.append(f"Duplicates (skipped):  {', '.join(self.duplicates)}")
        out += [f"WARNING: {error}" for error in self.errors]
        return out


def _apply_entry(paper: Paper, entry: BibEntry) -> None:
    """The reference manager's metadata wins over what the APIs say."""
    paper.entry_type = entry.entry_type
    paper.title = entry.title
    paper.authors = list(entry.authors) or paper.authors
    paper.year = entry.year or paper.year
    paper.pub_date = entry.pub_date or paper.pub_date
    paper.venue = entry.journal or paper.venue
    paper.abstract = entry.abstract or paper.abstract
    paper.pmid = entry.pmid or paper.pmid
    paper.url = entry.url or paper.url
    paper.updated_at = now()


def _title_key(title: str, year: int | None) -> str:
    return " ".join(title.lower().split()) + f"|{year or ''}"


def _contradicts(paper: Paper, entry: BibEntry) -> bool:
    """The entry names another paper than ``paper`` (different DOI or PMID)."""
    return bool(
        (entry.doi and paper.doi and entry.doi != paper.doi)
        or (entry.pmid and paper.pmid and entry.pmid != paper.pmid)
    )


def sync_library(
    session: Session,
    library: Library,
    entries: list[BibEntry],
    report: IngestReport,
    replace: bool = True,
) -> list[Paper]:
    """Put the entries into ``library``; with ``replace``, papers that are no
    longer in the file leave the library (they stay in others and in your
    reading history)."""
    links = session.exec(select(LibraryPaper).where(LibraryPaper.library_id == library.id)).all()
    by_link_key = {link.citation_key: link.paper_id for link in links if link.citation_key}
    before = {link.paper_id for link in links}
    papers = session.exec(select(Paper)).all()
    by_doi = {p.doi: p for p in papers if p.doi}
    by_pmid = {p.pmid: p for p in papers if p.pmid}
    # papers without a DOI in the file: also matched by title + year (their DOI may
    # have been found by litrad since, e.g. via Crossref)
    by_title = {_title_key(p.title, p.year): p for p in papers}
    seen: set[int] = set()
    imported = []
    for entry in entries:
        paper = None
        if entry.key in by_link_key and not entry.generated_key:
            paper = session.get(Paper, by_link_key[entry.key])
            if paper is not None and _contradicts(paper, entry):
                paper = None  # the key now names another paper (keys can be reused)
        if paper is None and entry.doi:
            paper = by_doi.get(entry.doi)
        if paper is None and entry.pmid:
            paper = by_pmid.get(entry.pmid)
        if paper is None and not entry.doi:
            paper = by_title.get(_title_key(entry.title, entry.year))
        if paper is not None and paper.id in seen:
            # the same paper twice in one file (same DOI, PMID or title): keep the first
            report.duplicates.append(f"{entry.key} ({entry.doi or entry.pmid or 'same title'})")
            continue
        if paper is None:
            paper = Paper(title=entry.title)
        _apply_entry(paper, entry)
        if entry.doi and paper.doi != entry.doi:
            owner = by_doi.get(entry.doi)
            if owner is not None and owner is not paper:
                report.duplicates.append(f"{entry.key} ({entry.doi})")
            else:
                paper.doi = entry.doi
                by_doi[entry.doi] = paper
        if paper.doi and paper.doi == entry.doi:
            paper.resolved_via = "bibtex"
            report.doi_from_bibtex += 1
        session.add(paper)
        session.flush()  # assigns the id of new papers
        seen.add(paper.id)
        # later entries of this file find it too (the same paper twice)
        if paper.pmid:
            by_pmid.setdefault(paper.pmid, paper)
        by_title.setdefault(_title_key(paper.title, paper.year), paper)
        if paper.id in before:
            report.updated += 1
        else:
            report.added += 1
        libraries.link(session, library, paper, entry.key, entry.folders, entry.tags)
        imported.append(paper)

    removed = set()
    if replace:
        for link in links:
            if link.paper_id not in seen:
                session.delete(link)
                removed.add(link.paper_id)
        report.removed = len(removed)
    session.flush()
    libraries.refresh_flags(session, seen | removed)
    return imported


def resolve_missing_dois(
    session: Session, papers: list[Paper], http: HttpClient, cfg: Config, report: IngestReport
) -> None:
    """Find DOIs for library papers without DOI and PMID via Crossref."""
    taken = set(session.exec(select(Paper.doi).where(Paper.doi.is_not(None))).all())
    for paper in papers:
        if paper.doi or paper.pmid:
            continue  # PMID-only papers are resolved through OpenAlex
        first_author = paper.authors[0] if paper.authors else None
        try:
            doi = crossref.resolve_doi(
                http, paper.title, first_author, paper.year, cfg.api.contact_email
            )
        except HttpError as error:
            report.errors.append(f"Crossref: {error}")
            return
        if doi and doi not in taken:
            paper.doi, paper.resolved_via = doi, "crossref"
            taken.add(doi)
            report.doi_from_crossref += 1
            session.add(paper)


def enrich_openalex(
    session: Session, papers: list[Paper], http: HttpClient, cfg: Config, report: IngestReport
) -> None:
    """Add OpenAlex metrics (citations, percentile, venue) in batches of 50."""
    client = OpenAlex(http, cfg.api.openalex_key, cfg.api.contact_email)
    try:
        by_doi = client.works_by_doi([p.doi for p in papers if p.doi])
        by_pmid = client.works_by_pmid([p.pmid for p in papers if not p.doi and p.pmid])
    except HttpError as error:
        report.errors.append(f"OpenAlex: {error}")
        return

    taken = set(session.exec(select(Paper.doi).where(Paper.doi.is_not(None))).all())
    venues = {}
    for paper in papers:
        work = by_doi.get(paper.doi) if paper.doi else by_pmid.get(paper.pmid or "")
        if work is None:
            continue
        if not paper.doi and work.doi and work.doi not in taken:
            paper.doi, paper.resolved_via = work.doi, "openalex"
            taken.add(work.doi)
            report.doi_from_openalex += 1
        paper.openalex_id = work.openalex_id
        paper.pmid = paper.pmid or work.pmid
        paper.abstract = paper.abstract or work.abstract
        paper.pub_date = paper.pub_date or work.pub_date
        paper.year = paper.year or work.year
        paper.venue = paper.venue or work.venue
        paper.venue_openalex_id = work.venue_id
        paper.citation_count = work.cited_by_count
        paper.fwci = work.fwci
        paper.citation_percentile = work.citation_percentile
        paper.topics = list(work.topics)
        paper.enriched_at = now()
        if work.venue_id:
            venues.setdefault(work.venue_id, []).append(paper)
        session.add(paper)
        report.openalex_enriched += 1

    try:
        citedness = client.venue_mean_citedness(list(venues))
    except HttpError as error:
        report.errors.append(f"OpenAlex sources: {error}")
        return
    for venue_id, venue_papers in venues.items():
        for paper in venue_papers:
            paper.venue_mean_citedness = citedness.get(venue_id)


def s2_id(paper: Paper) -> str | None:
    if paper.doi:
        return f"DOI:{paper.doi}"
    if paper.pmid:
        return f"PMID:{paper.pmid}"
    return None


def enrich_semanticscholar(
    session: Session, papers: list[Paper], http: HttpClient, cfg: Config, report: IngestReport
) -> None:
    """Add SPECTER2 embeddings, TLDRs and influential citations."""
    client = SemanticScholar(http, cfg.api.semanticscholar_key)
    ids = {s2_id(p): p for p in papers if s2_id(p)}
    try:
        found = client.papers(list(ids))
    except HttpError as error:
        report.errors.append(
            f"Semantic Scholar: {error} (an API key avoids the shared anonymous rate limit)"
        )
        return
    for requested, s2 in found.items():
        paper = ids[requested]
        paper.s2_id = s2.s2_id
        paper.tldr = s2.tldr
        paper.influential_citation_count = s2.influential_citation_count
        if paper.citation_count is None:
            paper.citation_count = s2.citation_count
        session.add(paper)
        report.s2_enriched += 1
        if s2.embedding:
            store_embedding(session, paper.id, EMBEDDING_MODEL, s2.embedding)
            report.embeddings += 1


MODES = {
    "new": "a new library",
    "add": "add to the library",
    "replace": "replace the library's content",
}


def import_into(
    cfg: Config,
    session: Session,
    http: HttpClient,
    library: Library,
    path: Path,
    mode: str = "replace",
    source: str = "file",
    use_s2: bool = True,
    fmt: str | None = None,
) -> IngestReport:
    """Import an export file into ``library`` and enrich its papers.

    ``mode``: ``new`` (a library created for this file), ``add`` (keep what
    the library has) or ``replace`` (the file is the complete library). With
    ``new`` and ``replace`` the file becomes the library's source file, which
    re-imports (``litrad ingest``, *Update from file*) read again.
    Raises ValueError for files that cannot be parsed, before anything changes.
    """
    duplicate_keys: list[str] = []
    fmt, entries = read_references(path, cfg.library.ignore_labels, duplicate_keys, fmt)
    report = IngestReport(library=library.name, format=fmt)
    report.errors += [
        f"duplicate citation key {key}: only the first entry is used" for key in duplicate_keys
    ]
    report.entries = len(entries)
    report.check = check_entries(entries, duplicate_keys)
    report.check.format = fmt
    papers = sync_library(session, library, entries, report, replace=mode != "add")
    resolve_missing_dois(session, papers, http, cfg, report)
    if cfg.sources.openalex:
        enrich_openalex(session, papers, http, cfg, report)
    if use_s2 and cfg.sources.semanticscholar:
        enrich_semanticscholar(session, papers, http, cfg, report)
    report.unresolved = [
        p.bibtex_key or p.title[:40] for p in papers if not (p.doi or p.openalex_id)
    ]
    library.format = fmt
    if mode in ("new", "replace"):
        library.source_path = str(path.resolve())
    library.updated_at = now()
    session.add(library)
    raw = path.read_bytes()
    session.add(
        IngestRun(
            library_id=library.id,
            mode=mode,
            format=fmt,
            source=source,
            file_name=path.name,
            sha256=hashlib.sha256(raw).hexdigest(),
            size=len(raw),
            entries=report.entries,
            added=report.added,
            updated=report.updated,
            removed=report.removed,
            check=report.check.to_dict(),
            warnings=list(report.errors),
        )
    )
    session.commit()
    return report


def ingest_library(
    cfg: Config, session: Session, http: HttpClient, use_s2: bool = True, source: str = "file"
) -> IngestReport:
    """Import ``paths.bibtex`` (e.g. kept up to date by rclone) into its library.

    That is the library whose source file it is, else "My library" (created
    if needed). For all libraries with a source file: ``refresh_libraries``.
    """
    target = cfg.paths.bibtex.resolve()
    library = next(
        (lib for lib in libraries.all_libraries(session) if lib.source_path == str(target)),
        None,
    )
    if library is None:
        try:
            library = libraries.find(session, libraries.DEFAULT_NAME)
        except libraries.LibraryError:
            library = libraries.create(session, libraries.DEFAULT_NAME)
    return import_into(cfg, session, http, library, cfg.paths.bibtex, "replace", source, use_s2)


def refresh_libraries(
    cfg: Config, session: Session, http: HttpClient, use_s2: bool = True
) -> list[tuple[Library, IngestReport | str]]:
    """Re-import every library from its source file; problems are reported per library."""
    results = []
    for library in libraries.all_libraries(session):
        if library.kind != libraries.IMPORTED or not library.source_path:
            continue
        path = Path(library.source_path)
        if not path.exists():
            results.append((library, f"its file {path} does not exist"))
            continue
        try:
            report = import_into(cfg, session, http, library, path, "replace", "file", use_s2)
        except (ValueError, OSError, UnicodeDecodeError) as error:
            session.rollback()
            results.append((library, f"not imported: {error}"))
            continue
        results.append((library, report))
    return results


def last_ingest(session: Session, library: Library | None = None) -> IngestRun | None:
    query = select(IngestRun).order_by(IngestRun.id.desc())
    if library is not None:
        query = query.where(IngestRun.library_id == library.id)
    return session.exec(query).first()
