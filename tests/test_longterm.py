"""Months and years of use: calendar edges, long gaps, the top of the ladder, re-logging."""

from datetime import date, datetime, timedelta

import pytest
import yaml
from sqlmodel import Session, select
from typer.testing import CliRunner

import litresearch.cli as cli
from litresearch.db import (
    Achievement,
    Category,
    Paper,
    ReadStatus,
    Recommendation,
    RecommendationBatch,
    XpEvent,
    init_db,
)
from litresearch.game import (
    level_for,
    next_read_streak,
    personal_bests,
    reading_calendar,
    reading_weeks,
    stats,
    streaks,
    unlock_badges,
    week_progress,
    week_start,
    weekly_summary,
)
from litresearch.profile import start_profile
from litresearch.reading import ReadingError, drop_from_queue, queue_add, record_reading
from litresearch.recommend.engine import exclusion_sets
from litresearch.rules import GameRules


@pytest.fixture
def db(cfg):
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        yield cfg, session
    engine.dispose()


def add(session, title="Paper", **kwargs) -> Paper:
    paper = Paper(title=title, **kwargs)
    session.add(paper)
    session.commit()
    return paper


def xp_of(session, kind):
    return session.exec(select(XpEvent).where(XpEvent.kind == kind)).all()


# --- calendar edges -------------------------------------------------------------------


@pytest.mark.parametrize(
    "monday",
    [
        date(2020, 12, 21),  # 2020 has an ISO week 53 (2020-12-28 .. 2021-01-03)
        date(2026, 12, 21),  # 2026 too (2026-12-28 .. 2027-01-03)
        date(2027, 12, 20),  # 2027 does not
    ],
)
def test_streak_runs_across_the_year_boundary(monday):
    weeks = [monday + timedelta(weeks=i) for i in range(4)]
    assert streaks(weeks, weeks[-1]) == (4, 4)
    # alive through the following week, gone after it
    assert streaks(weeks, weeks[-1] + timedelta(days=13)) == (4, 4)
    assert streaks(weeks, weeks[-1] + timedelta(days=14)) == (0, 4)


def test_week_53_reads_form_one_reading_week(db):
    cfg, session = db
    start_profile(session, cfg, at=datetime(2026, 12, 1))
    # Thursday 2026-12-31 and Saturday 2027-01-02 are in ISO week 2026-W53
    record_reading(session, cfg, add(session, "A"), ReadStatus.read, at=datetime(2026, 12, 31, 9))
    record_reading(session, cfg, add(session, "B"), ReadStatus.skimmed, at=datetime(2027, 1, 2, 9))
    record_reading(session, cfg, add(session, "C"), ReadStatus.skimmed, at=datetime(2027, 1, 3, 23))
    assert reading_weeks(session) == [date(2026, 12, 28)]
    quests = xp_of(session, "quest")
    assert [q.reason for q in quests] == ["Weekly quest 2026-12-28"]
    assert week_progress(session, GameRules(), date(2027, 1, 3))["done"]
    # Monday 2027-01-04 starts a new week (and the streak goes on)
    assert not week_progress(session, GameRules(), date(2027, 1, 4))["total"]
    assert streaks(reading_weeks(session), date(2027, 1, 10)) == (1, 1)


def test_leap_day_and_old_paper(db):
    cfg, session = db
    start_profile(session, cfg, at=datetime(2028, 2, 1))
    old = add(session, "Old", pub_date=date(2003, 2, 28))
    record_reading(session, cfg, old, ReadStatus.read, at=datetime(2028, 2, 29, 12))
    figures = stats(session, date(2028, 2, 29))
    assert figures["old_papers"] == 1
    assert reading_weeks(session) == [date(2028, 2, 28)]


def test_long_gap_resets_streak_and_counts_one_comeback(db):
    cfg, session = db
    rules = GameRules(streak_step=0.5, streak_max_steps=10)
    start_profile(session, cfg, rules=rules, at=datetime(2025, 1, 6))
    for week in range(3):
        at = datetime(2025, 1, 6, 9) + timedelta(weeks=week)
        record_reading(session, cfg, add(session, f"W{week}"), ReadStatus.skimmed, at=at)
    # 40 weeks later
    back = datetime(2025, 1, 20, 9) + timedelta(weeks=40)
    award = record_reading(session, cfg, add(session, "Back"), ReadStatus.skimmed, at=back).award
    assert "streak" not in {e.kind for e in award.events}  # streak 1: no bonus
    figures = stats(session, back.date())
    assert (figures["streak"], figures["best_streak"], figures["comebacks"]) == (1, 3, 1)


