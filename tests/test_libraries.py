"""Libraries: formats, imports (new, add, replace), exports, special libraries, UI and CLI."""

import json
import os
import time
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, func, select
from typer.testing import CliRunner

import litresearch.cli as cli
import litresearch.web.app as web
from conftest import SAMPLE_BIB, FakeApis, enter_profile, profile_cfg
from litresearch import export, libraries
from litresearch.backup import export_progress, import_progress
from litresearch.db import IngestRun, Library, LibraryPaper, Paper, Reading, ReadStatus, init_db
from litresearch.ingest.bibtex import BibEntry
from litresearch.ingest.check import check_entries, check_file
from litresearch.ingest.formats import detect_format, parse_csl_json, parse_ris, read_references
from litresearch.ingest.pipeline import import_into, ingest_library, last_ingest, refresh_libraries
from litresearch.reading import queue_add

runner = CliRunner()

RIS = """\
TY  - JOUR
ID  - Smith2020
TI  - A first paper about
      clonal evolution
AU  - Smith, Anna
AU  - Jones, Ben
PY  - 2020/04/26
JO  - Nature
DO  - https://doi.org/10.1/RIS-ONE
AN  - 12345678
KW  - Projects/Evolution
KW  - review
AB  - An abstract.
ER  -

TY  - CHAP
TI  - A chapter without identifiers
AU  - Doe, J
PY  - 2018
ER  -
"""

CSL = [
    {
        "id": "Lee2021",
        "type": "article-journal",
        "title": "A CSL paper",
        "author": [{"family": "Lee", "given": "Kim"}, {"literal": "The Consortium"}],
        "issued": {"date-parts": [[2021, 3]]},
        "container-title": "Cell",
        "DOI": "10.1/CSL",
        "PMID": "999",
        "keyword": "Projects/Imaging, ctDNA",
    },
    {"title": "No id, no year"},
]


def entry(key, **kwargs) -> BibEntry:
    kwargs.setdefault("title", f"Title {key}")
    kwargs.setdefault("entry_type", "article")
    return BibEntry(key=key, **kwargs)


# --- checks -------------------------------------------------------------------------


def test_complete_export_is_ok():
    full = [
        entry(
            str(i),
            authors=["A, B"],
            year=2020,
            doi=f"10.1/{i}",
            abstract="x",
            folders=["F/G"],
            journal="J",
            pmid="1",
            url="u",
        )
        for i in range(5)
    ]
    result = check_entries(full)
    assert result.ok and [f.level for f in result.findings] == ["ok"]
    assert all(share == 1.0 for share in result.coverage.values())


def test_missing_fields_give_specific_warnings():
    sparse = [entry(str(i), authors=["A, B"], year=2020) for i in range(4)]
    sparse[0].doi = "10.1/x"
    result = check_entries(sparse, duplicate_keys=["dup1"])
    messages = " | ".join(f.message for f in result.findings)
    assert result.ok  # warnings only
    assert "DOI: only 25%" in messages
    assert "Abstract: missing in all entries. Include it in the export" in messages
    assert "Folders / labels (keywords): missing in all entries" in messages
    assert "duplicate citation key" in messages and "dup1" in messages
    assert "PMID" not in messages  # optional fields never warn


def test_errors_block_imports(tmp_path):
    assert not check_entries([]).ok
    assert not check_entries([entry("a", title=""), entry("b", title="")]).ok
    broken = tmp_path / "broken.bib"
    broken.write_text('@ARTICLE{x,\n  title = "unterminated\n')
    result = check_file(broken)
    assert not result.ok and "Not valid BibTeX" in result.findings[0].message
    assert "Cannot read" in check_file(tmp_path / "missing.bib").findings[0].message
    binary = tmp_path / "binary.bib"
    binary.write_bytes(b"\xff\xfe\x00\x00garbage")
    assert not check_file(binary).ok
    truncated = tmp_path / "cut.ris"
    truncated.write_text("TY  - JOUR\nTI  - no end\n")
    assert "Not valid RIS: The last RIS record has no" in check_file(truncated).findings[0].message
    bad_json = tmp_path / "x.json"
    bad_json.write_text('{"items": 3}')
    assert "Not valid CSL-JSON" in check_file(bad_json).findings[0].message


