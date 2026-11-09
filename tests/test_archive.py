"""The "export everything" archive: export, checks, import (new, merge, partial, dry run)."""

import hashlib
import json
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from sqlmodel import Session, select
from typer.testing import CliRunner

import litresearch.cli as cli
from litresearch import archive, examples, libraries, notes, profiles
from litresearch.archive import ArchiveError, export_archive, import_into, import_new
from litresearch.backup import profile_settings
from litresearch.config import load_config
from litresearch.dataformat import DATA_FORMAT_VERSION, load_schema, schema_names
from litresearch.db import (
    Achievement,
    LibraryPaper,
    NoteFile,
    Paper,
    QueueItem,
    Reading,
    ReadStatus,
    Recommendation,
    RecommendationBatch,
    StatusEvent,
    XpEvent,
    init_db,
)
from litresearch.reading import queue_add, record_reading

runner = CliRunner()
AT = datetime(2026, 9, 7, 9, 0, 0)

CONFIG = """\
paths:
  bibtex: {tmp}/x.bib
  database: {tmp}/legacy.sqlite
  cache_dir: {tmp}/cache
  profiles_dir: {tmp}/profiles
backup:
  dir: {tmp}/backups
"""


@pytest.fixture
def config_path(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG.format(tmp=tmp_path))
    return path


@pytest.fixture
def base(config_path):
    return load_config(config_path)


def build_profile(base, name="Source", papers=6) -> str:
    """A profile with something in every table the archive covers."""
    slug = profiles.create_profile(
        base, name, settings={"topics": [{"name": "evo", "queries": ["clonal evolution"]}]}
    )
    cfg = profiles.profile_config(base, slug)
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        stored = []
        for number in range(papers):
            paper = Paper(
                title=f"Paper {number} on clonal evolution",
                doi=f"10.1/{number}" if number % 2 == 0 else None,
                pmid=str(1000 + number) if number % 3 == 0 else None,
                authors=["Doe, Jane", "Roe, R"],
                year=2020 + number % 5,
                pub_date=date(2020 + number % 5, 3, 1),
                venue="Journal",
                abstract=f"Abstract {number}",
                citation_percentile=50.0 + number,
                topics=["Evolution"],
            )
            session.add(paper)
            stored.append(paper)
        session.commit()
        library = libraries.create(session, "My library")
        for number, paper in enumerate(stored[:4]):
            libraries.link(
                session, library, paper, f"Doe{2020 + number}", ["a/b"], [f"tag{number}"]
            )
        side = libraries.create(session, "Side project", use_for_recommendations=False)
        libraries.link(session, side, stored[0], "Doe2020x")
        session.commit()
        libraries.refresh_flags(session)
        session.commit()
        batch = RecommendationBatch(
            week_start=date(2026, 8, 31),
            created_at=AT - timedelta(days=1),
            n_candidates=40,
            funnel={"candidates": 40, "filtered": 3},
            params={"n_total": 5},
        )
        session.add(batch)
        session.commit()
        for rank, paper in enumerate(stored[4:6], start=1):
            session.add(
                Recommendation(
                    batch_id=batch.id,
                    paper_id=paper.id,
                    rank=rank,
                    category="open",
                    score=0.5,
                    breakdown={"similarity": 0.4, "evidence": {"seed": "x"}},
                    reason="similar",
                )
            )
        session.commit()
        queue_add(session, cfg, stored[4], "recommendation")
        queue_add(session, cfg, stored[5])
        queue_add(session, cfg, stored[3])
        record_reading(
            session, cfg, stored[4], ReadStatus.thorough, rating=5, note="x" * 200, at=AT
        )
        record_reading(
            session, cfg, stored[0], ReadStatus.read, 4, "short note", AT + timedelta(days=7)
        )
        record_reading(session, cfg, stored[1], ReadStatus.skimmed, at=AT + timedelta(days=8))
        record_reading(session, cfg, stored[2], ReadStatus.dismissed, at=AT + timedelta(days=9))
        examples.include(session, stored[1], "positive")
        session.commit()
    engine.dispose()
    return slug


def export(base, slug, target: Path) -> Path:
    cfg = profiles.profile_config(base, slug)
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        notes.refresh_quietly(session, cfg)
        settings = (cfg.paths.database.parent / "settings.yaml").read_text("utf-8")
        export_archive(session, cfg, target, settings)
    engine.dispose()
    return target


@pytest.fixture
def exported(base, tmp_path):
    slug = build_profile(base)
    return slug, export(base, slug, tmp_path / "export.zip")


