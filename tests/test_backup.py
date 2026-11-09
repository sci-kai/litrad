import json
import sqlite3
import stat
import threading
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select
from typer.testing import CliRunner

import litresearch.cli as cli
from conftest import enter_profile
from litresearch import libraries
from litresearch.backup import (
    BackupError,
    atomic_write_text,
    backup_loop,
    checksum,
    export_progress,
    import_progress,
    integrity_ok,
    latest_backup,
    restore,
    rotate,
    run_backup,
    snapshot,
    verify_checksum,
)
from litresearch.config import Config
from litresearch.db import (
    Achievement,
    Library,
    Paper,
    QueueItem,
    Reading,
    ReadStatus,
    StatusEvent,
    XpEvent,
    init_db,
)
from litresearch.game import total_xp
from litresearch.reading import queue_add, record_reading

AT = datetime(2026, 10, 5, 9, 0, 0)
runner = CliRunner()


def make_cfg(tmp_path, name="a", **extra) -> Config:
    base = tmp_path / name
    return Config.model_validate(
        {
            "paths": {
                "bibtex": "x.bib",
                "database": str(base / "db.sqlite"),
                "cache_dir": str(base / "cache"),
            },
            "backup": {"dir": str(base / "backups"), "keep": 3},
            **extra,
        }
    )


def add_papers(session, papers):
    stored = []
    for info in papers:
        paper = Paper(**info)
        session.add(paper)
        session.commit()
        stored.append(paper)
    return stored


PAPERS = [
    {"title": "Clonal evolution", "doi": "10.1/a", "in_library": True, "bibtex_key": "A2020"},
    {"title": "Fibroblasts", "pmid": "222"},
    {"title": "Only a title"},
    {"title": "Queued paper", "doi": "10.1/q"},
]


@pytest.fixture
def source(tmp_path):
    """A database with some reading progress."""
    cfg = make_cfg(tmp_path, "a")
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        a, b, c, q = add_papers(session, PAPERS)
        queue_add(session, cfg, a)
        queue_add(session, cfg, q, source="recommendation")
        record_reading(session, cfg, a, ReadStatus.thorough, rating=5, note="key paper", at=AT)
        record_reading(session, cfg, b, ReadStatus.skimmed, at=AT + timedelta(hours=1))
        record_reading(session, cfg, c, ReadStatus.abstract, at=AT + timedelta(hours=2))
    yield cfg, engine
    engine.dispose()


@pytest.fixture
def target(tmp_path):
    """Another installation: same papers, different internal ids, no progress."""
    cfg = make_cfg(tmp_path, "b")
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        add_papers(session, [{"title": "Unrelated"}, {"title": "Also unrelated", "doi": "10.9/z"}])
        add_papers(session, list(reversed(PAPERS[:2])))  # A and B, different ids
    yield cfg, engine
    engine.dispose()


def counts(engine):
    with Session(engine) as session:
        return {
            model.__name__: len(session.exec(select(model)).all())
            for model in (Paper, Reading, StatusEvent, XpEvent, Achievement, QueueItem)
        }


# --- snapshots ------------------------------------------------------------------------


def test_snapshot_is_consistent_and_checked(source, tmp_path):
    cfg, _ = source
    copy = snapshot(cfg.paths.database, tmp_path / "copy.sqlite")
    assert integrity_ok(copy)
    assert stat.S_IMODE(copy.stat().st_mode) == 0o600
    connection = sqlite3.connect(copy)
    assert connection.execute("select count(*) from reading").fetchone()[0] == 3
    connection.close()
    with pytest.raises(BackupError, match="No database"):
        snapshot(tmp_path / "missing.sqlite", tmp_path / "x.sqlite")


def test_snapshot_while_another_connection_writes(source, tmp_path):
    cfg, engine = source
    stop = threading.Event()

    def writer():
        with Session(engine) as session:
            i = 0
            while not stop.is_set() and i < 200:
                session.add(XpEvent(points=1, kind="test", reason=f"w{i}", at=AT))
                session.commit()
                i += 1

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        for n in range(3):
            assert integrity_ok(snapshot(cfg.paths.database, tmp_path / f"s{n}.sqlite"))
    finally:
        stop.set()
        thread.join()


