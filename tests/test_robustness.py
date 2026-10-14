"""Failure scenarios: nothing may crash half-way or lose progress.

Covers crashes in the middle of an action, concurrent writers, restarts,
broken inputs and data, unusual content, calendar edge cases, scale, and a
long randomised reading session whose invariants are checked after every
step.
"""

import random
import threading
import time
from datetime import date, datetime, timedelta

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session, func, select
from typer.testing import CliRunner

import litresearch.cli as cli
import litresearch.reading as reading_module
import litresearch.web.app as web
from conftest import FakeApis, enter_profile, make_work, profile_cfg
from litresearch.backup import export_progress, import_progress
from litresearch.config import Config, ConfigError, load_config
from litresearch.db import (
    Paper,
    QueueItem,
    Reading,
    ReadStatus,
    RecommendationBatch,
    StatusEvent,
    XpEvent,
    depth,
    init_db,
)
from litresearch.game import (
    ledger,
    level_for,
    personal_bests,
    reading_calendar,
    stats,
    streaks,
    total_xp,
    week_progress,
    weekly_summaries,
)
from litresearch.ingest.pipeline import ingest_library
from litresearch.reading import (
    ReadingError,
    add_note,
    drop_from_queue,
    queue_add,
    queue_items,
    queue_move,
    record_reading,
)
from litresearch.recommend.engine import build_recommendations
from litresearch.recommend.scoring import Candidate, LibraryProfile, rank_candidates
from litresearch.rules import GameRules
from litresearch.sources.http import HttpClient, HttpError
from litresearch.sources.openalex import OpenAlex

AT = datetime(2026, 10, 5, 9, 0, 0)
runner = CliRunner()


@pytest.fixture
def db(cfg):
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        papers = [Paper(title=f"Paper {i}", doi=f"10.1/{i}") for i in range(12)]
        session.add_all(papers)
        session.commit()
        yield cfg, engine, session, papers
    engine.dispose()


def snapshot_counts(engine) -> dict:
    with Session(engine) as session:
        return {
            model.__name__: session.exec(select(func.count()).select_from(model)).one()
            for model in (Paper, Reading, StatusEvent, QueueItem, XpEvent, RecommendationBatch)
        }


# --- crashes in the middle of an action -------------------------------------------------


def test_crash_while_awarding_points_saves_nothing(db, monkeypatch):
    cfg, engine, session, papers = db
    queue_add(session, cfg, papers[0])
    before = snapshot_counts(engine)

    def boom(*_args, **_kwargs):
        raise RuntimeError("power cut")

    monkeypatch.setattr(reading_module, "award_reading", boom)
    with pytest.raises(RuntimeError):
        record_reading(session, cfg, papers[0], ReadStatus.read, note="lost?", at=AT)
    session.rollback()
    # the half-done action left no trace, the paper is still queued
    assert snapshot_counts(engine) == before
    monkeypatch.undo()
    outcome = record_reading(session, cfg, papers[0], ReadStatus.read, note="retry", at=AT)
    assert outcome.left_queue and outcome.award.points > 0


def test_crash_while_dropping_keeps_the_queue(db, monkeypatch):
    cfg, engine, session, papers = db
    queue_add(session, cfg, papers[0], source="recommendation")
    monkeypatch.setattr(
        reading_module, "unread_drop_penalty", lambda *a, **k: (_ for _ in ()).throw(OSError())
    )
    with pytest.raises(OSError):
        drop_from_queue(session, cfg, papers[0], at=AT)
    session.rollback()
    assert [p.id for _, p in queue_items(session)] == [papers[0].id]


