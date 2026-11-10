from datetime import date

import numpy as np
import pytest

from litrad.config import Selection, Weights
from litrad.db import Category, Paper
from litrad.recommend.scoring import (
    Candidate,
    LibraryProfile,
    age_days,
    classify,
    describe,
    impact_score,
    knn,
    log_scale,
    mmr_select,
    percentile_ranks,
    rank_candidates,
    timeliness_score,
    venue_impact,
)
from litrad.recommend.text import tfidf_matrix, tokens

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


def test_classify_follows_settings():
    sel = Selection(fresh_window_months=1, landmark_min_percentile=99.5, landmark_max_age_years=30)
    assert classify(paper(pub_date=date(2026, 8, 1)), TODAY, sel) is None
    assert classify(paper(pub_date=date(2005, 1, 1), citation_percentile=99.9), TODAY, sel)
    assert classify(paper(pub_date=date(2020, 1, 1), citation_percentile=99.0), TODAY, sel) is None


# --- components -----------------------------------------------------------


def test_log_scale_and_venue_impact():
    assert (
        log_scale(0, 30) == 0 and log_scale(30, 30) == pytest.approx(1) and log_scale(99, 30) == 1
    )
    assert venue_impact(None, SEL) == SEL.unknown_venue_impact
    assert venue_impact(2, SEL) < venue_impact(10, SEL) < venue_impact(20, SEL)
    assert venue_impact(10, Selection(top_venue_citedness=10)) == pytest.approx(1)


def test_impact_fresh_uses_venue_and_early_citations():
    top, top_ev = impact_score(paper(venue_mean_citedness=25.0), "fresh", TODAY, SEL)
    weak, _ = impact_score(paper(venue_mean_citedness=1.0), "fresh", TODAY, SEL)
    cited, cited_ev = impact_score(
        paper(venue_mean_citedness=1.0, citation_count=20), "fresh", TODAY, SEL
    )
    assert top > weak and cited > weak
    assert top_ev["basis"] == "journal" and top_ev["venue_mean_citedness"] == 25.0
    assert cited_ev["early_citation_bonus"] > 0
    capped, _ = impact_score(
        paper(venue_mean_citedness=500, citation_count=500), "fresh", TODAY, SEL
    )
    assert capped == 1


def test_impact_landmark_uses_the_percentile():
    value, evidence = impact_score(paper(citation_percentile=99.5), "landmark", TODAY, SEL)
    assert value == 0.995 and evidence["basis"] == "citation percentile"
    # only a paper with a percentile can be a landmark at all
    assert classify(paper(pub_date=date(2016, 1, 1), citation_count=5000), TODAY, SEL) is None


def test_timeliness():
    assert timeliness_score("fresh", 0, SEL)[0] == 1.0
    window = SEL.fresh_window_months * 30.5
    assert timeliness_score("fresh", window / 2, SEL)[0] == pytest.approx(0.75)
    assert timeliness_score("fresh", 400, SEL)[0] == 0.5
    assert timeliness_score("landmark", 4000, SEL)[0] == SEL.landmark_timeliness
    assert timeliness_score("landmark", 1, Selection(landmark_timeliness=0.9))[0] == 0.9


def test_percentile_ranks_handles_ties_and_small_inputs():
    assert percentile_ranks(np.array([])).size == 0
    assert percentile_ranks(np.array([0.3])).tolist() == [1.0]
    ranks = percentile_ranks(np.array([0.1, 0.5, 0.5, 0.9]))
    assert ranks[0] == 0 and ranks[3] == 1 and ranks[1] == ranks[2] == 0.5


def test_knn_returns_neighbours_in_order():
    library = np.array([[1.0, 0, 0], [0.9, 0.1, 0], [0, 0, 1]])
    candidates = np.array([[1.0, 0.05, 0], [0, 1.0, 0]])
    mean, top, top_sims = knn(candidates, library, k=2)
    assert mean[0] > 0.9 > mean[1]
    assert top[0].tolist() == [0, 1] and top_sims[0][0] >= top_sims[0][1]
    empty_mean, empty_top, _ = knn(candidates, np.zeros((0, 3)), 2)
    assert empty_mean.tolist() == [0, 0] and empty_top.shape == (2, 0)


