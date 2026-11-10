"""Searching Europe PMC and PubMed and adding the results (recorded API answers)."""

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select
from typer.testing import CliRunner

import litrad.cli as cli
import litrad.web.app as web
from conftest import DATA, FakeApis, enter_profile, profile_cfg
from litrad.db import Paper, QueueItem, init_db
from litrad.papers import upsert_hit
from litrad.reading import ReadingError, fetch_paper, find_paper, queue_add
from litrad.search import SearchQuery, search, search_library
from litrad.sources.europepmc import clean, parse_result
from litrad.sources.hits import Hit
from litrad.sources.pubmed import parse_summary

RECORDED = json.loads((DATA / "literature_search.json").read_text("utf-8"))
EPMC = RECORDED["europepmc_search"]["resultList"]["result"]
SUMMARIES = RECORDED["pubmed_esummary"]["result"]


@pytest.fixture
def db(cfg):
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        yield cfg, session
    engine.dispose()


# --- parsing ------------------------------------------------------------------------------


def test_europepmc_records():
    hit = parse_result(EPMC[0])
    assert hit.title == "Somatic evolution following cancer treatment in normal tissue"
    assert hit.doi == "10.1038/s41586-025-09792-4" and hit.pmid == "41372419"
    assert hit.pmcid == "PMC13190248" and hit.open_access is True
    assert hit.journal == "Nature" and hit.year == 2026 and hit.pub_date == date(2025, 12, 10)
    assert hit.authors[0] == "Pich O" and isinstance(hit.cited_by, int)
    assert hit.ref == hit.doi and hit.url.endswith("/PMC13190248")
    # missing fields never break parsing
    bare = parse_result({"title": "Only <i>a</i> title."})
    assert bare.title == "Only a title" and bare.doi is None and bare.authors == []
    assert bare.ref is None and bare.year is None


def test_markup_is_removed():
    assert clean("<h4>Aim</h4>Cells with <i>KRAS</i> &amp; TP53.<br/>End") == (
        "Aim Cells with KRAS & TP53. End"
    )
    assert clean("") is None and clean(None) is None
    assert clean("G&lt;sub&gt;q&lt;/sub&gt;, p<0.05") == "Gq, p<0.05"


def test_pubmed_records():
    hit = parse_summary(SUMMARIES["41372419"])
    assert hit.source == "pubmed" and hit.pmid and hit.doi and hit.doi == hit.doi.lower()
    assert hit.journal == "Nature" and hit.authors
    dates = parse_summary({"uid": "1", "title": "x", "pubdate": "2017 Apr 26"})
    assert dates.pub_date == date(2017, 4, 26) and dates.year == 2017
    assert parse_summary({"uid": "1", "title": "x", "pubdate": "2019"}).pub_date == date(2019, 1, 1)
    assert parse_summary({"uid": "1", "title": "x", "pubdate": "n/a"}).pub_date is None
    pmc = parse_summary(
        {"uid": "2", "title": "y", "articleids": [{"idtype": "pmc", "value": "PMC5812436"}]}
    )
    assert pmc.pmcid == "PMC5812436" and pmc.open_access is True and pmc.ref == "pmid:2"


def test_merging_fills_gaps():
    a = Hit(source="pubmed", title="T", pmid="1", doi="10.1/x", found_in=["pubmed"])
    b = Hit(
        source="europepmc",
        title="T",
        pmid="1",
        pmcid="PMC9",
        abstract="A",
        cited_by=4,
        found_in=["europepmc"],
    )
    a.merge(b)
    assert a.pmcid == "PMC9" and a.abstract == "A" and a.cited_by == 4
    assert a.found_in == ["pubmed", "europepmc"] and a.doi == "10.1/x"


# --- queries ------------------------------------------------------------------------------


