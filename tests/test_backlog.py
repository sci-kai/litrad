"""Papers read before litrad ("read earlier") and the rules formats."""

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from sqlmodel import Session, select
from typer.testing import CliRunner

import litrad.cli as cli
from conftest import archive_data, make_profile
from litrad.backup import merge
from litrad.db import (
    Achievement,
    Paper,
    ReadStatus,
    Recommendation,
    RecommendationBatch,
    StatusEvent,
    XpEvent,
    init_db,
)
from litrad.game import (
    level_for,
    personal_bests,
    reading_calendar,
    reading_impact_factor,
    reading_weeks,
    stats,
    total_xp,
    week_progress,
    week_start,
    weekly_summary,
)
from litrad.profile import start_profile
from litrad.reading import ReadingError, mark_read_earlier, queue_add, record_reading
from litrad.rules import (
    BADGE_CODES,
    BASELINE_BADGES,
    FINGERPRINT_FIELDS,
    RULES_FORMAT,
    GameRules,
)

REPO = Path(__file__).resolve().parent.parent
# a Saturday night: every time-based badge would notice it
NIGHT = datetime(2027, 3, 6, 23, 30)


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


def kinds(session) -> dict[str, int]:
    out: dict[str, int] = {}
    for event in session.exec(select(XpEvent)).all():
        out[event.kind] = out.get(event.kind, 0) + event.points
    return out


def test_read_earlier_earns_the_share_of_the_base_points_and_no_bonus(db):
    cfg, session = db
    rules = GameRules()
    start_profile(session, cfg, at=NIGHT - timedelta(days=1))
    paper = add(session, "Old favourite", pub_date=NIGHT.date(), citation_percentile=99.9)
    batch = RecommendationBatch(week_start=week_start(NIGHT.date()))
    session.add(batch)
    session.commit()
    session.add(
        Recommendation(batch_id=batch.id, paper_id=paper.id, rank=1, category="open", score=1)
    )
    session.commit()
    queue_add(session, cfg, paper)  # the top (and only) paper of the queue
    outcome = record_reading(session, cfg, paper, ReadStatus.thorough, at=NIGHT, earlier=True)
    assert outcome.left_queue
    got = kinds(session)
    assert got["earlier"] == round(rules.xp_thorough * rules.backlog_share)
    # no reading bonus for when or how: cheese, streak, fresh, recommendation, inbox zero
    for kind in ("read", "cheese", "streak", "hot", "recommended", "inbox_zero", "quest"):
        assert kind not in got, kind
    assert got["classic"] == rules.classic_xp  # landmark: what you read, not when
    event = session.exec(select(StatusEvent)).one()
    assert event.earlier and event.read_on is None and not event.was_queue_top


def test_read_earlier_counts_for_papers_depth_and_levels_but_not_for_time(db):
    cfg, session = db
    start_profile(session, cfg, at=NIGHT - timedelta(days=1))
    papers = [add(session, f"Paper {n}", topics=[f"Topic {n % 6}"]) for n in range(30)]
    outcome = mark_read_earlier(session, cfg, papers[:20], ReadStatus.skimmed, at=NIGHT)
    mark_read_earlier(session, cfg, papers[20:], ReadStatus.thorough, date(2019, 5, 1), NIGHT)
    assert len(outcome.logged) == 20
    current = stats(session, NIGHT.date())
    assert current["papers_read"] == 30 and current["deep_reads"] == 10
    assert current["thorough"] == 10 and current["topics"] == 6
    for stat in ("max_day", "max_week", "night_reads", "weekend_reads", "best_streak"):
        assert current[stat] == 0, stat
    assert current["quests"] == current["a_weeks"] == current["cheeses"] == 0
    assert reading_weeks(session) == []
    assert week_progress(session, GameRules(), NIGHT.date())["total"] == 0
    owned = set(session.exec(select(Achievement.code)).all())
    assert {"first_read", "page_turner", "bookworm", "deep_diver", "free_diver"} <= owned
    for code in ("marathon", "binge_week", "night_owl", "weekend_warrior", "streak_2"):
        assert code not in owned, code
    for code in ("questing", "top_marks", "first_bite", "comeback", "early_bird", "scoop"):
        assert code not in owned, code
    assert level_for(total_xp(session)).number > 1
    # nothing on the calendar, in the weekly report or in the pace
    today = NIGHT.date()
    assert all(day["count"] == 0 for week in reading_calendar(session, today) for day in week)
    assert weekly_summary(session, GameRules(), week_start(today), today)["papers"] == []
    assert reading_impact_factor(session, today) == pytest.approx(
        sum(e.points for e in session.exec(select(XpEvent)) if e.kind != "earlier") / 4
    )
    assert personal_bests(session, GameRules(), today)["busiest_week_papers"] == 0