def test_mmr_trades_score_for_diversity():
    scores = np.array([1.0, 0.98, 0.7])
    sims = np.array([[1, 0.99, 0.1], [0.99, 1, 0.1], [0.1, 0.1, 1]])
    assert [p[0] for p in mmr_select(scores, sims, 2, lam=0.7)] == [0, 2]
    assert [p[0] for p in mmr_select(scores, sims, 2, lam=1.0)] == [0, 1]
    picks = mmr_select(scores, sims, 10, lam=0.7)
    assert [p[0] for p in picks] == [0, 2, 1]
    assert picks[0][1:] == (0.0, None)
    assert picks[2][1] == pytest.approx(0.99) and picks[2][2] == 0


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
        found_by=[{"source": s, "topic": next(iter(topics), None), "query": "q"} for s in sources],
    )


LIBRARY = LibraryProfile(
    ids=[11, 12, 13],
    titles=["Clonal evolution of tumours", "Tumour phylogeny", "Single-cell sequencing"],
    texts=["clonal evolution of tumours", "tumour phylogeny", "single-cell sequencing"],
    vector_ids=[11, 12, 13],
    vectors=np.array([[1.0, 0, 0, 0], [0.9, 0.1, 0, 0], [0, 1.0, 0, 0]]),
)
TEXT_ONLY_LIBRARY = LibraryProfile(ids=LIBRARY.ids, titles=LIBRARY.titles, texts=LIBRARY.texts)
WEIGHTS = {"evo": 1.0, "other": 0.5}


def rank(candidates, library=LIBRARY, sel=SEL):
    return rank_candidates(candidates, library, WEIGHTS, sel, TODAY)


def test_rank_selects_at_most_five_ranked_by_score():
    candidates = [candidate(f"Paper {i}", [1.0, i / 10, 0, 0], topics=["evo"]) for i in range(8)]
    result = rank(candidates)
    assert len(result.picks) == 5
    assert all(r.category == Category.open for r in result.picks)  # no prescribed depth
    scores = [r.score for r in result.picks]
    assert scores == sorted(scores, reverse=True)
    assert len(rank(candidates, sel=Selection(n_recommendations=2)).picks) == 2
    assert result.funnel == {
        "scored": 8,
        "not_fresh_or_landmark": 0,
        "eligible": 8,
        "above_floor": 8,
        "picked": 5,
    }


def test_ineligible_and_weak_papers_are_not_picked():
    good = candidate("Clonal evolution", [1.0, 0, 0, 0], topics=["evo"])
    stale = candidate("Old and uncited", [1.0, 0, 0, 0], pub_date=date(2018, 1, 1))
    weak = candidate("Off-topic", [0, 0, 0, 1.0], venue_mean_citedness=None)
    result = rank([good, stale, weak])
    assert [r.paper.title for r in result.picks] == ["Clonal evolution"]
    assert result.funnel["not_fresh_or_landmark"] == 1 and result.funnel["above_floor"] == 1


def test_floor_is_configurable():
    good = candidate("Clonal evolution", [1.0, 0, 0, 0], topics=["evo"])
    weak = candidate("Off-topic", [0, 0, 0, 1.0], venue_mean_citedness=None)
    result = rank([good, weak], sel=Selection(min_relative_score=0))
    assert len(result.picks) == 2


def test_text_fallback_without_embeddings():
    on_topic = candidate("Tumour phylogeny and clonal evolution", topics=["evo"])
    off_topic = candidate("Cardiac muscle physiology in mice", topics=["evo"])
    result = rank([off_topic, on_topic], library=TEXT_ONLY_LIBRARY)
    assert result.picks[0].paper.title.startswith("Tumour phylogeny")
    assert all(r.relevance < result.picks[0].relevance for r in result.picks[1:])
    assert result.picks[0].evidence["relevance"]["method"] == "TF-IDF"


def test_topic_weight_and_s2_bonus():
    a = candidate("A", [1.0, 0, 0, 0], topics=["evo"])
    b = candidate("B", [1.0, 0, 0, 0], topics=["other"])
    c = candidate("C", [1.0, 0, 0, 0], topics=[], sources=["s2-recs"])
    relevance = {r.paper.title: r.relevance for r in rank([a, b, c]).picks}
    assert relevance["A"] > relevance["B"]
    assert relevance["C"] == pytest.approx(relevance["B"])  # both get half the bonus


def test_similarity_share_setting():
    a = candidate("A", [1.0, 0, 0, 0], topics=["evo"])
    b = candidate("B", [0, 1.0, 0, 0], topics=["other"])
    only_topics = {
        r.paper.title: r.relevance
        for r in rank([a, b], sel=Selection(similarity_share=0, min_relative_score=0)).picks
    }
    assert only_topics == {"A": 1.0, "B": 0.5}