def test_crash_while_building_recommendations_keeps_old_batch(cfg, make_http, monkeypatch):
    cfg = Config.model_validate(
        {**cfg.model_dump(mode="json"), "topics": [{"name": "t", "queries": ["q"]}]}
    )
    apis = FakeApis()
    apis.search_results["fresh"] = [make_work("10.9/a", "A", date.today().isoformat())]
    engine = init_db(cfg.paths.database)
    http = make_http(apis)
    with Session(engine) as session:
        ingest_library(cfg, session, http, use_s2=False)
        build_recommendations(cfg, session, http, use_s2=False)
    before = snapshot_counts(engine)

    def boom(*_args, **_kwargs):
        raise RuntimeError("crash during scoring")

    monkeypatch.setattr("litresearch.recommend.engine.rank_candidates", boom)
    with Session(engine) as session, pytest.raises(RuntimeError):
        build_recommendations(cfg, session, http, use_s2=False)
    assert snapshot_counts(engine) == before
    engine.dispose()


def test_crash_during_ingest_keeps_previous_library(cfg, make_http, monkeypatch):
    engine = init_db(cfg.paths.database)
    http = make_http(FakeApis())
    with Session(engine) as session:
        ingest_library(cfg, session, http, use_s2=False)
    before = snapshot_counts(engine)
    monkeypatch.setattr(
        "litresearch.ingest.pipeline.enrich_openalex",
        lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    with Session(engine) as session, pytest.raises(KeyboardInterrupt):
        ingest_library(cfg, session, http, use_s2=False)
    assert snapshot_counts(engine) == before
    with Session(engine) as session:
        assert all(p.in_library for p in session.exec(select(Paper)).all())
    engine.dispose()


def test_crash_inside_a_web_request(cfg, tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text(
        f"paths:\n  bibtex: {cfg.paths.bibtex}\n  database: {cfg.paths.database}\n"
        f"  cache_dir: {cfg.paths.cache_dir}\n"
    )
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        session.add(Paper(title="P", doi="10.1/p", in_library=True))
        session.commit()
    engine.dispose()
    client = TestClient(web.create_app(path), raise_server_exceptions=False)
    enter_profile(client)  # takes over the existing data
    engine = init_db(profile_cfg(path).paths.database)
    before = snapshot_counts(engine)

    def boom(*_args, **_kwargs):
        raise RuntimeError("bug")

    original = web.record_reading
    monkeypatch.setattr(web, "record_reading", boom)
    assert client.post("/papers/1/read", data={"level": "read"}).status_code == 500
    assert snapshot_counts(engine) == before
    monkeypatch.setattr(web, "record_reading", original)
    # the server keeps working after the error
    assert client.post("/papers/1/read", data={"level": "read"}).status_code == 200
    assert snapshot_counts(engine)["Reading"] == 1
    client.close()
    engine.dispose()


# --- concurrency, durability and restarts -------------------------------------------------


def test_concurrent_writers_do_not_fail(db):
    cfg, engine, _, papers = db
    second = init_db(cfg.paths.database)  # e.g. the CLI while the web UI runs
    errors = []
    # ORM objects are not thread-safe: threads only share plain ids
    ids = [paper.id for paper in papers]

    def worker(engine_, offset):
        try:
            with Session(engine_) as session:
                for i in range(25):
                    paper = session.get(Paper, ids[(offset + i) % len(ids)])
                    session.add(
                        XpEvent(
                            paper_id=paper.id, points=1, kind="test", reason=f"{offset}-{i}", at=AT
                        )
                    )
                    session.commit()
        except Exception as error:  # pragma: no cover - reported below
            errors.append(error)

    threads = [
        threading.Thread(target=worker, args=(e, o))
        for e, o in ((engine, 0), (second, 6), (engine, 3), (second, 9))
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    second.dispose()
    assert errors == []
    with Session(engine) as session:
        assert total_xp(session) == 100


def test_commits_are_durable_and_survive_restarts(db):
    cfg, engine, session, papers = db
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA synchronous")).scalar() == 2  # FULL
    paper_id = papers[0].id
    record_reading(session, cfg, papers[0], ReadStatus.thorough, rating=5, note="n", at=AT)
    xp = total_xp(session)
    session.close()  # simulate the process ending
    engine.dispose()
    restarted = init_db(cfg.paths.database)
    with Session(restarted) as fresh:
        assert total_xp(fresh) == xp
        assert fresh.get(Reading, paper_id).notes.endswith("n")
    restarted.dispose()


def test_progress_survives_a_web_restart(cfg, tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        f"paths:\n  bibtex: {cfg.paths.bibtex}\n  database: {cfg.paths.database}\n"
        f"  cache_dir: {cfg.paths.cache_dir}\n"
    )
    first = TestClient(web.create_app(path))
    enter_profile(first, adopt=False)
    first.post("/queue/add", data={"title": "Survivor"})
    first.post("/papers/1/read", data={"level": "read", "note": "remember me"})
    first.close()
    second = TestClient(web.create_app(path))
    second.post("/welcome/select", data={"slug": "tester"})
    assert "remember me" in second.get("/papers/1").text
    assert "Badge: First Steps" in second.get("/ledger").text
    second.close()


# --- broken inputs ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content, message",
    [
        ("paths: [unclosed", "not valid YAML"),
        ("- just\n- a list\n", "must contain settings"),
        ("paths:\n  bibtex: x.bib\nselection:\n  n_recommendations: 9\n", "invalid settings"),
        ("paths:\n  bibtex: x.bib\nunknown_key: 1\n", "invalid settings"),
    ],
)
def test_broken_config_gives_a_clear_message(tmp_path, content, message):
    path = tmp_path / "config.yaml"
    path.write_text(content)
    with pytest.raises(ConfigError, match=message):
        load_config(path)


def test_cli_entry_point_reports_config_problems(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["litresearch", "status", "-c", str(tmp_path / "nope.yaml")])
    with pytest.raises(SystemExit) as exit_info:
        cli.run()
    assert exit_info.value.code == 2
    assert "No config file" in capsys.readouterr().out


def test_ingest_without_bibtex_file(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        f"paths:\n  bibtex: {tmp_path / 'missing.bib'}\n  database: {tmp_path / 'db.sqlite'}\n"
    )
    result = runner.invoke(cli.app, ["ingest", "-c", str(path)])
    assert result.exit_code == 1 and "No library has a file to import yet" in result.stdout
    assert not (tmp_path / "db.sqlite").exists()  # nothing was created


def test_check_reports_a_damaged_database(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(f"paths:\n  bibtex: x.bib\n  database: {tmp_path / 'db.sqlite'}\n")
    (tmp_path / "db.sqlite").write_bytes(b"\x00garbage" * 500)
    result = runner.invoke(cli.app, ["check", "-c", str(path)])
    assert result.exit_code == 1 and "damaged" in result.stdout


def test_non_json_api_responses_become_http_errors():
    html = httpx.MockTransport(lambda r: httpx.Response(200, text="<html>maintenance</html>"))
    with pytest.raises(HttpError, match="not JSON"):
        HttpClient(transport=html).get("https://api.example.org/x")


def test_malformed_openalex_records_are_skipped():
    good = make_work("10.1/good", "Good", "2026-01-01")
    body = {
        "results": [
            good,
            {"no": "id"},
            "not a dict",
            {**good, "id": None},
            {**good, "doi": "10.1/bad", "publication_date": "not a date"},
        ]
    }
    client = HttpClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)))
    works = OpenAlex(client).search_works("q", {})
    assert [w.doi for w in works] == ["10.1/good"]