def test_query_translation():
    query = SearchQuery(
        keywords="ctDNA OR cfDNA",
        title='clonal "evolution"',
        author="Swanton C; Turajlic S",
        journal="Nature",
        year_from=2020,
        open_access=True,
    )
    assert query.europepmc() == (
        'TITLE_ABS:(ctDNA OR cfDNA) AND TITLE:"clonal evolution" AND (AUTH:"Swanton C" OR '
        'AUTH:"Turajlic S") AND (JOURNAL:"Nature") AND PUB_YEAR:[2020 TO 3000] AND OPEN_ACCESS:y'
    )
    assert query.pubmed() == (
        '(ctDNA OR cfDNA) AND "clonal evolution"[ti] AND ("Swanton C"[au] OR "Turajlic S"[au]) '
        'AND ("Nature"[journal]) AND 2020:3000[dp] AND "free full text"[sb]'
    )
    assert "author: Swanton C; Turajlic S" in query.describe()
    # keywords in Europe PMC's own field syntax are passed on unchanged
    assert SearchQuery(keywords='MESH:"Neoplasms"').europepmc() == '(MESH:"Neoplasms")'

    assert SearchQuery().is_empty() and not SearchQuery(year_to=2001).is_empty()
    assert SearchQuery(journal=" ; ").europepmc() == ""


# --- searching ----------------------------------------------------------------------------


def test_search_merges_both_databases(db, make_http):
    cfg, session = db
    apis = FakeApis()
    found = search(SearchQuery(author="Swanton C"), cfg, make_http(apis), session)
    assert set(found.totals) == {"europepmc", "pubmed"} and not found.errors
    keys = [r.hit.key for r in found.results]
    assert len(keys) == len(set(keys)) == 4  # 3 + 3 records, 2 of them the same papers
    both = [r.hit for r in found.results if len(r.hit.found_in) == 2]
    assert len(both) == 2 and all(h.pmid and h.doi for h in both)
    assert any(h.abstract and h.pmcid for h in both)  # the other one is an erratum
    # the queries actually sent
    epmc = [r for r in apis.requests if r.url.host == "www.ebi.ac.uk"][0]
    assert epmc.url.params["query"] == '(AUTH:"Swanton C")'
    assert "email" not in str(apis.requests[-1].url)  # no personal data
    ordered = search(SearchQuery(author="Swanton C"), cfg, make_http(apis), session, sort="cited")
    counts = [r.hit.cited_by or 0 for r in ordered.results]
    assert counts == sorted(counts, reverse=True)


def test_one_database_down(db, make_http):
    cfg, session = db
    apis = FakeApis(down=("www.ebi.ac.uk",))
    found = search(SearchQuery(keywords="evolution"), cfg, make_http(apis, max_retries=0), session)
    assert "europepmc" in found.errors and found.totals["pubmed"] > 0
    assert found.results and all(r.hit.found_in == ["pubmed"] for r in found.results)


def test_disabled_database_is_not_asked(db, make_http):
    cfg, session = db
    cfg.sources.pubmed = False
    apis = FakeApis()
    found = search(SearchQuery(keywords="evolution"), cfg, make_http(apis), session)
    assert set(found.totals) == {"europepmc"} and apis.count("eutils.ncbi.nlm.nih.gov") == 0


def test_results_know_what_you_did(db, make_http):
    cfg, session = db
    known = Paper(title="Known", doi="10.1038/s41586-025-09792-4", in_library=True)
    session.add(known)
    session.commit()
    queue_add(session, cfg, known)
    found = search(SearchQuery(author="Swanton C"), cfg, make_http(FakeApis()), session)
    mine = next(r for r in found.results if r.hit.doi == known.doi)
    assert mine.paper_id == known.id and mine.in_library and mine.queued
    assert sum(r.paper_id is not None for r in found.results) == 1


