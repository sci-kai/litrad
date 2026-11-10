from datetime import date, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlmodel import Session, select

from litrad.config import Config
from litrad.db import (
    Achievement,
    Category,
    Paper,
    QueueItem,
    ReadStatus,
    Recommendation,
    RecommendationBatch,
    StatusEvent,
    XpEvent,
    init_db,
)
from litrad.game import (
    badge_progress,
    grade,
    ledger,
    level_for,
    next_goals,
    personal_bests,
    reading_calendar,
    reading_impact_factor,
    stats,
    streak_multiplier,
    streaks,
    total_xp,
    week_progress,
    weekly_summaries,
    weeks_of_history,
)
from litrad.reading import drop_from_queue, queue_add, queue_items, queue_move, record_reading
from litrad.rules import GameRules

MONDAY = datetime(2026, 10, 5, 9, 0, 0)


# the mechanics below are checked with the numbers of rules format 1 (a profile
# locked by litrad 1.0 or earlier); the defaults of new profiles: test_backlog.py
RULES = GameRules()


@pytest.fixture
def db(cfg):
    cfg.game = RULES
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        yield cfg, session
    engine.dispose()


def add_paper(session, title="Paper", **kwargs) -> Paper:
    paper = Paper(title=title, **kwargs)
    session.add(paper)
    session.commit()
    return paper


def kinds(award) -> dict[str, int]:
    return {e.kind: e.points for e in award.events}


# --- levels and streaks -------------------------------------------------------


def test_levels_of_the_default_ladder():
    assert level_for(0, RULES).number == 1 and level_for(0, RULES).title == "Curious Undergrad"
    assert level_for(19, RULES).number == 1 and level_for(20, RULES).number == 2
    assert level_for(45, RULES).progress(45) == pytest.approx(0.5)  # 20 -> 70
    assert level_for(0, RULES).next_title == "First-Year Student"
    assert level_for(0, RULES).icon == "🎓"
    top = level_for(10**6, RULES)
    assert top.number == len(RULES.levels) == 60
    assert top.title == RULES.levels[-1].title and top.next_floor is None
    assert top.progress(10**6) == 1


def test_levels_of_the_default_rules():
    levels = GameRules().levels
    second, third = levels[1].xp, levels[2].xp
    assert level_for(0).title == levels[0].title and level_for(0).next_title == levels[1].title
    assert level_for(second - 1).number == 1 and level_for(second).number == 2
    middle = (second + third) // 2
    assert level_for(middle).progress(middle) == pytest.approx((middle - second) / (third - second))
    top = level_for(10**6)
    assert top.number == len(levels) and top.title == levels[-1].title == "Hallmarks Author"
    assert top.next_floor is None


def test_streaks():
    w = [date(2026, 9, 7), date(2026, 9, 14), date(2026, 9, 21), date(2026, 10, 5)]
    # gap on 2026-09-28: the current streak restarts at 1, best stays 3
    assert streaks(w, date(2026, 10, 7)) == (1, 3)
    # last reading week was last week: streak still alive this week
    assert streaks(w[:3], date(2026, 9, 30)) == (3, 3)
    # two weeks without reading: streak broken
    assert streaks(w[:3], date(2026, 10, 7)) == (0, 3)
    assert streaks([], date(2026, 10, 7)) == (0, 0)


def test_streak_multiplier():
    assert streak_multiplier(0) == streak_multiplier(1) == 1.0
    assert streak_multiplier(3) == pytest.approx(1.10)
    assert streak_multiplier(50) == pytest.approx(1.5)


@pytest.mark.parametrize(
    "progress, over, expected",
    [
        ({"done": True, "thorough": 1, "fraction": 1, "total": 3}, False, "S"),
        ({"done": True, "thorough": 0, "fraction": 1, "total": 3}, False, "A"),
        ({"done": False, "thorough": 0, "fraction": 0.67, "total": 2}, False, "B"),
        ({"done": False, "thorough": 0, "fraction": 0.33, "total": 1}, False, "C"),
        ({"done": False, "thorough": 0, "fraction": 0, "total": 0}, True, "-"),
    ],
)
def test_grade(progress, over, expected):
    assert grade(progress, over)[0] == expected


# --- awarding -----------------------------------------------------------------


