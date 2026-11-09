"""Renamed from litresearch to litrad: everything written or set up before still works.

Commands, environment variables, per-user folders, database and backup file
names, data formats, note files, cookies, the installer's service and the
branding (see docs/documentation.md, "Renamed from litresearch").
"""

import errno
import importlib.util
import json
import os
import shutil
import sqlite3
import struct
import subprocess
import sys
import tomllib
import zipfile
from datetime import datetime
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlmodel import Session
from typer.testing import CliRunner

import litrad.cli as cli
from conftest import enter_profile
from litrad import archive, backup, desktop, locations, migrations, notes, profiles
from litrad.config import load_config
from litrad.dataformat import validate_schema
from litrad.db import Paper, ReadStatus, init_db
from litrad.reading import record_reading
from litrad.web.app import create_app
from litrad.web.auth import LEGACY_COOKIE

ROOT = Path(__file__).resolve().parents[1]
COMPAT = ROOT / "tests" / "data" / "compat"
FIXTURE = "dev-20261109"
runner = CliRunner()


# --- commands ------------------------------------------------------------------------------


def test_old_commands_are_aliases():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert project["name"] == "litrad"
    assert project["scripts"] == {
        "litrad": "litrad.cli:run",
        "litresearch": "litrad.cli:run_legacy",
    }
    assert project["gui-scripts"] == {
        "litrad-app": "litrad.desktop:main",
        "litresearch-app": "litrad.desktop:main_legacy",
    }


def test_litresearch_command_works_and_names_the_new_one(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["litresearch", "version"])
    with pytest.raises(SystemExit) as done:
        cli.run_legacy()
    assert done.value.code in (0, None)
    output = capsys.readouterr()
    assert output.out.strip() == cli.__version__
    assert output.err.strip() == "Note: litresearch is now called litrad; use the command 'litrad'."


def test_litresearch_app_works_and_names_the_new_one(monkeypatch, capsys):
    started = []
    monkeypatch.setattr(desktop, "run", lambda *args: started.append(args) or 0)
    with pytest.raises(SystemExit):
        desktop.main_legacy(["--no-browser", "--port", "4711"])
    assert started == [(None, 4711, False)]
    assert "use 'litrad-app'" in capsys.readouterr().err


def test_alias_without_console_does_not_fail(monkeypatch):
    monkeypatch.setattr(sys, "stderr", None)  # windowed builds
    monkeypatch.setattr(desktop, "run", lambda *args: 0)
    with pytest.raises(SystemExit):
        desktop.main_legacy([])


def test_installed_alias_runs(tmp_path):
    """The console script of the alias, as pip installed it (skipped if not installed)."""
    script = Path(sys.executable).parent / "litresearch"
    if not script.exists():
        pytest.skip("package not installed with its console scripts")
    env = {**os.environ, "LITRAD_CONFIG": str(tmp_path / "config.yaml")}
    result = subprocess.run(
        [str(script), "version"], capture_output=True, text=True, env=env, cwd=tmp_path
    )
    assert result.returncode == 0 and result.stdout.strip() == cli.__version__
    assert "now called litrad" in result.stderr


def test_help_names_the_game():
    result = runner.invoke(cli.app, ["--help"])
    assert "Literature Radar Game" in result.output


# --- environment variables -------------------------------------------------------------------


def test_old_config_variable_counts_and_new_one_wins(tmp_path):
    old = {"LITRESEARCH_CONFIG": str(tmp_path / "old.yaml")}
    assert locations.find_config(env=old) == locations.ConfigChoice(tmp_path / "old.yaml", "env")
    both = {**old, "LITRAD_CONFIG": str(tmp_path / "new.yaml")}
    assert locations.find_config(env=both).path == tmp_path / "new.yaml"


def test_old_config_variable_creates_the_first_config(tmp_path, monkeypatch):
    monkeypatch.setenv("LITRESEARCH_CONFIG", str(tmp_path / "data" / "config.yaml"))
    choice = locations.locate_config()
    assert choice.rule == "env" and choice.path.is_file()