def test_ingest_survives_an_api_returning_html(cfg, make_http):
    class Broken(FakeApis):
        def handler(self, request):
            if request.url.host == "api.openalex.org":
                return httpx.Response(200, text="<html>oops</html>")
            return super().handler(request)

    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        report = ingest_library(cfg, session, make_http(Broken()), use_s2=False)
    assert report.entries == 7 and any("OpenAlex" in e for e in report.errors)
    engine.dispose()


# --- unusual content ------------------------------------------------------------------------


def test_html_and_unicode_are_shown_safely(cfg, tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        f"paths:\n  bibtex: {cfg.paths.bibtex}\n  database: {cfg.paths.database}\n"
        f"  cache_dir: {cfg.paths.cache_dir}\n"
    )
    client = TestClient(web.create_app(path))
    enter_profile(client, adopt=False)
    evil = '<script>alert("x")</script> α-β 🧬 Ünïcödé'
    client.post("/queue/add", data={"title": evil})
    client.post("/papers/1/read", data={"level": "read", "note": "<img src=x onerror=alert(1)>"})
    for url in ("/queue", "/papers/1", "/progress", "/", "/profile", "/welcome"):
        page = client.get(url).text
        assert "<script>alert" not in page and "<img src=x" not in page
    assert "🧬 Ünïcödé" in client.get("/papers/1").text
    client.close()


