"""Large data: big libraries, long reading histories, many recommendation sets.

The data is generated and bulk-inserted; the tests assert on query counts and on
how many rows are loaded, which does not depend on the speed of the machine.
"""

import random
from contextlib import contextmanager
from datetime import date, datetime, timedelta

import numpy as np
import pytest
from sqlalchemy import event, insert
from sqlmodel import Session

from litresearch import game
from litresearch.config import Selection
from litresearch.db import (
    Embedding,
    Paper,
    Reading,
    ReadStatus,
    Recommendation,
    RecommendationBatch,
    StatusEvent,
    XpEvent,
    init_db,
)
from litresearch.game import ledger, quest_progress, stats, total_xp, week_progress, week_start
from litresearch.reading import find_paper, record_reading
from litresearch.recommend import scoring
from litresearch.recommend.engine import vectors_for
from litresearch.recommend.scoring import Candidate, LibraryProfile, knn, rank_candidates
from litresearch.recommend.text import tfidf_matrix
from litresearch.rules import GameRules
from litresearch.sources.semanticscholar import EMBEDDING_MODEL

N_PAPERS = 5000
WEEKS = 110  # two years and a bit, across two year boundaries
START = datetime(2024, 12, 2, 8)


@pytest.fixture
def engine(cfg):
    engine = init_db(cfg.paths.database)
    yield engine
    engine.dispose()


@contextmanager
def counting(engine):
    """Counts SQL statements and loaded Paper rows."""
    counts = {"queries": 0, "papers": 0}

    def on_query(*_):
        counts["queries"] += 1

    def on_load(*_):
        counts["papers"] += 1

    event.listen(engine, "before_cursor_execute", on_query)
    event.listen(Paper, "load", on_load)
    try:
        yield counts
    finally:
        event.remove(engine, "before_cursor_execute", on_query)
        event.remove(Paper, "load", on_load)


def fill(engine, papers=N_PAPERS, weeks=WEEKS, batches=300):
    """A library, two years of reading, XP and recommendation sets."""
    rng = random.Random(7)
    created = datetime(2024, 1, 1)
    events, xp, read = [], [], {}
    for week in range(weeks):
        if week % 9 == 8:
            continue  # a rest week now and then
        for slot in range(rng.randint(1, 4)):
            at = START + timedelta(weeks=week, days=slot, hours=rng.randint(0, 14))
            paper_id = rng.randint(1, papers)
            status = rng.choice(list(ReadStatus))
            events.append({"paper_id": paper_id, "status": status, "at": at})
            xp.append({"paper_id": paper_id, "points": 15, "kind": "read", "reason": "r", "at": at})
            read[paper_id] = {"paper_id": paper_id, "status": status, "notes": "", "updated_at": at}
    with engine.begin() as connection:
        connection.execute(
            insert(Paper),
            [
                {
                    "id": i,
                    "title": f"Paper {i}: tumour évolution in cohort {i % 97}",
                    "doi": f"10.1000/p{i}",
                    "year": 1990 + i % 36,
                    "authors": [],
                    "topics": [f"Topic {i % 31}"],
                    "folders": [],
                    "tags": [],
                    "in_library": i % 2 == 0,
                    "created_at": created,
                    "updated_at": created,
                }
                for i in range(1, papers + 1)
            ],
        )
        connection.execute(insert(StatusEvent), events)
        connection.execute(insert(XpEvent), xp)
        connection.execute(insert(Reading), list(read.values()))
        connection.execute(
            insert(RecommendationBatch),
            [
                {
                    "id": b,
                    "created_at": created + timedelta(days=2 * b),
                    "week_start": week_start((created + timedelta(days=2 * b)).date()),
                    "warnings": [],
                }
                for b in range(1, batches + 1)
            ],
        )
        connection.execute(
            insert(Recommendation),
            [
                {
                    "batch_id": b,
                    "paper_id": rng.randint(1, papers),
                    "rank": r,
                    "category": "open",
                    "score": 0.5,
                    "breakdown": {},
                }
                for b in range(1, batches + 1)
                for r in range(1, 6)
            ],
        )
    return events