def test_library_search(db):
    cfg, session = db
    session.add(
        Paper(
            title="Clonal evolution in lung",
            authors=["Swanton, Charles"],
            venue="Nature",
            year=2021,
            pmcid="PMC1",
        )
    )
    session.add(
        Paper(title="Clonal evolution in skin", authors=["Other, A"], venue="Cell", year=2015)
    )
    session.commit()
    titles = [p.title for p in search_library(session, SearchQuery(keywords="clonal"))]
    assert len(titles) == 2
    assert [p.title for p in search_library(session, SearchQuery(author="swanton"))] == [
        "Clonal evolution in lung"
    ]
    assert len(search_library(session, SearchQuery(journal="Nature; Cell"))) == 2
    assert len(search_library(session, SearchQuery(keywords="clonal", year_from=2020))) == 1
    assert len(search_library(session, SearchQuery(keywords="clonal", open_access=True))) == 1


# --- adding -------------------------------------------------------------------------------


def test_fetch_by_pmid_pmc_and_doi(db, make_http):
    cfg, session = db
    http = make_http(FakeApis())
    by_pmid = fetch_paper(session, cfg, http, "pmid:41372419")
    assert by_pmid.doi == "10.1038/s41586-025-09792-4" and by_pmid.pmcid == "PMC13190248"
    assert by_pmid.abstract and by_pmid.resolved_via == "europepmc"
    session.commit()
    assert find_paper(session, "PMC13190248").id == by_pmid.id
    assert find_paper(session, "pmid:41372419").id == by_pmid.id
    assert find_paper(session, "pmid:999") is None  # not stored yet: fetched when added
    # a DOI that OpenAlex does not know, but Europe PMC does
    by_doi = fetch_paper(session, cfg, http, "10.1038/s41586-024-07738-w")
    assert by_doi.pmid and by_doi.title.startswith("Author Correction")
    # a PMID only PubMed knows (Europe PMC's recorded answer does not contain it)
    only_pubmed = next(u for u in SUMMARIES["uids"] if u not in {r["pmid"] for r in EPMC})
    from_pubmed = fetch_paper(session, cfg, http, f"pmid:{only_pubmed}")
    assert from_pubmed.pmid == only_pubmed and from_pubmed.resolved_via == "pubmed"


def test_fetch_errors(db, make_http):
    cfg, session = db
    http = make_http(FakeApis(), max_retries=0)
    with pytest.raises(ReadingError, match="No database knows 10.9/nowhere"):
        fetch_paper(session, cfg, http, "10.9/nowhere")
    with pytest.raises(ReadingError, match="Could not find pmid:1"):
        fetch_paper(session, cfg, http, "pmid:1")
    with pytest.raises(ReadingError, match="Enter a DOI"):
        fetch_paper(session, cfg, http, "some title words")
    down = make_http(FakeApis(down=("www.ebi.ac.uk", "eutils.ncbi.nlm.nih.gov")), max_retries=0)
    with pytest.raises(ReadingError, match="Europe PMC: .*PubMed"):
        fetch_paper(session, cfg, down, "pmid:41372419")


def test_hits_do_not_overwrite_stored_papers(db):
    cfg, session = db
    stored = Paper(title="Paperpile title", doi="10.1/x", citation_count=500, in_library=True)
    session.add(stored)
    session.commit()
    hit = Hit(
        source="europepmc",
        title="Other title",
        doi="10.1/x",
        pmid="7",
        pmcid="PMC7",
        abstract="An abstract",
        cited_by=3,
    )
    paper = upsert_hit(session, hit)
    assert paper.id == stored.id and paper.title == "Paperpile title"
    assert paper.citation_count == 500 and paper.pmcid == "PMC7" and paper.abstract


# --- web and command line -----------------------------------------------------------------


@pytest.fixture
def site(tmp_path, monkeypatch, make_http):
    path = tmp_path / "config.yaml"
    path.write_text(f"paths:\n  cache_dir: {tmp_path / 'cache'}\n")
    apis = FakeApis()
    monkeypatch.setattr(web, "make_http", lambda cfg: make_http(apis))
    client = TestClient(web.create_app(path))
    enter_profile(client, "Ada")
    yield client, path, apis
    client.close()


