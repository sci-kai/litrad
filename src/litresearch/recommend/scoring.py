"""Scoring and selection of candidate papers (pure functions, no I/O).

Each candidate gets three components in [0, 1]:

- relevance: similarity to the library (SPECTER2, TF-IDF as fallback),
  boosted when a configured topic found the paper;
- impact: citation percentile for established papers, journal citedness for
  fresh ones (they cannot have citations yet);
- timeliness: fresh papers decay over the fresh window, landmark papers get
  a constant value.

A paper must be either *fresh* or a *landmark*, otherwise it is ignored. The
final list is chosen with maximal marginal relevance (MMR) so that the papers
do not all cover the same subject.
"""

import math
from dataclasses import dataclass, field
from datetime import date

import numpy as np

from litresearch.config import Selection
from litresearch.db import Category, Paper
from litresearch.recommend.text import tfidf_matrix

KNN = 5
# a missing venue is a warning sign (preprint servers do count as venues)
UNKNOWN_VENUE_IMPACT = 0.05
# journal 2-year mean citedness that counts as "top" (log-scaled up to here)
TOP_VENUE_CITEDNESS = 30.0
LANDMARK_TIMELINESS = 0.4
S2_ONLY_TOPIC_BONUS = 0.5
# diversity must not pull in weak papers: picks need this share of the best score
MIN_RELATIVE_SCORE = 0.6


@dataclass
class Candidate:
    paper: Paper
    topics: set[str] = field(default_factory=set)
    sources: set[str] = field(default_factory=set)
    vector: np.ndarray | None = None


@dataclass
class Scored:
    candidate: Candidate
    kind: str  # fresh | landmark
    relevance: float
    impact: float
    timeliness: float
    score: float
    category: Category = Category.skim

    @property
    def paper(self) -> Paper:
        return self.candidate.paper

    def breakdown(self) -> dict:
        return {
            "kind": self.kind,
            "relevance": round(self.relevance, 3),
            "impact": round(self.impact, 3),
            "timeliness": round(self.timeliness, 3),
            "score": round(self.score, 3),
            "topics": sorted(self.candidate.topics),
            "sources": sorted(self.candidate.sources),
        }


def age_days(paper: Paper, today: date) -> int | None:
    if paper.pub_date:
        return max((today - paper.pub_date).days, 0)
    if paper.year:
        return max((today - date(paper.year, 7, 1)).days, 0)
    return None


def classify(paper: Paper, today: date, sel: Selection) -> str | None:
    """'fresh', 'landmark' or None (neither: not eligible)."""
    age = age_days(paper, today)
    if age is None:
        return None
    if age <= sel.fresh_window_months * 30.5:
        return "fresh"
    percentile = paper.citation_percentile
    if (
        percentile is not None
        and percentile >= sel.landmark_min_percentile
        and age <= sel.landmark_max_age_years * 365.25
    ):
        return "landmark"
    return None


def venue_impact(citedness: float | None) -> float:
    if citedness is None:
        return UNKNOWN_VENUE_IMPACT
    return min(1.0, math.log1p(max(citedness, 0)) / math.log1p(TOP_VENUE_CITEDNESS))


def impact_score(paper: Paper, kind: str, today: date) -> float:
    if kind == "fresh":
        early = 0.2 * min(1.0, math.log1p(paper.citation_count or 0) / math.log1p(50))
        return min(1.0, venue_impact(paper.venue_mean_citedness) + early)
    if paper.citation_percentile is not None:
        return paper.citation_percentile / 100
    years = max((age_days(paper, today) or 365) / 365.25, 1)
    per_year = (paper.citation_count or 0) / years
    return min(1.0, math.log1p(per_year) / math.log1p(200))


def timeliness_score(kind: str, age: int, window_days: float) -> float:
    if kind == "fresh":
        return 0.5 + 0.5 * max(0.0, 1 - age / window_days)
    return LANDMARK_TIMELINESS


