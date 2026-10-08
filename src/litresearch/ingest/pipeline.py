"""Library ingest: BibTeX -> database, DOI resolution, metadata enrichment."""

import hashlib
import logging
from dataclasses import dataclass, field

from sqlmodel import Session, select

from litresearch.config import Config
from litresearch.db import IngestRun, Paper, now, store_embedding
from litresearch.ingest.bibtex import BibEntry, parse_bibtex
from litresearch.ingest.check import BibCheck, check_entries
from litresearch.sources import crossref
from litresearch.sources.http import HttpClient, HttpError
from litresearch.sources.openalex import OpenAlex
from litresearch.sources.semanticscholar import EMBEDDING_MODEL, SemanticScholar

log = logging.getLogger(__name__)


@dataclass
class IngestReport:
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
            f"BibTeX entries:        {self.entries}",
            f"  added / updated:     {self.added} / {self.updated}",
            f"  removed from library:{self.removed:>3}",
            f"DOI from BibTeX:       {self.doi_from_bibtex}",
            f"New DOI via Crossref:  {self.doi_from_crossref}",
            f"New DOI via PMID:      {self.doi_from_openalex}",
            f"Not found anywhere:    {len(self.unresolved)} {', '.join(self.unresolved)}".rstrip(),
            f"Enriched (OpenAlex):   {self.openalex_enriched}",
            f"Enriched (S2):         {self.s2_enriched}",
            f"SPECTER2 embeddings:   {self.embeddings}",
        ]
        if self.duplicates:
            out.append(f"Duplicate DOIs:        {', '.join(self.duplicates)}")
        out += [f"WARNING: {error}" for error in self.errors]
        return out


def _apply_bibtex(paper: Paper, entry: BibEntry) -> None:
    paper.bibtex_key = entry.key
    paper.entry_type = entry.entry_type
    paper.title = entry.title
    paper.authors = list(entry.authors)
    paper.year = entry.year
    paper.pub_date = entry.pub_date or paper.pub_date
    paper.venue = entry.journal or paper.venue
    paper.abstract = entry.abstract or paper.abstract
    paper.pmid = entry.pmid or paper.pmid
    paper.url = entry.url
    paper.folders = list(entry.folders)
    paper.tags = list(entry.tags)
    paper.in_library = True
    paper.updated_at = now()


def sync_library(session: Session, entries: list[BibEntry], report: IngestReport) -> list[Paper]:
    """Upsert BibTeX entries; papers missing from the export leave the library."""
    by_key = {
        paper.bibtex_key: paper
        for paper in session.exec(select(Paper).where(Paper.bibtex_key.is_not(None)))
    }
    by_doi = {
        paper.doi: paper for paper in session.exec(select(Paper).where(Paper.doi.is_not(None)))
    }
    seen_keys, library = set(), []
    for entry in entries:
        seen_keys.add(entry.key)
        paper = by_key.get(entry.key)
        if paper is None and entry.doi and entry.doi in by_doi:
            # e.g. a recommended paper that has since been added to Paperpile
            candidate = by_doi[entry.doi]
            paper = candidate if candidate.bibtex_key is None else None
        if paper is None:
            paper = Paper(title=entry.title)
            report.added += 1
        else:
            report.updated += 1
        _apply_bibtex(paper, entry)
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
        library.append(paper)

    for key, paper in by_key.items():
        if key not in seen_keys and paper.in_library:
            paper.in_library = False
            session.add(paper)
            report.removed += 1
    session.flush()
    return library


def resolve_missing_dois(
    session: Session, papers: list[Paper], http: HttpClient, cfg: Config, report: IngestReport
) -> None:
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


def ingest_library(
    cfg: Config, session: Session, http: HttpClient, use_s2: bool = True, source: str = "file"
) -> IngestReport:
    report = IngestReport()
    duplicate_keys = []
    entries = parse_bibtex(cfg.paths.bibtex, cfg.library.ignore_labels, duplicate_keys)
    report.errors += [
        f"duplicate BibTeX key {key}: only the first entry is used" for key in duplicate_keys
    ]
    report.entries = len(entries)
    report.check = check_entries(entries, duplicate_keys)
    papers = sync_library(session, entries, report)
    resolve_missing_dois(session, papers, http, cfg, report)
    if cfg.sources.openalex:
        enrich_openalex(session, papers, http, cfg, report)
    if use_s2 and cfg.sources.semanticscholar:
        enrich_semanticscholar(session, papers, http, cfg, report)
    report.unresolved = [p.bibtex_key for p in papers if not (p.doi or p.openalex_id)]
    raw = cfg.paths.bibtex.read_bytes()
    session.add(
        IngestRun(
            source=source,
            file_name=cfg.paths.bibtex.name,
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


def last_ingest(session: Session) -> IngestRun | None:
    return session.exec(select(IngestRun).order_by(IngestRun.id.desc())).first()
