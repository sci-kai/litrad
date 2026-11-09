"""Versioned database schema: migrations, snapshots before upgrades, refusing newer files."""

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlmodel import Session, select
from typer.testing import CliRunner

import litrad.cli as cli
from litrad import migrations
from litrad.backup import BackupError, check_database, restore
from litrad.config import Config
from litrad.db import Paper, RecommendationBatch, init_db
from litrad.migrations import (
    SCHEMA_VERSION,
    STEPS,
    MigrationError,
    SchemaTooNew,
    Step,
    migrate,
    read_version,
)

COMPAT = Path(__file__).parent / "data" / "compat"
runner = CliRunner()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def old_database(path: Path) -> Path:
    """A database from before schema versions (0.1): a table with fewer columns."""
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE recommendationbatch (id INTEGER PRIMARY KEY, created_at DATETIME, "
        "week_start DATE, n_candidates INTEGER, warnings JSON)"
    )
    connection.execute(
        "INSERT INTO recommendationbatch VALUES (1, '2026-10-05', '2026-10-05', 3, '[]')"
    )
    connection.commit()
    connection.close()
    return path


def test_steps_are_numbered_without_gaps():
    assert [s.version for s in STEPS] == list(range(1, SCHEMA_VERSION + 1))
    assert all(s.description for s in STEPS)


def test_models_match_the_recorded_schema(tmp_path):
    """Changing a model needs a new schema version and step (and a new entry here)."""
    init_db(tmp_path / "new.sqlite").dispose()
    connection = sqlite3.connect(tmp_path / "new.sqlite")
    current = sorted(
        r[0] for r in connection.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL")
    )
    connection.close()
    recorded = json.loads((COMPAT / "schema.json").read_text())
    assert str(SCHEMA_VERSION) in recorded, "record the schema of the new version"
    assert current == recorded[str(SCHEMA_VERSION)], (
        "the models changed: add a migration step, raise SCHEMA_VERSION and record the schema"
    )


def test_new_database_gets_the_current_version_without_snapshot(tmp_path):
    path = tmp_path / "new.sqlite"
    result = migrate(path)
    assert result.created and result.snapshot is None
    assert read_version(path) == (SCHEMA_VERSION, True)
    assert not (tmp_path / migrations.BACKUP_FOLDER).exists()
    assert migrate(path).applied == []  # nothing to do the second time


def test_old_database_is_snapshotted_then_upgraded(tmp_path):
    path = old_database(tmp_path / "old.sqlite")
    result = migrate(path)
    assert result.from_version == 0 and result.to_version == SCHEMA_VERSION
    assert result.snapshot.parent == tmp_path / migrations.BACKUP_FOLDER
    assert read_version(result.snapshot)[0] == 0
    connection = sqlite3.connect(result.snapshot)  # the database as it was
    tables = [r[0] for r in connection.execute("SELECT name FROM sqlite_master")]
    rows = connection.execute("SELECT * FROM recommendationbatch").fetchall()
    connection.close()
    assert tables == ["recommendationbatch"] and len(rows) == 1
    engine = init_db(path)
    with Session(engine) as session:
        batch = session.get(RecommendationBatch, 1)
        assert batch.n_candidates == 3 and batch.funnel is None
    engine.dispose()


def test_upgrade_copies_are_rotated_after_a_successful_upgrade(tmp_path):
    folder = tmp_path / migrations.BACKUP_FOLDER
    folder.mkdir()
    for day in range(1, 6):  # copies of earlier upgrades
        (folder / f"litrad_2026010{day}_070000_before-schema-0-to-1.sqlite").write_text("x")
    (folder / "my-own-copy.sqlite").write_text("keep")  # not ours: never deleted
    result = migrate(old_database(tmp_path / "old.sqlite"))
    names = sorted(p.name for p in folder.iterdir())
    assert result.snapshot.name in names
    assert len([n for n in names if "_before-schema-" in n]) == migrations.UPGRADE_BACKUPS_KEEP
    assert "my-own-copy.sqlite" in names
    assert "litrad_20260101_070000_before-schema-0-to-1.sqlite" not in names


def test_a_failed_upgrade_keeps_every_copy(tmp_path):
    folder = tmp_path / migrations.BACKUP_FOLDER
    folder.mkdir()
    for day in range(1, 6):
        (folder / f"litrad_2026010{day}_070000_before-schema-0-to-1.sqlite").write_text("x")

    def broken(connection):
        raise RuntimeError("boom")

    with pytest.raises(MigrationError):
        migrate(old_database(tmp_path / "old.sqlite"), (Step(1, "broken", broken),))
    assert len(list(folder.iterdir())) == 6  # five old ones and the new copy