def test_the_sample_export_passes():
    result = check_file(SAMPLE_BIB, ["Import Mendeley"])
    assert result.ok and result.entries == 7 and result.format == "bibtex"


# --- formats -------------------------------------------------------------------------


def test_ris(tmp_path):
    entries = parse_ris(RIS, {"review"})
    first, second = entries
    assert first.key == "Smith2020" and first.title == "A first paper about clonal evolution"
    assert first.authors == ["Smith, Anna", "Jones, Ben"] and first.journal == "Nature"
    assert first.doi == "10.1/ris-one" and first.pmid == "12345678"
    assert first.pub_date.isoformat() == "2020-04-26" and first.year == 2020
    assert first.folders == ["Projects/Evolution"] and first.tags == []  # "review" ignored
    assert second.key == "ris-2" and second.entry_type == "incollection" and second.doi is None
    path = tmp_path / "export.txt"  # RIS without the .ris extension
    path.write_text(RIS)
    assert detect_format(path) == "ris"
    duplicates = []
    parse_ris(RIS + RIS.replace("ris-2", "x"), set(), duplicates)
    assert duplicates == ["Smith2020"]


def test_csl_json(tmp_path):
    first, second = parse_csl_json(json.dumps(CSL))
    assert first.key == "Lee2021" and first.authors == ["Lee, Kim", "The Consortium"]
    assert first.doi == "10.1/csl" and first.pmid == "999" and first.journal == "Cell"
    assert first.year == 2021 and first.pub_date.isoformat() == "2021-03-01"
    assert first.folders == ["Projects/Imaging"] and first.tags == ["ctDNA"]
    assert second.key == "csl-2" and second.year is None
    path = tmp_path / "zotero.json"
    path.write_text(json.dumps({"items": CSL}))
    assert read_references(path)[0] == "csl-json"


# --- libraries -----------------------------------------------------------------------


@pytest.fixture
def db(cfg):
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        yield cfg, session
    engine.dispose()


def count(session, model) -> int:
    return session.exec(select(func.count()).select_from(model)).one()


def test_names(db):
    _, session = db
    libraries.create(session, "Paperpile")
    for name, message in [
        ("", "name"),
        ("x" * 81, "at most 80"),
        ("PAPERPILE", "already"),
        ("Reading queue", "name of your queue"),
    ]:
        with pytest.raises(libraries.LibraryError, match=message):
            libraries.create(session, name)
    other = libraries.create(session, "Zotero")
    with pytest.raises(libraries.LibraryError, match="already"):
        libraries.update(session, other, name="paperpile")
    assert libraries.find(session, "zotero").id == other.id
    assert libraries.find(session, str(other.id)).id == other.id