def test_very_long_notes_and_titles(db):
    cfg, _, session, papers = db
    papers[0].title = "T" * 5000
    session.add(papers[0])
    session.commit()
    record_reading(session, cfg, papers[0], ReadStatus.read, note="x" * 100_000, at=AT)
    add_note(session, cfg, papers[0], "y" * 50_000, at=AT)
    assert len(session.get(Reading, papers[0].id).notes) > 150_000


def test_empty_installation_does_not_crash(cfg):
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        today = date(2026, 10, 7)
        assert stats(session, today)["xp"] == 0
        assert weekly_summaries(session, cfg, today, weeks=3)[0]["grade"] == "-"
        assert ledger(session) == ([], 0)
        assert len(reading_calendar(session, today)) == 26
        assert personal_bests(session, cfg, today)["best_week"] is None
        batch = build_recommendations(cfg, session, None, today=today, use_s2=False)
        assert batch.n_candidates == 0
    engine.dispose()


# --- calendar edge cases -------------------------------------------------------------------


def test_week_boundaries(db):
    cfg, _, session, papers = db
    sunday_night = datetime(2026, 10, 11, 23, 59, 59)
    monday_morning = datetime(2026, 10, 12, 0, 0, 0)
    record_reading(session, cfg, papers[0], ReadStatus.read, at=sunday_night)
    record_reading(session, cfg, papers[1], ReadStatus.read, at=monday_morning)
    assert week_progress(session, cfg, date(2026, 10, 11))["detail"] == 1
    assert week_progress(session, cfg, date(2026, 10, 12))["detail"] == 1


def test_streak_across_new_year():
    weeks = [date(2026, 12, 21), date(2026, 12, 28), date(2027, 1, 4)]
    assert streaks(weeks, date(2027, 1, 6)) == (3, 3)


# --- scale --------------------------------------------------------------------------------


def test_ranking_scales_to_a_large_library():
    rng = np.random.default_rng(1)
    library = LibraryProfile(
        ids=list(range(1500)),
        titles=[f"lib {i}" for i in range(1500)],
        texts=[f"tumour evolution study {i}" for i in range(1500)],
        vector_ids=list(range(1500)),
        vectors=rng.normal(size=(1500, 768)).astype(np.float32),
    )
    candidates = [
        Candidate(
            Paper(title=f"cand {i}", pub_date=date(2026, 9, 1), venue_mean_citedness=5.0),
            {"t"},
            {"openalex-fresh"},
            rng.normal(size=768).astype(np.float32),
        )
        for i in range(400)
    ]
    start = time.perf_counter()
    result = rank_candidates(
        candidates,
        library,
        {"t": 1.0},
        Config.model_validate({"paths": {"bibtex": "x"}}).selection,
        date(2026, 10, 7),
    )
    assert len(result.picks) == 5
    assert time.perf_counter() - start < 60


# --- long randomised session ----------------------------------------------------------------