def test_where_names_the_upgrade_copies(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text(f"paths:\n  profiles_dir: {tmp_path}/profiles\n")
    result = runner.invoke(cli.app, ["where", "--config", str(path)])
    assert result.exit_code == 0, result.output
    assert migrations.BACKUP_FOLDER in result.output and "Upgrade copies" in result.output


def test_newer_database_is_refused_and_not_changed(tmp_path):
    path = tmp_path / "newer.sqlite"
    init_db(path).dispose()
    connection = sqlite3.connect(path)
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    connection.commit()
    connection.close()
    before = digest(path)
    with pytest.raises(SchemaTooNew, match="newer litrad"):
        init_db(path)
    assert digest(path) == before
    assert not (tmp_path / migrations.BACKUP_FOLDER).exists()


def test_failed_step_rolls_back_and_keeps_the_version(tmp_path):
    path = old_database(tmp_path / "old.sqlite")

    def broken(connection):
        connection.execute(text("CREATE TABLE half_done (id INTEGER)"))
        raise RuntimeError("boom")

    last = SCHEMA_VERSION
    steps = (*STEPS, Step(last + 1, "broken", broken))
    original = migrations.SCHEMA_VERSION
    migrations.SCHEMA_VERSION = last + 1
    try:
        with pytest.raises(MigrationError, match=f"stays at version {last}"):
            migrate(path, steps)
        assert read_version(path)[0] == last
        connection = sqlite3.connect(path)
        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master")}
        connection.close()
        assert "half_done" not in tables and "paper" in tables
        # fixed step: resumes at the failed one
        fixed = (
            *STEPS,
            Step(last + 1, "fixed", lambda c: c.execute(text("CREATE TABLE extra (x)"))),
        )
        result = migrate(path, fixed)
        assert result.from_version == last
        assert [a.split(":")[0] for a in result.applied] == [str(last + 1)]
        assert read_version(path)[0] == last + 1
    finally:
        migrations.SCHEMA_VERSION = original


@pytest.mark.parametrize("version", ["0.1.0-alpha.1", "0.2.0-dev"])
def test_released_databases_upgrade_to_the_current_columns(tmp_path, version):
    path = tmp_path / "old.sqlite"
    shutil.copy(COMPAT / version / "litresearch.sqlite", path)
    init_db(path).dispose()
    init_db(tmp_path / "new.sqlite").dispose()

    def columns(file):
        connection = sqlite3.connect(file)
        tables = [
            r[0]
            for r in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            )
        ]
        out = {
            t: sorted(r[1] for r in connection.execute(f'PRAGMA table_info("{t}")')) for t in tables
        }
        indexes = sorted(
            r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
        )
        connection.close()
        return out, indexes

    assert columns(path) == columns(tmp_path / "new.sqlite")
    assert read_version(path)[0] == SCHEMA_VERSION


# --- restore and check --------------------------------------------------------------------


def make_cfg(tmp_path) -> Config:
    return Config.model_validate(
        {
            "paths": {"bibtex": "x.bib", "database": str(tmp_path / "live" / "db.sqlite")},
            "backup": {"dir": str(tmp_path / "backups")},
        }
    )


def test_restore_refuses_a_newer_snapshot(tmp_path):
    cfg = make_cfg(tmp_path)
    init_db(cfg.paths.database).dispose()
    live = digest(cfg.paths.database)
    snapshot = tmp_path / "newer.sqlite"
    init_db(snapshot).dispose()
    connection = sqlite3.connect(snapshot)
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 5}")
    connection.commit()
    connection.close()
    with pytest.raises(BackupError, match="newer litrad"):
        restore(cfg, snapshot)
    assert digest(cfg.paths.database) == live


def test_restore_refuses_rows_pointing_to_missing_papers(tmp_path):
    cfg = make_cfg(tmp_path)
    snapshot = tmp_path / "broken.sqlite"
    engine = init_db(snapshot)
    with Session(engine) as session:
        session.add(Paper(title="A"))
        session.commit()
    engine.dispose()
    connection = sqlite3.connect(snapshot)
    connection.execute(
        "INSERT INTO reading (paper_id, status, notes, updated_at) "
        "VALUES (99, 'read', '', '2026-10-01 10:00:00')"
    )
    connection.commit()
    connection.close()
    problems = check_database(snapshot)
    assert problems == ["reading row 1: refers to a missing paper"] or problems[0].startswith(
        "reading row"
    )
    with pytest.raises(BackupError, match="refers to a missing paper"):
        restore(cfg, snapshot)
    assert not cfg.paths.database.exists()


def test_restore_of_an_old_snapshot_upgrades_it(tmp_path):
    cfg = make_cfg(tmp_path)
    init_db(cfg.paths.database).dispose()
    snapshot = tmp_path / "litrad_20261001_100000.sqlite"
    shutil.copy(COMPAT / "0.1.0-alpha.1" / "litresearch.sqlite", snapshot)
    restore(cfg, snapshot)
    assert read_version(cfg.paths.database)[0] == SCHEMA_VERSION
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        assert len(session.exec(select(Paper)).all()) == 7
    engine.dispose()


def test_check_command_shows_the_schema_version(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        f"paths:\n  bibtex: x.bib\n  database: {tmp_path / 'db.sqlite'}\n"
        f"backup:\n  dir: {tmp_path / 'backups'}\n"
    )
    init_db(tmp_path / "db.sqlite").dispose()
    result = runner.invoke(cli.app, ["check", "--config", str(config)])
    assert result.exit_code == 0, result.output
    assert f"version {SCHEMA_VERSION}, this litrad: {SCHEMA_VERSION}" in result.output


def test_command_line_reports_a_newer_database(tmp_path, monkeypatch, capsys):
    config = tmp_path / "config.yaml"
    database = tmp_path / "db.sqlite"
    config.write_text(f"paths:\n  bibtex: x.bib\n  database: {database}\n")
    init_db(database).dispose()
    connection = sqlite3.connect(database)
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    connection.commit()
    connection.close()
    monkeypatch.setattr("sys.argv", ["litrad", "queue", "--config", str(config)])
    with pytest.raises(SystemExit) as exit_info:
        cli.run()
    assert exit_info.value.code == 2
    assert "newer litrad" in capsys.readouterr().out
