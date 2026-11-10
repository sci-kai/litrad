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

Every number that influences the result is recorded in ``Scored.evidence``,
which the UI shows as an evidence tree. The criteria are documented in
``docs/documentation.md`` ("How papers are recommended") and shown in the UI by
``web/templates/_how_recommend.html``; keep all three in sync.
"""

import math
from dataclasses import dataclass, field
from datetime import date
from functools import cached_property

import numpy as np

from litrad.config import Selection
from litrad.db import Category, Paper
from litrad.recommend.text import tfidf_matrix

NEIGHBOURS_SHOWN = 5
# library rows compared at once (bounds the memory of the similarity computation)
KNN_CHUNK = 2048


@dataclass
class Candidate:
    paper: Paper
    topics: set[str] = field(default_factory=set)
    sources: set[str] = field(default_factory=set)
    vector: np.ndarray | None = None
    # how the candidate was found: [{"source", "topic", "query"}]
    found_by: list[dict] = field(default_factory=list)


@dataclass
class LibraryProfile:
    """Library papers as seen by the scorer."""

    ids: list[int]
    titles: list[str]
    texts: list[str]
    # SPECTER2 vectors of the library papers that have one
    vector_ids: list[int] = field(default_factory=list)
    vectors: np.ndarray = field(default_factory=lambda: np.zeros((0, 0)))

    @cached_property
    def _titles_by_id(self) -> dict[int, str]:
        return dict(zip(self.ids, self.titles, strict=True))

    def title_of(self, paper_id: int) -> str:
        return self._titles_by_id[paper_id]


@dataclass
class Scored:
    candidate: Candidate
    kind: str  # fresh | landmark
    relevance: float
    impact: float
    timeliness: float
    score: float
    category: Category = Category.open
    evidence: dict = field(default_factory=dict)

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
            "evidence": self.evidence,
        }


@dataclass
class RankResult:
    picks: list[Scored]
    funnel: dict


def age_days(paper: Paper, today: date) -> int | None:
    if paper.pub_date:
        return max((today - paper.pub_date).days, 0)
    if paper.year:
        return max((today - date(paper.year, 7, 1)).days, 0)
    return None


def fresh_window_days(sel: Selection) -> float:
    return sel.fresh_window_months * 30.5


def classify(paper: Paper, today: date, sel: Selection) -> str | None:
    """'fresh', 'landmark' or None (neither: not eligible)."""
    age = age_days(paper, today)
    if age is None:
        return None
    if age <= fresh_window_days(sel):
        return "fresh"
    percentile = paper.citation_percentile
    if (
        percentile is not None
        and percentile >= sel.landmark_min_percentile
        and age <= sel.landmark_max_age_years * 365.25
    ):
        return "landmark"
    return None


def eligibility_evidence(paper: Paper, kind: str, today: date, sel: Selection) -> dict:
    return {
        "kind": kind,
        "age_days": age_days(paper, today),
        "fresh_window_days": round(fresh_window_days(sel)),
        "citation_percentile": paper.citation_percentile,
        "landmark_min_percentile": sel.landmark_min_percentile,
        "landmark_max_age_years": sel.landmark_max_age_years,
    }


def log_scale(value: float, saturation: float) -> float:
    """0 at 0, 1 at ``saturation`` (and above), logarithmic in between."""
    return min(1.0, math.log1p(max(value, 0)) / math.log1p(saturation))


def venue_impact(citedness: float | None, sel: Selection) -> float:
    if citedness is None:
        return sel.unknown_venue_impact
    return log_scale(citedness, sel.top_venue_citedness)


def impact_score(paper: Paper, kind: str, today: date, sel: Selection) -> tuple[float, dict]:
    """Impact in [0, 1]: citation percentile for landmarks, journal for fresh papers."""
    if kind == "fresh":
        venue = venue_impact(paper.venue_mean_citedness, sel)
        early = sel.early_citation_bonus * log_scale(
            paper.citation_count or 0, sel.early_citation_saturation
        )
        value = min(1.0, venue + early)
        return value, {
            "basis": "journal",
            "venue": paper.venue,
            "venue_mean_citedness": paper.venue_mean_citedness,
            "top_venue_citedness": sel.top_venue_citedness,
            "venue_score": round(venue, 3),
            "citations": paper.citation_count or 0,
            "early_citation_bonus": round(early, 3),
        }
    # a landmark always has a citation percentile (see ``paper_kind``)
    return (paper.citation_percentile or 0) / 100, {
        "basis": "citation percentile",
        "citation_percentile": paper.citation_percentile,
        "citations": paper.citation_count,
    }


def timeliness_score(kind: str, age: int, sel: Selection) -> tuple[float, dict]:
    window = fresh_window_days(sel)
    if kind == "fresh":
        value = 0.5 + 0.5 * max(0.0, 1 - age / window)
        return value, {"basis": "fresh", "age_days": age, "fresh_window_days": round(window)}
    return sel.landmark_timeliness, {"basis": "landmark (constant)", "age_days": age}


def normalize_rows(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1
    return matrix / norms


def knn(candidates: np.ndarray, library: np.ndarray, k: int, self_columns=None, normalized=False):
    """Mean cosine similarity to the k most similar library rows.

    ``self_columns[i]`` is the library row of candidate ``i`` itself (or -1);
    a library paper must not count as its own neighbour. ``normalized``: the
    rows are already L2-normalised (TF-IDF rows restricted to the candidates'
    words must not be normalised again).
    Returns ``(mean, top_indices, top_similarities)``, the latter two sorted
    from most to least similar.
    """
    n = len(candidates)
    if n == 0 or len(library) == 0:
        return np.zeros(n), np.zeros((n, 0), dtype=int), np.zeros((n, 0))
    scale = (lambda rows: rows) if normalized else normalize_rows
    query = scale(candidates)
    sims = np.empty((n, len(library)), dtype=np.result_type(query, library))
    for start in range(0, len(library), KNN_CHUNK):
        sims[:, start : start + KNN_CHUNK] = query @ scale(library[start : start + KNN_CHUNK]).T
    excluded = 0
    if self_columns is not None:
        for row, column in enumerate(self_columns):
            if column >= 0:
                sims[row, column] = -np.inf
                excluded = 1
    k = min(k, sims.shape[1] - excluded)
    if k <= 0:
        return np.zeros(n), np.zeros((n, 0), dtype=int), np.zeros((n, 0))
    top = np.argsort(-sims, axis=1, kind="stable")[:, :k]
    top_sims = np.take_along_axis(sims, top, axis=1)
    return top_sims.mean(axis=1), top, top_sims


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
    library: LibraryProfile,
    candidate_tfidf: np.ndarray,
    library_tfidf: np.ndarray,
    topic_weights: dict[str, float],
    sel: Selection,
) -> tuple[np.ndarray, list[dict]]:
    """Library similarity (as a rank among candidates) blended with a topic bonus."""
    n = len(candidates)
    similarity = np.zeros(n)
    evidence: list[dict] = [{} for _ in range(n)]

    def fill(indices, mean, top, top_sims, method, ids):
        ranks = percentile_ranks(mean)
        for row, i in enumerate(indices):
            similarity[i] = ranks[row]
            evidence[i] = {
                "method": method,
                "knn_mean_similarity": round(float(mean[row]), 4),
                "similarity_rank": round(float(ranks[row]), 3),
                "rank_among": len(indices),
                "neighbours": [
                    {
                        "paper_id": ids[j],
                        "title": library.title_of(ids[j]),
                        "similarity": round(float(s), 4),
                    }
                    for j, s in zip(
                        top[row][:NEIGHBOURS_SHOWN], top_sims[row][:NEIGHBOURS_SHOWN], strict=True
                    )
                ],
            }

    def own_rows(indices, ids):
        position = {paper_id: row for row, paper_id in enumerate(ids)}
        return [position.get(candidates[i].paper.id, -1) for i in indices]

    embedded = [i for i, c in enumerate(candidates) if c.vector is not None]
    use_vectors = bool(embedded) and len(library.vectors) > 0
    if use_vectors:
        vectors = np.stack([candidates[i].vector for i in embedded])
        result = knn(vectors, library.vectors, sel.knn, own_rows(embedded, library.vector_ids))
        fill(embedded, *result, "SPECTER2", library.vector_ids)
    text_only = [i for i in range(n) if not use_vectors or candidates[i].vector is None]
    if text_only:
        mean, top, top_sims = knn(
            candidate_tfidf[text_only],
            library_tfidf,
            sel.knn,
            own_rows(text_only, library.ids),
            normalized=True,
        )
        fill(text_only, mean, top, top_sims, "TF-IDF", library.ids)

    max_weight = max(topic_weights.values(), default=1.0) or 1.0
    relevance = np.zeros(n)
    for i, candidate in enumerate(candidates):
        weights = {t: topic_weights.get(t, 0.0) for t in candidate.topics}
        if weights:
            best = max(weights, key=weights.get)
            bonus = weights[best] / max_weight
            reason = f"topic '{best}' (weight {weights[best]} / max {max_weight})"
        elif "s2-recs" in candidate.sources:
            bonus = sel.s2_only_topic_bonus
            reason = "found only by library-based recommendations"
        else:
            bonus, reason = 0.0, "no topic"
        relevance[i] = sel.similarity_share * similarity[i] + (1 - sel.similarity_share) * bonus
        evidence[i].update(
            {
                "similarity_share": sel.similarity_share,
                "topic_bonus": round(bonus, 3),
                "topic_bonus_reason": reason,
            }
        )
    return relevance, evidence


def similarity_matrix(candidates: list[Candidate], tfidf: np.ndarray) -> np.ndarray:
    """Pairwise similarity; SPECTER2 where both papers have it, TF-IDF otherwise."""
    sims = tfidf @ tfidf.T
    embedded = [i for i, c in enumerate(candidates) if c.vector is not None]
    if embedded:
        vectors = normalize_rows(np.stack([candidates[i].vector for i in embedded]))
        sims[np.ix_(embedded, embedded)] = vectors @ vectors.T
    return sims


def mmr_select(
    scores: np.ndarray, sims: np.ndarray, n: int, lam: float
) -> list[tuple[int, float, int | None]]:
    """Greedy maximal marginal relevance.

    Returns ``(index, redundancy, most_similar_earlier_index)`` per pick, where
    redundancy is the highest similarity to an earlier pick.
    """
    picks: list[tuple[int, float, int | None]] = []
    remaining = list(range(len(scores)))
    while remaining and len(picks) < n:

        def redundancy(i: int) -> tuple[float, int | None]:
            if not picks:
                return 0.0, None
            j = max((p[0] for p in picks), key=lambda j: sims[i, j])
            return float(sims[i, j]), j

        best = max(remaining, key=lambda i: lam * scores[i] - (1 - lam) * redundancy(i)[0])
        picks.append((best, *redundancy(best)))
        remaining.remove(best)
    return picks


def rank_candidates(
    candidates: list[Candidate],
    library: LibraryProfile,
    topic_weights: dict[str, float],
    sel: Selection,
    today: date,
) -> RankResult:
    """Score all eligible candidates and return the final picks, detail first."""
    eligible, kinds = [], []
    for candidate in candidates:
        kind = classify(candidate.paper, today, sel)
        if kind:
            eligible.append(candidate)
            kinds.append(kind)
    funnel = {"scored": len(candidates), "not_fresh_or_landmark": len(candidates) - len(eligible)}
    if not eligible:
        return RankResult([], {**funnel, "eligible": 0, "above_floor": 0, "picked": 0})

    texts = [f"{c.paper.title} {c.paper.abstract or ''}" for c in eligible]
    # columns only for the candidates' words: all that candidate similarities need
    tfidf = tfidf_matrix(texts + library.texts, columns_from=len(texts))
    candidate_tfidf, library_tfidf = tfidf[: len(texts)], tfidf[len(texts) :]
    relevance, relevance_evidence = relevance_scores(
        eligible, library, candidate_tfidf, library_tfidf, topic_weights, sel
    )

    # divided by their sum: only the ratio of the weights matters
    w = sel.weights.normalized()
    scored = []
    for i, (candidate, kind) in enumerate(zip(eligible, kinds, strict=True)):
        paper = candidate.paper
        impact, impact_evidence = impact_score(paper, kind, today, sel)
        timeliness, time_evidence = timeliness_score(kind, age_days(paper, today) or 0, sel)
        contributions = {
            "relevance": w["relevance"] * relevance[i],
            "impact": w["impact"] * impact,
            "timeliness": w["timeliness"] * timeliness,
        }
        total = sum(contributions.values())
        evidence = {
            "found_by": candidate.found_by,
            "eligibility": eligibility_evidence(paper, kind, today, sel),
            "relevance": relevance_evidence[i],
            "impact": impact_evidence,
            "timeliness": time_evidence,
            "weights": {k: round(v, 4) for k, v in w.items()},
            "contributions": {k: round(float(v), 4) for k, v in contributions.items()},
        }
        scored.append(
            Scored(
                candidate,
                kind,
                float(relevance[i]),
                impact,
                timeliness,
                float(total),
                evidence=evidence,
            )
        )

    order = sorted(range(len(scored)), key=lambda i: -scored[i].score)
    best = scored[order[0]].score
    floor = sel.min_relative_score * best
    strong = [i for i in order if scored[i].score >= floor]
    sims = similarity_matrix(eligible, candidate_tfidf)
    picks = mmr_select(
        np.array([scored[i].score for i in strong]),
        sims[np.ix_(strong, strong)],
        sel.n_recommendations,
        sel.mmr_lambda,
    )
    chosen = []
    for pick_number, (local, redundancy, earlier) in enumerate(picks, start=1):
        item = scored[strong[local]]
        item.evidence["selection"] = {
            "score_rank": order.index(strong[local]) + 1,
            "eligible": len(scored),
            "best_score": round(best, 4),
            "floor": round(floor, 4),
            "min_relative_score": sel.min_relative_score,
            "above_floor": len(strong),
            "mmr_lambda": sel.mmr_lambda,
            "mmr_pick": pick_number,
            "redundancy": round(redundancy, 4),
            "most_similar_pick": scored[strong[earlier]].paper.title
            if earlier is not None
            else None,
        }
        chosen.append(item)
    funnel.update({"eligible": len(scored), "above_floor": len(strong), "picked": len(chosen)})
    # how deeply to read is the reader's choice: the list is simply ranked by score
    return RankResult(sorted(chosen, key=lambda s: -s.score), funnel)


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
    if paper.in_library:
        parts.append("in your library, not read yet")
    if item.candidate.topics:
        parts.append("topic: " + ", ".join(sorted(item.candidate.topics)))
    elif "s2-recs" in item.candidate.sources:
        parts.append("similar to your library")
    return " · ".join(parts)