def test_no_candidates():
    result = rank([])
    assert result.picks == [] and result.funnel["eligible"] == 0


def test_evidence_tree_contents():
    a = candidate("Clonal evolution", [1.0, 0, 0, 0], topics=["evo"])
    b = candidate("Phylogeny", [0.95, 0.05, 0, 0], topics=["evo"])
    result = rank([a, b])
    first = next(r for r in result.picks if r.paper.title == "Clonal evolution")
    ev = first.evidence
    assert ev["found_by"] == [{"source": "openalex-fresh", "topic": "evo", "query": "q"}]
    assert ev["eligibility"]["kind"] == "fresh" and ev["eligibility"]["age_days"] == 36
    rel = ev["relevance"]
    assert rel["method"] == "SPECTER2" and rel["topic_bonus"] == 1.0
    assert rel["neighbours"][0] == {
        "paper_id": 11,
        "title": "Clonal evolution of tumours",
        "similarity": 1.0,
    }
    assert "topic 'evo'" in rel["topic_bonus_reason"]
    assert ev["impact"]["basis"] == "journal"
    assert ev["timeliness"]["basis"] == "fresh"
    assert sum(ev["contributions"].values()) == pytest.approx(first.score, abs=1e-3)
    assert ev["weights"] == {"relevance": 0.5, "impact": 0.3, "timeliness": 0.2}
    selection = ev["selection"]
    assert selection["eligible"] == 2 and selection["score_rank"] in (1, 2)
    second = [r for r in result.picks if r is not first][0]
    assert second.evidence["selection"]["mmr_pick"] + selection["mmr_pick"] == 3
    later = max(result.picks, key=lambda r: r.evidence["selection"]["mmr_pick"])
    assert later.evidence["selection"]["most_similar_pick"] is not None
    assert "category" not in ev
    assert first.breakdown()["evidence"] is ev


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
    text = {r.paper.title: describe(r) for r in rank([landmark, fresh]).picks}
    assert text["L"] == "landmark · top 0.5% cited · 1,234 citations · Nature · topic: evo"
    assert text["F"] == "fresh · 1 citation · similar to your library"


def test_library_paper_is_not_its_own_neighbour():
    own = candidate("Clonal evolution of tumours", [1.0, 0, 0, 0], topics=["evo"])
    own.paper.id = 11  # the same paper as library entry 11
    other = candidate("Something new", [1.0, 0, 0, 0], topics=["evo"])
    other.paper.id = 99
    result = rank([own, other], sel=Selection(min_relative_score=0))
    by_title = {r.paper.title: r for r in result.picks}
    own_neighbours = [
        n["paper_id"]
        for n in by_title["Clonal evolution of tumours"].evidence["relevance"]["neighbours"]
    ]
    assert 11 not in own_neighbours and own_neighbours
    other_neighbours = [
        n["paper_id"] for n in by_title["Something new"].evidence["relevance"]["neighbours"]
    ]
    assert other_neighbours[0] == 11
    # with self excluded, the library paper is not more similar than an identical new paper
    assert by_title["Clonal evolution of tumours"].relevance <= by_title["Something new"].relevance


def test_knn_with_self_masking_and_tiny_library():
    library = np.array([[1.0, 0.0]])
    mean, top, _ = knn(np.array([[1.0, 0.0]]), library, k=5, self_columns=[0])
    assert mean.tolist() == [0.0] and top.shape == (1, 0)


def test_only_the_ratio_of_the_weights_matters():
    candidates = [candidate(f"Paper {i}", [1.0, i / 10, 0, 0], topics=["evo"]) for i in range(8)]
    small = rank(candidates)
    scaled = SEL.model_copy(update={"weights": Weights(relevance=5, impact=3, timeliness=2)})
    large = rank(candidates, sel=scaled)
    assert [r.candidate.paper.title for r in large.picks] == [
        r.candidate.paper.title for r in small.picks
    ]
    assert [r.score for r in large.picks] == pytest.approx([r.score for r in small.picks])
    assert all(0 <= r.score <= 1 for r in large.picks)
    with pytest.raises(ValueError):
        Weights(relevance=0, impact=0, timeliness=0)
    with pytest.raises(ValueError):
        Weights(relevance=-1)
