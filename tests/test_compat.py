"""Files and databases written by every release since 1.0 still load (tests/data/compat).

Each folder was produced by ``tools/make_compat_fixtures.py`` with that
version's own code; ``expected.json`` holds the counts that version computed.
Every release adds its folder; until the first one exists, the same checks run
on fixtures that the builder writes with the current code (``current``).
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from sqlmodel import Session, func, select

from litrad import archive, notes, profiles
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

REPO = Path(__file__).resolve().parent.parent
COMPAT = Path(__file__).parent / "data" / "compat"
FILES = ("litrad.sqlite", "settings.yaml", "export.zip", "expected.json")
VERSIONS = sorted(p.name for p in COMPAT.iterdir() if (p / "expected.json").is_file())


@pytest.fixture(scope="module")
def current(tmp_path_factory) -> Path:
    """Fixtures of the current code, written by the builder of make_compat_fixtures.py."""
    sys.path.insert(0, str(REPO / "tools"))
    try:
        import make_compat_fixtures as tool
    finally:
        sys.path.remove(str(REPO / "tools"))
    root = tmp_path_factory.mktemp("compat-current")
    sandbox, out = root / "run", root / "current"
    for folder in (sandbox, sandbox / "home", sandbox / "config", sandbox / "share"):
        folder.mkdir(parents=True)
    arguments = {
        "work": str(sandbox),
        "out": str(out),
        "name": "current",
        "tag": "",
        "source": str(REPO),
        "bib": str(tool.BIB_FILE),
    }
    env = {
        **os.environ,
        "HOME": str(sandbox / "home"),
        "XDG_CONFIG_HOME": str(sandbox / "config"),
        "XDG_DATA_HOME": str(sandbox / "share"),
    }
    done = subprocess.run(
        [sys.executable, "-c", tool.BUILDER, json.dumps(arguments)],
        cwd=sandbox,
        env=env,
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr[-3000:]
    assert sorted(p.name for p in out.iterdir()) == sorted(tool.FIXTURE_FILES)
    return out


@pytest.fixture(params=[*VERSIONS, "current"])
def fixture(request) -> Path:
    if request.param == "current":
        return request.getfixturevalue("current")
    return COMPAT / request.param


def test_released_fixtures_are_complete():
    for version in VERSIONS:  # *.sqlite is git-ignored elsewhere: these must be committed
        for name in FILES:
            assert (COMPAT / version / name).is_file(), f"{version}/{name} missing"


@pytest.fixture
def base(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        f"paths:\n  profiles_dir: {tmp_path}/profiles\nbackup:\n  dir: {tmp_path}/backups\n"
    )
    return load_config(path)


def install(base, folder: Path) -> str:
    """The fixture database as a profile, exactly as that version left it."""
    home = base.paths.profiles_dir / "old"
    home.mkdir(parents=True)
    shutil.copy(folder / "litrad.sqlite", home / profiles.PROFILE_DB)
    shutil.copy(folder / "settings.yaml", home / profiles.PROFILE_SETTINGS)
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


def expected(folder: Path) -> dict:
    return json.loads((folder / "expected.json").read_text())


def check_state(found: dict, wanted: dict, keys=None) -> None:
    for key in keys or found:
        if key in wanted and key in found:
            assert found[key] == wanted[key], key


def test_database_opens_upgrades_and_keeps_everything(base, fixture):
    version = fixture
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
        report = notes.refresh_all(session, cfg)
        assert report.problem is None
        assert len(notes.scan(notes.notes_dir(cfg))) == expected(version)["readings_with_notes"]
    engine.dispose()


def test_database_exports_and_imports_again(base, fixture, tmp_path):
    slug = install(base, fixture)
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


def test_archives_of_earlier_versions_import(base, fixture):
    version = fixture
    checked = archive.check(archive.read_archive(version / "export.zip"), base)
    assert checked.report.ok, checked.report.lines()
    result = archive.import_new(base, version / "export.zip", "From archive")
    cfg = profiles.profile_config(base, result.slug)
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        check_state(
            state(session), expected(version), [k for k in expected(version) if k != "papers"]
        )
    engine.dispose()
    assert result.notes_written == expected(version)["note_files"]
