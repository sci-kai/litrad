"""Weekly recommendations: gather candidates, score them, store the batch."""

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

import numpy as np
from sqlmodel import Session, select

from litresearch.config import Config
from litresearch.db import (
    Embedding,
    Paper,
    QueueItem,
    Reading,
    ReadStatus,
    Recommendation,
    RecommendationBatch,
    load_embedding,
    store_embedding,
)
from litresearch.papers import upsert_work
from litresearch.recommend.scoring import Candidate, LibraryProfile, describe, rank_candidates
from litresearch.sources.http import HttpClient, HttpError
from litresearch.sources.openalex import OpenAlex, Work
from litresearch.sources.semanticscholar import EMBEDDING_MODEL, SemanticScholar

log = logging.getLogger(__name__)

ALLOWED_TYPES = {None, "article", "review", "preprint"}
# conference abstracts in journal supplements, e.g. "0865 The role of ..." or "P-123 ..."
MEETING_ABSTRACT = re.compile(
    r"^\s*(?:[A-Z]{1,3}[-.]\d{1,5}[A-Z]?[\s.:]"  # P-123, LB-01
    r"|[A-Z]{1,3}\d{1,5}[.:]\s"  # LB01. / P123:  (but not "CD8 T cells")
    r"|0\d{1,4}\s"  # zero-padded abstract numbers: 0865
    r"|\d{1,5}[.:]\s)"  # 123. / 123:
)
S2_POSITIVES = 100
S2_NEGATIVES = 50


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


