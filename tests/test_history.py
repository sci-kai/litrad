"""Reading history: the Read papers tab, its TSV export and `litrad read-papers`."""

import re
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from rich.console import Console
from sqlalchemy import event
from sqlmodel import Session
from typer.testing import CliRunner

import litrad.cli as cli
import litrad.cli_reading as cli_reading
import litrad.web.app as web
from conftest import enter_profile, profile_cfg, within
from litrad import history
from litrad.db import (
    Library,
    LibraryPaper,
    Paper,
    Reading,
    ReadStatus,
    Recommendation,
    RecommendationBatch,
    StatusEvent,
    XpEvent,
    init_db,
)
from test_web_polish import unlabelled
from web_bigdata import populate

runner = CliRunner()


def at(day: str) -> datetime:
    return datetime.fromisoformat(f"2026-{day}T10:00:00")


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text(
        f"paths:\n  cache_dir: {tmp_path / 'cache'}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(web, "make_http", lambda cfg: pytest.fail("no network"))
    client = TestClient(web.create_app(path))
    enter_profile(client, "Kai")
    yield client, path
    client.close()


def add(session, title, steps, rating=None, notes="", **fields):
    """A paper with its status changes: steps = [(status, 'MM-DD', xp), ...]."""
    paper = Paper(title=title, authors=fields.pop("authors", ["Doe, J."]), **fields)
    session.add(paper)
    session.flush()
    for status, day, xp in steps:
        session.add(StatusEvent(paper_id=paper.id, status=status, at=at(day)))
        if xp:
            session.add(XpEvent(paper_id=paper.id, points=xp, kind="read", reason="x", at=at(day)))
    status, day, _ = steps[-1]
    session.add(
        Reading(paper_id=paper.id, status=status, rating=rating, notes=notes, updated_at=at(day))
    )
    session.flush()
    return paper


@pytest.fixture
def filled(env):
    client, path = env
    engine = init_db(profile_cfg(path).paths.database)
    with Session(engine) as s:
        lib = Library(name="Paperpile")
        other = Library(name="Elsewhere")
        s.add(lib)
        s.add(other)
        s.flush()
        a = add(
            s,
            "Alpha clonal evolution",
            [(ReadStatus.skimmed, "01-02", 10), (ReadStatus.thorough, "03-04", 40)],
            rating=5,
            notes="Über die Evolution\tmit Tab\nzweite Zeile",
            year=2020,
            venue="Nature",
            doi="10.1/a",
            pmid="111",
            authors=["Müller, A.", "Second, B."],
            in_library=True,
        )
        s.add(LibraryPaper(library_id=lib.id, paper_id=a.id))
        b = add(s, "Beta tracing", [(ReadStatus.abstract, "02-01", 5)], rating=2, year=2019)
        s.add(LibraryPaper(library_id=other.id, paper_id=b.id))
        add(s, "Gamma ärger", [(ReadStatus.read, "05-06", 20)], notes="solid methods", year=2021)
        add(s, "Delta irrelevant", [(ReadStatus.dismissed, "06-07", -15)])
        # read, later dismissed: deepest depth stays "read"
        add(s, "Epsilon", [(ReadStatus.read, "01-01", 20), (ReadStatus.dismissed, "07-08", 0)])
        s.add(RecommendationBatch(week_start=at("01-05").date()))
        s.flush()
        s.add(Recommendation(batch_id=1, paper_id=b.id, rank=1, category="open", score=0.5))
        s.commit()
        ids = {"lib": lib.id, "other": other.id}
    engine.dispose()
    return client, path, ids


def titles(html: str) -> list[str]:
    return re.findall(r'<a href="/papers/\d+"><b>([^<]*)</b>', html)


def get(client, **params):
    response = client.get("/read", params=params)
    assert response.status_code == 200, response.text[:300]
    return response.text


def test_empty_profile_renders(env):
    client, _ = env
    html = get(client)
    assert "No papers." in html and 'href="/read"' in html
    assert client.get("/read.tsv").text.splitlines() == ["\t".join(history.TSV_COLUMNS)]


def test_filled_profile_row_content(filled):
    client, _, _ = filled
    html = get(client)
    # default: last read, newest first; dismissed papers (Delta, Epsilon) hidden
    assert titles(html) == ["Gamma ärger", "Alpha clonal evolution", "Beta tracing"]
    row = html.split("Alpha clonal evolution")[1].split("</tr>")[0]
    assert "Müller et al. · 2020 · Nature" in row and "🔬</span> thorough" in row
    assert "2026-03-04" in row and "2026-01-02" in row  # last, first
    assert "★★★★★" in row and "+50" in row and "library" in row
    assert "Über die Evolution" in row
    assert "recommendation" in html.split("Beta tracing")[1].split("</tr>")[0]
    assert "added by you" in html.split("Gamma")[1].split("</tr>")[0]
    assert "👀 abstract <b>1</b>" in html and "🔬 thorough <b>1</b>" in html


@pytest.mark.parametrize(
    ("sort", "expected"),
    [
        ("last", ["Gamma ärger", "Alpha clonal evolution", "Beta tracing"]),
        ("first", ["Alpha clonal evolution", "Beta tracing", "Gamma ärger"]),
        ("depth", ["Alpha clonal evolution", "Gamma ärger", "Beta tracing"]),
        ("rating", ["Alpha clonal evolution", "Beta tracing", "Gamma ärger"]),
        ("xp", ["Alpha clonal evolution", "Gamma ärger", "Beta tracing"]),
        ("title", ["Alpha clonal evolution", "Beta tracing", "Gamma ärger"]),
        ("nonsense", ["Gamma ärger", "Alpha clonal evolution", "Beta tracing"]),
    ],
)
def test_sorts(filled, sort, expected):
    client, _, _ = filled
    assert titles(get(client, sort=sort)) == expected


def test_filters(filled):
    client, _, ids = filled
    assert titles(get(client, depth=["read", "abstract"])) == ["Gamma ärger", "Beta tracing"]
    assert titles(get(client, depth="thorough")) == ["Alpha clonal evolution"]
    assert titles(get(client, rating="3")) == ["Alpha clonal evolution"]
    assert titles(get(client, since="2026-04-01")) == ["Gamma ärger"]
    assert titles(get(client, until="2026-03-04")) == ["Alpha clonal evolution", "Beta tracing"]
    assert titles(get(client, since="garbage")) and titles(get(client, rating="x"))
    assert titles(get(client, library=ids["other"])) == ["Beta tracing"]
    assert titles(get(client, library=ids["lib"])) == ["Alpha clonal evolution"]
    assert titles(get(client, notes="1")) == ["Gamma ärger", "Alpha clonal evolution"]


def test_dismissed_toggle(filled):
    client, _, _ = filled
    html = get(client, dismissed="1")
    assert titles(html) == [
        "Epsilon",
        "Delta irrelevant",
        "Gamma ärger",
        "Alpha clonal evolution",
        "Beta tracing",
    ]
    assert "not relevant" in html.split("Delta irrelevant")[1].split("</tr>")[0]
    # read first, dismissed later: the deepest depth stays
    assert "📖</span> read" in html.split("Epsilon")[1].split("</tr>")[0]


def test_search_is_case_insensitive_and_unicode(filled):
    client, _, _ = filled
    assert titles(get(client, q="CLONAL")) == ["Alpha clonal evolution"]
    assert titles(get(client, q="ÄRGER")) == ["Gamma ärger"]
    assert titles(get(client, q="müller")) == ["Alpha clonal evolution"]  # author
    assert titles(get(client, q="über evolution")) == ["Alpha clonal evolution"]  # notes
    assert titles(get(client, q="solid")) == ["Gamma ärger"]
    assert "No papers." in get(client, q="nothing like this")


def test_pagination_edges(env):
    client, path = env
    engine = init_db(profile_cfg(path).paths.database)
    with Session(engine) as s:
        for n in range(101):
            add(s, f"Paper {n:03d}", [(ReadStatus.read, "01-01", 0)])
        s.commit()
    engine.dispose()
    assert "page 1/3" in get(client, sort="title") and len(titles(get(client))) == 50
    assert len(titles(get(client, page=3))) == 1
    assert "page 3/3" in get(client, page=99) and "page 1/3" in get(client, page=-4)
    filtered = get(client, q="Paper")  # filters carry over to the pager
    assert "page 1/3" in filtered and "page=2" in filtered and "q=Paper" in filtered


def test_tsv_export_has_all_filtered_rows_and_escapes(filled):
    client, _, _ = filled
    response = client.get("/read.tsv", params={"sort": "title", "notes": "1"})
    assert response.headers["content-type"].startswith("text/tab-separated-values")
    lines = response.text.splitlines()
    assert lines[0].split("\t") == history.TSV_COLUMNS
    assert len(lines) == 3  # newline and tab in the notes did not add rows or columns
    alpha = lines[1].split("\t")
    assert len(alpha) == len(history.TSV_COLUMNS)
    assert alpha[0] == "Alpha clonal evolution" and alpha[1] == "Müller, A.; Second, B."
    assert alpha[2:6] == ["2020", "Nature", "10.1/a", "111"]
    assert alpha[6:10] == ["thorough", "2026-01-02T10:00:00", "2026-03-04T10:00:00", "5"]
    assert alpha[10] == "Über die Evolution\\tmit Tab\\nzweite Zeile"
    assert alpha[11:] == ["50", "library"]
    gamma = lines[2].split("\t")
    assert gamma[5] == "n/a" and gamma[9] == "n/a" and gamma[12] == "added by you"
    # more rows than a page: the export is not paged
    assert len(client.get("/read.tsv", params={"dismissed": "1"}).text.splitlines()) == 6


def test_every_control_has_a_label_and_tab_links(filled):
    client, _, _ = filled
    html = get(client)
    assert unlabelled(html) == []
    for label in ("Search", "Sort by", "Library", "Minimum rating", "From", "To", "Has notes"):
        assert label in html
    assert 'href="/read"' in client.get("/queue").text
    assert 'href="/read"' in client.get("/recommendations").text
    assert "Export TSV" in html and "Filter" in html


# --- command line ----------------------------------------------------------------------


@pytest.fixture
def cli_env(filled, monkeypatch):
    console = Console(color_system=None, width=300, highlight=False)
    monkeypatch.setattr(cli, "console", console)
    monkeypatch.setattr(cli_reading, "console", console)
    _, path, _ = filled
    return path


def test_cli_lists_and_filters(cli_env):
    from litrad.profiles import get_current, profile_config  # noqa: F401

    out = runner.invoke(cli.app, ["read-papers", "-c", str(cli_env)]).stdout
    assert "total 3" in out and out.index("Gamma") < out.index("Alpha") < out.index("Beta")
    out = runner.invoke(
        cli.app,
        ["read-papers", "-c", str(cli_env), "--depth", "read", "--search", "ÄRGER"],
    ).stdout
    assert "total 1" in out and "Gamma" in out and "Alpha" not in out
    out = runner.invoke(cli.app, ["read-papers", "-c", str(cli_env), "--library", "Elsewhere"])
    assert "total 1" in out.stdout and "Beta" in out.stdout
    bad = runner.invoke(cli.app, ["read-papers", "-c", str(cli_env), "--library", "Nope"])
    assert bad.exit_code == 1 and "Paperpile" in bad.stdout
    assert runner.invoke(cli.app, ["read-papers", "-c", str(cli_env), "--sort", "x"]).exit_code == 2
    assert (
        runner.invoke(cli.app, ["read-papers", "-c", str(cli_env), "--since", "x"]).exit_code == 2
    )


def test_cli_tsv_matches_the_web_export(filled, cli_env, tmp_path):
    client, _, _ = filled
    target = tmp_path / "read.tsv"
    result = runner.invoke(
        cli.app,
        ["read-papers", "-c", str(cli_env), "--tsv", str(target), "--dismissed", "--sort", "title"],
    )
    assert result.exit_code == 0 and "Wrote 5 rows" in result.stdout
    assert target.read_text(encoding="utf-8") == client.get(
        "/read.tsv", params={"dismissed": "1", "sort": "title"}
    ).content.decode("utf-8")


# --- scale -----------------------------------------------------------------------------


def test_scale_is_bounded(env):
    client, path = env
    populate(
        profile_cfg(path).paths.database,
        n_papers=10_000,
        n_libraries=6,
        n_batches=50,
        n_readings=5000,
        n_xp=20_000,
    )
    engine = init_db(profile_cfg(path).paths.database)
    queries = []
    event.listen(engine, "before_cursor_execute", lambda *a: queries.append(a[2]))
    papers = []
    event.listen(
        engine,
        "before_cursor_execute",
        lambda conn, cur, stmt, *a: (
            papers.append(1) if "FROM paper" in stmt.split("WHERE")[0] else 0
        ),
    )
    # the bound that matters: a fixed number of queries, whatever the size
    with within(6.0, "history rows"), Session(engine) as session:
        found = history.rows(session, history.HistoryFilter())
        shown = history.entries(session, found[:50])
    assert len(found) > 3500 and len(shown) == 50
    assert len(queries) <= 6, queries
    # the page and the other filters stay fast, with a search over all notes
    # (generous budgets, scaled by the machine's current speed: see conftest.within)
    for params in ({}, {"sort": "xp", "depth": "read"}, {"q": "clonal"}, {"page": 7}):
        with within(8.0, f"/read {params}"):
            assert client.get("/read", params=params).status_code == 200
    engine.dispose()