def test_bulk_skips_papers_read_at_least_as_deeply(db):
    cfg, session = db
    start_profile(session, cfg, at=NIGHT)
    a, b = add(session, "A"), add(session, "B")
    record_reading(session, cfg, a, ReadStatus.read, at=NIGHT)
    outcome = mark_read_earlier(session, cfg, [a, b], ReadStatus.skimmed, at=NIGHT)
    assert [p.id for p in outcome.logged] == [b.id]
    assert [p.id for p in outcome.skipped] == [a.id]
    with pytest.raises(ReadingError, match="needs a depth"):
        mark_read_earlier(session, cfg, [b], ReadStatus.dismissed, at=NIGHT)


def test_deeper_reading_now_pays_the_full_difference_and_counts_for_the_week(db):
    cfg, session = db
    rules = GameRules()
    start_profile(session, cfg, at=NIGHT)
    paper = add(session, "Revisited")
    record_reading(session, cfg, paper, ReadStatus.skimmed, at=NIGHT, earlier=True)
    # the same level again (also when recommended again) earns nothing
    record_reading(session, cfg, paper, ReadStatus.skimmed, at=NIGHT + timedelta(days=2))
    assert "read" not in kinds(session)
    record_reading(session, cfg, paper, ReadStatus.read, at=NIGHT + timedelta(days=3))
    assert kinds(session)["read"] == rules.xp_read - rules.xp_skimmed
    assert reading_weeks(session) == [week_start((NIGHT + timedelta(days=3)).date())]


def test_read_on_must_be_in_the_past_and_implies_earlier(db):
    cfg, session = db
    paper = add(session)
    with pytest.raises(ReadingError, match="in the past"):
        record_reading(
            session, cfg, paper, ReadStatus.read, at=NIGHT, read_on=NIGHT.date() + timedelta(1)
        )
    session.rollback()
    record_reading(session, cfg, paper, ReadStatus.read, at=NIGHT, read_on=date(2020, 1, 2))
    event = session.exec(select(StatusEvent)).one()
    assert event.earlier and event.read_on == date(2020, 1, 2)


def _config(tmp_path) -> Path:
    config = tmp_path / "config.yaml"
    paths = {"cache_dir": str(tmp_path / "cache"), "profiles_dir": str(tmp_path / "profiles")}
    config.write_text(yaml.safe_dump({"paths": paths}), "utf-8")
    return config


def test_cli_read_earlier_with_date_and_history_label(tmp_path, cfg):
    config = _config(tmp_path)
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        paper_id = add(session, "Backlog classic", doi="10.1/old").id
    engine.dispose()
    make_profile(config, database=cfg.paths.database)
    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "read",
            f"#{paper_id}",
            "thorough",
            "--earlier",
            "--date",
            "2021-04-05",
            "-c",
            str(config),
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "(read earlier)" in result.stdout
    result = runner.invoke(cli.app, ["history", "-c", str(config)])
    assert "(read earlier 2021-04-05)" in result.stdout
    bad = runner.invoke(
        cli.app, ["read", f"#{paper_id}", "read", "--date", "05/04/2021", "-c", str(config)]
    )
    assert bad.exit_code != 0


