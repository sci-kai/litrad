"""Long-term use: repeated re-imports, messy exports, progress files and backups over time."""

import json
import sqlite3
import time
from datetime import datetime, timedelta

import pytest
from sqlmodel import Session, func, select

from conftest import FakeApis
from litrad import export, libraries
from litrad.backup import (
    export_progress,
    import_progress,
    restore,
    rotate,
    run_backup,
    snapshot,
)
from litrad.db import Library, LibraryPaper, Paper, Reading, ReadStatus, init_db
from litrad.ingest.formats import read_references
from litrad.ingest.pipeline import import_into
from litrad.reading import record_reading
from litrad.sources.http import CACHE_KEEP, ResponseCache

AT = datetime(2026, 1, 5, 9, 0, 0)


@pytest.fixture
def db(cfg):
    cfg.sources.openalex = False  # these tests are about matching, not enrichment
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        yield cfg, session
    engine.dispose()


def count(session, model) -> int:
    return session.exec(select(func.count()).select_from(model)).one()


def ris_record(title, doi=None, year=2020, key=None) -> str:
    lines = ["TY  - JOUR"]
    if key:
        lines.append(f"ID  - {key}")
    lines += [f"TI  - {title}", "AU  - Doe, Jane", f"PY  - {year}"]
    if doi:
        lines.append(f"DO  - {doi}")
    return "\n".join(lines + ["ER  - "]) + "\n"


def by_title(session, title) -> Paper:
    return session.exec(select(Paper).where(Paper.title == title)).one()


# --- re-importing updated exports ----------------------------------------------------------


@pytest.mark.parametrize("fmt", ["ris", "csl-json"])
def test_reimport_without_citation_keys_keeps_paper_identity(db, make_http, tmp_path, fmt):
    """RIS without ID / CSL without id get keys from their position; a new entry at the
    top of the next export must not turn one paper into another (and move its readings)."""
    cfg, session = db
    http = make_http(FakeApis())

    def write(titles):
        path = tmp_path / ("lib.ris" if fmt == "ris" else "lib.json")
        if fmt == "ris":
            path.write_text("\n".join(ris_record(t, doi=f"10.1/{t}") for t in titles))
        else:
            items = [{"title": t, "DOI": f"10.1/{t}", "type": "article"} for t in titles]
            path.write_text(json.dumps(items))
        return path

    lib = libraries.create(session, "EndNote")
    import_into(cfg, session, http, lib, write(["alpha", "beta"]), "new", use_s2=False)
    alpha = by_title(session, "alpha")
    record_reading(session, cfg, alpha, ReadStatus.read, note="alpha notes", at=AT)
    import_into(cfg, session, http, lib, write(["new", "alpha", "beta"]), "replace", use_s2=False)
    session.expire_all()
    assert count(session, Paper) == 3
    alpha = by_title(session, "alpha")
    assert alpha.doi == "10.1/alpha"
    assert session.get(Reading, alpha.id).notes.endswith("alpha notes")
    assert by_title(session, "new").doi == "10.1/new"
    assert libraries.size(session, lib) == 3


def test_reused_citation_key_for_another_paper(db, make_http, tmp_path):
    """A key that now names a different paper (other DOI) creates that paper instead
    of overwriting the old one."""
    cfg, session = db
    http = make_http(FakeApis())
    path = tmp_path / "lib.bib"
    path.write_text("@article{Smith2020, title={Old paper}, doi={10.1/old}, year=2020}\n")
    lib = libraries.create(session, "Zotero")
    import_into(cfg, session, http, lib, path, "new", use_s2=False)
    old = by_title(session, "Old paper")
    record_reading(session, cfg, old, ReadStatus.read, at=AT)
    path.write_text("@article{Smith2020, title={New paper}, doi={10.1/new}, year=2020}\n")
    report = import_into(cfg, session, http, lib, path, "replace", use_s2=False)
    session.expire_all()
    assert report.added == 1 and report.removed == 1
    old = session.exec(select(Paper).where(Paper.doi == "10.1/old")).one()
    assert old.title == "Old paper" and session.get(Reading, old.id) is not None
    assert not old.in_library
    assert by_title(session, "New paper").in_library


