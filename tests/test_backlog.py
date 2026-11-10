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
from litrad.rules import BADGE_CODES_V1, FINGERPRINT_FIELDS, GameRules

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
    # no reading bonus for when or how: frog, streak, fresh, recommendation, inbox zero
    for kind in ("read", "frog", "streak", "hot", "recommended", "inbox_zero", "quest"):
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
    assert current["quests"] == current["a_weeks"] == current["frogs"] == 0
    assert reading_weeks(session) == []
    assert week_progress(session, GameRules(), NIGHT.date())["total"] == 0
    owned = set(session.exec(select(Achievement.code)).all())
    assert {"first_read", "page_turner", "bookworm", "deep_diver", "free_diver"} <= owned
    for code in ("marathon", "binge_week", "night_owl", "weekend_warrior", "streak_2"):
        assert code not in owned, code
    for code in ("questing", "top_marks", "tadpole", "comeback", "early_bird", "scoop"):
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


# --- rules formats -----------------------------------------------------------------------


def test_new_profiles_get_format_2_and_stored_rules_keep_format_1():
    rules = GameRules()
    assert rules.rules_format == 2 and len(rules.badges()) > len(BADGE_CODES_V1)
    stored = json.loads((REPO / "tests/data/compat/dev-20261109/profile.json").read_text())
    stored_rules = stored["profile"]["rules"] if "profile" in stored else stored["rules"]
    old = GameRules.model_validate(stored_rules)
    assert old.rules_format == 1
    assert {b.code for b in old.badges()} == BADGE_CODES_V1
    assert old.levels == GameRules.model_validate(stored_rules).levels
    # a config with only a few rules is the new format
    assert GameRules.model_validate({"quest_xp": 60}).rules_format == 2
    # the full set of format-1 rules without a format: format 1
    full = {name: getattr(rules, name) for name in FINGERPRINT_FIELDS[GameRules]}
    assert GameRules.model_validate(full).rules_format == 1
    assert GameRules.model_validate(rules.model_dump()).rules_format == 2
    assert GameRules.model_validate(full).fingerprint() != rules.fingerprint()


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