def check_invariants(session, cfg):
    items = queue_items(session)
    assert [item.position for item, _ in items] == list(range(1, len(items) + 1))
    assert len(items) <= cfg.queue.max_size
    for _, paper in items:
        reading = session.get(Reading, paper.id)
        assert reading is None or depth(reading.status) <= 1  # unread or abstract only
    rows, _ = ledger(session, limit=10**9)
    assert total_xp(session) == sum(r["event"].points for r in rows)
    base_per_paper: dict[int, int] = {}
    for event in session.exec(select(XpEvent).where(XpEvent.kind == "read")).all():
        base_per_paper[event.paper_id] = base_per_paper.get(event.paper_id, 0) + event.points
    maximum = GameRules().xp_thorough
    assert all(points <= maximum for points in base_per_paper.values())
    for kind in ("classic", "hot", "recommended", "rating", "reflection"):
        paid = session.exec(select(XpEvent.paper_id).where(XpEvent.kind == kind)).all()
        assert len(paid) == len(set(paid)), f"{kind} paid twice"
    # the frog bonus is half of newly earned base points, so per paper it is
    # bounded by half of the maximum base points, however often a paper returns
    frog: dict[int, int] = {}
    for event in session.exec(select(XpEvent).where(XpEvent.kind == "frog")).all():
        frog[event.paper_id] = frog.get(event.paper_id, 0) + event.points
    assert all(points <= maximum * GameRules().frog_bonus + 1 for points in frog.values())
    level_for(total_xp(session))


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_random_reading_sessions_keep_invariants(cfg, tmp_path, seed):
    rng = random.Random(seed)
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        papers = [
            Paper(
                title=f"P{i}",
                doi=f"10.2/{i}",
                citation_percentile=rng.choice([None, 50, 99.5]),
                pub_date=date(2026, 9, 1) if i % 3 == 0 else date(2015, 1, 1),
            )
            for i in range(15)
        ]
        session.add_all(papers)
        session.commit()
        moment = AT
        for _step in range(250):
            moment += timedelta(hours=rng.choice([1, 5, 26, 80]))
            paper = rng.choice(papers)
            action = rng.choice(["add", "add", "drop", "move", "read", "read", "dismiss", "note"])
            try:
                if action == "add":
                    queue_add(
                        session,
                        cfg,
                        paper,
                        source=rng.choice(["recommendation", "manual", "library"]),
                        position=rng.choice([None, 1, 3]),
                    )
                elif action == "drop":
                    drop_from_queue(session, cfg, paper, at=moment)
                elif action == "move":
                    queue_move(session, paper, rng.randint(1, 6))
                elif action == "read":
                    level = rng.choice(["abstract", "skimmed", "read", "thorough"])
                    record_reading(
                        session,
                        cfg,
                        paper,
                        ReadStatus(level),
                        rating=rng.choice([None, 3, 5]),
                        note=rng.choice([None, "short", "long " * 40]),
                        at=moment,
                    )
                elif action == "dismiss":
                    record_reading(session, cfg, paper, ReadStatus.dismissed, at=moment)
                else:
                    add_note(session, cfg, paper, "thought " * rng.randint(1, 30), at=moment)
            except ReadingError:
                session.rollback()  # refused actions (full queue, ...) are fine
            check_invariants(session, cfg)
        today = moment.date()
        summary = weekly_summaries(session, cfg, today, weeks=12)
        assert sum(w["xp"] for w in summary) <= total_xp(session) + 1000
        data = export_progress(session)
        xp = total_xp(session)
        statuses = {
            p.doi: r.status
            for r, p in session.exec(
                select(Reading, Paper).join(Paper, Paper.id == Reading.paper_id)
            ).all()
        }
    engine.dispose()

    # the whole session moves to a fresh installation without losing anything
    other = Config.model_validate(
        {
            **cfg.model_dump(mode="json"),
            "paths": {
                **cfg.model_dump(mode="json")["paths"],
                "database": str(tmp_path / f"other{seed}.sqlite"),
            },
        }
    )
    fresh = init_db(other.paths.database)
    with Session(fresh) as session:
        import_progress(session, other, data)
        assert total_xp(session) == xp
        moved = {
            p.doi: r.status
            for r, p in session.exec(
                select(Reading, Paper).join(Paper, Paper.id == Reading.paper_id)
            ).all()
        }
        assert moved == statuses
        check_invariants(session, other)
    fresh.dispose()