def test_citation_key_still_matches_after_title_fix(db, make_http, tmp_path):
    cfg, session = db
    http = make_http(FakeApis())
    path = tmp_path / "lib.bib"
    path.write_text("@article{K1, title={Tpyo in title}, year=2020}\n")
    lib = libraries.create(session, "P")
    import_into(cfg, session, http, lib, path, "new", use_s2=False)
    first = by_title(session, "Tpyo in title").id
    path.write_text("@article{K1, title={Typo in title}, year=2020}\n")
    report = import_into(cfg, session, http, lib, path, "replace", use_s2=False)
    assert report.updated == 1 and report.added == 0
    assert by_title(session, "Typo in title").id == first and count(session, Paper) == 1


def test_duplicates_within_one_file_without_doi(db, make_http, tmp_path):
    """The same paper twice (by PMID, or by title + year without identifiers) is one paper."""
    cfg, session = db
    path = tmp_path / "dup.bib"
    path.write_text(
        "@article{A1, title={By PMID}, pmid={111}, year=2020}\n"
        "@article{A2, title={By PMID (copy)}, pmid={111}, year=2020}\n"
        "@article{B1, title={Same  title}, year=2019}\n"
        "@article{B2, title={same title}, year=2019}\n"
        "@article{C1, title={Same title}, year=2021}\n"
    )
    lib = libraries.create(session, "P")
    report = import_into(cfg, session, make_http(FakeApis()), lib, path, "new", use_s2=False)
    assert count(session, Paper) == 3  # PMID 111, "same title" 2019, "same title" 2021
    assert len(report.duplicates) == 2 and libraries.size(session, lib) == 3


def test_messy_dois_match_one_paper(db, make_http, tmp_path):
    cfg, session = db
    http = make_http(FakeApis())
    a = libraries.create(session, "A")
    b = libraries.create(session, "B")
    (tmp_path / "a.bib").write_text("@article{x, title={T}, doi={10.1/ABC}}\n")
    (tmp_path / "b.ris").write_text(ris_record("T", doi="https://doi.org/10.1/abc", key="y"))
    (tmp_path / "c.json").write_text(
        json.dumps([{"id": "z", "title": "T", "URL": "http://dx.doi.org/10.1/Abc"}])
    )
    import_into(cfg, session, http, a, tmp_path / "a.bib", "new", use_s2=False)
    import_into(cfg, session, http, b, tmp_path / "b.ris", "new", use_s2=False)
    import_into(cfg, session, http, b, tmp_path / "c.json", "add", use_s2=False)
    assert count(session, Paper) == 1 and count(session, LibraryPaper) == 2


def test_many_reimports_add_and_replace(db, make_http, tmp_path):
    """Repeated add/replace of overlapping exports into two libraries keeps flags right."""
    cfg, session = db
    http = make_http(FakeApis())
    a = libraries.create(session, "A")
    b = libraries.create(session, "B")

    def bib(name, numbers):
        path = tmp_path / name
        path.write_text(
            "".join(
                f"@article{{k{n}, title={{Paper {n}}}, doi={{10.1/{n}}}, year=2020,"
                f" keywords={{{name}/f{n}}}}}\n"
                for n in numbers
            )
        )
        return path

    for _ in range(3):
        import_into(cfg, session, http, a, bib("a.bib", range(0, 6)), "replace", use_s2=False)
        import_into(cfg, session, http, b, bib("b.bib", range(4, 10)), "replace", use_s2=False)
        import_into(cfg, session, http, b, bib("extra.bib", [20]), "add", use_s2=False)
    # paper 5 moves from A to B only; paper 0 leaves everything
    import_into(cfg, session, http, a, bib("a.bib", range(1, 5)), "replace", use_s2=False)
    session.expire_all()
    assert count(session, Paper) == 11
    flags = {p.doi: p for p in session.exec(select(Paper)).all()}
    assert not flags["10.1/0"].in_library and flags["10.1/0"].folders == []
    assert flags["10.1/5"].in_library and flags["10.1/5"].folders == ["b.bib/f5"]
    assert flags["10.1/4"].folders == ["a.bib/f4", "b.bib/f4"]
    assert libraries.size(session, b) == 7  # 4..9 + 20
    libraries.delete(session, b)
    session.commit()
    assert sum(p.in_library for p in session.exec(select(Paper)).all()) == 4
    assert count(session, LibraryPaper) == 4