def members(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as handle:
        return {name: handle.read(name) for name in handle.namelist()}


def rewrite(path: Path, target: Path, change, fix_checksums: bool = True) -> Path:
    """A copy of the archive with ``change(files)`` applied (checksums updated)."""
    files = members(path)
    change(files)
    manifest = json.loads(files["manifest.json"])
    if fix_checksums:
        manifest["files"] = {
            name: {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
            for name, data in files.items()
            if name != "manifest.json"
        }
    files["manifest.json"] = json.dumps(manifest).encode()
    with zipfile.ZipFile(target, "w") as handle:
        for name, data in files.items():
            handle.writestr(name, data)
    return target


def edit_json(name: str, edit):
    def change(files):
        data = json.loads(files[name])
        edit(data)
        files[name] = json.dumps(data).encode()

    return change


def normalized(path: Path) -> dict:
    """Archive content without ids, timestamps of the export and checksums."""
    files = members(path)
    papers = json.loads(files["papers.json"])["papers"]
    by_key = {p["key"]: p for p in papers}

    def ident(paper_key):
        if paper_key is None:
            return None
        paper = by_key[paper_key]
        return (paper["title"], paper["doi"])

    def strip(value):
        if isinstance(value, dict):
            out = {}
            for k, v in value.items():
                if k in ("key",):
                    continue
                out[k] = ident(v) if k == "paper" else strip(v)
            return out
        if isinstance(value, list):
            return [strip(v) for v in value]
        return value

    out = {"papers": sorted(json.dumps(strip(p), sort_keys=True) for p in papers)}
    for name in archive.JSON_FILES:
        if name != "papers.json":
            content = strip(json.loads(files[name]))
            if name == "profile.json" and content["profile"]:
                content["profile"].pop("app_version")
            out[name] = content
    for content in out.values():
        if isinstance(content, dict):
            for key_name, value in content.items():
                if isinstance(value, list):
                    content[key_name] = sorted(json.dumps(v, sort_keys=True) for v in value)
    out["notes"] = sorted(
        (n.rsplit("/", 1)[-1], files[n].decode().split("---\n", 2)[-1])
        for n in files
        if n.startswith("notes/")
    )
    out["bib"] = sorted(n for n in files if n.startswith("libraries/"))
    return out


# --- export ------------------------------------------------------------------------------


def test_archive_contains_every_file_with_checksums(exported):
    _, path = exported
    files = members(path)
    manifest = json.loads(files["manifest.json"])
    assert manifest["format"] == "litresearch-export"
    assert manifest["format_version"] == DATA_FORMAT_VERSION
    assert manifest["profile"] == "Source"
    datetime.fromisoformat(manifest["created"])
    for name in (
        "README.md",
        "papers.json",
        "libraries.json",
        "readings.json",
        "status_events.json",
        "xp_ledger.json",
        "badges.json",
        "queue.json",
        "recommendations.json",
        "example_choices.json",
        "profile.json",
        "settings.yaml",
        "reading_log.tsv",
        "xp_ledger.tsv",
        "libraries/my-library.bib",
        "libraries/my-library.json",
        "libraries/side-project.bib",
        "schemas/papers.schema.json",
        "schemas/manifest.schema.json",
    ):
        assert name in files, name
    assert set(manifest["files"]) == set(files) - {"manifest.json"}
    for name, info in manifest["files"].items():
        assert hashlib.sha256(files[name]).hexdigest() == info["sha256"]
    assert manifest["counts"]["readings"] == 4 and manifest["counts"]["notes"] == 2
    assert {n for n in files if n.startswith("notes/")} == {
        "notes/Doe2020.md",
        "notes/doi-10.1-4.md",
    }
    readme = files["README.md"].decode()
    for name in ("papers.json", "xp_ledger.json", "notes/", "schemas/"):
        assert f"`{name}`" in readme
    ledger = json.loads(files["xp_ledger.json"])
    assert ledger["total"] == sum(e["points"] for e in ledger["events"])
    assert ledger["events"][-1]["balance"] == ledger["total"]
    papers = json.loads(files["papers.json"])["papers"]
    assert len(papers) == 6 and all(p["key"].startswith("p") for p in papers)
    assert papers[0]["abstract"] == "Abstract 0"
    batches = json.loads(files["recommendations.json"])["batches"]
    assert batches[0]["items"][0]["breakdown"]["evidence"] == {"seed": "x"}
    assert batches[0]["funnel"] == {"candidates": 40, "filtered": 3}
    csl = json.loads(files["libraries/my-library.json"])
    assert len(csl) == 4 and csl[0]["author"][0] == {"family": "Doe", "given": "Jane"}
    assert files["reading_log.tsv"].decode().splitlines()[0].startswith("title\t")
    assert len(files["xp_ledger.tsv"].decode().splitlines()) == len(ledger["events"]) + 1


def test_every_json_file_matches_its_schema(exported):
    from jsonschema import Draft202012Validator

    _, path = exported
    files = members(path)
    for name, (schema, _, _) in archive.JSON_FILES.items():
        Draft202012Validator(load_schema(schema)).validate(json.loads(files[name]))
        assert json.loads(files[f"schemas/{schema}.schema.json"]) == load_schema(schema)
    Draft202012Validator(load_schema("manifest")).validate(json.loads(files["manifest.json"]))
    for name in schema_names():
        Draft202012Validator.check_schema(load_schema(name))


def test_example_libraries_are_not_exported_but_choices_are(exported):
    _, path = exported
    files = members(path)
    kinds = {lib["kind"] for lib in json.loads(files["libraries.json"])["libraries"]}
    assert kinds <= {"imported", "recommended"}
    choices = json.loads(files["example_choices.json"])["example_choices"]
    assert [(c["kind"], c["action"]) for c in choices] == [("positive", "include")]


# --- round trip ----------------------------------------------------------------------------


def test_round_trip_new_profile(base, exported, tmp_path):
    slug, path = exported
    result = import_new(base, path, "Copy")
    assert result.report.ok and result.slug
    assert result.notes_written == 2
    again = export(base, result.slug, tmp_path / "again.zip")
    first, second = normalized(path), normalized(again)
    first["profile.json"]["profile"]["name"] = "Copy"
    for name in first:
        assert first[name] == second[name], name
    cfg = profiles.profile_config(base, result.slug)
    folder = notes.notes_dir(cfg)
    assert sorted(p.name for p in notes.scan(folder)) == ["Doe2020.md", "doi-10.1-4.md"]
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        rows = session.exec(select(NoteFile)).all()
        assert len(rows) == 2 and not any(r.conflict for r in rows)
        for row in rows:
            note = notes.read_note(folder / row.path)
            assert note.meta["litresearch_id"] == row.paper_id
        report = notes.sync(session, cfg, dry_run=True)
        assert not report.conflicts and not report.orphans
    engine.dispose()
    settings = yaml.safe_load((cfg.paths.database.parent / "settings.yaml").read_text())
    assert settings["topics"][0]["name"] == "evo"


def test_import_into_is_idempotent(base, exported, tmp_path):
    slug, path = exported
    target = profiles.create_profile(base, "Target")
    cfg = profiles.profile_config(base, target)

    def state():
        engine = init_db(cfg.paths.database)
        with Session(engine) as session:
            out = {
                model.__name__: len(session.exec(select(model)).all())
                for model in (
                    Paper,
                    Reading,
                    StatusEvent,
                    XpEvent,
                    Achievement,
                    QueueItem,
                    RecommendationBatch,
                    Recommendation,
                    LibraryPaper,
                    NoteFile,
                )
            }
        engine.dispose()
        return out, sorted(p.name for p in notes.scan(notes.notes_dir(cfg)))

    first = import_into(cfg, path)
    assert first.merge.readings_added == 4 and first.notes_written == 2
    after_first = state()
    second = import_into(cfg, path)
    assert second.merge.readings_added == 0 and second.merge.xp_added == 0
    assert second.merge.papers_created == 0 and second.notes_written == 0
    assert state() == after_first


def test_import_into_matches_existing_papers(base, exported):
    _, path = exported
    target = profiles.create_profile(base, "Target")
    cfg = profiles.profile_config(base, target)
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        session.add(Paper(title="Paper 0 on clonal evolution", doi="10.1/0"))
        session.add(Paper(title="paper 1 ON clonal evolution", year=2021))
        session.commit()
    engine.dispose()
    result = import_into(cfg, path)
    assert result.merge.papers_matched == 2 and result.merge.papers_created == 4


# --- checks ----------------------------------------------------------------------------------


def failing(base, path, *changes, fix=True, partial=False):
    broken = path.with_name("broken.zip")

    def change(files):
        for item in changes:
            item(files)

    rewrite(path, broken, change, fix)
    with pytest.raises(ArchiveError) as caught:
        import_new(base, broken, "Broken", partial=partial)
    assert not (base.paths.profiles_dir / "broken").exists()
    return str(caught.value), caught.value.report


def test_missing_paper_is_reported_by_file_and_record(base, exported):
    _, path = exported

    def remove(data):
        data["papers"] = [p for p in data["papers"] if p["title"] != "Paper 0 on clonal evolution"]

    message, report = failing(base, path, edit_json("papers.json", remove))
    locations = [p.location for p in report.errors]
    assert any(loc.startswith("readings.json[") for loc in locations)
    assert any(loc.startswith("libraries.json[0].papers[") for loc in locations)
    assert any(loc.startswith("notes/Doe2020.md") for loc in locations)
    assert "not in papers.json" in message


def test_wrong_checksum_is_refused(base, exported):
    _, path = exported

    def tamper(files):
        files["readings.json"] = files["readings.json"].replace(b'"rating": 4', b'"rating": 5')

    message, _ = failing(base, path, tamper, fix=False)
    assert "readings.json: checksum mismatch" in message


def test_unknown_badge_is_refused(base, exported):
    _, path = exported
    message, _ = failing(
        base,
        path,
        edit_json(
            "badges.json",
            lambda d: d["badges"].append(
                {"code": "moon_landing", "unlocked_at": "2026-09-08T10:00:00"}
            ),
        ),
    )
    assert "badges.json[1].code: unknown badge 'moon_landing'" in message


def test_ledger_mismatch_is_refused(base, exported):
    _, path = exported

    def more(data):
        data["events"][0]["points"] += 7

    message, _ = failing(base, path, edit_json("xp_ledger.json", more))
    assert "xp_ledger.json[0].balance: running balance" in message
    assert "xp_ledger.json.total: stated total" in message


def test_profile_total_must_match_the_ledger(base, exported):
    _, path = exported
    message, _ = failing(
        base, path, edit_json("profile.json", lambda d: d.update(xp_total=d["xp_total"] + 1))
    )
    assert "profile.json.xp_total" in message


def test_duplicate_key_is_refused(base, exported):
    _, path = exported

    def twice(data):
        data["papers"].append(dict(data["papers"][0], doi=None))

    message, _ = failing(base, path, edit_json("papers.json", twice))
    assert "papers.json[6]: key p1 is used twice (also papers.json[0])" in message


def test_bad_date_is_refused(base, exported):
    _, path = exported

    def bad(data):
        data["status_events"][0]["at"] = "2026-02-30T10:00:00"

    message, _ = failing(base, path, edit_json("status_events.json", bad))
    assert "status_events.json[0].at: '2026-02-30T10:00:00' is not a valid ISO 8601" in message


def test_schema_errors_name_the_record_and_field(base, exported):
    _, path = exported

    def bad(data):
        data["readings"][1]["rating"] = 9

    message, report = failing(base, path, edit_json("readings.json", bad))
    assert "readings.json[1].rating: 9 is greater than the maximum of 5" in message


def test_library_file_must_match_libraries_json(base, exported):
    _, path = exported

    def extra(files):
        files["libraries/my-library.bib"] += b"\n@article{Extra1, title={Extra}}\n"

    message, _ = failing(base, path, extra)
    assert "libraries/my-library.bib: 5 entries, but libraries.json[0] lists 4 papers" in message


def test_unknown_fields_are_ignored_with_a_note(base, exported):
    _, path = exported
    patched = rewrite(
        path,
        path.with_name("extra.zip"),
        edit_json("readings.json", lambda d: [r.update(mood="happy") for r in d["readings"]]),
    )
    result = import_new(base, patched, "Extra", dry_run=True)
    assert result.report.ok
    assert any("readings.json[].mood" in str(n) for n in result.report.notes)


def test_newer_format_is_refused(base, exported):
    _, path = exported
    newer = rewrite(
        path,
        path.with_name("newer.zip"),
        edit_json("manifest.json", lambda d: d.update(format_version="99")),
        fix_checksums=False,
    )
    with pytest.raises(ArchiveError, match="newer litresearch"):
        import_new(base, newer, "Newer")


def test_not_a_zip(base, tmp_path):
    path = tmp_path / "x.zip"
    path.write_text("hello")
    with pytest.raises(ArchiveError, match="not a zip archive"):
        import_new(base, path)


def test_unsafe_names_are_refused(base, tmp_path):
    path = tmp_path / "evil.zip"
    with zipfile.ZipFile(path, "w") as handle:
        handle.writestr("../evil.md", "x")
    with pytest.raises(ArchiveError, match="unsafe file name"):
        import_new(base, path)


# --- all or nothing, partial, dry run ------------------------------------------------------


def test_failure_during_import_into_changes_nothing(base, exported, monkeypatch):
    _, path = exported
    target = profiles.create_profile(base, "Target")
    cfg = profiles.profile_config(base, target)

    def counts():
        engine = init_db(cfg.paths.database)
        with Session(engine) as session:
            out = [len(session.exec(select(m)).all()) for m in (Paper, Reading, XpEvent)]
        engine.dispose()
        return out

    before = counts()

    def explode(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(archive, "_place", explode)
    monkeypatch.setattr(archive, "_stage_notes", explode)
    with pytest.raises(RuntimeError, match="disk full"):
        import_into(cfg, path, backup=False)
    assert counts() == before
    folder = notes.notes_dir(cfg)
    assert list(folder.rglob("*.md")) == []
    assert not any(p.name.startswith(".litresearch-import") for p in folder.iterdir())


def test_failure_during_import_new_leaves_no_profile(base, exported, monkeypatch):
    _, path = exported

    def explode(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(archive, "_place", explode)
    with pytest.raises(RuntimeError):
        import_new(base, path, "Copy")
    assert [i.name for i in profiles.list_profiles(base)] == ["Source"]
    assert not (base.paths.profiles_dir / "copy").exists()


def test_partial_imports_only_the_consistent_records(base, exported):
    _, path = exported

    def remove(data):
        data["papers"] = [p for p in data["papers"] if p["title"] != "Paper 0 on clonal evolution"]

    broken = rewrite(path, path.with_name("partial.zip"), edit_json("papers.json", remove))
    result = import_new(base, broken, "Partial", partial=True)
    assert result.report.ok and result.report.skipped
    skipped = [p.location for p in result.report.skipped]
    assert any(loc.startswith("readings.json[") for loc in skipped)
    assert "notes/Doe2020.md" in skipped
    cfg = profiles.profile_config(base, result.slug)
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        assert len(session.exec(select(Reading)).all()) == 3
        assert session.exec(select(Paper).where(Paper.doi == "10.1/0")).first() is None
    engine.dispose()


def test_dry_run_changes_nothing(base, exported):
    _, path = exported
    before = sorted(str(p) for p in base.paths.profiles_dir.rglob("*"))
    result = import_new(base, path, "Copy", dry_run=True)
    assert result.dry_run and result.merge.readings_added == 4 and result.notes_written == 2
    target = profiles.create_profile(base, "Target")
    cfg = profiles.profile_config(base, target)
    before = sorted(str(p) for p in base.paths.profiles_dir.rglob("*"))
    result = import_into(cfg, path, dry_run=True)
    assert result.merge.readings_added == 4
    assert sorted(str(p) for p in base.paths.profiles_dir.rglob("*")) == before


# --- command line --------------------------------------------------------------------------


def test_cli_export_all_and_import_all(config_path, base, tmp_path):
    build_profile(base)
    target = tmp_path / "out" / "all.zip"
    result = runner.invoke(cli.app, ["export-all", str(target), "--config", str(config_path)])
    assert result.exit_code == 0, result.output
    assert target.exists() and "4 readings" in result.output
    result = runner.invoke(
        cli.app,
        ["import-all", str(target), "--new", "Copy", "--dry-run", "--config", str(config_path)],
    )
    assert result.exit_code == 0, result.output
    assert "Dry run" in result.output
    result = runner.invoke(
        cli.app, ["import-all", str(target), "--new", "Copy", "--config", str(config_path)]
    )
    assert result.exit_code == 0, result.output
    assert {i.name for i in profiles.list_profiles(base)} == {"Source", "Copy"}
    result = runner.invoke(
        cli.app, ["import-all", str(target), "--into", "Copy", "--config", str(config_path)]
    )
    assert result.exit_code == 0, result.output
    assert "Readings added / updated:  0 / 0" in result.output


def test_cli_import_all_reports_problems(config_path, base, tmp_path):
    slug = build_profile(base)
    path = export(base, slug, tmp_path / "a.zip")
    broken = rewrite(
        path,
        tmp_path / "b.zip",
        edit_json("queue.json", lambda d: d["queue"].append(dict(d["queue"][0]))),
    )
    result = runner.invoke(
        cli.app, ["import-all", str(broken), "--new", "X", "--config", str(config_path)]
    )
    assert result.exit_code == 1
    assert "queue.json[2]" in result.output and "queued twice" in result.output


def test_settings_of_the_profile_are_exported(base, exported):
    slug, path = exported
    cfg = profiles.profile_config(base, slug)
    assert yaml.safe_load(members(path)["settings.yaml"]) == profile_settings(cfg)
