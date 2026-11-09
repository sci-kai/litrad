from datetime import date, datetime

import pytest
from sqlmodel import Session

from litrad.config import Config
from litrad.db import (
    Category,
    Paper,
    QueueItem,
    Reading,
    ReadStatus,
    Recommendation,
    RecommendationBatch,
    init_db,
)
from litrad.game import level_for, total_xp
from litrad.reading import (
    ReadingError,
    add_note,
    drop_from_queue,
    drop_would_cost,
    find_paper,
    queue_add,
    queue_items,
    queue_move,
    queue_remove,
    record_reading,
    source_of,
)

AT = datetime(2026, 10, 7, 8, 30, 0)


@pytest.fixture
def db(cfg):
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        papers = [
            Paper(
                title="Clonal evolution in melanoma",
                doi="10.1/a",
                bibtex_key="Smith2020-ab",
                in_library=True,
                pmid="111",
            ),
            Paper(title="Clonal evolution in lung cancer", doi="10.1/b"),
            Paper(title="Fibroblast heterogeneity", doi="10.1/c"),
            Paper(title="Spatial transcriptomics atlas", doi="10.1/d"),
        ]
        session.add_all(papers)
        batch = RecommendationBatch(week_start=date(2026, 10, 5))
        session.add(batch)
        session.commit()
        session.add(
            Recommendation(
                batch_id=batch.id,
                paper_id=papers[1].id,
                rank=1,
                category=Category.detail,
                score=0.9,
            )
        )
        session.commit()
        yield cfg, session, papers
    engine.dispose()


def titles(session):
    return [p.title for _, p in queue_items(session)]


# --- references -------------------------------------------------------------


def test_find_paper_reference_forms(db):
    cfg, session, papers = db
    queue_add(session, cfg, papers[2])
    assert find_paper(session, "r1") == papers[1]
    assert find_paper(session, "q1") == papers[2]
    assert find_paper(session, f"#{papers[3].id}") == papers[3]
    assert find_paper(session, "10.1/A") == papers[0]
    assert find_paper(session, "https://doi.org/10.1/b") == papers[1]
    assert find_paper(session, "pmid:111") == papers[0]
    assert find_paper(session, "Smith2020-ab") == papers[0]
    assert find_paper(session, "fibroblast") == papers[2]
    assert find_paper(session, "clonal melanoma") == papers[0]
    assert find_paper(session, "10.1/unknown") is None  # caller may fetch it


@pytest.mark.parametrize(
    "ref, message",
    [
        ("r9", "No recommendation"),
        ("q3", "no position"),
        ("#999", "No paper"),
        ("zebrafish", "Nothing matches"),
        ("clonal evolution", "matches 2 papers"),
    ],
)
def test_find_paper_errors(db, ref, message):
    _, session, _ = db
    with pytest.raises(ReadingError, match=message):
        find_paper(session, ref)


def test_unknown_identifiers_are_not_errors(db):
    # unknown DOIs, PMIDs and PMC ids are fetched by fetch_paper when added
    _, session, _ = db
    assert find_paper(session, "pmid:9") is None
    assert find_paper(session, "PMC123") is None
    assert find_paper(session, "10.9/unknown") is None


def test_source_of(db):
    _, session, papers = db
    assert [source_of(session, p) for p in papers[:3]] == ["library", "recommendation", "manual"]


# --- queue ------------------------------------------------------------------


def test_queue_order_insert_and_move(db):
    cfg, session, papers = db
    for paper in papers[:3]:
        queue_add(session, cfg, paper)
    queue_add(session, cfg, papers[3], position=1)
    assert titles(session)[0] == "Spatial transcriptomics atlas"
    queue_move(session, papers[3], 99)  # clamped to the end
    assert titles(session)[-1] == "Spatial transcriptomics atlas"
    queue_move(session, papers[2], 1)
    assert [i.position for i, _ in queue_items(session)] == [1, 2, 3, 4]
    assert titles(session)[0] == "Fibroblast heterogeneity"


def test_queue_limit(db):
    cfg, session, papers = db
    cfg = Config.model_validate({**cfg.model_dump(mode="json"), "queue": {"max_size": 2}})
    queue_add(session, cfg, papers[0])
    queue_add(session, cfg, papers[1])
    with pytest.raises(ReadingError, match="Queue is full \\(2/2\\)"):
        queue_add(session, cfg, papers[2])


def test_queue_rejects_duplicates_and_read_papers(db):
    cfg, session, papers = db
    queue_add(session, cfg, papers[0])
    with pytest.raises(ReadingError, match="Already on the queue"):
        queue_add(session, cfg, papers[0])
    record_reading(session, cfg, papers[1], ReadStatus.read, at=AT)
    with pytest.raises(ReadingError, match="Already read"):
        queue_add(session, cfg, papers[1])
    # an abstract-only paper may still be queued for a proper read
    record_reading(session, cfg, papers[2], ReadStatus.abstract, at=AT)
    queue_add(session, cfg, papers[2])


def test_queue_remove_renumbers(db):
    cfg, session, papers = db
    for paper in papers[:3]:
        queue_add(session, cfg, paper)
    assert queue_remove(session, papers[0])
    assert not queue_remove(session, papers[0])
    assert [i.position for i, _ in queue_items(session)] == [1, 2]
    with pytest.raises(ReadingError, match="Not on the queue"):
        queue_move(session, papers[0], 1)