def test_run_backup_and_rotation(source):
    cfg, engine = source
    with Session(engine) as session:
        for minute in range(5):
            result = run_backup(cfg, session, AT + timedelta(minutes=minute))
    files = sorted(p.name for p in cfg.backup.dir.iterdir())
    assert files == [
        "litresearch_20261005_090200.sqlite",
        "litresearch_20261005_090300.sqlite",
        "litresearch_20261005_090400.sqlite",
        # the documented archive of "export everything", with the note files
        "litresearch_backup_20261005_090200.zip",
        "litresearch_backup_20261005_090300.zip",
        "litresearch_backup_20261005_090400.zip",
    ]
    assert len(result.removed) == 2  # snapshot and archive of the oldest
    assert latest_backup(cfg) == AT + timedelta(minutes=4)
    from litresearch import archive

    checked = archive.check_file(result.archive, cfg)
    assert not checked.report.errors
    assert checked.notes  # the note files are in the archive
    assert result.progress == result.archive  # the name before 1.0


def test_rotation_counts_legacy_and_new_backups_together(tmp_path):
    """Backups from before 1.0 (progress JSON + notes zip) age out with the archives."""
    for day in range(1, 4):
        (tmp_path / f"litresearch_progress_2026010{day}_070000.json").write_text("{}")
        (tmp_path / f"litresearch_notes_2026010{day}_070000.zip").write_text("")
    for day in range(4, 6):
        (tmp_path / f"litresearch_backup_2026010{day}_070000.zip").write_text("")
    (tmp_path / "litresearch_export_kai_20260101_070000.zip").write_text("")  # yours
    (tmp_path / "litresearch_profile_kai_20260101_070000.zip").write_text("")  # yours
    rotate(tmp_path, keep=3)
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "litresearch_backup_20260104_070000.zip",
        "litresearch_backup_20260105_070000.zip",
        "litresearch_export_kai_20260101_070000.zip",
        "litresearch_notes_20260103_070000.zip",
        "litresearch_profile_kai_20260101_070000.zip",
        "litresearch_progress_20260103_070000.json",
    ]


def test_latest_backup_reads_archives_and_legacy_files(tmp_path):
    cfg = make_cfg(tmp_path)
    cfg.backup.dir.mkdir(parents=True)
    (cfg.backup.dir / "litresearch_progress_20261001_070000.json").write_text("{}")
    assert latest_backup(cfg) == datetime(2026, 10, 1, 7)
    (cfg.backup.dir / "litresearch_backup_20261002_070000.zip").write_text("")
    assert latest_backup(cfg) == datetime(2026, 10, 2, 7)


def test_restore_refuses_an_archive_with_a_hint(source, tmp_path):
    cfg, engine = source
    with Session(engine) as session:
        result = run_backup(cfg, session, AT)
    with pytest.raises(BackupError, match="is an archive"):
        restore(cfg, result.archive)


def test_rotate_ignores_other_files(tmp_path):
    (tmp_path / "notes.txt").write_text("keep me")
    for i in range(3):
        (tmp_path / f"litresearch_2026010{i}_000000.sqlite").write_text("")
    removed = rotate(tmp_path, keep=1)
    assert len(removed) == 2 and (tmp_path / "notes.txt").exists()


def test_latest_backup_without_backups(tmp_path):
    cfg = make_cfg(tmp_path)
    assert latest_backup(cfg) is None
    cfg.backup.dir.mkdir(parents=True)
    (cfg.backup.dir / "litresearch_progress_garbage.json").write_text("{}")
    assert latest_backup(cfg) is None


def test_atomic_write_keeps_old_file_on_failure(tmp_path, monkeypatch):
    path = tmp_path / "file.txt"
    path.write_text("old")

    def broken_replace(*_):
        raise OSError("disk full")

    monkeypatch.setattr("litresearch.backup.os.replace", broken_replace)
    with pytest.raises(OSError):
        atomic_write_text(path, "new")
    assert path.read_text() == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["file.txt"]  # no temp files left


# --- export / import ------------------------------------------------------------------