def test_base_points_by_depth_and_upgrades(db):
    cfg, session = db
    paper = add_paper(session)
    assert (
        kinds(record_reading(session, cfg, paper, ReadStatus.abstract, at=MONDAY).award)["read"]
        == 5
    )
    upgrade = record_reading(session, cfg, paper, ReadStatus.read, at=MONDAY).award
    assert kinds(upgrade)["read"] == 35  # 40 - 5 already paid


def test_no_farming_by_downgrading_and_upgrading(db):
    cfg, session = db
    paper = add_paper(session)
    record_reading(session, cfg, paper, ReadStatus.thorough, at=MONDAY)
    record_reading(session, cfg, paper, ReadStatus.abstract, at=MONDAY)
    again = record_reading(session, cfg, paper, ReadStatus.thorough, at=MONDAY).award
    assert "read" not in kinds(again) and "cheese" not in kinds(again)


def test_dismiss_earns_nothing(db):
    cfg, session = db
    paper = add_paper(session)
    award = record_reading(session, cfg, paper, ReadStatus.dismissed, at=MONDAY).award
    assert award.points == 0


def test_cheese_bonus_only_for_top_of_queue(db):
    cfg, session = db
    top, second = add_paper(session, "Top"), add_paper(session, "Second")
    queue_add(session, cfg, top)
    queue_add(session, cfg, second)
    assert "cheese" not in kinds(
        record_reading(session, cfg, second, ReadStatus.read, at=MONDAY).award
    )
    award = record_reading(session, cfg, top, ReadStatus.read, at=MONDAY).award
    assert kinds(award)["cheese"] == 8  # 20 % of the 40 base points


def test_cheese_needs_more_than_the_abstract(db):
    cfg, session = db
    top = add_paper(session, "Top")
    queue_add(session, cfg, top)
    award = record_reading(session, cfg, top, ReadStatus.abstract, at=MONDAY).award
    assert "cheese" not in kinds(award)


def test_once_per_paper_bonuses(db):
    cfg, session = db
    paper = add_paper(
        session,
        "Classic and new",
        citation_percentile=99.5,
        pub_date=MONDAY.date() - timedelta(days=3),
    )
    long_note = "x" * 150
    award = record_reading(
        session, cfg, paper, ReadStatus.read, rating=4, note=long_note, at=MONDAY
    ).award
    assert {"classic", "hot", "rating", "reflection"} <= set(kinds(award))
    again = record_reading(
        session, cfg, paper, ReadStatus.thorough, rating=5, note="more", at=MONDAY
    ).award
    assert set(kinds(again)) - {"badge"} == {"read"}  # only the depth upgrade pays


def test_classic_requires_a_real_read(db):
    cfg, session = db
    paper = add_paper(session, citation_percentile=99.9)
    award = record_reading(session, cfg, paper, ReadStatus.skimmed, at=MONDAY).award
    assert "classic" not in kinds(award)


def test_recommendation_bonus_at_any_real_reading_depth(db):
    cfg, session = db
    first, second = add_paper(session, "First"), add_paper(session, "Second")
    batch = RecommendationBatch(week_start=MONDAY.date())
    session.add(batch)
    session.flush()
    for rank, paper in enumerate((first, second), start=1):
        session.add(
            Recommendation(
                batch_id=batch.id, paper_id=paper.id, rank=rank, category=Category.open, score=1
            )
        )
    session.commit()
    # an abstract glance is not enough; any real read is, at the depth you choose
    assert "recommended" not in kinds(
        record_reading(session, cfg, first, ReadStatus.abstract, at=MONDAY).award
    )
    assert "recommended" in kinds(
        record_reading(session, cfg, first, ReadStatus.skimmed, at=MONDAY).award
    )
    assert "recommended" in kinds(
        record_reading(session, cfg, second, ReadStatus.thorough, at=MONDAY).award
    )
    # paid once per paper
    assert "recommended" not in kinds(
        record_reading(session, cfg, first, ReadStatus.thorough, at=MONDAY).award
    )


def test_streak_bonus_grows_week_by_week(db):
    cfg, session = db
    bonuses = []
    for week in range(3):
        paper = add_paper(session, f"Week {week}")
        at = MONDAY + timedelta(weeks=week)
        bonuses.append(
            kinds(record_reading(session, cfg, paper, ReadStatus.read, at=at).award).get(
                "streak", 0
            )
        )
    assert bonuses == [0, 2, 4]  # +5 % and +10 % of 40