def test_import_modes(db, make_http, tmp_path):
    cfg, session = db
    http = make_http(FakeApis())
    pp = libraries.create(session, "Paperpile", "everything")
    report = import_into(cfg, session, http, pp, SAMPLE_BIB, "new", use_s2=False)
    assert report.added == 7 and report.library == "Paperpile" and pp.source_path
    assert libraries.size(session, pp) == 7 and count(session, Paper) == 7
    # a second library with an overlapping export: no duplicate papers
    ris = tmp_path / "zotero.ris"
    ris.write_text(export.to_ris(export.library_items(session, pp)[:3]) + RIS)
    zot = libraries.create(session, "Zotero")
    import_into(cfg, session, http, zot, ris, "new", use_s2=False)
    assert libraries.size(session, zot) == 5 and count(session, Paper) == 9
    # add keeps what is there; replace makes the library equal to the file
    small = tmp_path / "small.ris"
    small.write_text(RIS.split("\n\nTY  - CHAP")[0] + "\n")
    report = import_into(cfg, session, http, zot, small, "add", use_s2=False)
    assert report.updated == 1 and report.removed == 0 and libraries.size(session, zot) == 5
    assert zot.source_path == str(ris.resolve())  # "add" does not change the library's file
    report = import_into(cfg, session, http, zot, small, "replace", use_s2=False)
    assert report.removed == 4 and libraries.size(session, zot) == 1
    assert zot.source_path == str(small.resolve())
    # papers that left Zotero but are in Paperpile stay "in your library"
    papers = session.exec(select(Paper)).all()
    assert sum(p.in_library for p in papers) == 8  # 7 Paperpile + Smith2020
    chapter = session.exec(
        select(Paper).where(Paper.title == "A chapter without identifiers")
    ).one()
    assert not chapter.in_library and chapter.folders == []
    runs = session.exec(select(IngestRun).where(IngestRun.library_id == zot.id)).all()
    assert [r.mode for r in runs] == ["new", "add", "replace"] and runs[0].format == "ris"


def test_delete_keeps_papers_elsewhere(db, make_http):
    cfg, session = db
    http = make_http(FakeApis())
    a = libraries.create(session, "A")
    b = libraries.create(session, "B")
    import_into(cfg, session, http, a, SAMPLE_BIB, "new", use_s2=False)
    import_into(cfg, session, http, b, SAMPLE_BIB, "new", use_s2=False)
    assert libraries.delete(session, a) == 7
    session.commit()
    assert all(p.in_library for p in session.exec(select(Paper)).all())
    assert libraries.delete(session, b) == 7
    assert not any(p.in_library for p in session.exec(select(Paper)).all())
    assert count(session, Paper) == 7 and count(session, LibraryPaper) == 0


def test_recommended_library_only_for_queued_recommendations(db):
    cfg, session = db
    picked, manual = Paper(title="Picked"), Paper(title="Manual")
    session.add_all([picked, manual])
    session.commit()
    queue_add(session, cfg, manual, "manual")
    assert session.exec(select(Library)).first() is None
    queue_add(session, cfg, picked, "recommendation")
    rec = session.exec(select(Library).where(Library.kind == "recommended")).one()
    assert rec.name == "Recommended" and not rec.use_for_recommendations
    assert [p.title for p, _ in libraries.members(session, rec)] == ["Picked"]
    assert not picked.in_library  # not an imported library


def test_libraries_for_recommendations(db, make_http):
    cfg, session = db
    lib = libraries.create(session, "Side project")
    import_into(cfg, session, make_http(FakeApis()), lib, SAMPLE_BIB, "new", use_s2=False)
    assert len(libraries.recommendation_library(session)) == 7
    libraries.update(session, lib, use_for_recommendations=False)
    assert libraries.recommendation_library(session) == []


def test_migration_of_older_databases(cfg, make_http):
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        ingest_library(cfg, session, make_http(FakeApis()), use_s2=False)
        # simulate a database from before libraries existed
        for model in (LibraryPaper, Library):
            for row in session.exec(select(model)).all():
                session.delete(row)
        session.commit()
        assert libraries.recommendation_library(session)  # falls back to in_library
        library = libraries.migrate(session, cfg)
        assert library.name == "My library" and libraries.size(session, library) == 7
        assert library.source_path == str(cfg.paths.bibtex.resolve())
        assert last_ingest(session, library) is not None
        assert libraries.migrate(session, cfg) is None  # only once
        member = libraries.members(session, library)[0][1]
        assert member.citation_key
    engine.dispose()