def test_export_contents(source):
    _, engine = source
    with Session(engine) as session:
        data = export_progress(session, AT)
    assert data["format_version"] == 2 and data["exported_at"] == "2026-10-05T09:00:00"
    assert data["profile"]["name"] == "My reading journey"  # auto-started by the first read
    assert verify_checksum(data) is True
    assert len(data["readings"]) == 3 and len(data["queue"]) == 1  # A left the queue
    assert {p["title"] for p in data["papers"].values()} == {
        "Clonal evolution",
        "Fibroblasts",
        "Only a title",
        "Queued paper",
    }
    assert data["achievements"][0]["code"] == "first_read"
    json.dumps(data)  # serialisable


def test_import_into_other_installation(source, target):
    cfg_a, engine_a = source
    cfg_b, engine_b = target
    with Session(engine_a) as session:
        data = export_progress(session)
        xp_a = total_xp(session)
    with Session(engine_b) as session:
        report = import_progress(session, cfg_b, data)
        assert report.papers_matched == 2 and report.papers_created == 2
        assert report.readings_added == 3 and report.queue_added == 1
        assert total_xp(session) == xp_a
        a = session.exec(select(Paper).where(Paper.doi == "10.1/a")).one()
        reading = session.get(Reading, a.id)
        assert reading.status == ReadStatus.thorough and reading.rating == 5
        assert "key paper" in reading.notes
        title_only = session.exec(select(Paper).where(Paper.title == "Only a title")).one()
        assert session.get(Reading, title_only.id).status == ReadStatus.abstract
        queued = session.exec(select(QueueItem)).one()
        assert queued.source == "recommendation"  # the drop penalty still applies


def test_import_is_idempotent(source, target):
    _, engine_a = source
    cfg_b, engine_b = target
    with Session(engine_a) as session:
        data = export_progress(session)
    with Session(engine_b) as session:
        import_progress(session, cfg_b, data)
    first = counts(engine_b)
    with Session(engine_b) as session:
        report = import_progress(session, cfg_b, data)
    assert counts(engine_b) == first
    assert report.readings_added == report.xp_added == report.events_added == 0
    assert report.papers_created == 0


def test_import_merges_notes_and_newer_status_wins(source, target):
    _, engine_a = source
    cfg_b, engine_b = target
    with Session(engine_a) as session:
        data = export_progress(session)
    with Session(engine_b) as session:
        a = session.exec(select(Paper).where(Paper.doi == "10.1/a")).one()
        later = AT + timedelta(days=1)
        session.add(
            Reading(paper_id=a.id, status=ReadStatus.read, notes="local note", updated_at=later)
        )
        b = session.exec(select(Paper).where(Paper.pmid == "222")).one()
        session.add(
            Reading(
                paper_id=b.id,
                status=ReadStatus.abstract,
                notes="",
                updated_at=AT - timedelta(days=1),
            )
        )
        session.commit()
        report = import_progress(session, cfg_b, data)
        a_reading, b_reading = session.get(Reading, a.id), session.get(Reading, b.id)
    # A: the local entry is newer, so its status stays, but no note is lost
    assert a_reading.status == ReadStatus.read
    assert a_reading.notes.startswith("local note") and "key paper" in a_reading.notes
    # B: the incoming entry is newer
    assert b_reading.status == ReadStatus.skimmed
    assert report.readings_updated == 1 and report.notes_merged == 1


def test_import_respects_queue_limit(source, tmp_path):
    _, engine_a = source
    cfg_b = make_cfg(tmp_path, "c", queue={"max_size": 1})
    engine_b = init_db(cfg_b.paths.database)
    with Session(engine_b) as session:
        session.add(Paper(title="Mine", doi="10.1/mine"))
        session.commit()
        queue_add(session, cfg_b, session.exec(select(Paper)).one())
    with Session(engine_a) as session:
        data = export_progress(session)
    with Session(engine_b) as session:
        report = import_progress(session, cfg_b, data)
    assert report.queue_added == 0 and report.queue_skipped == ["Queued paper"]
    engine_b.dispose()


@pytest.mark.parametrize(
    "data, message",
    [
        ({"format": "something else"}, "Not a litresearch progress export"),
        ({"format": "litresearch-progress", "format_version": 99}, "newer litresearch"),
        (
            {
                "format": "litresearch-progress",
                "papers": {},
                "readings": [{"paper": "p1", "status": "read"}],
            },
            "Invalid progress file",
        ),
        (
            {
                "format": "litresearch-progress",
                "papers": {"p1": {"title": "X"}},
                "readings": [{"paper": "p1", "status": "bogus"}],
            },
            "Invalid progress file",
        ),
    ],
)
def test_invalid_imports_change_nothing(target, data, message):
    cfg_b, engine_b = target
    before = counts(engine_b)
    with Session(engine_b) as session, pytest.raises(BackupError, match=message):
        import_progress(session, cfg_b, data)
    assert counts(engine_b) == before