def test_report_card_for_a_week_without_activity(db):
    cfg, session = db
    start_profile(session, cfg, at=datetime(2026, 1, 5))
    record_reading(session, cfg, add(session, "A"), ReadStatus.read, at=datetime(2026, 1, 5, 9))
    empty = weekly_summary(session, GameRules(), date(2026, 3, 2), today=date(2026, 6, 1))
    assert (empty["grade"], empty["comment"], empty["xp"], empty["papers"]) == (
        "-",
        "Rest week",
        0,
        [],
    )
    running = weekly_summary(session, GameRules(), date(2026, 6, 1), today=date(2026, 6, 3))
    assert running["comment"] == "Nothing read yet this week" and running["current"]


def test_next_read_streak(db):
    cfg, session = db
    start_profile(session, cfg, at=datetime(2026, 1, 5))
    assert next_read_streak(session, date(2026, 1, 5)) == 1
    record_reading(session, cfg, add(session, "A"), ReadStatus.skimmed, at=datetime(2026, 1, 5, 9))
    assert next_read_streak(session, date(2026, 1, 6)) == 1  # this week is already counted
    assert next_read_streak(session, date(2026, 1, 12)) == 2  # the next read extends it
    assert next_read_streak(session, date(2026, 1, 19)) == 1  # the streak has ended


# --- levels and badges over a long game -----------------------------------------------


def test_top_of_the_ladder_and_below_zero():
    rules = GameRules()
    top = level_for(10**7, rules)
    assert top.number == len(rules.levels) and top.next_floor is None
    assert top.progress(10**7) == 1.0
    exact = level_for(rules.levels[-1].xp, rules)
    assert exact.number == len(rules.levels)
    assert level_for(-500, rules).number == 1


def test_badges_are_unlocked_and_paid_once(db):
    cfg, session = db
    start_profile(session, cfg, at=datetime(2026, 1, 5))
    record_reading(session, cfg, add(session, "A"), ReadStatus.skimmed, at=datetime(2026, 1, 5, 9))
    at = datetime(2026, 1, 6, 9)
    for _ in range(3):
        assert unlock_badges(session, GameRules(), at.date(), at, []) == []
    assert len(session.exec(select(Achievement)).all()) == 1
    assert len(xp_of(session, "badge")) == 1


def test_relogging_one_paper_at_night_is_one_night_read(db):
    cfg, session = db
    start_profile(session, cfg, rules=GameRules(badge_targets={"night_owl": 2}))
    paper = add(session, "Night")
    for day in range(5):
        status = ReadStatus.skimmed if day % 2 else ReadStatus.read
        record_reading(session, cfg, paper, status, at=datetime(2026, 1, 5 + day, 23))
    assert stats(session, date(2026, 1, 10))["night_reads"] == 1
    assert session.get(Achievement, "night_owl") is None


def recommend_again(session, paper, day):
    batch = RecommendationBatch(week_start=week_start(day))
    session.add(batch)
    session.flush()
    session.add(
        Recommendation(
            batch_id=batch.id, paper_id=paper.id, rank=1, category=Category.open, score=1
        )
    )
    session.commit()


def test_relogging_read_papers_counts_for_no_quest_streak_or_report(db):
    """Only a deeper level is progress, also for a paper recommended again."""
    cfg, session = db
    start_profile(session, cfg, at=datetime(2026, 1, 1))
    papers = [add(session, f"P{i}") for i in range(3)]
    first_week = date(2026, 1, 5)
    for i, paper in enumerate(papers):
        status = ReadStatus.read if i == 0 else ReadStatus.skimmed
        record_reading(session, cfg, paper, status, at=datetime(2026, 1, 5 + i, 9))
    assert week_progress(session, GameRules(), first_week)["done"]
    xp_before = sum(e.points for e in session.exec(select(XpEvent)).all())

    # three weeks later: the same papers, recommended again and logged again
    later = date(2026, 1, 26)
    for i, paper in enumerate(papers):
        recommend_again(session, paper, later)
        status = ReadStatus.read if i == 0 else ReadStatus.skimmed
        record_reading(session, cfg, paper, status, at=datetime(2026, 1, 26 + i, 9))
        record_reading(session, cfg, paper, ReadStatus.abstract, at=datetime(2026, 1, 26 + i, 10))

    progress = week_progress(session, GameRules(), later)
    assert progress["total"] == 0 and not progress["done"]
    assert reading_weeks(session) == [first_week]
    assert sum(e.points for e in session.exec(select(XpEvent)).all()) == xp_before
    assert [q.reason for q in xp_of(session, "quest")] == ["Weekly quest 2026-01-05"]
    summary = weekly_summary(session, GameRules(), later, date(2026, 2, 1))
    assert summary["papers"] == [] and summary["grade"] == "-"
    s = stats(session, date(2026, 2, 1))
    assert s["max_week"] == 3 and s["a_weeks"] == 1 and s["papers_read"] == 3
    assert personal_bests(session, GameRules(), date(2026, 2, 1))["busiest_week"] == first_week
    logged = [d for column in reading_calendar(session, date(2026, 2, 1), 5) for d in column]
    assert {d["date"] for d in logged if d["count"]} == {date(2026, 1, 5 + i) for i in range(3)}