TODAY = (START + timedelta(weeks=WEEKS)).date()


def test_stats_queries_do_not_grow_with_weeks_or_library(engine):
    events = fill(engine)
    with Session(engine) as session, counting(engine) as counts:
        figures = stats(session, TODAY, GameRules())
    read_ids = {e["paper_id"] for e in events if e["status"] not in ("abstract", "dismissed")}
    assert figures["papers_read"] == len(read_ids)
    # one query per table and kind, not one per week (there are ~100 reading weeks)
    assert counts["queries"] <= 15, counts
    # only a few columns of the papers you read are queried: no Paper objects at all
    assert counts["papers"] == 0 and len(read_ids) < N_PAPERS / 2


def test_report_card_grades_match_week_progress(engine):
    fill(engine, papers=300, weeks=60, batches=5)
    rules = GameRules()
    with Session(engine) as session:
        figures = stats(session, TODAY, rules)
        grades = [
            game.grade(week_progress(session, rules, monday), True)[0]
            for monday in game.reading_weeks(session)
        ]
    assert figures["a_weeks"] == sum(g in "AS" for g in grades)
    assert figures["s_weeks"] == grades.count("S")


def test_quest_progress_counts_deepest_level_per_paper():
    progress = quest_progress([3, 2, 2, 1, 0], GameRules())
    assert (progress["detail"], progress["total"], progress["done"]) == (1, 3, True)
    assert quest_progress([], GameRules(weekly_detail_goal=0, weekly_skim_goal=0))["done"]


def test_ledger_loads_only_the_papers_it_shows(engine):
    fill(engine)
    with Session(engine) as session, counting(engine) as counts:
        rows, total = ledger(session, limit=10)
        assert counts["papers"] <= 10
        assert total > 100
        assert rows[0]["balance"] == total_xp(session)
        assert all(row["paper"] is not None for row in rows)
        later, _ = ledger(session, limit=10, offset=10)
    assert later[0]["balance"] == rows[-1]["balance"] - rows[-1]["event"].points


def test_find_paper_by_title_words_does_not_load_the_library(engine):
    fill(engine, weeks=1, batches=1)
    with Session(engine) as session, counting(engine) as counts:
        paper = find_paper(session, "paper 4321: tumour")
        assert paper.id == 4321
        assert counts["papers"] == 1
        with pytest.raises(ValueError, match=r"'ÉVOLUTION cohort 13' matches \d+ papers"):
            find_paper(session, "ÉVOLUTION cohort 13")  # unicode titles match case-insensitively
        assert counts["papers"] == 1


def test_record_reading_stays_cheap_after_years(engine, cfg):
    fill(engine)
    at = datetime.combine(TODAY, START.time())
    with Session(engine) as session:
        # the first reading starts the profile and unlocks a pile of badges at once
        record_reading(session, cfg, session.get(Paper, 1), ReadStatus.read, at=at)
        with counting(engine) as counts:
            paper = session.get(Paper, 2)
            record_reading(session, cfg, paper, ReadStatus.read, at=at + timedelta(hours=1))
    assert counts["queries"] < 40, counts
    assert counts["papers"] < 1500  # the read papers for the badge statistics, not all 5000


def test_vectors_for_more_ids_than_sqlite_parameters(engine):
    with engine.begin() as connection:
        connection.execute(
            insert(Paper),
            [
                {"id": i, "title": f"P{i}", "authors": [], "topics": [], "folders": [], "tags": []}
                for i in range(1, 4)
            ],
        )
        connection.execute(
            insert(Embedding),
            [
                {
                    "paper_id": i,
                    "model": EMBEDDING_MODEL,
                    "vector": np.full(4, i, dtype=np.float32).tobytes(),
                }
                for i in (1, 3)
            ],
        )
    with Session(engine) as session:
        # more than SQLite's 32766 parameters in one query
        vectors = vectors_for(session, list(range(1, 40_001)))
    assert sorted(vectors) == [1, 3] and vectors[3][0] == 3