# --- restore -------------------------------------------------------------------------


def test_restore_round_trip(source):
    cfg, engine = source
    with Session(engine) as session:
        snap = run_backup(cfg, session, AT).snapshot
        session.add(XpEvent(points=999, kind="test", reason="after backup", at=AT))
        session.commit()
    engine.dispose()
    safety = restore(cfg, snap, AT + timedelta(hours=1))
    assert safety.name == "litresearch_20261005_100000_before_restore.sqlite"
    restored = init_db(cfg.paths.database)
    with Session(restored) as session:
        assert not session.exec(select(XpEvent).where(XpEvent.points == 999)).all()
    restored.dispose()
    # the state before the restore can be restored again
    connection = sqlite3.connect(safety)
    assert connection.execute("select count(*) from xpevent where points = 999").fetchone()[0] == 1
    connection.close()


def test_restore_rejects_foreign_files(source, tmp_path):
    cfg, _ = source
    junk = tmp_path / "junk.sqlite"
    junk.write_bytes(b"this is not sqlite at all" * 100)
    with pytest.raises(BackupError, match="not a valid"):
        restore(cfg, junk)
    other = tmp_path / "other.sqlite"
    connection = sqlite3.connect(other)
    connection.execute("create table something (x)")
    connection.close()
    with pytest.raises(BackupError, match="does not look like"):
        restore(cfg, other)


# --- automatic backups ------------------------------------------------------------------


