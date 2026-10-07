import shutil
from datetime import date

import yaml
from typer.testing import CliRunner

import litresearch.cli as cli
from conftest import SAMPLE_BIB, FakeApis, make_work

runner = CliRunner()


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
    result = runner.invoke(cli.app, ["serve", "-c", str(write_config(tmp_path))])
    assert result.exit_code == 1


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
