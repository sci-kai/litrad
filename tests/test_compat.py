"""Files and databases written by every earlier version still load (tests/data/compat).

Each folder was produced by ``tools/make_compat_fixtures.py`` with that
version's own code; ``expected.json`` holds the counts that version computed.
"""

import json
import shutil
from pathlib import Path

import pytest
from sqlmodel import Session, func, select

from litrad import archive, notes, profiles
from litrad.backup import check_file, export_progress, import_progress, summarize
from litrad.config import load_config
from litrad.db import (
    Achievement,
    Library,
    LibraryPaper,
    Paper,
    QueueItem,
    Reading,
    Recommendation,
    RecommendationBatch,
    StatusEvent,
    XpEvent,
    init_db,
)
from litrad.migrations import BACKUP_FOLDER, SCHEMA_VERSION, read_version

COMPAT = Path(__file__).parent / "data" / "compat"
VERSIONS = sorted(p.name for p in COMPAT.iterdir() if (p / "expected.json").is_file())
RELEASED = [v for v in VERSIONS if not v.startswith("dev-")]


def test_every_released_version_has_fixtures():
    assert {"0.1.0-alpha.1", "0.2.0-dev"} <= set(RELEASED)
    assert any(v.startswith("dev-") for v in VERSIONS)
    for version in VERSIONS:  # *.sqlite is git-ignored elsewhere: these must be committed
        for name in ("litresearch.sqlite", "settings.yaml", "progress.json", "profile.json"):
            assert (COMPAT / version / name).is_file(), f"{version}/{name} missing"


@pytest.fixture
def base(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        f"paths:\n  bibtex: {tmp_path}/x.bib\n  database: {tmp_path}/legacy.sqlite\n"
        f"  profiles_dir: {tmp_path}/profiles\nbackup:\n  dir: {tmp_path}/backups\n"
    )
    return load_config(path)


def install(base, version: str) -> str:
    """The fixture database as a profile, exactly as that version left it."""
    home = base.paths.profiles_dir / "old"
    home.mkdir(parents=True)
    # under its name before the rename: opening the profile renames it to litrad.sqlite
    shutil.copy(COMPAT / version / "litresearch.sqlite", home / profiles.LEGACY_PROFILE_DB)
    shutil.copy(COMPAT / version / "settings.yaml", home / profiles.PROFILE_SETTINGS)
    return "old"


def state(session) -> dict:
    def count(model, *where):
        return session.exec(select(func.count()).select_from(model).where(*where)).one()

    queue = session.exec(select(QueueItem).order_by(QueueItem.position)).all()
    return {
        "papers": count(Paper),
        "library_papers": count(Paper, Paper.in_library),
        "readings": count(Reading),
        "readings_with_notes": count(Reading, Reading.notes != ""),
        "status_events": count(StatusEvent),
        "xp_events": count(XpEvent),
        "xp_total": session.exec(select(func.coalesce(func.sum(XpEvent.points), 0))).one(),
        "achievements": sorted(a.code for a in session.exec(select(Achievement))),
        "queue": [session.get(Paper, q.paper_id).title for q in queue],
        "batches": count(RecommendationBatch),
        "recommendations": count(Recommendation),
        "libraries": count(Library),
        "library_memberships": count(LibraryPaper),
    }


def expected(version: str) -> dict:
    return json.loads((COMPAT / version / "expected.json").read_text())


def check_state(found: dict, wanted: dict, keys=None) -> None:
    for key in keys or found:
        if key in wanted and key in found:
            assert found[key] == wanted[key], key


@pytest.mark.parametrize("version", VERSIONS)
def test_database_opens_upgrades_and_keeps_everything(base, version):
    slug = install(base, version)
    cfg = profiles.profile_config(base, slug)
    old_version = read_version(cfg.paths.database)[0]
    info = profiles.describe(base, slug)  # opens the database: the upgrade runs
    assert info.xp == expected(version)["xp_total"]
    assert read_version(cfg.paths.database)[0] == SCHEMA_VERSION
    backups = list((cfg.paths.database.parent / BACKUP_FOLDER).glob("*.sqlite"))
    assert len(backups) == (1 if old_version < SCHEMA_VERSION else 0)
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        check_state(state(session), expected(version))
        report = notes.refresh_all(session, cfg)  # notes of 0.1-0.4 become files
        assert report.problem is None
        assert len(notes.scan(notes.notes_dir(cfg))) == expected(version)["readings_with_notes"]
    engine.dispose()


@pytest.mark.parametrize("version", VERSIONS)
def test_database_exports_and_imports_again(base, version, tmp_path):
    slug = install(base, version)
    cfg = profiles.profile_config(base, slug)
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        notes.refresh_quietly(session, cfg)
        settings = (cfg.paths.database.parent / "settings.yaml").read_text("utf-8")
        archive.export_archive(session, cfg, tmp_path / "all.zip", settings)
        original = state(session)
    engine.dispose()
    result = archive.import_new(base, tmp_path / "all.zip", "Copy")
    assert result.report.ok, result.report.lines()
    copy = profiles.profile_config(base, result.slug)
    engine = init_db(copy.paths.database)
    with Session(engine) as session:
        copied = state(session)
    engine.dispose()
    # papers referenced by nothing (e.g. candidates) are not part of an export
    for key in original:
        if key != "papers":
            assert copied[key] == original[key], key
    assert copied["papers"] <= original["papers"]
    again = archive.import_into(copy, tmp_path / "all.zip", backup=False)
    assert again.merge.readings_added == again.merge.xp_added == 0


@pytest.mark.parametrize("version", VERSIONS)
@pytest.mark.parametrize("name", ["progress.json", "profile.json"])
def test_progress_and_profile_files_load(base, version, name, tmp_path):
    data = json.loads((COMPAT / version / name).read_text())
    check_file(data)  # checksum, schema and consistency
    engine = init_db(tmp_path / "fresh.sqlite")
    cfg = profiles.profile_config(base, profiles.create_profile(base, "Fresh"))
    with Session(engine) as session:
        report = import_progress(session, cfg, data)
        assert report.readings_added == expected(version)["readings"]
        check_state(
            state(session),
            expected(version),
            ["readings", "status_events", "xp_events", "xp_total", "achievements", "queue"],
        )
        again = import_progress(session, cfg, export_progress(session))
        assert again.readings_added == again.xp_added == again.events_added == 0
    engine.dispose()
    figures = summarize(data, cfg, name)
    assert figures["xp"] == expected(version)["xp_total"]
    loaded = profiles.import_profile_file(base, data, f"Loaded {version}")
    assert profiles.describe(base, loaded).xp == expected(version)["xp_total"]


@pytest.mark.parametrize("version", [v for v in VERSIONS if (COMPAT / v / "export.zip").exists()])
def test_archives_of_earlier_versions_import(base, version):
    checked = archive.check(archive.read_archive(COMPAT / version / "export.zip"), base)
    assert checked.report.ok, checked.report.lines()
    result = archive.import_new(base, COMPAT / version / "export.zip", "From archive")
    cfg = profiles.profile_config(base, result.slug)
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        check_state(
            state(session), expected(version), [k for k in expected(version) if k != "papers"]
        )
    engine.dispose()
    assert result.notes_written == expected(version)["note_files"]