def test_refresh_from_files(db, make_http, tmp_path):
    cfg, session = db
    http = make_http(FakeApis())
    path = tmp_path / "synced.bib"
    path.write_bytes(SAMPLE_BIB.read_bytes())
    lib = libraries.create(session, "Synced")
    import_into(cfg, session, http, lib, path, "new", use_s2=False)
    gone = libraries.create(session, "Gone")
    gone.source_path = str(tmp_path / "missing.bib")
    broken = libraries.create(session, "Broken")
    broken.source_path = str(tmp_path / "broken.bib")
    (tmp_path / "broken.bib").write_text('@ARTICLE{x, title = "open')
    session.commit()
    results = {lib.name: r for lib, r in refresh_libraries(cfg, session, http, use_s2=False)}
    assert results["Synced"].updated == 7
    assert "does not exist" in results["Gone"] and "not imported" in results["Broken"]


# --- exports -----------------------------------------------------------------------------


def test_exports_round_trip(db, make_http, tmp_path):
    cfg, session = db
    lib = libraries.create(session, "Paperpile")
    import_into(cfg, session, make_http(FakeApis()), lib, SAMPLE_BIB, "new", use_s2=False)
    paper = libraries.members(session, lib)[0][0]
    session.add(Reading(paper_id=paper.id, status=ReadStatus.read, rating=4))
    session.commit()
    items = export.library_items(session, lib)
    for fmt in ("bibtex", "ris"):
        text = export.render(items, fmt, with_status=True)
        path = tmp_path / f"out.{'bib' if fmt == 'bibtex' else 'ris'}"
        path.write_text(text)
        _, back = read_references(path)
        assert len(back) == 7
        assert {e.doi for e in back} == {p.doi for p, _ in libraries.members(session, lib)}
        assert {e.key for e in back} == {m.citation_key for _, m in libraries.members(session, lib)}
        tags = {t for e in back for t in e.tags}
        assert {"litresearch-read", "litresearch-rating-4"} <= tags
        assert any("/" in f for e in back for f in e.folders)  # folders survive
    plain = export.render(items, "bibtex")
    assert "litresearch-read" not in plain
    assert export.file_name("Paperpile – cancer", "ris", datetime(2026, 10, 14)) == (
        "paperpile-cancer_20261014.ris"
    )
    with pytest.raises(ValueError):
        export.render(items, "endnote-xml")


def test_export_without_keys_and_with_braces(db):
    _, session = db
    papers = [Paper(title="Odd {title}", authors=["Müller, A"], year=2020), Paper(title="B")]
    session.add_all(papers)
    session.commit()
    text = export.to_bibtex(export.paper_items(session, papers))
    assert "@article{Mller2020," in text and "title = {Odd title}" in text
    assert "@article{paper," in text


# --- web ------------------------------------------------------------------------------


@pytest.fixture
def site(tmp_path, monkeypatch, make_http):
    path = tmp_path / "config.yaml"
    path.write_text(
        f"paths:\n  bibtex: x.bib\n  cache_dir: {tmp_path / 'cache'}\n"
        "sources:\n  semanticscholar: false\n"
    )
    apis = FakeApis()
    monkeypatch.setattr(web, "make_http", lambda cfg: make_http(apis))
    client = TestClient(web.create_app(path))
    enter_profile(client, "Ada", adopt=False)
    yield client, path
    client.close()


def upload(client, content: bytes, filename="export.bib", action="import", **fields):
    data = {"action": action, "target": "new", "name": "Paperpile", **fields}
    return client.post(
        "/library/import", data=data, files={"file": (filename, content)}, follow_redirects=True
    )


def test_overview_and_first_import(site):
    client, path = site
    page = client.get("/library").text
    assert "Import your first library" in page and "Reading queue" in page
    assert "/docs/1-setup#what-an-export-must-contain" in page
    form = client.get("/library/import").text
    assert "A new library" in form and "An existing library" not in form
    assert "/docs/1-setup#what-an-export-must-contain" in form
    checked = upload(client, SAMPLE_BIB.read_bytes(), action="check").text
    assert "Check of “export.bib”: 7 entries (BibTeX)" in checked
    assert "Nothing was changed yet" in checked
    assert "Import your first library" in client.get("/library").text
    done = upload(client, SAMPLE_BIB.read_bytes(), description="from Paperpile")
    assert "Paperpile: 7 entries imported (7 new)" in done.text
    # ids 1 and 2: the example lists, created when the overview was shown
    assert done.url.path == "/library/3"
    assert "from Paperpile" in done.text and "<b>7</b> papers" in done.text
    overview = client.get("/library").text
    assert "Paperpile" in overview and "🎯" in overview
    # the upload is kept as the library's file
    files = list((profile_cfg(path).paths.database.parent / "libraries").glob("3_paperpile.bib"))
    assert files and files[0].read_bytes() == SAMPLE_BIB.read_bytes()


