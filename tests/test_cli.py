import shutil
from datetime import date

import pytest
import yaml
from rich.console import Console
from typer.testing import CliRunner

import litresearch.cli as cli
import litresearch.cli_reading as cli_reading
from conftest import SAMPLE_BIB, FakeApis, make_work

runner = CliRunner()


@pytest.fixture(autouse=True)
def plain_console(monkeypatch):
    """No colours and no line wrapping, so tests can match whole lines."""
    console = Console(color_system=None, width=300, highlight=False)
    monkeypatch.setattr(cli, "console", console)
    monkeypatch.setattr(cli_reading, "console", console)


def write_config(tmp_path, **extra):
    bib = tmp_path / "library.bib"
    shutil.copy(SAMPLE_BIB, bib)
    path = tmp_path / "config.yaml"
    settings = {
        "paths": {
            "bibtex": str(bib),
            "database": str(tmp_path / "db.sqlite"),
            "cache_dir": str(tmp_path / "cache"),
        },
        **extra,
    }
    path.write_text(yaml.safe_dump(settings), encoding="utf-8")
    return path


def test_version():
    result = runner.invoke(cli.app, ["version"])
    assert result.exit_code == 0 and result.stdout.strip() == cli.__version__


def test_db_init(tmp_path):
    config = write_config(tmp_path)
    result = runner.invoke(cli.app, ["db-init", "-c", str(config)])
    assert result.exit_code == 0
    assert (tmp_path / "db.sqlite").exists()


def test_ingest_and_status(tmp_path, monkeypatch, make_http):
    apis = FakeApis()
    monkeypatch.setattr(cli, "make_http", lambda cfg: make_http(apis))
    config = write_config(tmp_path)

    result = runner.invoke(cli.app, ["ingest", "-c", str(config)])
    assert result.exit_code == 0, result.output
    assert "BibTeX entries:        7" in result.stdout
    assert "SPECTER2 embeddings:   3" in result.stdout

    result = runner.invoke(cli.app, ["status", "-c", str(config)])
    assert result.exit_code == 0, result.output
    assert "Library papers:  7" in result.stdout
    assert "embedded:      3" in result.stdout
    assert "01 Biology / Medicine/Cancer/Metastasis" in result.stdout


def test_ingest_without_s2(tmp_path, monkeypatch, make_http):
    apis = FakeApis()
    monkeypatch.setattr(cli, "make_http", lambda cfg: make_http(apis))
    result = runner.invoke(cli.app, ["ingest", "--no-s2", "-c", str(write_config(tmp_path))])
    assert result.exit_code == 0
    assert apis.count("api.semanticscholar.org") == 0


def test_missing_config_fails(tmp_path):
    result = runner.invoke(cli.app, ["status", "-c", str(tmp_path / "nope.yaml")])
    assert result.exit_code != 0


def test_unimplemented_commands_exit_nonzero(tmp_path):
    result = runner.invoke(cli.app, ["export", "-c", str(write_config(tmp_path))])
    assert result.exit_code == 1


def test_serve_is_local_by_default(tmp_path, monkeypatch):
    import uvicorn

    calls = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.update(kwargs))
    config = str(write_config(tmp_path))
    result = runner.invoke(cli.app, ["serve", "--no-browser", "--port", "8123", "-c", config])
    assert result.exit_code == 0 and "http://127.0.0.1:8123/" in result.stdout
    assert calls == {"host": "127.0.0.1", "port": 8123, "log_level": "warning"}
    result = runner.invoke(cli.app, ["serve", "--no-browser", "--host", "0.0.0.0", "-c", config])
    assert "WARNING: listening on 0.0.0.0" in result.stdout and calls["port"] == 8080


def test_recommend_builds_once_per_week(tmp_path, monkeypatch, make_http):
    apis = FakeApis()
    fresh = date.today().isoformat()
    apis.search_results["fresh"] = [
        make_work("10.9/a", "Clonal evolution of tumours", fresh, abstract="clonal evolution")
    ]
    monkeypatch.setattr(cli, "make_http", lambda cfg: make_http(apis))
    config = write_config(tmp_path, topics=[{"name": "evo", "queries": ["clonal evolution"]}])
    runner.invoke(cli.app, ["ingest", "--no-s2", "-c", str(config)])

    result = runner.invoke(cli.app, ["recommend", "--no-s2", "-c", str(config)])
    assert result.exit_code == 0, result.output
    assert "r1" in result.stdout and "Clonal evolution of tumours" in result.stdout
    assert "queue add r1" in result.stdout
    searches = apis.count("api.openalex.org")

    # second call in the same week shows the stored batch without API calls
    result = runner.invoke(cli.app, ["recommend", "--no-s2", "-c", str(config)])
    assert result.exit_code == 0 and "Clonal evolution of tumours" in result.stdout
    assert apis.count("api.openalex.org") == searches