@pytest.mark.parametrize(
    "new, old, wanted",
    [
        (None, "1", False),
        (None, "0", True),
        ("0", "1", True),
        ("1", None, False),
        (None, None, True),
    ],
)
def test_old_no_browser_variable(monkeypatch, new, old, wanted):
    for name, value in ((desktop.ENV_NO_BROWSER, new), (desktop.LEGACY_ENV_NO_BROWSER, old)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    assert desktop.browser_wanted() is wanted


def test_a_running_instance_from_before_the_rename_is_recognised(monkeypatch):
    class Response:
        def __init__(self, app):
            self.body = json.dumps({"app": app, "pid": 7}).encode()

        def read(self):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    for app, expected in (("litresearch", True), ("litrad", True), ("other", False)):

        class Opener:
            def open(self, url, timeout):
                return Response(app)  # noqa: B023

        monkeypatch.setattr(desktop.urllib.request, "build_opener", lambda *a: Opener())
        assert desktop.answers(1234, pid=7) is expected


# --- per-user folders -------------------------------------------------------------------------


def legacy_installation() -> dict:
    """The per-user folders of litresearch as its first run created them, with a profile."""
    old_config, old_data = locations.legacy_user_config_dir(), locations.legacy_user_data_dir()
    config = locations.first_run_setup(old_config / "config.yaml", old_data)
    text = config.read_text().replace("litrad.sqlite", "litresearch.sqlite")
    config.write_text(text)  # what litresearch wrote
    home = old_data / "profiles" / "ada"
    home.mkdir(parents=True)
    shutil.copy(COMPAT / FIXTURE / "litresearch.sqlite", home / "litresearch.sqlite")
    bib = old_data / "paperpile" / "library.bib"
    bib.parent.mkdir(parents=True)
    bib.write_text("% export\n")
    (home / "settings.yaml").write_text(
        f"# my settings\ntopics: []\npaths:\n  bibtex: {bib}  # synced by rclone\n"
    )
    connection = sqlite3.connect(home / "litresearch.sqlite")
    with connection:
        connection.execute("UPDATE library SET source_path = ?", (str(bib),))
    connection.close()
    (old_data / "logs").mkdir()
    (old_data / "logs" / "app.log").write_text("old log\n")
    return {"config": old_config, "data": old_data, "bib": bib}


def test_folders_are_moved_and_paths_follow():
    old = legacy_installation()
    moves = locations.migrate_user_dirs()
    new_config, new_data = locations.user_config_dir(), locations.user_data_dir()
    assert [(m.old, m.new, m.action) for m in moves] == [
        (old["config"], new_config, "moved"),
        (old["data"], new_data, "moved"),
    ]
    for folder in (old["config"], old["data"]):  # only the note is left
        assert [p.name for p in folder.iterdir()] == [locations.MOVED_NOTE]
        assert "litrad" in (folder / locations.MOVED_NOTE).read_text()
    config = new_config / "config.yaml"
    raw = yaml.safe_load(config.read_text())
    assert raw["paths"]["base_dir"] == str(new_data)
    assert raw["paths"]["database"] == "litresearch.sqlite"  # named in the config: kept
    assert (new_config / "config.yaml.bak").is_file()
    base = load_config(config)
    assert base.paths.profiles_dir == new_data / "profiles"
    assert base.backup.dir == new_data / "backups"
    # profile settings (comments kept) and the library's file point to the new place
    settings = (new_data / "profiles" / "ada" / "settings.yaml").read_text()
    new_bib = new_data / "paperpile" / "library.bib"
    assert f"bibtex: {new_bib}" in settings and "# synced by rclone" in settings
    assert "# my settings" in settings
    cfg = profiles.profile_config(base, "ada")
    assert cfg.paths.bibtex == new_bib and cfg.paths.database.name == "litrad.sqlite"
    connection = sqlite3.connect(cfg.paths.database)
    assert {r[0] for r in connection.execute("SELECT source_path FROM library")} == {str(new_bib)}
    connection.close()
    # the desktop log moved with the data folder; a second start changes nothing
    assert desktop.log_file().read_text() == "old log\n"
    assert locations.migrate_user_dirs() == []
    assert locations.leftover_legacy_dirs() == []


def test_existing_new_folders_are_never_overwritten():
    old = legacy_installation()
    new_data = locations.user_data_dir()
    new_data.mkdir(parents=True)
    (new_data / "mine.txt").write_text("new")
    moves = locations.migrate_user_dirs()
    assert {(m.old, m.action) for m in moves} == {
        (old["config"], "moved"),  # no litrad config folder yet
        (old["data"], "kept"),
    }
    assert [p.name for p in new_data.iterdir()] == ["mine.txt"]
    assert (old["data"] / "profiles" / "ada" / "litresearch.sqlite").is_file()
    # the moved config still points to the old data folder, which is still there
    raw = yaml.safe_load((locations.user_config_dir() / "config.yaml").read_text())
    assert raw["paths"]["base_dir"] == str(old["data"])
    assert locations.leftover_legacy_dirs() == [old["data"]]
    result = runner.invoke(cli.app, ["where"])
    assert result.exit_code == 0
    assert "Old litresearch:" in result.output and str(old["data"]) in result.output


def test_another_drive_copies_and_keeps_the_original(monkeypatch):
    old = legacy_installation()
    real_rename = os.rename

    def cross_device(source, target):
        if Path(source) in (old["config"], old["data"]):
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real_rename(source, target)

    monkeypatch.setattr(locations.os, "rename", cross_device)
    moves = locations.migrate_user_dirs()
    assert [m.action for m in moves] == ["copied", "copied"]
    new_data = locations.user_data_dir()
    assert (new_data / "profiles" / "ada" / "litresearch.sqlite").is_file()
    assert (old["data"] / "profiles" / "ada" / "litresearch.sqlite").is_file()  # original
    assert (old["data"] / locations.MOVED_NOTE).is_file()
    assert not list(new_data.parent.glob(".*partial"))
    raw = yaml.safe_load((locations.user_config_dir() / "config.yaml").read_text())
    assert raw["paths"]["base_dir"] == str(new_data)
    assert "copied" in moves[0].message()
    # the next start uses the copy and only reports the original
    assert [m.action for m in locations.migrate_user_dirs()] == ["kept", "kept"]


def test_a_failed_copy_leaves_nothing_half_done(monkeypatch):
    old = legacy_installation()
    monkeypatch.setattr(locations.os, "rename", lambda *a: (_ for _ in ()).throw(OSError("no")))
    with pytest.raises(OSError):
        locations.migrate_user_dirs()
    assert not locations.user_config_dir().exists()
    assert (old["config"] / "config.yaml").is_file()


def test_shared_config_and_data_folder_moves_once(monkeypatch, tmp_path):
    """Windows and macOS: config and data share one per-user folder."""
    old, new = tmp_path / "AppData" / "litresearch", tmp_path / "AppData" / "litrad"
    for name, folder in (
        ("legacy_user_config_dir", old),
        ("legacy_user_data_dir", old),
        ("user_config_dir", new),
        ("user_data_dir", new),
    ):
        monkeypatch.setattr(locations, name, lambda folder=folder: folder)
    locations.first_run_setup(old / "config.yaml", old)
    moves = locations.migrate_user_dirs()
    assert [(m.old, m.new, m.action) for m in moves] == [(old, new, "moved")]
    assert yaml.safe_load((new / "config.yaml").read_text())["paths"]["base_dir"] == str(new)


def test_the_cli_moves_the_folders_on_its_first_start():
    legacy_installation()
    result = runner.invoke(cli.app, ["profile", "list"])
    assert result.exit_code == 0, result.output
    assert "(ada)" in result.output
    assert "Renamed to litrad: moved" in result.stderr
    assert (locations.user_data_dir() / "profiles" / "ada" / "litrad.sqlite").is_file()


def test_the_desktop_app_moves_the_folders_before_it_logs(monkeypatch):
    legacy_installation()
    monkeypatch.setattr(desktop, "_start", lambda *args: 0)
    assert desktop.run(browser=False) == 0
    log = desktop.log_file().read_text()
    assert log.startswith("old log\n") and "Renamed to litrad: moved" in log


def test_moved_path():
    moved = {Path("/home/u/.local/share/litresearch"): Path("/home/u/.local/share/litrad")}
    assert locations.moved_path("/home/u/.local/share/litresearch/x.bib", moved) == str(
        Path("/home/u/.local/share/litrad/x.bib")
    )
    assert locations.moved_path("/home/u/.local/share/litresearch", moved) == str(
        Path("/home/u/.local/share/litrad")
    )
    for unchanged in ("data/x.bib", "/home/u/.local/share/litresearch2/x", "", None, 3):
        assert locations.moved_path(unchanged, moved) is None


# --- database file names ------------------------------------------------------------------------


@pytest.fixture
def base(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        f"paths:\n  bibtex: {tmp_path}/x.bib\n  profiles_dir: {tmp_path}/profiles\n"
        f"backup:\n  dir: {tmp_path}/backups\n"
    )
    return load_config(path)


def test_profile_database_is_renamed_on_first_open(base):
    home = base.paths.profiles_dir / "old"
    home.mkdir(parents=True)
    shutil.copy(COMPAT / FIXTURE / "litresearch.sqlite", home / "litresearch.sqlite")
    (home / "litresearch.sqlite-wal").write_bytes(b"")  # journal files follow the database
    (home / "litresearch.sqlite-shm").write_bytes(b"")
    assert [info.slug for info in profiles.list_profiles(base)] == ["old"]
    assert sorted(p.name for p in home.glob("*.sqlite*")) == [
        "litrad.sqlite",
        "litrad.sqlite-shm",
        "litrad.sqlite-wal",
    ]
    assert profiles.profile_config(base, "old").paths.database == home / "litrad.sqlite"


def test_a_profile_with_both_names_keeps_the_new_database(base):
    home = base.paths.profiles_dir / "both"
    home.mkdir(parents=True)
    for name in ("litresearch.sqlite", "litrad.sqlite"):
        shutil.copy(COMPAT / FIXTURE / "litresearch.sqlite", home / name)
    assert profiles.profile_db(home) == home / "litrad.sqlite"
    assert (home / "litresearch.sqlite").is_file()  # nothing is deleted


def test_a_database_that_cannot_be_renamed_is_used_where_it_is(base, monkeypatch):
    home = base.paths.profiles_dir / "ro"
    home.mkdir(parents=True)
    shutil.copy(COMPAT / FIXTURE / "litresearch.sqlite", home / "litresearch.sqlite")
    (home / "litresearch.sqlite-journal").write_bytes(b"")
    real_replace = os.replace

    def refuse_database(source, target):
        if Path(source).name == "litresearch.sqlite":
            raise PermissionError("read-only")
        return real_replace(source, target)

    monkeypatch.setattr(profiles.os, "replace", refuse_database)
    assert profiles.profile_db(home) == home / "litresearch.sqlite"
    assert sorted(p.name for p in home.iterdir()) == [
        "litresearch.sqlite",
        "litresearch.sqlite-journal",
    ]


def git_installation(tmp_path, database_line: str) -> Path:
    """A git installation from before the rename: config/config.yaml and data/."""
    (tmp_path / "config").mkdir()
    (tmp_path / "data").mkdir()
    config = tmp_path / "config" / "config.yaml"
    config.write_text(f"paths:\n{database_line}  bibtex: data/paperpile/library.bib\n")
    engine = init_db(tmp_path / "data" / "litresearch.sqlite")
    with Session(engine) as session:
        session.add(Paper(title="Old paper", doi="10.1/old", in_library=True))
        session.commit()
    engine.dispose()
    return config


def test_a_configured_database_keeps_its_name(tmp_path):
    config = git_installation(tmp_path, "  database: data/litresearch.sqlite\n")
    base = load_config(config)
    assert base.paths.database == tmp_path / "data" / "litresearch.sqlite"
    assert profiles.legacy_available(base)["library"] == 1
    slug = profiles.adopt_legacy(base, "Kai")
    assert (tmp_path / "data" / "litresearch.sqlite").is_file()  # never renamed
    assert not (tmp_path / "data" / "litrad.sqlite").exists()
    assert profiles.profile_config(base, slug).paths.database.name == "litrad.sqlite"


def test_without_a_database_setting_the_old_default_file_is_used(tmp_path):
    config = git_installation(tmp_path, "")
    assert load_config(config).paths.database == tmp_path / "data" / "litresearch.sqlite"
    (tmp_path / "data" / "litrad.sqlite").write_bytes(b"")
    assert load_config(config).paths.database == tmp_path / "data" / "litrad.sqlite"


def test_vault_notes_folder_of_before_the_rename(tmp_path):
    vault = tmp_path / "vault"
    (vault / "litresearch").mkdir(parents=True)
    config = tmp_path / "config.yaml"
    config.write_text(f"paths:\n  obsidian_vault: {vault}\n")
    assert load_config(config).paths.notes_subfolder == "litresearch"
    config.write_text(f"paths:\n  obsidian_vault: {vault}\n  notes_subfolder: litrad\n")
    assert load_config(config).paths.notes_subfolder == "litrad"  # named: kept
    (vault / "litrad").mkdir()
    config.write_text(f"paths:\n  obsidian_vault: {vault}\n")
    assert load_config(config).paths.notes_subfolder == "litrad"


# --- backup, snapshot and export file names -------------------------------------------------------


def touch(folder: Path, *names: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        (folder / name).write_bytes(b"")


def test_rotation_sorts_old_and_new_names_by_time(tmp_path):
    touch(
        tmp_path,
        "litresearch_20261001_070000.sqlite",
        "litresearch_20261002_070000.sqlite",
        "litrad_20261003_070000.sqlite",
        "litrad_20261004_070000.sqlite",
        "litresearch_backup_20261001_070000.zip",
        "litresearch_progress_20261002_070000.json",
        "litresearch_notes_20261002_070000.zip",
        "litrad_backup_20261003_070000.zip",
        "litrad_backup_20261004_070000.zip",
        "litresearch_profile_kai_20261001_070000.zip",  # saved profiles are kept
    )
    removed = sorted(p.name for p in backup.rotate(tmp_path, keep=2))
    assert removed == [
        "litresearch_20261001_070000.sqlite",
        "litresearch_20261002_070000.sqlite",
        "litresearch_backup_20261001_070000.zip",
        "litresearch_notes_20261002_070000.zip",
        "litresearch_progress_20261002_070000.json",
    ]
    assert (tmp_path / "litresearch_profile_kai_20261001_070000.zip").exists()


def test_new_snapshots_are_not_deleted_before_old_ones(tmp_path):
    # by name, "litrad_" sorts before "litresearch_": the newest would go first
    touch(tmp_path, "litresearch_20261001_070000.sqlite", "litrad_20261009_070000.sqlite")
    assert [p.name for p in backup.rotate(tmp_path, keep=1)] == [
        "litresearch_20261001_070000.sqlite"
    ]


def test_latest_backup_and_saved_files_see_both_names(tmp_path):
    from test_backup import make_cfg

    cfg = make_cfg(tmp_path)
    touch(cfg.backup.dir, "litresearch_backup_20261005_070000.zip")
    assert backup.latest_backup(cfg) == datetime(2026, 10, 5, 7)
    touch(cfg.backup.dir, "litrad_backup_20261006_070000.zip")
    assert backup.latest_backup(cfg) == datetime(2026, 10, 6, 7)
    touch(
        cfg.backup.dir,
        "litresearch_profile_kai_20261001_070000.json",
        "litresearch_export_kai_20261001_070000.zip",
        "litrad_export_kai_20261002_070000.zip",
        "unrelated.zip",
    )
    names = {p.name for p in backup.saved_files(cfg.backup.dir)}
    assert names == {
        "litresearch_backup_20261005_070000.zip",
        "litrad_backup_20261006_070000.zip",
        "litresearch_profile_kai_20261001_070000.json",
        "litresearch_export_kai_20261001_070000.zip",
        "litrad_export_kai_20261002_070000.zip",
    }


def test_new_files_have_new_names(tmp_path):
    from test_backup import PAPERS, add_papers, make_cfg

    cfg = make_cfg(tmp_path)
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        add_papers(session, PAPERS)
        result = backup.run_backup(cfg, session, datetime(2026, 10, 5, 9))
        saved = backup.save_profile_archive(session, cfg, tmp_path / "saved")
    engine.dispose()
    assert result.snapshot.name == "litrad_20261005_090000.sqlite"
    assert result.archive.name == "litrad_backup_20261005_090000.zip"
    assert saved.name.startswith("litrad_profile_")
    assert archive.archive_name("Kai").startswith("litrad_export_kai_")
    manifest = json.loads(zipfile.ZipFile(result.archive).read("manifest.json"))
    assert manifest["format"] == "litrad-export"


def test_an_old_snapshot_restores_with_its_note_files(tmp_path):
    """A backup made by litresearch: snapshot and archive with the old names."""
    from test_backup import make_cfg

    cfg = make_cfg(tmp_path)
    old = make_cfg(tmp_path, "old")
    engine = init_db(old.paths.database)
    with Session(engine) as session:
        paper = Paper(title="Noted paper", doi="10.1/n", bibtex_key="Noted2020")
        session.add(paper)
        session.commit()
        record_reading(session, old, paper, ReadStatus.read, note="my note")
        result = backup.run_backup(old, session, datetime(2026, 10, 5, 9))
    engine.dispose()
    snapshot = result.snapshot.rename(
        result.snapshot.with_name("litresearch_20261005_090000.sqlite")
    )
    result.archive.rename(result.archive.with_name("litresearch_backup_20261005_090000.zip"))
    backup.restore(cfg, snapshot)
    note = notes.notes_dir(cfg) / "Noted2020.md"
    assert note.is_file() and "my note" in note.read_text()


def test_upgrade_copies_rotate_with_both_names(tmp_path):
    names = [
        "litresearch_20261001_070000_before-schema-1-to-2.sqlite",
        "litresearch_20261002_070000_before-schema-1-to-2.sqlite",
        "litrad_20261003_070000_before-schema-1-to-2.sqlite",
    ]
    touch(tmp_path, *names)
    removed = migrations.rotate_upgrade_backups(tmp_path, keep=2)
    assert [p.name for p in removed] == names[:1]


# --- formats --------------------------------------------------------------------------------------


def test_schemas_accept_both_format_names():
    for name, new, old in (
        ("manifest", "litrad-export", "litresearch-export"),
        ("progress", "litrad-progress", "litresearch-progress"),
    ):
        for value in (new, old):
            problems = validate_schema(name, {"format": value}, name)
            assert not [p for p in problems if p.location.endswith(".format")], problems
        wrong = validate_schema(name, {"format": "other"}, name)
        assert [p for p in wrong if p.location.endswith(".format")]


def test_old_progress_and_archive_formats_are_read(base):
    data = json.loads((COMPAT / FIXTURE / "progress.json").read_text())
    assert data["format"] == "litresearch-progress"
    backup.check_file(data)
    read = archive.read_archive(COMPAT / FIXTURE / "export.zip")
    assert read.manifest["format"] == "litresearch-export"
    checked = archive.check(read, base)
    assert checked.report.ok
    assert any("litresearch_id" in (record.meta or {}) for record in checked.notes)
    assert all(record.paper for record in checked.notes if record.meta)


def test_imported_old_notes_get_the_new_key(base):
    result = archive.import_new(base, COMPAT / FIXTURE / "export.zip", "From litresearch")
    folder = notes.notes_dir(profiles.profile_config(base, result.slug))
    texts = [p.read_text() for p in notes.scan(folder)]
    assert texts and all("litrad_id:" in t and "litresearch_id" not in t for t in texts)


def test_new_writers_use_the_new_progress_format(base):
    from litrad.db import init_db as open_db

    cfg = profiles.profile_config(base, profiles.create_profile(base, "Kai"))
    engine = open_db(cfg.paths.database)
    with Session(engine) as session:
        data = backup.export_progress(session)
    engine.dispose()
    assert data["format"] == "litrad-progress"
    backup.check_file(data)
    data_old = dict(data, format="litresearch-progress")  # the checksum covers the format
    assert backup.verify_checksum(data_old) is False


@pytest.fixture
def note_env(tmp_path):
    from test_backup import make_cfg

    cfg = make_cfg(tmp_path)
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        paper = Paper(title="Old note", doi="10.1/a", bibtex_key="Old2020", tags=["evo"])
        session.add(paper)
        session.commit()
        record_reading(session, cfg, paper, ReadStatus.skimmed, note="body", at=AT)
        path = notes.path_for(session, cfg, paper)
        # the file as litresearch wrote it (same values, old names), with a key of yours
        text = path.read_text().replace("litrad_id:", "litresearch_id:")
        text = text.replace("format: litrad-note/1", "format: litresearch-note/1")
        text = text.replace("---\n\n", "aliases: [mine]  # keep\n---\n\n", 1)
        path.write_text(text)
        notes.forget_sync_state(session)
        notes.refresh_paper(session, cfg, paper)  # links the file as it is
        yield cfg, session, paper, path
    engine.dispose()


AT = datetime(2026, 10, 7, 8, 30)


def test_old_note_files_are_not_rewritten_for_the_names(note_env):
    cfg, session, paper, path = note_env
    before = path.read_bytes()
    for _ in range(2):
        assert notes.refresh_paper(session, cfg, paper).actions == []
    notes.refresh_all(session, cfg)
    assert path.read_bytes() == before
    assert b"litresearch_id:" in before


def test_old_note_found_by_its_old_id_after_a_rename(note_env):
    cfg, session, paper, path = note_env
    moved = path.with_name("Renamed.md")
    path.rename(moved)
    state = notes.refresh_paper(session, cfg, paper)
    assert state.path == moved and state.body == "[2026-10-07T08:30:00] body"


def test_old_note_gets_the_new_names_when_updated_anyway(note_env):
    cfg, session, paper, path = note_env
    record_reading(session, cfg, paper, ReadStatus.thorough, rating=5, at=AT)
    text = path.read_text()
    meta = yaml.safe_load(notes.split(text)[0])
    assert meta["litrad_id"] == paper.id and "litresearch_id" not in meta
    assert meta["format"] == "litrad-note/1" and meta["status"] == "thorough"
    assert list(meta)[0] == "litrad_id"  # renamed in place
    assert "aliases: [mine]  # keep" in text


def test_values_hash_is_stable_across_the_rename():
    values = {"litrad_id": 3, "title": "T", "format": notes.FORMAT}
    old = {"litresearch_id": 3, "title": "T", "format": notes.LEGACY_FORMAT}
    assert notes.values_hash(values) == notes.values_hash(old)
    assert notes.note_id({"litresearch_id": 4}) == 4 == notes.note_id({"litrad_id": 4})
    assert notes.note_id({"litrad_id": 5, "litresearch_id": 4}) == 5


# --- web: cookies and branding -------------------------------------------------------------


@pytest.fixture
def client(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        f"paths:\n  bibtex: {tmp_path}/x.bib\n  database: {tmp_path}/legacy.sqlite\n"
        f"  profiles_dir: {tmp_path}/profiles\nbackup:\n  dir: {tmp_path}/backups\n"
    )
    return TestClient(create_app(config))


def test_branding_on_the_pages(client):
    for path in ("/welcome",):
        page = client.get(path).text
        assert "The Literature Radar Game" in page and 'src="/static/favicon.svg"' in page
        assert "📡" not in page
    enter_profile(client, "Kai", adopt=False)
    page = client.get("/").text
    assert '<span class="name">litrad</span>' in page and "The Literature Radar Game" in page
    assert "<title>" in page and "· litrad</title>" in page


def test_old_profile_cookie_is_still_read(client):
    enter_profile(client, "Kai", adopt=False)
    value = client.cookies.get("litrad_profile")
    client.cookies.clear()
    client.cookies.set("litresearch_profile", value)
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 200


def test_old_session_cookie_is_still_read(tmp_path):
    from litrad.web.auth import hash_password, load_secret, make_session

    config = tmp_path / "config.yaml"
    stored = hash_password("correct horse")
    config.write_text(
        f"paths:\n  bibtex: {tmp_path}/x.bib\n  database: {tmp_path}/legacy.sqlite\n"
        f"  profiles_dir: {tmp_path}/profiles\nweb:\n  password_hash: '{stored}'\n"
    )
    client = TestClient(create_app(config))
    assert client.get("/welcome", follow_redirects=False).status_code == 303
    session = make_session(load_secret(tmp_path / "web_secret"), stored)
    client.cookies.set(LEGACY_COOKIE, session)
    assert client.get("/welcome", follow_redirects=False).status_code == 200


def test_stopped_page_shows_the_logo_without_the_server(tmp_path):
    from fastapi.templating import Jinja2Templates
    from markupsafe import Markup

    web = ROOT / "src" / "litrad" / "web"
    templates = Jinja2Templates(directory=web / "templates")
    logo = (web / "static" / "favicon.svg").read_text()
    html = templates.get_template("stopped.html").render(logo_svg=Markup(logo))
    assert "<svg" in html and 'id="rat"' in html and "/static/" not in html


def png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR"
    width, height, depth, colour = struct.unpack(">IIBB", data[16:26])
    assert depth == 8 and colour == 6, "8-bit RGBA"
    return width, height


def test_logo_and_icons():
    static = ROOT / "src" / "litrad" / "web" / "static"
    logo = (static / "favicon.svg").read_text()
    for part in ('id="tile"', 'id="radar"', 'id="rat"', "#f5a524", "#1d2033"):
        assert part in logo  # tile, radar arcs, rat; gold tassel, black mortarboard
    assert "<image" not in logo and "href=" not in logo  # no external or embedded assets
    sizes = {
        "icon-192.png": (192, 192),
        "icon-512.png": (512, 512),
        "icon-maskable-512.png": (512, 512),
        "apple-touch-icon.png": (180, 180),
    }
    for name, size in sizes.items():
        assert png_size(static / name) == size, name


def test_icons_have_transparent_corners_except_full_bleed_ones():
    spec = importlib.util.spec_from_file_location("make_icons", ROOT / "tools" / "make_icons.py")
    make_icons = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(make_icons)
    static = ROOT / "src" / "litrad" / "web" / "static"
    for name, (_size, radius, _) in make_icons.ICONS.items():
        width, height, rows = make_icons.read_png(static / name)
        corner, centre = rows[0][3], rows[height // 2][4 * (width // 2) + 3]
        assert centre == 255, name
        assert corner == (0 if radius else 255), name


def test_icon_variants():
    spec = importlib.util.spec_from_file_location("make_icons", ROOT / "tools" / "make_icons.py")
    make_icons = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(make_icons)
    logo = make_icons.LOGO.read_text()
    maskable = make_icons.variant(logo, 0, 0.74)
    assert 'rx="0"' in maskable and "scale(0.74)" in maskable
    assert maskable.count("<g") == maskable.count("</g>")
    assert make_icons.variant(logo, 16, 1.0) == logo


# --- installer -----------------------------------------------------------------------------------


@pytest.fixture
def install(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("install", ROOT / "install.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    units = tmp_path / "systemd" / "user"
    units.mkdir(parents=True)
    monkeypatch.setattr(module, "SERVICE", units / "litrad.service")
    monkeypatch.setattr(module, "OLD_SERVICE", units / "litresearch.service")
    calls = []

    def fake_run(command, *args, **kwargs):
        calls.append(list(command))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setattr(module, "run", lambda *command: calls.append(list(command)))
    monkeypatch.setattr(module.shutil, "which", lambda name: "/usr/bin/" + name)
    return module, units, calls


def test_service_replaces_the_old_litresearch_service(install):
    module, units, calls = install
    (units / "litresearch.service").write_text("[Unit]\nDescription=litresearch web UI\n")
    module.install_service()
    assert not (units / "litresearch.service").exists()
    unit = (units / "litrad.service").read_text()
    assert "bin/litrad serve" in unit and "litresearch" not in unit
    disable = ["systemctl", "--user", "disable", "--now", "litresearch.service"]
    enable = ["systemctl", "--user", "enable", "--now", "litrad.service"]
    assert disable in calls and enable in calls
    assert calls.index(disable) < calls.index(enable)


def test_service_without_an_old_one(install):
    module, units, calls = install
    module.install_service()
    assert (units / "litrad.service").is_file()
    assert not any("litresearch.service" in c for call in calls for c in call)


def test_remove_service_also_removes_the_old_one(install):
    module, units, calls = install
    (units / "litresearch.service").write_text("[Unit]\n")
    module.remove_service()
    assert not (units / "litresearch.service").exists()


def test_old_package_is_uninstalled_before_installing(install, monkeypatch):
    module, _, calls = install
    module.install_package(Path("/venv/bin/python"), dev=False)
    show = ["/venv/bin/python", "-m", "pip", "show", "--quiet", "litresearch"]
    uninstall = ["/venv/bin/python", "-m", "pip", "uninstall", "--quiet", "--yes", "litresearch"]
    assert show in calls and uninstall in calls
    assert calls.index(uninstall) < next(i for i, c in enumerate(calls) if "--editable" in c)
    calls.clear()
    monkeypatch.setattr(
        module.subprocess, "run", lambda command, *a, **k: subprocess.CompletedProcess(command, 1)
    )
    module.install_package(Path("/venv/bin/python"), dev=False)
    assert not any("uninstall" in call for call in calls)  # nothing old installed


# --- packaging -----------------------------------------------------------------------------


def test_packaging_uses_the_new_names():
    docker = (ROOT / "Dockerfile").read_text()
    assert "useradd --create-home --uid 10001 litrad" in docker
    assert "LITRAD_CONFIG=/data/config.yaml" in docker
    assert 'CMD ["litrad", "serve"' in docker
    workflow = (ROOT / ".github" / "workflows" / "docker.yml").read_text()
    assert "images: ghcr.io/${{ github.repository_owner }}/litrad" in workflow
    release = (ROOT / ".github" / "workflows" / "release.yml").read_text()
    assert "NAME: litrad-${{ needs.test.outputs.version }}-linux-${{ matrix.arch }}.AppImage" in (
        release
    )
    assert "packaging/litrad.spec" in release
    iss = (ROOT / "packaging" / "windows" / "litrad.iss").read_text()
    assert r"DefaultDirName={localappdata}\Programs\litrad" in iss
    assert "05EC8161-C270-4A6F-817F-2014D25A8047" not in iss  # new AppId
    appimage = (ROOT / "tools" / "build_appimage.py").read_text()
    assert "Icon=litrad" in appimage and "Name=litrad" in appimage
    for path in [ROOT / "Dockerfile", ROOT / "packaging" / "litrad.spec", iss_path(), *WORKFLOWS]:
        assert "litresearch" not in path.read_text(), path


def iss_path() -> Path:
    return ROOT / "packaging" / "windows" / "litrad.iss"


WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))


def test_old_editable_install_still_starts_after_git_pull(tmp_path):
    """Before ``install.py`` runs again, .venv/bin/litresearch imports litresearch.cli."""
    script = (
        "import sys\nfrom litresearch.cli import run\n"
        "sys.argv = ['litresearch', 'version']\nrun()\n"
    )
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "src"),  # what the old editable install's .pth adds
        "LITRAD_CONFIG": str(tmp_path / "config.yaml"),
    }
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, env=env, cwd=tmp_path
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == cli.__version__
    assert "run 'python3 install.py'" in result.stderr


def test_the_import_shim_is_not_packaged():
    find = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["setuptools"]
    assert find["packages"]["find"]["exclude"] == ["litresearch*"]