def test_a_deeper_level_counts_in_its_week(db):
    cfg, session = db
    start_profile(session, cfg, at=datetime(2026, 1, 1))
    paper = add(session, "Deeper")
    record_reading(session, cfg, paper, ReadStatus.skimmed, at=datetime(2026, 1, 5, 9))
    record_reading(session, cfg, paper, ReadStatus.skimmed, at=datetime(2026, 1, 12, 9))
    assert week_progress(session, GameRules(), date(2026, 1, 12))["total"] == 0
    recommend_again(session, paper, date(2026, 1, 19))
    record_reading(session, cfg, paper, ReadStatus.thorough, at=datetime(2026, 1, 19, 9))
    progress = week_progress(session, GameRules(), date(2026, 1, 19))
    assert (progress["detail"], progress["thorough"]) == (1, 1)
    assert reading_weeks(session) == [date(2026, 1, 5), date(2026, 1, 19)]
    papers = weekly_summary(session, GameRules(), date(2026, 1, 19), date(2026, 1, 25))["papers"]
    assert [(p["paper"].title, p["status"]) for p in papers] == [("Deeper", ReadStatus.thorough)]


# --- drop penalty --------------------------------------------------------------------


def test_drop_penalty_is_applied_once(db):
    cfg, session = db
    start_profile(session, cfg)
    paper = add(session, "Queued")
    queue_add(session, cfg, paper)
    record_reading(session, cfg, paper, ReadStatus.dismissed)
    with pytest.raises(ReadingError):
        drop_from_queue(session, cfg, paper)
    record_reading(session, cfg, paper, ReadStatus.dismissed)
    assert [e.points for e in xp_of(session, "penalty")] == [-15]


def test_read_paper_dismissed_later_costs_nothing(db):
    cfg, session = db
    start_profile(session, cfg)
    paper = add(session, "Queued")
    queue_add(session, cfg, paper)
    record_reading(session, cfg, paper, ReadStatus.abstract)
    record_reading(session, cfg, paper, ReadStatus.skimmed)
    record_reading(session, cfg, paper, ReadStatus.dismissed)
    assert xp_of(session, "penalty") == []


# --- recommendations after a downgrade ------------------------------------------------


def test_a_read_paper_marked_abstract_later_is_not_recommended_again(db):
    cfg, session = db
    start_profile(session, cfg)
    paper = add(session, "Read once", doi="10.1/once")
    record_reading(session, cfg, paper, ReadStatus.thorough)
    record_reading(session, cfg, paper, ReadStatus.abstract)
    glance = add(session, "Only the abstract", doi="10.1/glance")
    record_reading(session, cfg, glance, ReadStatus.abstract)
    excluded = exclusion_sets(session, cfg, date.today())["read_or_dismissed"]
    assert paper.id in excluded
    assert glance.id not in excluded


# --- CLI ------------------------------------------------------------------------------


def test_history_shows_penalties_as_negative(tmp_path, cfg):
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({"paths": {"database": str(cfg.paths.database)}}), "utf-8")
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        start_profile(session, cfg)
        paper = add(session, "Dropped paper")
        queue_add(session, cfg, paper)
        record_reading(session, cfg, paper, ReadStatus.dismissed)
    engine.dispose()
    result = CliRunner().invoke(cli.app, ["history", "-c", str(config)])
    assert result.exit_code == 0, result.stdout
    assert "-15" in result.stdout and "+-" not in result.stdout


def test_week_start_is_monday_everywhere():
    for day in (date(2020, 12, 31), date(2021, 1, 3), date(2027, 1, 1), date(2028, 2, 29)):
        assert week_start(day).weekday() == 0 and 0 <= (day - week_start(day)).days < 7
