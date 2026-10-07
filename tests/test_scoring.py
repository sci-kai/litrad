from datetime import date

import numpy as np
import pytest

from litresearch.config import Selection
from litresearch.db import Category, Paper
from litresearch.recommend.scoring import (
    UNKNOWN_VENUE_IMPACT,
    Candidate,
    age_days,
    classify,
    describe,
    impact_score,
    knn_similarity,
    mmr_select,
    percentile_ranks,
    rank_candidates,
    timeliness_score,
    venue_impact,
)
from litresearch.recommend.text import tfidf_matrix, tokens

TODAY = date(2026, 10, 7)
SEL = Selection()


def paper(**kwargs) -> Paper:
    kwargs.setdefault("title", "A paper")
    return Paper(**kwargs)


# --- classification -------------------------------------------------------


def test_age_uses_date_then_mid_year():
    assert age_days(paper(pub_date=date(2026, 10, 1)), TODAY) == 6
    assert age_days(paper(year=2026), TODAY) == (TODAY - date(2026, 7, 1)).days
    assert age_days(paper(pub_date=date(2026, 12, 1)), TODAY) == 0  # future date
    assert age_days(paper(), TODAY) is None


@pytest.mark.parametrize(
    "kwargs, expected",
    [
        ({"pub_date": date(2026, 9, 1)}, "fresh"),
        ({"pub_date": date(2026, 4, 15)}, "fresh"),  # 175 days < 6 months
        ({"pub_date": date(2026, 1, 1)}, None),  # neither fresh nor cited
        ({"pub_date": date(2020, 1, 1), "citation_percentile": 99.0}, "landmark"),
        ({"pub_date": date(2020, 1, 1), "citation_percentile": 94.9}, None),
        ({"pub_date": date(2005, 1, 1), "citation_percentile": 99.9}, None),  # too old
        ({}, None),  # undated
    ],
)
def test_classify(kwargs, expected):
    assert classify(paper(**kwargs), TODAY, SEL) == expected


# --- components -----------------------------------------------------------


def test_venue_impact_scale():
    assert venue_impact(None) == UNKNOWN_VENUE_IMPACT
    assert venue_impact(0) == 0
    assert venue_impact(30) == pytest.approx(1.0)
    assert venue_impact(300) == 1.0
    assert venue_impact(2) < venue_impact(10) < venue_impact(20)


def test_impact_fresh_uses_venue_and_early_citations():
    top = paper(venue_mean_citedness=25.0)
    weak = paper(venue_mean_citedness=1.0)
    assert impact_score(top, "fresh", TODAY) > impact_score(weak, "fresh", TODAY)
    cited = paper(venue_mean_citedness=1.0, citation_count=20)
    assert impact_score(cited, "fresh", TODAY) > impact_score(weak, "fresh", TODAY)
    assert impact_score(paper(venue_mean_citedness=500, citation_count=500), "fresh", TODAY) == 1


def test_impact_landmark_uses_percentile_or_citations_per_year():
    assert impact_score(paper(citation_percentile=99.5), "landmark", TODAY) == 0.995
    many = paper(pub_date=date(2016, 1, 1), citation_count=5000)
    few = paper(pub_date=date(2016, 1, 1), citation_count=50)
    assert 0 < impact_score(few, "landmark", TODAY) < impact_score(many, "landmark", TODAY) <= 1


def test_timeliness():
    assert timeliness_score("fresh", 0, 180) == 1.0
    assert timeliness_score("fresh", 90, 180) == 0.75
    assert timeliness_score("fresh", 400, 180) == 0.5
    assert timeliness_score("landmark", 4000, 180) == 0.4


def test_percentile_ranks_handles_ties_and_small_inputs():
    assert percentile_ranks(np.array([])).size == 0
    assert percentile_ranks(np.array([0.3])).tolist() == [1.0]
    ranks = percentile_ranks(np.array([0.1, 0.5, 0.5, 0.9]))
    assert ranks[0] == 0 and ranks[3] == 1 and ranks[1] == ranks[2] == 0.5


def test_knn_similarity_prefers_close_vectors():
    library = np.array([[1.0, 0, 0], [0.9, 0.1, 0], [0, 0, 1]])
    candidates = np.array([[1.0, 0.05, 0], [0, 1.0, 0]])
    sims = knn_similarity(candidates, library, k=2)
    assert sims[0] > 0.9 > sims[1]
    assert knn_similarity(candidates, np.zeros((0, 3))).tolist() == [0, 0]


def test_mmr_trades_score_for_diversity():
    scores = np.array([1.0, 0.98, 0.7])
    sims = np.array([[1, 0.99, 0.1], [0.99, 1, 0.1], [0.1, 0.1, 1]])
    assert mmr_select(scores, sims, 2, lam=0.7) == [0, 2]
    assert mmr_select(scores, sims, 2, lam=1.0) == [0, 1]
    assert mmr_select(scores, sims, 10, lam=0.7) == [0, 2, 1]