# --- messy input -------------------------------------------------------------------------


def test_bom_crlf_latex_and_unicode(tmp_path):
    text = (
        "﻿@article{Müller2020,\r\n"
        '  title = {{\\"U}ber {T}umour evolution – α-cells},\r\n'
        '  author = {M{\\"u}ller, Anna and García, José},\r\n'
        "  doi = {DOI: 10.1/ABC},\r\n"
        "  year = {2020},\r\n"
        "  abstract = {" + "long " * 20000 + "},\r\n"
        "}\r\n"
    )
    path = tmp_path / "messy.bib"
    path.write_bytes(text.encode("utf-8"))
    _, entries = read_references(path)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.title.startswith("Über Tumour evolution")
    assert entry.authors[0] == "Müller, Anna" and entry.doi == "10.1/abc"
    assert len(entry.abstract) > 90000
    ris = tmp_path / "messy.ris"
    ris.write_bytes(("﻿" + ris_record("Résumé", doi="10.1/R")).replace("\n", "\r\n").encode())
    _, entries = read_references(ris)
    assert entries[0].title == "Résumé" and entries[0].doi == "10.1/r"


def test_malformed_entry_blocks_the_whole_import(db, make_http, tmp_path):
    cfg, session = db
    path = tmp_path / "lib.bib"
    path.write_text("@article{ok, title={Fine}, doi={10.1/ok}}\n")
    lib = libraries.create(session, "P")
    import_into(cfg, session, make_http(FakeApis()), lib, path, "new", use_s2=False)
    path.write_text('@article{ok, title={Fine}}\n@article{bad, title = "open\n')
    with pytest.raises(ValueError):
        import_into(cfg, session, make_http(FakeApis()), lib, path, "replace", use_s2=False)
    session.rollback()
    assert libraries.size(session, lib) == 1


# --- progress files ----------------------------------------------------------------------


def progress_source(session, cfg):
    papers = [
        Paper(title="Über Tumore", year=2020, in_library=True),
        Paper(title="Editorial", year=2019),
        Paper(title="Editorial", year=2021),
        Paper(title="With DOI", doi="10.1/d"),
    ]
    session.add_all(papers)
    session.commit()
    for number, paper in enumerate(papers):
        record_reading(session, cfg, paper, ReadStatus.read, note=f"n{number}", at=AT)
    return export_progress(session)


def test_progress_round_trip_fresh_and_existing(db, tmp_path):
    cfg, session = db
    data = progress_source(session, cfg)
    for target in ("fresh", "existing"):
        engine = init_db(tmp_path / f"{target}.sqlite")
        with Session(engine) as other:
            if target == "existing":
                other.add_all([Paper(title="Über Tumore", year=2020), Paper(title="Other")])
                other.commit()
            first = import_progress(other, cfg, data)
            second = import_progress(other, cfg, json.loads(json.dumps(data)))
            papers = other.exec(select(Paper)).all()
            expected = 5 if target == "existing" else 4
            assert len(papers) == expected, [p.title for p in papers]
            assert first.readings_added == 4
            assert second.papers_created == 0 and second.readings_added == 0
            assert second.events_added == 0 and second.xp_added == 0
            notes = sorted(r.notes.split()[-1] for r in other.exec(select(Reading)).all())
            assert notes == ["n0", "n1", "n2", "n3"]
        engine.dispose()