# --- scoring with large libraries -------------------------------------------------------

VOCABULARY = [f"term{i}" for i in range(3000)]


def texts(rng, count, words=60):
    return [" ".join(rng.choices(VOCABULARY, k=words)) for _ in range(count)]


def test_restricted_tfidf_keeps_candidate_similarities_exact():
    rng = random.Random(3)
    corpus = texts(rng, 40) + texts(rng, 400)
    full = tfidf_matrix(corpus)
    restricted = tfidf_matrix(corpus, columns_from=40)
    assert restricted.shape[1] < full.shape[1]
    np.testing.assert_allclose(restricted[:40] @ restricted.T, full[:40] @ full.T, atol=1e-5)


def test_chunked_knn_matches_one_block(monkeypatch):
    rng = np.random.default_rng(1)
    candidates, library = rng.random((7, 5)), rng.random((23, 5))
    whole = knn(candidates, library, 4, self_columns=[0, -1, 5, -1, -1, -1, 22])
    monkeypatch.setattr(scoring, "KNN_CHUNK", 3)
    chunked = knn(candidates, library, 4, self_columns=[0, -1, 5, -1, -1, -1, 22])
    for a, b in zip(whole, chunked, strict=True):
        np.testing.assert_allclose(a, b)


def make_candidates(rng, count, first_id):
    candidates = []
    for i, text in enumerate(texts(rng, count)):
        paper = Paper(
            id=first_id + i, title=text[:40], abstract=text, pub_date=date(2026, 9, 1 + i % 20)
        )
        candidates.append(Candidate(paper, {"t"}, {"openalex-fresh"}))
    return candidates


def test_large_library_ranking_equals_the_full_tfidf_ranking(monkeypatch):
    rng = random.Random(5)
    size = 3000
    library = LibraryProfile(
        ids=list(range(1, size + 1)),
        titles=[f"Library paper {i}" for i in range(1, size + 1)],
        texts=texts(rng, size),
    )
    candidates = make_candidates(rng, 60, size + 1)
    today = date(2026, 10, 1)
    result = rank_candidates(candidates, library, {"t": 1.0}, Selection(), today)
    # reference: a TF-IDF matrix with every word of the library
    monkeypatch.setattr(
        scoring, "tfidf_matrix", lambda corpus, columns_from=None: tfidf_matrix(corpus)
    )
    reference = rank_candidates(candidates, library, {"t": 1.0}, Selection(), today)
    assert [s.paper.id for s in result.picks] == [s.paper.id for s in reference.picks]
    for a, b in zip(result.picks, reference.picks, strict=True):
        assert a.relevance == pytest.approx(b.relevance)
        neighbours = a.evidence["relevance"]["neighbours"]
        assert [n["paper_id"] for n in neighbours] == [
            n["paper_id"] for n in b.evidence["relevance"]["neighbours"]
        ]
        assert all(n["title"] == f"Library paper {n['paper_id']}" for n in neighbours)


def test_ranking_ties_are_deterministic():
    paper_args = {"abstract": "same words here", "pub_date": date(2026, 9, 1)}
    library = LibraryProfile(ids=[1], titles=["L"], texts=["same words here"])

    def ranked(order):
        candidates = [
            Candidate(Paper(id=i, title="Same title", **paper_args), {"t"}, {"openalex-fresh"})
            for i in order
        ]
        return [
            s.paper.id
            for s in rank_candidates(
                candidates, library, {"t": 1.0}, Selection(mmr_lambda=1.0), date(2026, 10, 1)
            ).picks
        ]

    assert ranked(range(10, 20)) == ranked(range(10, 20))
    assert ranked(range(10, 20)) == list(range(10, 15))  # equal scores keep the input order
