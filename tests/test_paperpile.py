import os
import time
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select
from typer.testing import CliRunner

import litresearch.cli as cli
import litresearch.web.app as web
from conftest import SAMPLE_BIB, FakeApis, enter_profile, profile_cfg
from litresearch.db import IngestRun, init_db
from litresearch.ingest.bibtex import BibEntry
from litresearch.ingest.check import check_bibtex, check_entries
from litresearch.ingest.pipeline import ingest_library, last_ingest

runner = CliRunner()


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
    assert "Abstract: missing in all entries. Include it in the Paperpile BibTeX export" in messages
    assert "Folders / labels (keywords): missing in all entries" in messages
    assert "duplicate citation key" in messages and "dup1" in messages
    assert "PMID" not in messages  # optional fields never warn
    assert result.coverage["doi"] == 0.25


def test_errors_block_imports(tmp_path):
    assert not check_entries([]).ok
    no_titles = [entry("a", title=""), entry("b", title="")]
    assert not check_entries(no_titles).ok
    broken = tmp_path / "broken.bib"
    broken.write_text('@ARTICLE{x,\n  title = "unterminated\n')
    result = check_bibtex(broken)
    assert not result.ok and "Not valid BibTeX" in result.findings[0].message
    missing = check_bibtex(tmp_path / "missing.bib")
    assert not missing.ok and "Cannot read" in missing.findings[0].message
    binary = tmp_path / "binary.bib"
    binary.write_bytes(b"\xff\xfe\x00\x00garbage")
    assert not check_bibtex(binary).ok


def test_the_sample_export_passes():
    result = check_bibtex(SAMPLE_BIB, ["Import Mendeley"])
    assert result.ok and result.entries == 7


# --- import history ------------------------------------------------------------------


def test_ingest_records_a_run(cfg, make_http):
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        assert last_ingest(session) is None
        report = ingest_library(cfg, session, make_http(FakeApis()), use_s2=False, source="upload")
        run = last_ingest(session)
        assert run.source == "upload" and run.entries == report.entries == 7
        assert run.added == 7 and len(run.sha256) == 64 and run.size > 0
        assert run.check["entries"] == 7 and "doi" in run.check["coverage"]
        ingest_library(cfg, session, make_http(FakeApis()), use_s2=False)
        assert len(session.exec(select(IngestRun)).all()) == 2
        assert last_ingest(session).updated == 7
    engine.dispose()


# --- web page ---------------------------------------------------------------------------


@pytest.fixture
def site(tmp_path, monkeypatch, make_http):
    bib = tmp_path / "library.bib"
    path = tmp_path / "config.yaml"
    path.write_text(
        f"paths:\n  bibtex: {bib}\n  database: {tmp_path / 'db.sqlite'}\n"
        f"  cache_dir: {tmp_path / 'cache'}\nbackup:\n  dir: {tmp_path / 'backups'}\n"
        "sources:\n  semanticscholar: false\n"
    )
    apis = FakeApis()
    monkeypatch.setattr(web, "make_http", lambda cfg: make_http(apis))
    client = TestClient(web.create_app(path))
    enter_profile(client, "Ada", adopt=False)
    cfg = profile_cfg(path)
    # a new profile keeps its export in its own folder
    assert cfg.paths.bibtex.resolve() == tmp_path / "data/profiles/ada/library.bib"
    yield client, cfg.paths.bibtex, cfg.paths.database
    client.close()


def upload(client, content: bytes, action: str, name="export.bib"):
    return client.post(
        "/paperpile/upload",
        data={"action": action},
        files={"file": (name, content)},
        follow_redirects=True,
    )


def test_empty_library_page_explains_the_export(site):
    client, _, _ = site
    page = client.get("/paperpile").text
    assert "never" in page and "No library imported yet" in page
    assert "Workflows and Integrations" in page and "keywords" in page
    assert "required" in page and "optional" in page


def test_check_only_changes_nothing(site):
    client, bib, _ = site
    page = upload(client, SAMPLE_BIB.read_bytes(), "check").text
    assert "Check of “export.bib”: 7 entries" in page and "Nothing was changed yet" in page
    assert not bib.exists()