# --- backups over time -------------------------------------------------------------------


def test_backups_over_months(db, cfg):
    _, session = db
    cfg.backup.keep = 3
    cfg.backup.dir = cfg.paths.database.parent / "backups"
    session.add(Paper(title="x"))
    session.commit()
    for day in range(10):
        run_backup(cfg, session, moment=AT + timedelta(days=30 * day))
    files = sorted(p.name for p in cfg.backup.dir.iterdir())
    assert len(files) == 6, files
    assert files[-1] == "litrad_backup_20261002_090000.zip"
    assert not [p for p in cfg.backup.dir.iterdir() if p.name.startswith(".")]


def test_rotation_by_time_includes_restore_safety_copies(tmp_path):
    """Saved profiles are never rotated; safety copies of restores age like snapshots."""
    for day in range(1, 6):
        (tmp_path / f"litrad_2026010{day}_070000.sqlite").write_text("x")
        (tmp_path / f"litrad_progress_2026010{day}_070000.json").write_text("{}")
    (tmp_path / "litrad_20260101_080000_before_restore.sqlite").write_text("x")
    (tmp_path / "litrad_20260105_080000_before_restore.sqlite").write_text("x")
    (tmp_path / "litrad_profile_kai_20260101_070000.json").write_text("{}")
    rotate(tmp_path, 2)
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "litrad_20260105_070000.sqlite",
        "litrad_20260105_080000_before_restore.sqlite",
        "litrad_profile_kai_20260101_070000.json",
        "litrad_progress_20260104_070000.json",
        "litrad_progress_20260105_070000.json",
    ]


def test_failed_snapshot_leaves_no_temporary_file(tmp_path):
    broken = tmp_path / "broken.sqlite"
    broken.write_text("not a database" * 100)
    with pytest.raises(sqlite3.DatabaseError):
        snapshot(broken, tmp_path / "backups" / "litrad_20260101_070000.sqlite")
    assert list((tmp_path / "backups").iterdir()) == []


def test_restore_an_old_snapshot_after_schema_changes(db, cfg, tmp_path):
    """A snapshot from before libraries existed is upgraded when it is opened again."""
    _, session = db
    cfg.backup.dir = tmp_path / "backups"
    old = tmp_path / "old.sqlite"
    connection = sqlite3.connect(old)
    connection.executescript(
        "CREATE TABLE paper (id INTEGER PRIMARY KEY, title VARCHAR NOT NULL, doi VARCHAR,"
        " in_library BOOLEAN DEFAULT 0, bibtex_key VARCHAR, authors JSON);"
        "CREATE TABLE reading (paper_id INTEGER PRIMARY KEY, status VARCHAR, rating INTEGER,"
        " notes VARCHAR, updated_at DATETIME);"
        "CREATE TABLE xpevent (id INTEGER PRIMARY KEY, paper_id INTEGER, points INTEGER,"
        " kind VARCHAR, reason VARCHAR, at DATETIME);"
        "INSERT INTO paper (title, doi, in_library, bibtex_key, authors)"
        " VALUES ('Old', '10.1/o', 1, 'O1', '[\"Doe, J\"]');"
    )
    connection.commit()
    connection.close()
    restore(cfg, old)
    engine = init_db(cfg.paths.database)
    with Session(engine) as restored:
        paper = restored.exec(select(Paper)).one()
        assert (
            paper.title == "Old" and paper.folders == []
        )  # column added by init_db, empty not NULL
        library = libraries.migrate(restored, cfg)
        assert library is not None and libraries.size(restored, library) == 1
        assert export.to_bibtex(export.library_items(restored, library)).startswith("@article{O1,")
        assert restored.exec(select(Library)).one().name == "My library"
    engine.dispose()


# --- HTTP cache --------------------------------------------------------------------------