def test_weekly_quest_once_per_week(db):
    cfg, session = db  # default goals: 1 deep read + 1 more paper skimmed
    papers = [add_paper(session, f"P{i}") for i in range(4)]
    awards = [
        record_reading(session, cfg, papers[0], ReadStatus.read, at=MONDAY).award,
        record_reading(session, cfg, papers[1], ReadStatus.skimmed, at=MONDAY).award,
        record_reading(session, cfg, papers[2], ReadStatus.skimmed, at=MONDAY).award,
        record_reading(session, cfg, papers[3], ReadStatus.skimmed, at=MONDAY).award,
    ]
    assert ["quest" in kinds(a) for a in awards] == [False, True, False, False]
    progress = week_progress(session, cfg, MONDAY.date())
    assert progress["done"] and progress["detail"] == 1 and progress["total"] == 4
    next_week = week_progress(session, cfg, MONDAY.date() + timedelta(weeks=1))
    assert next_week["fraction"] == 0 and not next_week["done"]


def test_quest_needs_a_deep_read(db):
    cfg, session = db
    for i in range(5):
        record_reading(session, cfg, add_paper(session, f"S{i}"), ReadStatus.skimmed, at=MONDAY)
    progress = week_progress(session, cfg, MONDAY.date())
    assert not progress["done"] and progress["fraction"] == pytest.approx(1 / 2)


def test_quest_goals_are_configurable(db):
    cfg, session = db
    cfg = Config.model_validate(
        {**cfg.model_dump(mode="json"), "game": {"weekly_detail_goal": 0, "weekly_skim_goal": 1}}
    )
    award = record_reading(session, cfg, add_paper(session), ReadStatus.skimmed, at=MONDAY).award
    assert "quest" in kinds(award)


def test_badges_unlock_once_and_pay(db):
    cfg, session = db
    first = record_reading(session, cfg, add_paper(session), ReadStatus.skimmed, at=MONDAY).award
    codes = [b.code for b in first.badges]
    assert "first_read" in codes and len(codes) == len(set(codes))
    paid = sum(e.points for e in first.events if e.kind == "badge")
    assert paid == GameRules().badge_xp * len(codes)
    second = record_reading(session, cfg, add_paper(session), ReadStatus.skimmed, at=MONDAY).award
    assert not {b.code for b in second.badges} & set(codes)  # each badge only once
    assert {a.code for a in session.exec(select(Achievement))} >= set(codes)


def test_inbox_zero_only_by_reading(db):
    cfg, session = db
    a, b = add_paper(session, "A"), add_paper(session, "B")
    queue_add(session, cfg, a)
    queue_add(session, cfg, b)
    record_reading(session, cfg, a, ReadStatus.dismissed, at=MONDAY)
    award = record_reading(session, cfg, b, ReadStatus.read, at=MONDAY).award
    assert "inbox_zero" in kinds(award)
    assert stats(session, MONDAY.date(), cfg.game)["inbox_zero"] == 1  # the badge needs 3
    # dismissing the last paper does not count (and costs the drop penalty)
    c = add_paper(session, "C")
    queue_add(session, cfg, c)
    award = record_reading(session, cfg, c, ReadStatus.dismissed, at=MONDAY).award
    assert kinds(award) == {"penalty": -cfg.game.drop_penalty}


def test_level_up_is_reported(db):
    cfg, session = db
    session.add(XpEvent(points=10, kind="test", reason="setup", at=MONDAY))  # level 2 at 15
    session.commit()
    award = record_reading(session, cfg, add_paper(session), ReadStatus.skimmed, at=MONDAY).award
    assert award.level_before.number == 1 and award.level_after.number >= 2


def test_stats_and_impact_factor(db):
    cfg, session = db
    topical = add_paper(session, "T", topics=["Cancer Genomics"], folders=["Lib/Folder"])
    folder_only = add_paper(session, "F", folders=["Lib/Other"])
    queue_add(session, cfg, topical)
    record_reading(session, cfg, topical, ReadStatus.thorough, rating=5, at=MONDAY)
    record_reading(session, cfg, folder_only, ReadStatus.skimmed, at=MONDAY)
    s = stats(session, MONDAY.date())
    assert s["papers_read"] == 2 and s["thorough"] == 1 and s["deep_reads"] == 1
    assert s["cheeses"] == 1 and s["ratings"] == 1 and s["topics"] == 2
    assert s["streak"] == 1 and s["xp"] == total_xp(session)
    assert reading_impact_factor(session, MONDAY.date()) == pytest.approx(total_xp(session) / 4)
    # four weeks later the points have aged out of the impact factor
    assert reading_impact_factor(session, MONDAY.date() + timedelta(weeks=4)) == 0


