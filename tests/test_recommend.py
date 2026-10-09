import json
from datetime import date, timedelta

import pytest
from sqlmodel import Session, select

from conftest import FakeApis, make_work
from litresearch.config import Config
from litresearch.db import (
    Paper,
    QueueItem,
    Reading,
    ReadStatus,
    init_db,
)
from litresearch.ingest.pipeline import ingest_library
from litresearch.recommend.engine import (
    MEETING_ABSTRACT,
    batch_items,
    build_recommendations,
    latest_batch,
    week_start,
)

TODAY = date(2026, 10, 7)
EVOLUTION = "clonal evolution tumour phylogeny single-cell lineage tracing"


@pytest.fixture
def setup(cfg, make_http):
    cfg = Config.model_validate(
        {
            **cfg.model_dump(mode="json"),
            "topics": [{"name": "evo", "weight": 1.0, "queries": ["clonal evolution"]}],
            "excluded_terms": ["veterinary"],
        }
    )
    apis = FakeApis()
    apis.sources.append(
        {"id": "https://openalex.org/S1", "summary_stats": {"2yr_mean_citedness": 20.0}}
    )
    apis.search_results["fresh"] = [
        make_work(
            "10.9/fresh-good", "Clonal evolution in tumours", "2026-09-01", abstract=EVOLUTION
        ),
        make_work("10.9/fresh-two", "Lineage tracing of clones", "2026-08-15", abstract=EVOLUTION),
        make_work("10.9/meeting", "0865 Clonal evolution poster", "2026-09-01", abstract=EVOLUTION),
        make_work("10.9/editorial", "Clonal evolution editorial", "2026-09-01", kind="editorial"),
        make_work("10.9/vet", "Clonal evolution in veterinary oncology", "2026-09-01"),
        # already in the library (from the sample BibTeX)
        make_work("10.1038/s41588-022-01141-9", "Cancer cell states", "2022-08-01"),
    ]
    apis.search_results["landmark"] = [
        make_work(
            "10.9/landmark",
            "Tumour phylogeny classic",
            "2015-03-01",
            99.5,
            2000,
            abstract=EVOLUTION,
        ),
        make_work("10.9/mediocre", "Old average paper", "2012-01-01", 60.0, 30),
    ]
    rec = make_work("10.9/s2-rec", "Single-cell clonal lineages", "2026-09-20", abstract=EVOLUTION)
    apis.works.append(rec)
    apis.s2_recommendations = ["10.9/s2-rec", "10.9/unknown-to-openalex"]
    engine = init_db(cfg.paths.database)
    http = make_http(apis)
    with Session(engine) as session:
        ingest_library(cfg, session, http)
    yield cfg, engine, http, apis
    engine.dispose()


def build(setup, today=TODAY, **kwargs):
    cfg, engine, http, _ = setup
    with Session(engine) as session:
        batch = build_recommendations(cfg, session, http, today=today, **kwargs)
        items = batch_items(session, batch)
        return batch, [(r.model_copy(), p.model_copy()) for r, p in items]


def dois(items):
    return {p.doi for _, p in items}


def test_week_start_is_monday():
    assert week_start(date(2026, 10, 7)) == date(2026, 10, 5)
    assert week_start(date(2026, 10, 5)) == date(2026, 10, 5)
    assert week_start(date(2026, 10, 11)) == date(2026, 10, 5)


@pytest.mark.parametrize(
    "title, expected",
    [
        ("0865 The role of ctDNA", True),
        ("P-12: Poster", True),
        ("LB01. Late breaking", True),
        ("CD8 T cells in tumours", False),
        ("1000 Genomes", False),
        ("53BP1 loss", False),
    ],
)
def test_meeting_abstract_pattern(title, expected):
    assert bool(MEETING_ABSTRACT.match(title)) is expected


def test_batch_contents(setup):
    batch, items = build(setup)
    assert batch.week_start == date(2026, 10, 5)
    assert batch.warnings == []
    assert dois(items) == {"10.9/fresh-good", "10.9/fresh-two", "10.9/landmark", "10.9/s2-rec"}
    ranks = [r.rank for r, _ in items]
    assert ranks == list(range(1, len(items) + 1))
    assert all(r.category == "open" for r, _ in items)  # no prescribed depth
    scores = [r.score for r, _ in items]
    assert scores == sorted(scores, reverse=True)
    landmark = next(r for r, p in items if p.doi == "10.9/landmark")
    assert landmark.breakdown["kind"] == "landmark" and "top 0.5% cited" in landmark.reason
    s2_only = next(r for r, p in items if p.doi == "10.9/s2-rec")
    assert s2_only.breakdown["sources"] == ["s2-recs"]
    fresh = next(p for _, p in items if p.doi == "10.9/fresh-good")
    assert fresh.venue_mean_citedness == 20.0 and not fresh.in_library