def normalize_rows(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1
    return matrix / norms


def knn_similarity(candidates: np.ndarray, library: np.ndarray, k: int = KNN) -> np.ndarray:
    """Mean cosine similarity of each candidate to its k most similar library papers."""
    if len(candidates) == 0 or len(library) == 0:
        return np.zeros(len(candidates))
    sims = normalize_rows(candidates) @ normalize_rows(library).T
    k = min(k, sims.shape[1])
    top = np.sort(sims, axis=1)[:, -k:]
    return top.mean(axis=1)


def percentile_ranks(values: np.ndarray) -> np.ndarray:
    """Rank-based scaling to [0, 1]; ties share their mean rank."""
    n = len(values)
    if n == 0:
        return np.array([])
    if n == 1:
        return np.array([1.0])
    order = values.argsort(kind="stable")
    ranks = np.empty(n)
    ranks[order] = np.arange(n)
    for value in np.unique(values):
        mask = values == value
        ranks[mask] = ranks[mask].mean()
    return ranks / (n - 1)


def relevance_scores(
    candidates: list[Candidate],
    library_vectors: np.ndarray,
    candidate_tfidf: np.ndarray,
    library_tfidf: np.ndarray,
    topic_weights: dict[str, float],
) -> np.ndarray:
    """Combine library similarity (ranked) with a topic bonus."""
    n = len(candidates)
    similarity = np.zeros(n)
    tfidf_rank = percentile_ranks(knn_similarity(candidate_tfidf, library_tfidf))
    embedded = [i for i, c in enumerate(candidates) if c.vector is not None]
    if embedded and len(library_vectors):
        vectors = np.stack([candidates[i].vector for i in embedded])
        embed_rank = percentile_ranks(knn_similarity(vectors, library_vectors))
        similarity[embedded] = embed_rank
    missing = [i for i in range(n) if candidates[i].vector is None or not len(library_vectors)]
    similarity[missing] = tfidf_rank[missing]

    max_weight = max(topic_weights.values(), default=1.0) or 1.0
    bonus = np.zeros(n)
    for i, candidate in enumerate(candidates):
        weights = [topic_weights.get(t, 0.0) for t in candidate.topics]
        if weights:
            bonus[i] = max(weights) / max_weight
        elif "s2-recs" in candidate.sources:
            bonus[i] = S2_ONLY_TOPIC_BONUS
    return 0.7 * similarity + 0.3 * bonus


def similarity_matrix(candidates: list[Candidate], tfidf: np.ndarray) -> np.ndarray:
    """Pairwise similarity; SPECTER2 where both papers have it, TF-IDF otherwise."""
    sims = tfidf @ tfidf.T
    embedded = [i for i, c in enumerate(candidates) if c.vector is not None]
    if embedded:
        vectors = normalize_rows(np.stack([candidates[i].vector for i in embedded]))
        sims[np.ix_(embedded, embedded)] = vectors @ vectors.T
    return sims


def mmr_select(scores: np.ndarray, sims: np.ndarray, n: int, lam: float) -> list[int]:
    """Greedy maximal marginal relevance."""
    selected: list[int] = []
    remaining = list(range(len(scores)))
    while remaining and len(selected) < n:

        def value(i: int) -> float:
            redundancy = max((sims[i, j] for j in selected), default=0.0)
            return lam * scores[i] - (1 - lam) * redundancy

        best = max(remaining, key=value)
        selected.append(best)
        remaining.remove(best)
    return selected


def rank_candidates(
    candidates: list[Candidate],
    library_vectors: np.ndarray,
    library_texts: list[str],
    topic_weights: dict[str, float],
    sel: Selection,
    today: date,
) -> list[Scored]:
    """Score all eligible candidates and return the final ≤5 picks, detail first."""
    eligible = []
    kinds = []
    for candidate in candidates:
        kind = classify(candidate.paper, today, sel)
        if kind:
            eligible.append(candidate)
            kinds.append(kind)
    if not eligible:
        return []

    texts = [f"{c.paper.title} {c.paper.abstract or ''}" for c in eligible]
    tfidf = tfidf_matrix(texts + library_texts)
    candidate_tfidf, library_tfidf = tfidf[: len(texts)], tfidf[len(texts) :]

    relevance = relevance_scores(
        eligible, library_vectors, candidate_tfidf, library_tfidf, topic_weights
    )
    window = sel.fresh_window_months * 30.5
    w = sel.weights
    scored = []
    for i, (candidate, kind) in enumerate(zip(eligible, kinds, strict=True)):
        impact = impact_score(candidate.paper, kind, today)
        timeliness = timeliness_score(kind, age_days(candidate.paper, today) or 0, window)
        total = w.relevance * relevance[i] + w.impact * impact + w.timeliness * timeliness
        scored.append(Scored(candidate, kind, float(relevance[i]), impact, timeliness, total))

    best = max(s.score for s in scored)
    strong = [i for i, s in enumerate(scored) if s.score >= MIN_RELATIVE_SCORE * best]
    sims = similarity_matrix(eligible, candidate_tfidf)[np.ix_(strong, strong)]
    picks = mmr_select(
        np.array([scored[i].score for i in strong]),
        sims,
        sel.n_detail + sel.n_skim,
        sel.mmr_lambda,
    )
    chosen = [scored[strong[i]] for i in picks]
    # the most relevant *and* impactful papers deserve a careful read
    by_depth = sorted(chosen, key=lambda s: s.relevance + s.impact, reverse=True)
    for item in by_depth[: sel.n_detail]:
        item.category = Category.detail
    return sorted(chosen, key=lambda s: (s.category != Category.detail, -s.score))


def describe(item: Scored) -> str:
    """One-line reason shown next to a recommendation."""
    paper = item.paper
    parts = [item.kind]
    if item.kind == "landmark" and paper.citation_percentile is not None:
        parts.append(f"top {max(100 - paper.citation_percentile, 0.01):.2g}% cited")
    if paper.citation_count:
        noun = "citation" if paper.citation_count == 1 else "citations"
        parts.append(f"{paper.citation_count:,} {noun}")
    if paper.venue:
        parts.append(paper.venue)
    if item.candidate.topics:
        parts.append("topic: " + ", ".join(sorted(item.candidate.topics)))
    elif "s2-recs" in item.candidate.sources:
        parts.append("similar to your library")
    return " · ".join(parts)