def test_backup_loop_backs_up_and_survives_errors(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text(
        f"paths:\n  bibtex: x.bib\n  database: {tmp_path / 'db.sqlite'}\n"
        f"backup:\n  dir: {tmp_path / 'backups'}\n  interval_hours: 24\n"
    )
    init_db(tmp_path / "db.sqlite").dispose()
    stop, messages = threading.Event(), []

    class OneShot:
        def is_set(self):
            return bool(messages)

        def wait(self, _):
            return True

    backup_loop(path, OneShot(), messages.append)
    assert messages[0].startswith("backup written")

    def broken(*_args, **_kwargs):
        raise BackupError("disk full")

    monkeypatch.setattr("litresearch.backup.run_backup", broken)
    messages.clear()
    backup_loop(path, OneShot(), messages.append)
    assert messages == ["WARNING: backup of (no profile) failed: disk full"]
    assert not stop.is_set()


def test_backup_loop_disabled(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("paths:\n  bibtex: x.bib\nbackup:\n  interval_hours: 0\n")
    messages = []
    backup_loop(path, threading.Event(), messages.append)  # returns immediately
    assert messages == []


# --- CLI and web -----------------------------------------------------------------------


def write_config(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        f"paths:\n  bibtex: x.bib\n  database: {tmp_path / 'db.sqlite'}\n"
        f"  cache_dir: {tmp_path / 'cache'}\nbackup:\n  dir: {tmp_path / 'backups'}\n"
    )
    return path


@pytest.fixture
def plain_console(monkeypatch):
    from rich.console import Console

    import litresearch.cli_data as cli_data

    console = Console(color_system=None, width=300, highlight=False)
    monkeypatch.setattr(cli, "console", console)
    monkeypatch.setattr(cli_data, "console", console)


def test_cli_backup_export_import_check_restore(tmp_path, plain_console):
    path = write_config(tmp_path)
    c = ["-c", str(path)]
    result = runner.invoke(cli.app, ["check", *c])
    assert result.exit_code == 1 and "does not exist yet" in result.stdout
    engine = init_db(tmp_path / "db.sqlite")
    with Session(engine) as session:
        session.add(Paper(title="P", doi="10.1/p"))
        session.add(Reading(paper_id=1, status=ReadStatus.read, notes="n"))
        session.commit()
    engine.dispose()

    result = runner.invoke(cli.app, ["check", *c])
    assert (
        result.exit_code == 0 and "No backup yet" in result.stdout and "1 readings" in result.stdout
    )
    result = runner.invoke(cli.app, ["backup", *c])
    assert result.exit_code == 0 and "Snapshot:" in result.stdout
    result = runner.invoke(cli.app, ["check", *c])
    assert "Last backup" in result.stdout and "Integrity: ok" in result.stdout

    export = tmp_path / "progress.json"
    assert runner.invoke(cli.app, ["export-progress", str(export), *c]).exit_code == 0
    result = runner.invoke(cli.app, ["import-progress", str(export), "--dry-run", *c])
    assert result.exit_code == 0 and "Dry run" in result.stdout
    result = runner.invoke(cli.app, ["import-progress", str(tmp_path / "missing.json"), *c])
    assert result.exit_code == 1 and "Cannot read" in result.stdout
    (tmp_path / "bad.json").write_text('{"format": "x"}')
    result = runner.invoke(cli.app, ["import-progress", str(tmp_path / "bad.json"), *c])
    assert result.exit_code == 1 and "Not a litresearch" in result.stdout

    snap = sorted((tmp_path / "backups").glob("litresearch_2*.sqlite"))[0]
    result = runner.invoke(cli.app, ["restore", str(snap), *c], input="n\n")
    assert result.exit_code == 1  # aborted
    result = runner.invoke(cli.app, ["restore", str(snap), "--yes", *c])
    assert result.exit_code == 0 and "previous database was saved" in result.stdout


def test_web_backup_and_download(tmp_path):
    from litresearch.web.app import create_app

    path = write_config(tmp_path)
    init_db(tmp_path / "db.sqlite").dispose()
    client = TestClient(create_app(path))
    enter_profile(client, "Ada", adopt=False)
    page = client.get("/settings").text
    assert "Data &amp; backups" in page and "none yet" in page
    response = client.post("/backup", follow_redirects=False)
    location = response.headers["location"]
    assert location.startswith("/settings?c=") and location.endswith("#data")
    assert "Backup written" in client.get(location.split("#")[0]).text
    download = client.get("/progress.json")
    assert download.headers["content-disposition"].startswith(
        "attachment; filename=litresearch_progress_"
    )
    assert download.json()["format"] == "litresearch-progress"
    assert download.json()["settings"]["topics"] == []  # the profile's settings travel along
    # backups of a profile go to its own subfolder
    assert list((tmp_path / "backups" / "ada").glob("litresearch_*.sqlite"))
    client.close()


def test_restore_removes_stale_journal_files(source):
    cfg, engine = source
    with Session(engine) as session:
        snap = run_backup(cfg, session, AT).snapshot
    engine.dispose()
    db = cfg.paths.database
    stale = [db.with_name(db.name + suffix) for suffix in ("-journal", "-wal", "-shm")]
    for path in stale:
        path.write_bytes(b"stale")
    restore(cfg, snap, AT + timedelta(hours=1))
    assert not any(path.exists() for path in stale)
    assert integrity_ok(db)


def test_import_named_recommended_does_not_merge_into_recommended(source, target):
    cfg, engine = source
    tcfg, tengine = target
    with Session(engine) as session:
        paper = session.exec(select(Paper)).first()
        lib = Library(name="Recommended", description="my own list")  # as from another tool
        session.add(lib)
        session.flush()
        libraries.link(session, lib, paper)
        session.commit()
        data = export_progress(session)
    assert sorted(e["kind"] for e in data["libraries"]) == ["imported", "recommended"]
    data["libraries"] = [e for e in data["libraries"] if e["kind"] == "imported"]
    data["checksum"] = checksum(data)
    with Session(tengine) as session:
        local = libraries.recommended_library(session)
        session.commit()
        assert local.name == "Recommended"
        report = import_progress(session, tcfg, data)
        assert report.libraries_added == 1
        by_name = {lib.name: lib for lib in session.exec(select(Library)).all()}
        assert by_name["Recommended"].kind == "recommended"
        assert by_name["Recommended 2"].kind == "imported"
        assert libraries.size(session, by_name["Recommended"]) == 0
        assert libraries.size(session, by_name["Recommended 2"]) == 1
        assert import_progress(session, tcfg, data).libraries_added == 0  # idempotent