@dataclass
class Gathered:
    works: dict[str, Work] = field(default_factory=dict)
    topics: dict[str, set[str]] = field(default_factory=dict)
    sources: dict[str, set[str]] = field(default_factory=dict)
    found_by: dict[str, list[dict]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    without_doi: int = 0

    def add(
        self,
        work: Work,
        source: str,
        topic: str | None = None,
        query: str | None = None,
        position: int | None = None,
    ) -> None:
        if not work.doi:
            self.without_doi += 1
            return
        self.works.setdefault(work.doi, work)
        self.sources.setdefault(work.doi, set()).add(source)
        if topic:
            self.topics.setdefault(work.doi, set()).add(topic)
        self.found_by.setdefault(work.doi, []).append(
            {"source": source, "topic": topic, "query": query, "position": position}
        )


def gather_topic_candidates(cfg: Config, openalex: OpenAlex, today: date, out: Gathered) -> None:
    sel = cfg.selection
    fresh_from = today - timedelta(days=int(sel.fresh_window_months * 30.5))
    landmark_years = f"{today.year - sel.landmark_max_age_years}-{today.year - 1}"
    min_percentile = f">{sel.landmark_min_percentile / 100:.4f}"
    for topic in cfg.topics:
        for query in topic.queries:
            searches = [
                (
                    "openalex-fresh",
                    {
                        "from_publication_date": fresh_from.isoformat(),
                        "type": "article|preprint|review",
                    },
                ),
                (
                    "openalex-landmark",
                    {
                        "publication_year": landmark_years,
                        "citation_normalized_percentile.value": min_percentile,
                    },
                ),
            ]
            for source, filters in searches:
                try:
                    works = openalex.search_works(query, filters, sel.results_per_query)
                except HttpError as error:
                    out.warnings.append(f"OpenAlex search '{query}': {error}")
                    continue
                for position, work in enumerate(works, start=1):
                    out.add(work, source, topic.name, query, position)


def gather_s2_candidates(
    session: Session, s2: SemanticScholar, openalex: OpenAlex, out: Gathered
) -> None:
    """Recently published papers similar to the library and to well-rated papers."""
    library = session.exec(select(Paper).where(Paper.in_library, Paper.doi.is_not(None))).all()
    liked = session.exec(
        select(Paper)
        .join(Reading, Reading.paper_id == Paper.id)
        .where(Paper.doi.is_not(None), (Reading.rating >= 4) | (Reading.status == "thorough"))
    ).all()
    dismissed = session.exec(
        select(Paper)
        .join(Reading, Reading.paper_id == Paper.id)
        .where(Paper.doi.is_not(None), Reading.status == ReadStatus.dismissed)
    ).all()
    # liked papers first, then the most recent library papers
    recent = sorted(library, key=lambda p: p.pub_date or date(p.year or 1900, 7, 1), reverse=True)
    positives = list(dict.fromkeys(f"DOI:{p.doi}" for p in liked + recent))[:S2_POSITIVES]
    negatives = [f"DOI:{p.doi}" for p in dismissed][:S2_NEGATIVES]
    if not positives:
        return
    try:
        dois = s2.recommend(positives, negatives)
        works = openalex.works_by_doi(dois)
    except HttpError as error:
        out.warnings.append(f"Semantic Scholar recommendations: {error}")
        return
    rank = {doi: i for i, doi in enumerate(dois, start=1)}
    for work in works.values():
        out.add(work, "s2-recs", position=rank.get(work.doi))


def exclusion_sets(session: Session, cfg: Config, today: date) -> dict[str, set[int]]:
    """Papers that must not be recommended, by reason.

    Recommendations are made on demand, so every batch within the cooldown
    counts, also one from today: asking again brings new papers.
    """
    since = today - timedelta(weeks=cfg.selection.cooldown_weeks)
    return {
        # an abstract glance does not count as read; library papers are fine
        "read_or_dismissed": set(
            session.exec(
                select(Reading.paper_id).where(Reading.status != ReadStatus.abstract)
            ).all()
        ),
        "queued": set(session.exec(select(QueueItem.paper_id)).all()),
        "recently_recommended": set(
            session.exec(
                select(Recommendation.paper_id)
                .join(RecommendationBatch, RecommendationBatch.id == Recommendation.batch_id)
                .where(RecommendationBatch.created_at >= datetime.combine(since, time.min))
            ).all()
        ),
    }


def has_excluded_term(paper: Paper, terms: list[str]) -> bool:
    text = f"{paper.title} {paper.abstract or ''}".lower()
    return any(term.lower() in text for term in terms)


def fetch_embeddings(
    session: Session, papers: list[Paper], s2: SemanticScholar, warnings: list[str]
) -> None:
    have = set(
        session.exec(select(Embedding.paper_id).where(Embedding.model == EMBEDDING_MODEL)).all()
    )
    missing = {f"DOI:{p.doi}": p for p in papers if p.id not in have and p.doi}
    if not missing:
        return
    try:
        found = s2.papers(list(missing))
    except HttpError as error:
        warnings.append(f"Semantic Scholar embeddings: {error} (falling back to text similarity)")
        return
    for requested, info in found.items():
        paper = missing[requested]
        paper.s2_id, paper.tldr = info.s2_id, info.tldr or paper.tldr
        paper.influential_citation_count = info.influential_citation_count
        if info.embedding:
            store_embedding(session, paper.id, EMBEDDING_MODEL, info.embedding)


def vectors_for(session: Session, paper_ids: list[int]) -> dict[int, np.ndarray]:
    rows = session.exec(
        select(Embedding).where(
            Embedding.model == EMBEDDING_MODEL, Embedding.paper_id.in_(paper_ids)
        )
    ).all()
    return {row.paper_id: load_embedding(row) for row in rows}


def build_recommendations(
    cfg: Config,
    session: Session,
    http: HttpClient,
    today: date | None = None,
    use_s2: bool = True,
) -> RecommendationBatch:
    today = today or date.today()
    openalex = OpenAlex(http, cfg.api.openalex_key, cfg.api.contact_email)
    s2 = SemanticScholar(http, cfg.api.semanticscholar_key)
    gathered = Gathered()

    if cfg.sources.openalex:
        gather_topic_candidates(cfg, openalex, today, gathered)
    if use_s2 and cfg.sources.semanticscholar:
        gather_s2_candidates(session, s2, openalex, gathered)

    exclusions = exclusion_sets(session, cfg, today)
    removed = dict.fromkeys(["wrong_type", "meeting_abstract", *exclusions, "excluded_term"], 0)
    candidates: list[Candidate] = []
    for doi, work in gathered.works.items():
        if work.type not in ALLOWED_TYPES:
            removed["wrong_type"] += 1
            continue
        if MEETING_ABSTRACT.match(work.title or ""):
            removed["meeting_abstract"] += 1
            continue
        paper = upsert_work(session, work)
        session.flush()
        reason = next((r for r, ids in exclusions.items() if paper.id in ids), None)
        if reason is None and has_excluded_term(paper, cfg.excluded_terms):
            reason = "excluded_term"
        if reason:
            removed[reason] += 1
            continue
        candidates.append(
            Candidate(
                paper,
                gathered.topics.get(doi, set()),
                gathered.sources.get(doi, set()),
                found_by=gathered.found_by.get(doi, []),
            )
        )

    venues = {c.paper.venue_openalex_id for c in candidates if c.paper.venue_openalex_id}
    if venues:
        try:
            citedness = openalex.venue_mean_citedness(sorted(venues))
            for c in candidates:
                c.paper.venue_mean_citedness = citedness.get(c.paper.venue_openalex_id or "")
        except HttpError as error:
            gathered.warnings.append(f"OpenAlex venues: {error}")

    if use_s2 and cfg.sources.semanticscholar:
        fetch_embeddings(session, [c.paper for c in candidates], s2, gathered.warnings)
    session.flush()

    library = session.exec(select(Paper).where(Paper.in_library)).all()
    lib_vectors = vectors_for(session, [p.id for p in library])
    cand_vectors = vectors_for(session, [c.paper.id for c in candidates])
    for c in candidates:
        c.vector = cand_vectors.get(c.paper.id)
    profile = LibraryProfile(
        ids=[p.id for p in library],
        titles=[p.title for p in library],
        texts=[f"{p.title} {p.abstract or ''}" for p in library],
        vector_ids=list(lib_vectors),
        vectors=np.stack(list(lib_vectors.values())) if lib_vectors else np.zeros((0, 0)),
    )

    result = rank_candidates(
        candidates, profile, {t.name: t.weight for t in cfg.topics}, cfg.selection, today
    )
    funnel = {
        "gathered": len(gathered.works),
        "without_doi": gathered.without_doi,
        "removed": removed,
        **result.funnel,
        "library_papers": len(library),
        "library_embedded": len(lib_vectors),
        "candidates_embedded": len(cand_vectors),
    }
    batch = RecommendationBatch(
        week_start=week_start(today),
        n_candidates=len(candidates),
        warnings=gathered.warnings,
        funnel=funnel,
        params={
            "selection": cfg.selection.model_dump(mode="json"),
            "topics": [t.model_dump(mode="json") for t in cfg.topics],
            "excluded_terms": list(cfg.excluded_terms),
            "sources": {
                "openalex": cfg.sources.openalex,
                "semanticscholar": cfg.sources.semanticscholar and use_s2,
            },
        },
    )
    session.add(batch)
    session.flush()
    for rank, item in enumerate(result.picks, start=1):
        session.add(
            Recommendation(
                batch_id=batch.id,
                paper_id=item.paper.id,
                rank=rank,
                category=item.category,
                score=item.score,
                breakdown=item.breakdown(),
                reason=describe(item),
            )
        )
    session.commit()
    session.refresh(batch)
    return batch


def latest_batch(session: Session, today: date | None = None) -> RecommendationBatch | None:
    """The newest batch of the current week (or of any week if ``today`` is None)."""
    query = select(RecommendationBatch).order_by(RecommendationBatch.id.desc())
    if today is not None:
        query = query.where(RecommendationBatch.week_start == week_start(today))
    return session.exec(query).first()


def batch_items(session: Session, batch: RecommendationBatch) -> list[tuple[Recommendation, Paper]]:
    return list(
        session.exec(
            select(Recommendation, Paper)
            .join(Paper, Paper.id == Recommendation.paper_id)
            .where(Recommendation.batch_id == batch.id)
            .order_by(Recommendation.rank)
        ).all()
    )


def days_since_last_batch(session: Session, today: date | None = None) -> int | None:
    """Days since the newest recommendation batch (None: there is none)."""
    batch = latest_batch(session)
    if batch is None:
        return None
    return ((today or date.today()) - batch.created_at.date()).days
