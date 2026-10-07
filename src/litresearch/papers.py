"""Creating and finding papers outside the library (candidates, manual adds)."""

from sqlmodel import Session, select

from litresearch.db import Paper, now
from litresearch.sources.openalex import Work


def upsert_work(session: Session, work: Work) -> Paper:
    """Store an OpenAlex work, keyed by DOI.

    Library papers keep their Paperpile metadata; only the metrics are
    refreshed for them.
    """
    paper = session.exec(select(Paper).where(Paper.doi == work.doi)).first() if work.doi else None
    if paper is None:
        paper = Paper(title=work.title or "(untitled)", doi=work.doi, resolved_via="openalex")
    if not paper.in_library:
        paper.title = work.title or paper.title
        paper.authors = list(work.authors)
        paper.year = work.year
        paper.pub_date = work.pub_date
        paper.venue = work.venue
        paper.abstract = work.abstract or paper.abstract
    paper.pmid = paper.pmid or work.pmid
    paper.openalex_id = work.openalex_id
    paper.venue_openalex_id = work.venue_id
    paper.citation_count = work.cited_by_count
    paper.fwci = work.fwci
    paper.citation_percentile = work.citation_percentile
    paper.topics = list(work.topics)
    paper.enriched_at = paper.updated_at = now()
    session.add(paper)
    return paper