def test_import_then_browse_and_filter(site):
    client, bib, _ = site
    page = upload(client, SAMPLE_BIB.read_bytes(), "import").text
    assert "Library updated: 7 entries (7 new" in page
    assert bib.read_bytes() == SAMPLE_BIB.read_bytes()
    page = client.get("/paperpile").text
    assert "Papers in your library" in page and "Import history" in page and "upload" in page
    assert "Cancer cell states recur" in page and "📁 Projects/Example A/Interactions" in page
    folder = client.get("/paperpile?folder=01 Biology / Medicine/Cancer").text
    assert "A collective route" in folder and "Cancer cell states" in folder  # sub-folders too
    assert "Hofmann" not in folder
    search = client.get("/paperpile?q=collective").text
    assert "A collective route" in search and "Cancer cell states" not in search
    # queue from the library page, then the paper counts as read once read
    client.post("/queue/add", data={"ref": "Cheung2016-vl", "source": "library"})
    client.post("/papers/2/read", data={"level": "read"})
    unread = client.get("/paperpile?status=unread").text
    read = client.get("/paperpile?status=read").text
    assert "A collective route" not in unread and "A collective route" in read


def test_invalid_upload_keeps_the_old_library(site):
    client, bib, _ = site
    upload(client, SAMPLE_BIB.read_bytes(), "import")
    page = upload(client, b"@ARTICLE{x, title = {never closed", "import").text
    assert "Not valid BibTeX" in page and "your library was not changed" in page
    assert bib.read_bytes() == SAMPLE_BIB.read_bytes()
    assert not list(bib.parent.glob(".upload.*"))  # no temp files left


def test_reupload_keeps_a_backup_and_reimport(site):
    client, bib, _ = site
    upload(client, SAMPLE_BIB.read_bytes(), "import")
    smaller = SAMPLE_BIB.read_text().split("@INCOLLECTION")[0].encode()
    page = upload(client, smaller, "import").text
    assert "1 removed" in page
    assert bib.with_name("library.bib.bak").read_bytes() == SAMPLE_BIB.read_bytes()
    assert "Library updated" in client.post("/paperpile/reimport", follow_redirects=True).text


def test_reimport_without_file(site):
    client, _, _ = site
    page = client.post("/paperpile/reimport", follow_redirects=True).text
    assert "No BibTeX file" in page


def test_stale_and_changed_file_notices(site):
    client, bib, database = site
    upload(client, SAMPLE_BIB.read_bytes(), "import")
    engine = init_db(database)
    with Session(engine) as session:
        run = session.exec(select(IngestRun)).one()
        run.at = datetime.now() - timedelta(days=40)
        session.add(run)
        session.commit()
    engine.dispose()
    future = time.time() + 5
    os.utime(bib, (future, future))  # e.g. rclone pulled a new export
    page = client.get("/paperpile").text
    assert "last updated 40 days ago" in page and "changed after the last import" in page


def test_upload_requires_a_file(site):
    client, _, _ = site
    page = client.post("/paperpile/upload", data={"action": "import"}, follow_redirects=True).text
    assert "Choose a .bib file" in page


# --- CLI ----------------------------------------------------------------------------------


@pytest.fixture
def plain_console(monkeypatch):
    from rich.console import Console

    console = Console(color_system=None, width=300, highlight=False)
    monkeypatch.setattr(cli, "console", console)


def test_cli_check_bib(tmp_path, plain_console):
    path = tmp_path / "config.yaml"
    path.write_text(f"paths:\n  bibtex: {SAMPLE_BIB}\n")
    result = runner.invoke(cli.app, ["check-bib", "-c", str(path)])
    assert result.exit_code == 0 and "7 entries" in result.stdout and "DOI" in result.stdout
    sparse = tmp_path / "sparse.bib"
    sparse.write_text('@ARTICLE{a, title = "Only a title"}\n')
    result = runner.invoke(cli.app, ["check-bib", str(sparse), "-c", str(path)])
    assert result.exit_code == 0 and "WARNING" in result.stdout
    assert "How to export from Paperpile" in result.stdout
    empty = tmp_path / "empty.bib"
    empty.write_text("% nothing here\n")
    result = runner.invoke(cli.app, ["check-bib", str(empty), "-c", str(path)])
    assert result.exit_code == 1 and "ERROR" in result.stdout