def test_http_cache_drops_answers_nobody_can_use(tmp_path):
    path = tmp_path / "http_cache.sqlite"
    cache = ResponseCache(path)
    cache.put("fresh", {"a": 1})
    cache.put("old", {"b": 2})
    cache._db.execute(
        "UPDATE cache SET stored = ? WHERE key = 'old'",
        (time.time() - CACHE_KEEP.total_seconds() - 60,),
    )
    cache._db.commit()
    cache.close()
    cache = ResponseCache(path)
    assert cache.get("fresh", None) == {"a": 1}
    assert cache.get("old", None) is None
    assert cache._db.execute("SELECT count(*) FROM cache").fetchone()[0] == 1
    cache.close()


def test_http_cache_write_while_locked_is_skipped(tmp_path):
    path = tmp_path / "http_cache.sqlite"
    cache = ResponseCache(path)
    cache._db.close()
    cache._db = sqlite3.connect(path, timeout=0.05)
    other = sqlite3.connect(path)
    other.execute("BEGIN EXCLUSIVE")
    try:
        cache.put("key", {"a": 1})  # another process writes: no crash, just not cached
    finally:
        other.rollback()
        other.close()
    assert cache.get("key", None) is None
    cache.close()


def test_restore_of_a_missing_file_creates_nothing(cfg, tmp_path):
    from litrad.backup import BackupError

    cfg.backup.dir = tmp_path / "backups"
    with pytest.raises(BackupError, match="not a valid"):
        restore(cfg, tmp_path / "typo.sqlite")
    assert not (tmp_path / "typo.sqlite").exists()


# --- automatic backups survive broken settings -------------------------------------------


class _OneRound:
    """Stops the backup loop after its first round."""

    def __init__(self):
        self.waits = 0

    def is_set(self):
        return self.waits > 0

    def wait(self, _seconds):
        self.waits += 1
        return True


def test_backups_continue_with_an_invalid_profile_settings_file(tmp_path):
    from litrad import profiles
    from litrad.backup import backup_all
    from litrad.config import load_config

    path = tmp_path / "config.yaml"
    path.write_text(f"paths:\n  bibtex: x.bib\nbackup:\n  dir: {tmp_path / 'backups'}\n")
    base = load_config(path)
    profiles.create_profile(base, "Good")
    profiles.create_profile(base, "Broken")
    (base.paths.profiles_dir / "broken" / "settings.yaml").write_text("topics: [unclosed\n")
    messages = []
    assert backup_all(base, messages.append) == 0, messages
    for slug in ("good", "broken"):
        assert list((tmp_path / "backups" / slug).glob("litrad_2*.sqlite")), slug
    (base.paths.profiles_dir / "broken" / "settings.yaml").write_text("queue:\n  max_size: 999\n")
    assert backup_all(base, messages.append) == 0, messages


def test_backup_loop_survives_an_invalid_config_file(tmp_path):
    from litrad.backup import backup_loop

    path = tmp_path / "config.yaml"
    path.write_text("paths: [broken\n")
    messages, stop = [], _OneRound()
    backup_loop(path, stop, messages.append)  # logs and waits instead of ending the thread
    assert stop.waits == 1 and "WARNING" in messages[0]


def test_progress_file_with_one_paper_twice(db, tmp_path):
    """Two entries of a file that are the same paper here: one paper, one reading."""
    cfg, session = db
    data = progress_source(session, cfg)
    twin = dict(data["papers"]["p4"], doi=None)  # "With DOI", as known on another machine
    data["papers"]["p99"] = twin
    data["readings"].append(dict(data["readings"][-1], paper="p99", notes="twin note"))
    data["status_events"].append(dict(data["status_events"][-1], paper="p99"))
    engine = init_db(tmp_path / "other.sqlite")
    with Session(engine) as other:
        report = import_progress(other, cfg, data, allow_modified=True)
        assert report.papers_created == 4 and report.papers_matched == 1
        reading = other.exec(select(Reading).join(Paper).where(Paper.doi == "10.1/d")).one()
        assert reading.notes.endswith("twin note") and "n3" in reading.notes
        assert report.events_added == 4
    engine.dispose()
