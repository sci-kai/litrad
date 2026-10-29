"""Creating and finding papers outside the library (candidates, manual adds)."""

from sqlmodel import Session, select

from litresearch.db import Paper, now
from litresearch.sources.hits import Hit
from litresearch.sources.openalex import Work


def upsert_work(session: Session, work: Work) -> Paper:
    """Store an OpenAlex work, matched by DOI, then by PMID (a paper without DOI).

    Library papers keep their Paperpile metadata; only the metrics are
    refreshed for them.
    """
    paper = session.exec(select(Paper).where(Paper.doi == work.doi)).first() if work.doi else None
    if paper is None and work.pmid:
        paper = session.exec(
            select(Paper).where(Paper.pmid == work.pmid, Paper.doi.is_(None))
        ).first()
    if paper is None:
        paper = Paper(title=work.title or "(untitled)", doi=work.doi, resolved_via="openalex")
    paper.doi = paper.doi or work.doi
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


def upsert_hit(session: Session, hit: Hit) -> Paper:
    """Store a Europe PMC or PubMed result, matched by DOI, then PMID.

    A new paper takes all fields of the hit. A stored paper (from Paperpile or
    OpenAlex) keeps its data; the hit only fills what is missing, e.g. the
    PMC id or the abstract. Europe PMC's citation counts only cover its own
    collection, so OpenAlex counts are never replaced.
    """
    paper = None
    if hit.doi:
        paper = session.exec(select(Paper).where(Paper.doi == hit.doi)).first()
    if paper is None and hit.pmid:
        paper = session.exec(select(Paper).where(Paper.pmid == hit.pmid)).first()
    if paper is None:
        paper = Paper(title=hit.title, resolved_via=hit.source)
    paper.authors = paper.authors or list(hit.authors)
    paper.year = paper.year or hit.year
    paper.pub_date = paper.pub_date or hit.pub_date
    paper.venue = paper.venue or hit.journal
    if paper.citation_count is None:
        paper.citation_count = hit.cited_by
    paper.doi = paper.doi or hit.doi
    paper.pmid = paper.pmid or hit.pmid
    paper.pmcid = paper.pmcid or hit.pmcid
    paper.abstract = paper.abstract or hit.abstract
    paper.updated_at = now()
    session.add(paper)
    return paper


def paper_link(paper: Paper) -> str:
    """The best link to a paper: DOI, else PubMed, else PubMed Central, else its URL."""
    if paper.doi:
        return f"https://doi.org/{paper.doi}"
    if paper.pmid:
        return f"https://pubmed.ncbi.nlm.nih.gov/{paper.pmid}"
    if paper.pmcid:
        return f"https://europepmc.org/article/PMC/{paper.pmcid}"
    return paper.url or ""