def ingested(tmp_path, monkeypatch, make_http, **extra):
    apis = FakeApis()
    monkeypatch.setattr(cli, "make_http", lambda cfg: make_http(apis))
    monkeypatch.setattr(cli_reading, "make_http", lambda cfg: make_http(apis))
    config = str(write_config(tmp_path, **extra))
    runner.invoke(cli.app, ["ingest", "--no-s2", "-c", config])
    return config, apis


def invoke(*args):
    result = runner.invoke(cli.app, list(args))
    return result.exit_code, result.stdout


def test_reading_workflow(tmp_path, monkeypatch, make_http):
    config, _ = ingested(tmp_path, monkeypatch, make_http)
    code, out = invoke("queue", "-c", config)
    assert code == 0 and "Reading queue (0/5)" in out and "empty" in out

    assert invoke("queue", "add", "Cheung2016-vl", "-c", config)[0] == 0
    code, out = invoke("queue", "add", "--title", "Paper from a colleague", "--top", "-c", config)
    assert code == 0 and "q1" in out
    code, out = invoke("queue", "-c", config)
    assert "▶ UP NEXT" in out and out.index("Paper from a colleague") < out.index("A collective")

    code, out = invoke("next", "-c", config)
    assert code == 0 and "Paper from a colleague" in out and "litresearch read q1" in out

    code, out = invoke("read", "q1", "read", "--rating", "4", "--note", "useful", "-c", config)
    assert code == 0 and "Ate the frog" in out and "LEVEL" not in out
    assert "Badge unlocked: First Steps" in out and "Up next: q1 A collective" in out

    code, out = invoke("note", "Cheung2016-vl", "x", "-c", config)
    assert code == 1 and "Mark the paper first" in out
    invoke("read", "q1", "abstract", "-c", config)
    code, out = invoke("note", "q1", "a thought", "-c", config)
    assert code == 0 and "Note saved" in out

    code, out = invoke("show", "Cheung2016-vl", "-c", config)
    assert "Status: abstract" in out and "a thought" in out

    code, out = invoke("score", "-c", config)
    assert code == 0 and "Level 1 · Curious Undergrad" in out and "Badges 1/15" in out
    assert "Reading Impact Factor" in out and "This week" in out

    code, out = invoke("history", "-c", config)
    assert "abstract" in out and "read" in out


def test_queue_errors_and_limits(tmp_path, monkeypatch, make_http):
    config, _ = ingested(tmp_path, monkeypatch, make_http, queue={"max_size": 1})
    assert invoke("queue", "add", "Cheung2016-vl", "-c", config)[0] == 0
    code, out = invoke("queue", "add", "Barkley2022-ri", "-c", config)
    assert code == 1 and "Queue is full (1/1)" in out
    code, out = invoke("queue", "add", "-c", config)
    assert code == 1 and "Give a reference" in out
    code, out = invoke("queue", "drop", "Barkley2022-ri", "-c", config)
    assert code == 1 and "Not on the queue" in out
    code, out = invoke("queue", "drop", "q1", "-c", config)
    assert code == 0 and "Dropped" in out and "queue is empty" in out
    code, out = invoke("read", "nonexistent words here", "read", "-c", config)
    assert code == 1 and "Nothing matches" in out


def test_queue_add_unknown_doi_fetches_metadata(tmp_path, monkeypatch, make_http):
    config, apis = ingested(tmp_path, monkeypatch, make_http)
    apis.works.append(make_work("10.9/new", "A brand new paper", "2026-09-30"))
    code, out = invoke("queue", "add", "https://doi.org/10.9/NEW", "-c", config)
    assert code == 0 and "A brand new paper" in out
    code, out = invoke("queue", "add", "10.9/missing", "-c", config)
    assert code == 1 and "does not know 10.9/missing" in out


def test_move_and_dismiss(tmp_path, monkeypatch, make_http):
    config, _ = ingested(tmp_path, monkeypatch, make_http)
    invoke("queue", "add", "Cheung2016-vl", "-c", config)
    invoke("queue", "add", "Barkley2022-ri", "-c", config)
    code, out = invoke("queue", "move", "q2", "1", "-c", config)
    assert code == 0 and "Up next: q1 Cancer cell states" in out
    code, out = invoke("dismiss", "q1", "-c", config)
    assert code == 0 and "Dismissed" in out
    assert "Cancer cell states" not in invoke("queue", "-c", config)[1]