def test_tfidf_rows_are_normalised_and_ignore_stopwords():
    assert "the" not in tokens("The tumour evolves") and "tumour" in tokens("The tumour")
    matrix = tfidf_matrix(["clonal evolution tumour", "clonal evolution tumour", "fibroblast"])
    assert np.allclose(np.linalg.norm(matrix, axis=1), 1)
    assert matrix[0] @ matrix[1] == pytest.approx(1)
    assert matrix[0] @ matrix[2] == 0
    assert tfidf_matrix(["", "the and"]).shape[0] == 2


# --- ranking --------------------------------------------------------------


def candidate(title, vector=None, topics=(), sources=("openalex-fresh",), **kwargs):
    kwargs.setdefault("pub_date", date(2026, 9, 1))
    kwargs.setdefault("venue_mean_citedness", 10.0)
    return Candidate(
        paper(title=title, **kwargs),
        set(topics),
        set(sources),
        None if vector is None else np.array(vector, dtype=np.float32),
    )


LIBRARY_VECTORS = np.array([[1.0, 0, 0, 0], [0.9, 0.1, 0, 0], [0, 1.0, 0, 0]])
LIBRARY_TEXTS = ["clonal evolution of tumours", "tumour phylogeny", "single-cell sequencing"]
WEIGHTS = {"evo": 1.0, "other": 0.5}


def test_rank_selects_at_most_five_and_puts_detail_first():
    candidates = [candidate(f"Paper {i}", [1.0, i / 10, 0, 0], topics=["evo"]) for i in range(8)]
    ranked = rank_candidates(candidates, LIBRARY_VECTORS, LIBRARY_TEXTS, WEIGHTS, SEL, TODAY)
    assert len(ranked) == 5
    categories = [r.category for r in ranked]
    assert categories == [Category.detail] * 2 + [Category.skim] * 3
    assert ranked[0].breakdown()["topics"] == ["evo"]


def test_ineligible_and_weak_papers_are_not_picked():
    good = candidate("Clonal evolution", [1.0, 0, 0, 0], topics=["evo"])
    stale = candidate("Old and uncited", [1.0, 0, 0, 0], pub_date=date(2018, 1, 1))
    weak = candidate("Off-topic", [0, 0, 0, 1.0], venue_mean_citedness=None)
    ranked = rank_candidates(
        [good, stale, weak], LIBRARY_VECTORS, LIBRARY_TEXTS, WEIGHTS, SEL, TODAY
    )
    assert [r.paper.title for r in ranked] == ["Clonal evolution"]


def test_text_fallback_without_embeddings():
    on_topic = candidate("Tumour phylogeny and clonal evolution", topics=["evo"])
    off_topic = candidate("Cardiac muscle physiology in mice", topics=["evo"])
    ranked = rank_candidates(
        [off_topic, on_topic], np.zeros((0, 0)), LIBRARY_TEXTS, WEIGHTS, SEL, TODAY
    )
    assert ranked[0].paper.title.startswith("Tumour phylogeny")
    assert all(r.relevance < ranked[0].relevance for r in ranked[1:])


def test_topic_weight_and_s2_bonus():
    a = candidate("A", [1.0, 0, 0, 0], topics=["evo"])
    b = candidate("B", [1.0, 0, 0, 0], topics=["other"])
    c = candidate("C", [1.0, 0, 0, 0], topics=[], sources=["s2-recs"])
    ranked = rank_candidates([a, b, c], LIBRARY_VECTORS, LIBRARY_TEXTS, WEIGHTS, SEL, TODAY)
    relevance = {r.paper.title: r.relevance for r in ranked}
    assert relevance["A"] > relevance["B"]
    assert relevance["C"] == pytest.approx(relevance["B"])  # both get half the bonus


def test_no_candidates():
    assert rank_candidates([], LIBRARY_VECTORS, LIBRARY_TEXTS, WEIGHTS, SEL, TODAY) == []


def test_describe():
    landmark = candidate(
        "L",
        [1.0, 0, 0, 0],
        topics=["evo"],
        pub_date=date(2015, 1, 1),
        citation_percentile=99.5,
        citation_count=1234,
        venue="Nature",
    )
    fresh = candidate("F", [0.9, 0.1, 0, 0], sources=["s2-recs"], citation_count=1)
    ranked = rank_candidates([landmark, fresh], LIBRARY_VECTORS, LIBRARY_TEXTS, WEIGHTS, SEL, TODAY)
    text = {r.paper.title: describe(r) for r in ranked}
    assert text["L"] == "landmark · top 0.5% cited · 1,234 citations · Nature · topic: evo"
    assert text["F"] == "fresh · 1 citation · similar to your library"