def test_precomputed_stats_give_the_same_results(db):
    """Pages compute stats() once and pass it on; the results must not change."""
    cfg, session = db
    for n in range(3):
        paper = add_paper(session, f"P{n}")
        record_reading(session, cfg, paper, ReadStatus.read, at=MONDAY + timedelta(days=n))
    today = MONDAY.date() + timedelta(days=3)
    current = stats(session, today, cfg)
    assert badge_progress(session, today, cfg, current) == badge_progress(session, today, cfg)
    assert personal_bests(session, cfg, today, current) == personal_bests(session, cfg, today)
    assert next_goals(session, cfg, today, current) == next_goals(session, cfg, today)


def test_weekly_summaries_page_back_to_the_first_week(db):
    cfg, session = db
    today = MONDAY.date() + timedelta(weeks=5)
    assert weeks_of_history(session, today) == 1
    record_reading(session, cfg, add_paper(session), ReadStatus.read, at=MONDAY)
    assert weeks_of_history(session, today) == 6
    page = weekly_summaries(session, cfg, today, weeks=4, offset=4)
    assert [w["week_start"] for w in page] == [
        MONDAY.date() + timedelta(weeks=1) - timedelta(weeks=i) for i in range(4)
    ]


def test_ledger_filters_and_pages(db):
    cfg, session = db
    for n in range(3):
        record_reading(session, cfg, add_paper(session, f"L{n}"), ReadStatus.read, at=MONDAY)
    rows, total = ledger(session)
    assert total == len(rows) and rows[0]["balance"] == total_xp(session)
    reads, n_reads = ledger(session, "read", limit=2)
    assert n_reads == 3 and len(reads) == 2
    assert all(r["event"].kind == "read" and r["paper"] is not None for r in reads)
    # balances are those of the whole ledger, not of the filtered rows
    assert {r["balance"] for r in reads} <= {r["balance"] for r in rows}


def test_queue_is_untouched_by_abstract_reads(db):
    cfg, session = db
    paper = add_paper(session)
    queue_add(session, cfg, paper)
    outcome = record_reading(session, cfg, paper, ReadStatus.abstract, at=MONDAY)
    assert not outcome.left_queue
    assert session.exec(select(QueueItem)).one().paper_id == paper.id


# --- summaries -------------------------------------------------------------------


def test_weekly_summary(db):
    cfg, session = db
    a, b = add_paper(session, "A"), add_paper(session, "B")
    record_reading(session, cfg, a, ReadStatus.abstract, at=MONDAY)
    record_reading(session, cfg, a, ReadStatus.thorough, at=MONDAY + timedelta(days=1))
    record_reading(session, cfg, b, ReadStatus.skimmed, at=MONDAY + timedelta(days=2))
    summaries = weekly_summaries(session, cfg, MONDAY.date() + timedelta(days=3), weeks=2)
    this, last = summaries
    assert this["current"] and not last["current"]
    assert [e["paper"].title for e in this["papers"]] == ["A", "B"]
    assert this["papers"][0]["status"] == ReadStatus.thorough
    assert this["xp"] == total_xp(session)
    assert this["xp_by_kind"]["read"] == 95  # 5 + 75 + 15
    assert "first_read" in [b.code for b in this["badges"]]
    assert this["grade"] == "S" and last["grade"] == "-" and last["xp"] == 0


def test_ledger_running_balance_and_filter(db):
    cfg, session = db
    paper = add_paper(session, "A")
    record_reading(session, cfg, paper, ReadStatus.read, at=MONDAY)
    session.add(XpEvent(points=-15, kind="penalty", reason="test", at=MONDAY + timedelta(hours=1)))
    session.commit()
    rows, total = ledger(session)
    assert total == len(rows) >= 3
    assert rows[0]["event"].kind == "penalty"  # newest first
    assert rows[0]["balance"] == total_xp(session)
    assert rows[1]["balance"] == total_xp(session) + 15
    assert rows[-1]["paper"].title == "A"
    penalties, n = ledger(session, kind="penalty")
    assert n == 1 and penalties[0]["event"].points == -15
    page, _ = ledger(session, limit=1, offset=1)
    assert page[0]["event"] is rows[1]["event"]