def test_import_problems(site):
    client, _ = site
    assert "Give the new library a name" in upload(client, SAMPLE_BIB.read_bytes(), name="").text
    page = upload(client, b"@ARTICLE{x, title = {never closed").text
    assert "Not valid BibTeX" in page and "Nothing was imported" in page
    page = client.post("/library/import", data={"action": "import"}, follow_redirects=True).text
    assert "Choose an export file" in page
    upload(client, SAMPLE_BIB.read_bytes())
    page = upload(client, SAMPLE_BIB.read_bytes()).text  # same name again
    assert "There is already a library called" in page
    page = upload(client, SAMPLE_BIB.read_bytes(), target="existing", library_id="99").text
    assert "Choose the library to import into" in page


def test_import_into_existing_library(site):
    client, _ = site
    upload(client, SAMPLE_BIB.read_bytes())
    form = client.get("/library/import?to=1").text
    assert "An existing library" in form and 'value="existing" checked' in form
    done = upload(
        client, RIS.encode(), filename="new.ris", target="existing", library_id="1", mode="add"
    )
    assert "Paperpile: 2 entries imported (2 new)" in done.text and "<b>9</b> papers" in done.text
    smaller = SAMPLE_BIB.read_text().split("@INCOLLECTION")[0].encode()
    done = upload(client, smaller, target="existing", library_id="1", mode="replace")
    assert "removed" in done.text and "<b>6</b> papers" in done.text


def test_library_page_actions(site):
    client, path = site
    upload(client, SAMPLE_BIB.read_bytes())
    page = client.get("/library/1?q=collective").text
    assert "A collective route" in page and "Cancer cell states" not in page
    folder = client.get("/library/1?folder=01 Biology / Medicine/Cancer").text
    assert "A collective route" in folder and "Hofmann" not in folder
    client.post("/queue/add", data={"ref": "Cheung2016-vl", "source": "library"})
    client.post("/papers/2/read", data={"level": "read"})
    assert "A collective route" in client.get("/library/1?status=read").text
    assert "A collective route" not in client.get("/library/1?status=unread").text
    # edit, export, update from file, delete
    client.post("/library/1/edit", data={"name": "PP", "description": "renamed"})
    page = client.get("/library/1").text
    assert "PP" in page and "renamed" in page and "not used for recommendations" in page
    download = client.get("/library/1/export?format=ris&status=true")
    assert download.headers["content-disposition"].startswith("attachment; filename=pp_")
    assert "TY  - " in download.text and "litresearch-read" in download.text
    assert (
        "Unknown export format"
        in client.get("/library/1/export?format=x", follow_redirects=True).text
    )
    assert "7 entries imported" in client.post("/library/1/reimport", follow_redirects=True).text
    assert (
        "Type the exact library name"
        in client.post(
            "/library/1/delete", data={"confirm_name": "nope"}, follow_redirects=True
        ).text
    )
    gone = client.post("/library/1/delete", data={"confirm_name": "pp"}, follow_redirects=True)
    assert "Deleted the library PP (7 papers)" in gone.text
    assert "That library does not exist" in client.get("/library/1", follow_redirects=True).text