def test_reading_takes_paper_off_the_queue(db):
    cfg, session, papers = db
    queue_add(session, cfg, papers[0])
    queue_add(session, cfg, papers[1])
    outcome = record_reading(session, cfg, papers[1], ReadStatus.skimmed, at=AT)
    assert outcome.left_queue and outcome.previous is None
    assert titles(session) == ["Clonal evolution in melanoma"]
    outcome = record_reading(session, cfg, papers[0], ReadStatus.dismissed, at=AT)
    assert outcome.left_queue and titles(session) == []


# --- notes and ratings ---------------------------------------------------------


def test_notes_are_appended_with_timestamps(db):
    cfg, session, papers = db
    record_reading(session, cfg, papers[0], ReadStatus.read, note="first thought", at=AT)
    add_note(session, cfg, papers[0], "second thought", at=AT.replace(hour=9))
    notes = session.get(Reading, papers[0].id).notes
    assert notes == "[2026-10-07T08:30:00] first thought\n\n[2026-10-07T09:30:00] second thought"


def test_note_requires_a_marked_paper(db):
    cfg, session, papers = db
    with pytest.raises(ReadingError, match="Mark the paper first"):
        add_note(session, cfg, papers[3], "text")


def test_long_note_later_earns_reflection_bonus_once(db):
    cfg, session, papers = db
    record_reading(session, cfg, papers[0], ReadStatus.read, at=AT)
    award = add_note(session, cfg, papers[0], "y" * 200, at=AT)
    assert [e.kind for e in award.events if e.kind != "badge"] == ["reflection"]
    assert [b.code for b in award.badges] == ["note_taker"]  # its first-tier badge
    assert add_note(session, cfg, papers[0], "z" * 200, at=AT).points == 0


def test_rating_validation_and_update(db):
    cfg, session, papers = db
    with pytest.raises(ReadingError, match="between 1 and 5"):
        record_reading(session, cfg, papers[0], ReadStatus.read, rating=6, at=AT)
    record_reading(session, cfg, papers[0], ReadStatus.read, rating=3, at=AT)
    outcome = record_reading(session, cfg, papers[0], ReadStatus.thorough, rating=5, at=AT)
    assert outcome.reading.rating == 5 and outcome.previous == ReadStatus.read


# --- drop penalty --------------------------------------------------------------


def test_dropping_a_picked_recommendation_unread_costs_xp(db):
    cfg, session, papers = db
    queue_add(session, cfg, papers[1], source="recommendation")
    assert drop_would_cost(session, cfg, papers[1]) == 15
    penalty = drop_from_queue(session, cfg, papers[1], at=AT)
    assert penalty.points == -15 and penalty.kind == "penalty"
    assert total_xp(session) == -15
    assert level_for(total_xp(session)).number == 1  # levels never go below 1


def test_abstract_only_still_counts_as_unread(db):
    cfg, session, papers = db
    queue_add(session, cfg, papers[1], source="recommendation")
    record_reading(session, cfg, papers[1], ReadStatus.abstract, at=AT)
    assert drop_from_queue(session, cfg, papers[1], at=AT).points == -15


def test_every_queued_paper_costs_xp_when_dropped_unread(db):
    cfg, session, papers = db
    penalty = cfg.game.drop_penalty
    queue_add(session, cfg, papers[0], source="library")
    queue_add(session, cfg, papers[2], source="manual")
    assert drop_would_cost(session, cfg, papers[0]) == penalty
    assert drop_from_queue(session, cfg, papers[0], at=AT).points == -penalty
    event = drop_from_queue(session, cfg, papers[2], at=AT)
    assert event.points == -penalty and "left the queue unread" in event.reason
    with pytest.raises(ReadingError, match="Not on the queue"):
        drop_from_queue(session, cfg, papers[2], at=AT)
    # once skimmed, a paper can leave for free
    queue_add(session, cfg, papers[2], source="manual")
    record_reading(session, cfg, papers[2], ReadStatus.abstract, at=AT)
    assert drop_would_cost(session, cfg, papers[2]) == penalty  # an abstract is not enough
    record_reading(session, cfg, papers[2], ReadStatus.skimmed, at=AT)
    assert session.get(QueueItem, papers[2].id) is None  # skimming removes it anyway


def test_dismissing_a_picked_recommendation_costs_xp(db):
    cfg, session, papers = db
    queue_add(session, cfg, papers[1], source="recommendation")
    award = record_reading(session, cfg, papers[1], ReadStatus.dismissed, at=AT).award
    assert [e.kind for e in award.events] == ["penalty"] and award.points == -15
    # dismissing a recommendation you never picked is free
    award = record_reading(session, cfg, papers[3], ReadStatus.dismissed, at=AT).award
    assert award.points == 0


def test_penalty_can_be_disabled(db):
    cfg, session, papers = db
    cfg = Config.model_validate({**cfg.model_dump(mode="json"), "game": {"drop_penalty": 0}})
    queue_add(session, cfg, papers[1], source="recommendation")
    assert drop_would_cost(session, cfg, papers[1]) == 0
    assert drop_from_queue(session, cfg, papers[1], at=AT) is None