def test_reading_calendar(db):
    cfg, session = db
    record_reading(session, cfg, add_paper(session), ReadStatus.read, at=MONDAY)
    calendar = reading_calendar(session, MONDAY.date() + timedelta(days=2), weeks=4)
    assert len(calendar) == 4 and all(len(column) == 7 for column in calendar)
    monday = calendar[-1][0]
    assert monday["date"] == MONDAY.date() and monday["count"] == 1 and monday["xp"] > 0
    assert calendar[-1][6]["future"] and not calendar[0][0]["future"]


def test_personal_bests_and_hints(db):
    cfg, session = db
    hints = next_goals(session, cfg, MONDAY.date())
    assert any("Weekly quest: 1 deep read and 1 more skim" in h for h in hints)
    assert any("XP until Lv 2 · First-Year Student" in h for h in hints)
    for i in range(3):
        record_reading(session, cfg, add_paper(session, f"P{i}"), ReadStatus.read, at=MONDAY)
    bests = personal_bests(session, cfg, MONDAY.date())
    assert bests["best_week"] == MONDAY.date() and bests["busiest_week_papers"] == 3
    assert bests["best_week_xp"] == total_xp(session) and bests["quests"] == 1
    # a week later the streak needs attention
    hints = next_goals(session, cfg, MONDAY.date() + timedelta(weeks=1))
    assert any("keep your 1-week streak alive" in h for h in hints)


@pytest.mark.parametrize("value", [float("inf"), float("nan")])
def test_rules_reject_inf_and_nan(value):
    with pytest.raises(ValidationError):
        GameRules.model_validate({"cheese_bonus": value})


def test_bonuses_set_to_zero_xp_still_count_for_badges(cfg):
    cfg.game = GameRules(
        classic_xp=0,
        hot_xp=0,
        reflection_xp=0,
        inbox_zero_xp=0,
        quest_xp=0,
        badge_targets={"inbox_zero": 1, "questing": 1},
    )
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        paper = add_paper(
            session,
            "Classic and new",
            citation_percentile=99.5,
            pub_date=MONDAY.date() - timedelta(days=3),
        )
        queue_add(session, cfg, paper)
        record_reading(session, cfg, paper, ReadStatus.read, note="x" * 150, at=MONDAY)
        record_reading(session, cfg, add_paper(session, "Second"), ReadStatus.skimmed, at=MONDAY)
        current = stats(session, MONDAY.date(), cfg.game)
        for stat in ("classics", "hot", "reflections", "inbox_zero", "quests"):
            assert current[stat] == 1, stat
        zero = session.exec(select(XpEvent).where(XpEvent.points == 0)).all()
        assert {e.kind for e in zero} == {"classic", "hot", "reflection", "inbox_zero", "quest"}
        codes = set(session.exec(select(Achievement.code)).all())
        assert {"inbox_zero", "questing"} <= codes
    engine.dispose()


def test_points_are_rounded_half_up():
    from litrad.game import round_half_up

    assert [round_half_up(x) for x in (0.5, 1.5, 2.5, 7.5, 2.4999, 0.15 * 50)] == [1, 2, 3, 8, 2, 8]


def test_cheese_share_rounds_half_up(cfg):
    cfg.game = GameRules(xp_abstract=5, cheese_bonus=0.5, xp_skimmed=5)
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        top = add_paper(session, "Top")
        queue_add(session, cfg, top)
        award = record_reading(session, cfg, top, ReadStatus.skimmed, at=MONDAY).award
        assert kinds(award)["cheese"] == 3  # 5 x 0.5 = 2.5 -> 3 (round() would give 2)
    engine.dispose()


# --- the cheese: only for the queue's own order -----------------------------------


def cheese_of(session, cfg, paper, status=ReadStatus.read) -> int | None:
    return kinds(record_reading(session, cfg, paper, status, at=MONDAY).award).get("cheese")