def test_stale_and_changed_file_notices(site):
    client, path = site
    upload(client, SAMPLE_BIB.read_bytes())
    engine = init_db(profile_cfg(path).paths.database)
    with Session(engine) as session:
        run = session.exec(select(IngestRun)).one()
        run.at = datetime.now() - timedelta(days=40)
        session.add(run)
        source = session.get(Library, 1).source_path
        session.commit()
    engine.dispose()
    future = time.time() + 5
    os.utime(source, (future, future))  # e.g. rclone pulled a new export
    page = client.get("/library/1").text
    assert "Last updated 40 days ago" in page and "file changed after the last import" in page
    assert "40 days ago" in client.get("/library").text


def test_queue_and_recommended_libraries(site):
    client, _ = site
    upload(client, SAMPLE_BIB.read_bytes())
    for ref, source in (("#1", "recommendation"), ("#2", "library"), ("#3", "manual")):
        client.post("/queue/add", data={"ref": ref, "source": source})
    overview = client.get("/library").text
    assert "<b>3</b>/5 papers" in overview and "✨ Recommended" in overview
    queue = client.get("/library/queue/export?format=bibtex")
    assert queue.text.count("@") == 3 and "reading-queue_" in queue.headers["content-disposition"]
    client.post("/papers/1/read", data={"level": "read"})  # leaves the queue ...
    assert client.get("/library/queue/export").text.count("@") == 2
    recommended = client.get("/library/2").text  # ... but stays in Recommended
    assert "Recommended" in recommended and "<b>1</b> paper ·" in recommended
    client.post("/library/2/remove/1")
    assert "This library is empty" in client.get("/library/2").text


def test_old_addresses(site):
    client, _ = site
    assert client.get("/paperpile", follow_redirects=False).headers["location"] == "/library"
    moved = client.get("/library?q=clonal", follow_redirects=False)
    assert moved.headers["location"] == "/search?q=clonal"
    nav = client.get("/queue").text
    assert "Libraries" in nav and 'href="/search"' in nav and "Paperpile library" not in nav


# --- profile files and the command line -------------------------------------------------


def test_profile_files_keep_libraries(db, make_http, tmp_path):
    cfg, session = db
    lib = libraries.create(session, "Paperpile", "mine")
    import_into(cfg, session, make_http(FakeApis()), lib, SAMPLE_BIB, "new", use_s2=False)
    libraries.update(session, lib, use_for_recommendations=False)
    session.commit()
    data = export_progress(session)
    assert data["libraries"][0]["name"] == "Paperpile"
    assert len(data["libraries"][0]["papers"]) == 7 and "source_path" not in data["libraries"][0]
    other = init_db(tmp_path / "other.sqlite")
    with Session(other) as fresh:
        report = import_progress(fresh, cfg, data)
        assert report.libraries_added == 1 and report.papers_created == 7
        copy = fresh.exec(select(Library)).one()
        assert copy.description == "mine" and not copy.use_for_recommendations
        assert libraries.size(fresh, copy) == 7
        assert all(p.in_library for p in fresh.exec(select(Paper)).all())
        assert import_progress(fresh, cfg, data).libraries_added == 0  # idempotent
        assert libraries.size(fresh, copy) == 7
    other.dispose()


@pytest.fixture
def plain_console(monkeypatch):
    from rich.console import Console

    import litresearch.cli_library as cli_library

    console = Console(color_system=None, width=300, highlight=False)
    monkeypatch.setattr(cli, "console", console)
    monkeypatch.setattr(cli_library, "console", console)