def test_web_dialog_and_bulk_action(tmp_path, cfg):
    from fastapi.testclient import TestClient

    import litrad.web.app as web
    from conftest import enter_profile, profile_cfg
    from litrad import libraries

    config = _config(tmp_path)
    client = TestClient(web.create_app(config))
    enter_profile(client)
    engine = init_db(profile_cfg(config).paths.database)
    with Session(engine) as session:
        papers = [add(session, f"Library paper {n}") for n in range(4)]
        library = libraries.create(session, "Mine")
        for n, paper in enumerate(papers):
            libraries.link(session, library, paper, f"Key{n}")
        session.commit()
        libraries.refresh_flags(session)
        session.commit()
        ids, library_id = [p.id for p in papers], library.id
    engine.dispose()
    page = client.get(f"/papers/{ids[0]}")
    assert 'name="earlier"' in page.text and 'name="read_on"' in page.text
    response = client.post(
        f"/papers/{ids[0]}/read",
        data={"level": "read", "earlier": "1", "read_on": "2022-02-02", "next_url": "/read"},
        follow_redirects=True,
    )
    assert response.status_code == 200 and "read earlier" in response.text
    page = client.get(f"/library/{library_id}")
    assert "Mark as read earlier" in page.text and 'form="read-earlier"' in page.text
    response = client.post(
        f"/library/{library_id}/read-earlier",
        data={"paper": [str(i) for i in ids], "level": "skimmed", "read_on": ""},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "3 marked as read earlier" in response.text and "1 already read" in response.text
    page = client.get("/read")
    assert page.text.count('title="read earlier, before litrad"') == 4
    client.close()
    engine = init_db(profile_cfg(config).paths.database)
    with Session(engine) as session:
        events = session.exec(select(StatusEvent)).all()
        assert len(events) == 4 and all(e.earlier for e in events)
        assert {e.read_on for e in events} == {date(2022, 2, 2), None}
    engine.dispose()


def test_merge_round_trip_keeps_the_flag(tmp_path, cfg):
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        start_profile(session, cfg, at=NIGHT)
        a, b = add(session, "Earlier", doi="10.1/a"), add(session, "Now", doi="10.1/b")
        record_reading(session, cfg, a, ReadStatus.read, at=NIGHT, read_on=date(2018, 3, 4))
        record_reading(session, cfg, b, ReadStatus.skimmed, at=NIGHT + timedelta(hours=1))
        data = archive_data(session, cfg)
    engine.dispose()
    events = data["status_events"]
    assert events[0]["earlier"] is True and events[0]["read_on"] == "2018-03-04"
    assert "earlier" not in events[1]  # only set when true: older readers see nothing new
    other = cfg.model_copy(deep=True)
    other.paths.database = tmp_path / "other.sqlite"
    engine = init_db(other.paths.database)
    with Session(engine) as session:
        report = merge(session, other, data)
        assert report.events_added == 2
        rows = {e.read_on: e.earlier for e in session.exec(select(StatusEvent)).all()}
        assert rows == {date(2018, 3, 4): True, None: False}
    engine.dispose()


def test_archive_round_trip_keeps_the_flag(tmp_path):
    from litrad import profiles
    from litrad.archive import import_new
    from litrad.config import load_config
    from test_archive import export

    path = _config(tmp_path)
    base = load_config(path)
    slug = profiles.create_profile(base, "Source")
    cfg = profiles.profile_config(base, slug)
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        paper = add(session, "Backlog paper", doi="10.1/x")
        record_reading(session, cfg, paper, ReadStatus.thorough, at=NIGHT, read_on=date(2017, 1, 2))
    engine.dispose()
    archive = export(base, slug, tmp_path / "export.zip")
    result = import_new(base, archive, "Copy")
    assert result.report.ok, result.report
    engine = init_db(profiles.profile_config(base, result.slug).paths.database)
    with Session(engine) as session:
        event = session.exec(select(StatusEvent)).one()
        assert event.earlier and event.read_on == date(2017, 1, 2)
        assert stats(session, NIGHT.date())["thorough"] == 1
    engine.dispose()


# --- rules format ---------------------------------------------------------------------


def test_rules_have_one_format_and_every_field_in_the_fingerprint():
    rules = GameRules()
    assert rules.rules_format == RULES_FORMAT == 1
    assert FINGERPRINT_FIELDS[GameRules] == set(GameRules.model_fields)
    assert {b.code for b in rules.badges()} == BASELINE_BADGES == BADGE_CODES
    # a config with only a few rules gets the defaults for the rest
    assert GameRules.model_validate({"quest_xp": 60}).backlog_share == rules.backlog_share


# --- the simulator ----------------------------------------------------------------------


def test_simulator_runs_briefly_offline(tmp_path, monkeypatch):
    import socket

    def no_network(*args, **kwargs):
        raise AssertionError("the simulator must not use the network")

    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.syspath_prepend(str(REPO / "tools"))
    sys.modules.pop("simulate_game", None)
    import simulate_game

    assert simulate_game.HAS_EARLIER
    out = tmp_path / "balance"
    code = simulate_game.main(
        ["--persona", "experienced", "--persona", "regular", "--weeks", "2", "--out", str(out)]
    )
    assert code == 0
    summary = (out / "summary.md").read_text()
    assert "| experienced |" in summary and "| regular |" in summary
    text = (out / "experienced.md").read_text()
    assert "After the day-1 backlog (185 papers)" in text and "Badges unlocked" in text
    raw = json.loads((out / "raw.json").read_text())
    assert len(raw["regular"]["xp"]) == 2 and raw["experienced"]["backlog_xp"] > 0
    # the backlog earns no time-based badge
    for code in ("marathon", "binge_week", "night_owl", "early_riser", "weekend_warrior"):
        assert code not in raw["experienced"]["badges"]
    assert "XP shares (reading / badges / quests / other)" in summary
    assert "The first 10 papers of a new profile" in summary


def simulator(monkeypatch):
    monkeypatch.syspath_prepend(str(REPO / "tools"))
    sys.modules.pop("simulate_game", None)
    import simulate_game

    return simulate_game


def test_the_first_papers_are_motivating_but_not_too_fast(monkeypatch):
    """Default rules: at most one level per skim, two per deep read; badges step by step."""
    sim = simulator(monkeypatch)
    for name, sequence in sim.EARLY_SCENARIOS.items():
        rows = sim.early_game(sequence)
        total = 0
        for row in rows:
            total += len(row["badges"])
            jump = row["level"] - row["level_before"]
            deep = row["status"] in ("read", "thorough")
            assert jump <= (2 if deep else 1), (name, row)
            assert len(row["badges"]) <= 2, (name, row)
            if row["paper"] in (3, 4, 5):
                assert row["level"] <= 5 and 1 <= total <= 2, (name, row, total)
        assert rows[0]["badges"] == ["First Steps"], name


def test_xp_shares_count_reading_bonuses_as_reading(monkeypatch, cfg):
    sim = simulator(monkeypatch)
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        for kind, points in (
            ("read", 40),
            ("cheese", 20),
            ("earlier", 20),
            ("badge", 8),
            ("quest", 20),
            ("rating", 2),
            ("penalty", -15),
        ):
            session.add(XpEvent(kind=kind, points=points, reason="x", at=datetime(2027, 1, 5)))
        session.commit()
        shares = sim.xp_shares(session, datetime(2028, 1, 1))
    engine.dispose()
    assert shares == pytest.approx(
        {"reading": 80 / 110, "badges": 8 / 110, "quests": 20 / 110, "other": 2 / 110}
    )