def flags(session) -> dict[str, bool]:
    return {
        session.get(Paper, item.paper_id).title: item.reordered
        for item in session.exec(select(QueueItem)).all()
    }


def queued(session, cfg, *titles) -> list[Paper]:
    papers = [add_paper(session, title) for title in titles]
    for paper in papers:
        queue_add(session, cfg, paper)
    return papers


def test_cheese_not_for_a_paper_moved_to_the_top(db):
    cfg, session = db
    a, b = queued(session, cfg, "A", "B")
    queue_move(session, b, 1)
    assert flags(session) == {"A": False, "B": True}  # only the moved paper
    assert cheese_of(session, cfg, b) is None
    assert cheese_of(session, cfg, a) == 8  # risen to q1 by reading B: 20 % of 40


def test_cheese_not_for_a_paper_moved_up_or_down(db):
    cfg, session = db
    a, b, c = queued(session, cfg, "A", "B", "C")
    queue_move(session, c, 2)  # up, not to the top
    queue_move(session, a, 3)  # down
    assert [p.title for p in (i[1] for i in queue_items(session))] == ["C", "B", "A"]
    assert flags(session) == {"A": True, "B": False, "C": True}
    assert cheese_of(session, cfg, c) is None
    assert cheese_of(session, cfg, b) == 8
    assert cheese_of(session, cfg, a) is None


def test_a_move_to_the_same_place_keeps_the_cheese(db):
    cfg, session = db
    a, _ = queued(session, cfg, "A", "B")
    queue_move(session, a, 1)
    assert not flags(session)["A"]
    assert cheese_of(session, cfg, a) == 8


def test_cheese_not_for_a_paper_added_as_read_next(db):
    cfg, session = db
    first = add_paper(session, "First")
    queue_add(session, cfg, first, position=1)  # empty queue: q1 by the queue's order
    later = add_paper(session, "Later")
    queue_add(session, cfg, later, position=2)  # the end: also the queue's order
    top = add_paper(session, "Top")
    queue_add(session, cfg, top, position=1)  # ahead of the others: your choice
    middle = add_paper(session, "Middle")
    queue_add(session, cfg, middle, position=3)
    assert flags(session) == {"First": False, "Later": False, "Top": True, "Middle": True}
    assert cheese_of(session, cfg, top) is None
    assert cheese_of(session, cfg, first) == 8


def test_cheese_kept_when_rising_because_others_leave(db):
    cfg, session = db
    a, b, c, d = queued(session, cfg, "A", "B", "C", "D")
    drop_from_queue(session, cfg, a, at=MONDAY)  # removed
    assert cheese_of(session, cfg, b, ReadStatus.dismissed) is None  # dismissed: no reading
    assert cheese_of(session, cfg, c) == 8  # rose to q1: still the queue's order
    assert cheese_of(session, cfg, d, ReadStatus.skimmed) == 3  # 20 % of 15


def test_reordered_flag_survives_a_restart(cfg):
    cfg.game = RULES
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        _, b = queued(session, cfg, "A", "B")
        queue_move(session, b, 1)
    engine.dispose()
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        assert flags(session) == {"A": False, "B": True}
        b = session.exec(select(Paper).where(Paper.title == "B")).one()
        assert cheese_of(session, cfg, b) is None
    engine.dispose()


def test_reordered_top_reads_are_no_cheese_for_badges_and_stats(db):
    cfg, session = db
    moved = []
    for i in range(3):
        a, b = queued(session, cfg, f"A{i}", f"B{i}")
        queue_move(session, b, 1)
        record_reading(session, cfg, b, ReadStatus.read, at=MONDAY)
        drop_from_queue(session, cfg, a, at=MONDAY)
        moved.append(b)
    ids = [p.id for p in moved]
    events = session.exec(select(StatusEvent).where(StatusEvent.paper_id.in_(ids)))
    assert not any(e.was_queue_top for e in events)
    assert stats(session, MONDAY.date(), RULES)["cheeses"] == 0
    assert session.get(Achievement, "first_bite") is None
    for paper in queued(session, cfg, "C0", "C1", "C2"):
        record_reading(session, cfg, paper, ReadStatus.read, at=MONDAY)
    assert stats(session, MONDAY.date(), RULES)["cheeses"] == 3
    assert "first_bite" in set(session.exec(select(Achievement.code)).all())