def test_batch_records_funnel_params_and_provenance(setup):
    batch, items = build(setup)
    funnel = batch.funnel
    assert funnel["gathered"] == 9  # 6 fresh + 2 landmark + 1 S2 recommendation
    assert funnel["removed"] == {
        "wrong_type": 1,
        "meeting_abstract": 1,
        "read_or_dismissed": 0,
        "queued": 0,
        "recently_recommended": 0,
        "excluded_term": 1,
    }
    # the library paper (2022) passes the filters but is neither fresh nor a landmark
    assert funnel["scored"] == 6 and funnel["not_fresh_or_landmark"] == 2
    assert funnel["picked"] == len(items) == 4
    assert funnel["library_papers"] == 7 and funnel["library_embedded"] == 3
    assert batch.params["selection"]["n_recommendations"] == 5
    assert batch.params["topics"][0]["queries"] == ["clonal evolution"]
    rec = next(r for r, p in items if p.doi == "10.9/fresh-good")
    found = rec.breakdown["evidence"]["found_by"]
    assert found == [
        {"source": "openalex-fresh", "topic": "evo", "query": "clonal evolution", "position": 1}
    ]
    s2 = next(r for r, p in items if p.doi == "10.9/s2-rec")
    assert s2.breakdown["evidence"]["found_by"][0]["source"] == "s2-recs"


def test_latest_batch_per_week(setup):
    _, engine, _, _ = setup
    first, _ = build(setup)
    with Session(engine) as session:
        assert latest_batch(session, TODAY).id == first.id
        assert latest_batch(session, TODAY + timedelta(days=7)) is None
        assert latest_batch(session).id == first.id


def test_a_new_list_shows_other_papers(setup):
    # asking again (even the same day) gives papers that were not shown yet
    _, first = build(setup)
    _, second = build(setup)
    assert dois(first) and not dois(first) & dois(second)


def test_cooldown_hides_last_weeks_papers(setup):
    _, first = build(setup)
    _, next_week = build(setup, today=TODAY + timedelta(days=7))
    assert not dois(first) & dois(next_week)
    # after the cooldown (8 weeks) they may return
    _, later = build(setup, today=TODAY + timedelta(weeks=10))
    assert dois(later)


def test_read_dismissed_and_queued_papers_are_excluded(setup):
    _, engine, _, _ = setup
    build(setup)  # stores the candidate papers
    with Session(engine) as session:
        ids = {
            doi: session.exec(select(Paper.id).where(Paper.doi == doi)).one()
            for doi in ("10.9/fresh-good", "10.9/landmark", "10.9/s2-rec")
        }
        session.add(Reading(paper_id=ids["10.9/fresh-good"], status=ReadStatus.skimmed))
        session.add(Reading(paper_id=ids["10.9/landmark"], status=ReadStatus.dismissed))
        session.add(QueueItem(paper_id=ids["10.9/s2-rec"], position=1))
        session.commit()
    # well after the cooldown, only the untouched paper can come back
    _, items = build(setup, today=TODAY + timedelta(weeks=20))
    assert dois(items) <= {"10.9/fresh-two"}


def test_dismissed_papers_are_negative_examples_for_s2(setup):
    _, engine, _, apis = setup
    build(setup)
    with Session(engine) as session:
        landmark = session.exec(select(Paper).where(Paper.doi == "10.9/landmark")).one()
        session.add(Reading(paper_id=landmark.id, status=ReadStatus.dismissed))
        session.commit()
    build(setup, today=TODAY + timedelta(weeks=1))
    request = [r for r in apis.requests if r.url.path.startswith("/recommendations/")][-1]
    body = json.loads(request.content)
    assert "DOI:10.9/landmark" in body["negativePaperIds"]
    assert "DOI:10.1038/s41588-022-01141-9" in body["positivePaperIds"]


def test_without_s2(setup):
    _, _, _, apis = setup
    before = apis.count("api.semanticscholar.org")
    _, items = build(setup, use_s2=False)
    assert apis.count("api.semanticscholar.org") == before
    assert "10.9/s2-rec" not in dois(items) and dois(items)


def test_failures_become_warnings(cfg, make_http):
    apis = FakeApis(down=("api.openalex.org", "api.semanticscholar.org"))
    cfg = Config.model_validate(
        {**cfg.model_dump(mode="json"), "topics": [{"name": "t", "queries": ["q"]}]}
    )
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        session.add(Paper(title="lib", doi="10.1/lib", in_library=True))
        session.commit()
        batch = build_recommendations(cfg, session, make_http(apis, max_retries=0), today=TODAY)
        assert batch_items(session, batch) == []
        warnings = " | ".join(batch.warnings)
    engine.dispose()
    assert "OpenAlex search 'q'" in warnings and "Semantic Scholar recommendations" in warnings


def test_unread_library_papers_can_be_recommended(setup):
    _, engine, _, _ = setup
    library_doi = "10.1038/s41588-022-01141-9"
    with Session(engine) as session:
        paper = session.exec(select(Paper).where(Paper.doi == library_doi)).one()
        paper.pub_date = TODAY - timedelta(days=10)  # make it fresh
        session.add(paper)
        session.commit()
        paper_id = paper.id
    _, items = build(setup)
    rec = next(r for r, p in items if p.doi == library_doi)
    assert "in your Paperpile, not read yet" in rec.reason
    # a library paper is never its own nearest neighbour
    neighbours = rec.breakdown["evidence"]["relevance"]["neighbours"]
    assert paper_id not in [n["paper_id"] for n in neighbours]

    # an abstract glance keeps it eligible (after the cooldown) ...
    with Session(engine) as session:
        session.add(Reading(paper_id=paper_id, status=ReadStatus.abstract))
        session.commit()
    _, items = build(setup, today=TODAY + timedelta(weeks=9))
    assert library_doi in dois(items)
    # ... reading it removes it
    with Session(engine) as session:
        reading = session.get(Reading, paper_id)
        reading.status = ReadStatus.skimmed
        session.add(reading)
        session.commit()
    _, items = build(setup, today=TODAY + timedelta(weeks=18))
    assert library_doi not in dois(items)