def test_cli_libraries(tmp_path, plain_console, monkeypatch, make_http):
    import litresearch.cli_library as cli_library

    apis = FakeApis()
    monkeypatch.setattr(cli_library, "make_http", lambda cfg: make_http(apis))
    monkeypatch.setattr(cli, "make_http", lambda cfg: make_http(apis))
    path = tmp_path / "config.yaml"
    path.write_text(f"paths:\n  bibtex: x.bib\n  cache_dir: {tmp_path / 'cache'}\n")
    c = ["-c", str(path)]
    runner.invoke(cli.app, ["profile", "create", "Ada", "--yes", *c])
    assert "No libraries yet" in runner.invoke(cli.app, ["library", "list", *c]).stdout
    assert runner.invoke(cli.app, ["ingest", *c]).exit_code == 1  # nothing to update yet
    result = runner.invoke(cli.app, ["library", "import", str(SAMPLE_BIB), *c])
    assert result.exit_code == 1 and "either --new NAME or --to" in result.stdout
    result = runner.invoke(
        cli.app, ["library", "import", str(SAMPLE_BIB), "--new", "Paperpile", "--no-s2", *c]
    )
    assert result.exit_code == 0 and "Entries:               7" in result.stdout
    ris = tmp_path / "more.ris"
    ris.write_text(RIS)
    assert "(RIS)" in runner.invoke(cli.app, ["library", "import", str(ris), "--check", *c]).stdout
    result = runner.invoke(cli.app, ["library", "import", str(ris), "--to", "paperpile", *c])
    assert result.exit_code == 0 and "added / updated:     2 / 0" in result.stdout
    listing = runner.invoke(cli.app, ["library", "list", *c]).stdout
    assert "Paperpile" in listing and "9 papers" in listing and "Reading queue" in listing
    out = tmp_path / "pp.bib"
    result = runner.invoke(cli.app, ["library", "export", "Paperpile", str(out), "--status", *c])
    assert result.exit_code == 0 and out.read_text().count("@") == 9
    assert runner.invoke(cli.app, ["library", "export", "queue", str(out), *c]).exit_code == 0
    assert out.read_text() == ""
    assert "Library:               Paperpile" in runner.invoke(cli.app, ["ingest", *c]).stdout
    edit = ["library", "edit", "Paperpile", "--name", "PP", "--no-recommendations", *c]
    assert runner.invoke(cli.app, edit).exit_code == 0
    assert "PP" in runner.invoke(cli.app, ["status", *c]).stdout
    result = runner.invoke(cli.app, ["library", "delete", "PP", "--yes", *c])
    # `ingest` replaced the library with its file again: the 2 added papers left
    assert result.exit_code == 0 and "7 papers" in result.stdout
    assert runner.invoke(cli.app, ["library", "delete", "PP", "--yes", *c]).exit_code == 1


def test_cli_check_bib(tmp_path, plain_console):
    path = tmp_path / "config.yaml"
    path.write_text(f"paths:\n  bibtex: {SAMPLE_BIB}\n")
    result = runner.invoke(cli.app, ["check-bib", "-c", str(path)])
    assert result.exit_code == 0 and "7 entries (BibTeX)" in result.stdout
    sparse = tmp_path / "sparse.bib"
    sparse.write_text('@ARTICLE{a, title = "Only a title"}\n')
    result = runner.invoke(cli.app, ["check-bib", str(sparse), "-c", str(path)])
    assert result.exit_code == 0 and "WARNING" in result.stdout and "How to export" in result.stdout
    ris = tmp_path / "x.ris"
    ris.write_text(RIS)
    assert "(RIS)" in runner.invoke(cli.app, ["check-bib", str(ris), "-c", str(path)]).stdout
    empty = tmp_path / "empty.bib"
    empty.write_text("% nothing here\n")
    result = runner.invoke(cli.app, ["check-bib", str(empty), "-c", str(path)])
    assert result.exit_code == 1 and "ERROR" in result.stdout


def test_refresh_flags_handles_many_ids(db):
    cfg, session = db
    lib = libraries.create(session, "Big", "")
    papers = [Paper(title=f"Paper {i}") for i in range(1200)]
    session.add_all(papers)
    session.flush()
    for paper in papers:
        libraries.link(session, lib, paper)
    libraries.refresh_flags(session, {p.id for p in papers})
    assert all(p.in_library for p in papers)
    for paper in papers:
        paper.in_library = False
        session.add(paper)
    session.flush()
    libraries.refresh_flags(session)  # every paper in a library
    assert all(p.in_library for p in papers)