def test_search_page(site):
    client, path, _ = site
    page = client.get("/search").text
    assert (
        "Find &amp; add papers" in page and "Europe PMC + PubMed" in page and "My library" in page
    )
    page = client.get("/search?author=Swanton+C&journal=Nature&sort=cited").text
    assert "4 results" in page and "Somatic evolution following cancer treatment" in page
    assert "Europe PMC: 11 matches · PubMed: 11 matches" in page
    assert "free full text" in page and "PMID 41372419" in page
    # the queries sent are shown (transparency)
    assert "(AUTH:&#34;Swanton C&#34;) AND (JOURNAL:&#34;Nature&#34;)" in page
    assert "(&#34;Swanton C&#34;[au]) AND (&#34;Nature&#34;[journal])" in page
    # add a result: it is fetched by its DOI and queued
    response = client.post(
        "/queue/add",
        data={"ref": "pmid:41372419", "source": "manual", "next_url": "/search?author=x"},
        follow_redirects=False,
    )
    assert response.headers["location"].startswith("/search?author=x")
    engine = init_db(profile_cfg(path).paths.database)
    with Session(engine) as session:
        item = session.exec(select(QueueItem)).one()
        paper = session.get(Paper, item.paper_id)
        assert paper.pmcid == "PMC13190248" and item.source == "manual"
    engine.dispose()
    again = client.get("/search?author=Swanton+C").text
    assert "on your queue" in again and "🔓 full text" in client.get(f"/papers/{paper.id}").text


def test_search_page_library_and_outage(site, monkeypatch, make_http):
    client, _, _ = site
    page = client.get("/search?q=nothing&where=library").text
    assert "0 papers in your library" in page
    monkeypatch.setattr(
        web, "make_http", lambda cfg: make_http(FakeApis(down=("www.ebi.ac.uk",)), max_retries=0)
    )
    page = client.get("/search?q=evolution&where=europepmc").text
    assert "Europe PMC did not answer" in page and "Nothing found" in page


@pytest.fixture
def cli_profile(tmp_path, monkeypatch, make_http):
    from rich.console import Console

    import litrad.cli_reading as cli_reading

    console = Console(color_system=None, width=200, highlight=False)
    monkeypatch.setattr(cli_reading, "console", console)
    monkeypatch.setattr(cli, "console", console)
    apis = FakeApis()
    monkeypatch.setattr(cli_reading, "make_http", lambda cfg: make_http(apis))
    path = tmp_path / "config.yaml"
    path.write_text(f"paths:\n  cache_dir: {tmp_path / 'cache'}\n")
    runner = CliRunner()
    runner.invoke(cli.app, ["profile", "create", "Ada", "--yes", "-c", str(path)])
    return runner, str(path)


def test_cli_search_and_add(cli_profile):
    runner, path = cli_profile
    result = runner.invoke(cli.app, ["search", "--author", "Swanton C", "-c", path])
    assert result.exit_code == 0, result.output
    assert "Somatic evolution following cancer treatment" in result.stdout
    assert "Europe PMC" in result.stdout and "--add N" in result.stdout
    result = runner.invoke(cli.app, ["search", "--author", "Swanton C", "--add", "1", "-c", path])
    assert result.exit_code == 0 and "Added to the queue at q1" in result.stdout
    assert (
        "ON YOUR QUEUE"
        in runner.invoke(cli.app, ["search", "--author", "Swanton C", "-c", path]).stdout
    )
    assert runner.invoke(cli.app, ["queue", "add", "pmid:41372419", "-c", path]).exit_code == 1
    library = runner.invoke(cli.app, ["search", "evolution", "-s", "library", "-c", path])
    assert "1 paper(s) in your library" in library.stdout


def test_cli_search_needs_a_query(cli_profile):
    runner, path = cli_profile
    result = runner.invoke(cli.app, ["search", "-c", path])
    assert result.exit_code == 2
    result = runner.invoke(cli.app, ["search", "x", "--sort", "best", "-c", path])
    assert result.exit_code == 2
    result = runner.invoke(cli.app, ["search", "x", "-s", "scopus", "-c", path])
    assert result.exit_code == 1 and "--source must be" in result.stdout
