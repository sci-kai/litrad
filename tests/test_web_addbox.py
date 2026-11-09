"""Web UI: the single add box (DOI, PubMed ID or title) and the shorter texts."""

import re
from urllib.parse import parse_qs, urlsplit

from sqlmodel import Session, select

import litrad.web.app as web
from conftest import FakeApis
from litrad import cli
from litrad.db import Paper, QueueItem
from test_search import cli_profile  # noqa: F401  (fixture: CLI profile)
from test_web import app_env, celebration, db, paper_id, post  # noqa: F401
from test_web_examples import example_url, labels_ok

SOMATIC = "Somatic evolution following cancer treatment"  # in the recorded Europe PMC search


def query_of(response) -> dict:
    query = parse_qs(urlsplit(str(response.url)).query)
    return {k: v[0] for k, v in query.items() if k != "c"}  # c: the toast's token


def queued_titles(path) -> list[str]:
    engine = db(path)
    with Session(engine) as session:
        rows = session.exec(
            select(Paper.title).join(QueueItem, QueueItem.paper_id == Paper.id)
        ).all()
    engine.dispose()
    return list(rows)


def test_add_box_identifier_is_fetched(app_env):  # noqa: F811
    client, path, _ = app_env
    response = post(client, "/queue/add", {"ref": "pmid:41372419"})
    assert SOMATIC in celebration(response)["message"]
    assert any(SOMATIC in t for t in queued_titles(path))


def test_add_box_title_of_one_of_your_papers(app_env):  # noqa: F811
    client, path, apis = app_env
    before = len(apis.requests)
    response = post(client, "/queue/add", {"ref": "watching   EVOLVE", "top": "true"})
    assert "Added to your queue: Watching cancer cells evolve" in celebration(response)["message"]
    assert len(apis.requests) == before  # found locally: no network
    assert urlsplit(str(response.url)).path == "/queue"


def test_add_box_title_with_several_matches_lists_them(app_env):  # noqa: F811
    client, _, _ = app_env
    response = post(client, "/queue/add", {"ref": "metastasis"})
    assert query_of(response) == {"title": "metastasis", "add": "1", "where": "library"}
    assert "2 of your papers match: pick one." in celebration(response)["message"]
    assert "2 papers in your library" in response.text
    assert response.text.count("➕ Add to queue") >= 2


def test_add_box_unknown_title_shows_search_candidates(app_env):  # noqa: F811
    client, path, _ = app_env
    response = post(client, "/queue/add", {"ref": "somatic evolution normal tissue", "top": "1"})
    query = query_of(response)
    assert query["where"] == "databases" and query["add"] == "1" and query["top"] == "true"
    assert "Not among your papers: pick a search result." in celebration(response)["message"]
    page = response.text
    assert SOMATIC in page and "➕ Add to queue" in page
    assert '<input type="hidden" name="top" value="true">' in page  # carried to the picks
    assert "Add without DOI" in page  # the fallback for papers no database knows
    labels_ok(page)
    # pick the candidate: it is fetched by its identifier and queued
    ref = re.search(r'name="ref" value="(pmid:41372419|10\.1038/s41586-025-09792-4)"', page)
    done = post(client, "/queue/add", {"ref": ref.group(1), "source": "manual", "top": "true"})
    assert SOMATIC in celebration(done)["message"]
    assert any(SOMATIC in t for t in queued_titles(path))


def test_add_box_nothing_found_offers_a_paper_without_doi(app_env, monkeypatch, make_http):  # noqa: F811
    client, path, _ = app_env
    down = FakeApis(down=("www.ebi.ac.uk", "eutils.ncbi.nlm.nih.gov"))
    monkeypatch.setattr(web, "make_http", lambda cfg: make_http(down, max_retries=0))
    response = post(client, "/queue/add", {"ref": "A preprint from the lab meeting"})
    page = response.text
    assert "Nothing found" in page and "did not answer" in page
    assert 'name="title" value="A preprint from the lab meeting"' in page
    added = post(client, "/queue/add", {"title": "A preprint from the lab meeting"})
    assert "A preprint from the lab meeting" in celebration(added)["message"]
    pid = paper_id(path, title="A preprint from the lab meeting")
    engine = db(path)
    with Session(engine) as session:
        assert session.get(Paper, pid).doi is None
    engine.dispose()


def test_add_box_of_an_example_list_picks_into_the_list(app_env):  # noqa: F811
    client, _, _ = app_env
    url = example_url(client, "negative")
    library_id = url.rsplit("/", 1)[1]
    response = post(client, f"{url}/add", {"ref": "somatic evolution normal tissue"})
    assert query_of(response)["to"] == library_id
    page = response.text
    assert f'action="/library/{library_id}/add"' in page and "➕ Add to Negative" in page
    assert "Add without DOI" not in page  # examples need a DOI
    ref = re.search(
        r'action="/library/\d+/add">\s*<input type="hidden" name="ref" value="([^"]+)"', page
    )
    done = post(client, f"{url}/add", {"ref": ref.group(1)})
    assert "Added: " + SOMATIC in done.text
    # a single local title match is added at once
    done = post(client, f"{url}/add", {"ref": "watching evolve"})
    assert "Added: Watching cancer cells evolve" in done.text
    assert client.get("/search?title=x&to=999999").status_code == 404


def test_cli_queue_add_unknown_title_points_to_the_search(cli_profile):  # noqa: F811
    runner, path = cli_profile
    result = runner.invoke(cli.app, ["queue", "add", "no such words here", "-c", path])
    assert result.exit_code == 1
    assert 'litrad search --title "no such words here"' in result.output
    assert "--title" in result.output


def test_owner_examples_of_unneeded_text_are_gone(app_env):  # noqa: F811
    client, path, _ = app_env
    pid = paper_id(path, title="Watching cancer cells evolve")
    post(client, f"/papers/{pid}/read", {"level": "skimmed", "next_url": "/"})
    queue = client.get("/queue").text
    assert "Unknown DOIs are looked up on OpenAlex" not in queue
    assert "or by title only" not in queue
    assert 'placeholder="DOI, PubMed ID or title"' in queue
    labels_ok(queue)
    libraries = client.get("/library").text
    assert "the similarity score and the positive-example rules" not in libraries
    dashboard = client.get("/").text
    assert "Monday–Sunday" not in dashboard and "<b>S</b> quest + thorough read" not in dashboard
    for comment in (
        "Outstanding: quest done",
        "Quest complete",
        "Over halfway there",
        "Off to a start",
        "Rest week",
        "Nothing read yet this week",
    ):
        assert comment not in dashboard, comment
    assert 'class="grade' in dashboard and "S quest and a thorough read" in dashboard


def test_icon_links_have_accessible_names(app_env):  # noqa: F811
    client, path, _ = app_env
    post(client, "/recommendations/build", {"s2": "false"})
    pid = paper_id(path, doi="10.9/a")
    post(client, f"/papers/{pid}/example", {"role": "positive"})
    pages = ["/", "/queue", "/recommendations", "/library", "/settings", f"/papers/{pid}"]
    pages += [example_url(client, "positive"), "/library/import"]
    seen = 0
    for url in pages:
        html = client.get(url).text
        for link in re.findall(r'<a class="icon-link[^"]*"[^>]*>.*?</a>', html, re.S):
            seen += 1
            label = re.search(r'aria-label="([^"]+)"', link)
            assert label and len(label.group(1)) > 3, link
            assert f'title="{label.group(1)}"' in link, link
            assert re.search(r'href="[^"]+"', link), link
        for wordy in ("manage →", "open →", "Adjust →", ">change<", "see the list"):
            assert wordy not in html, (url, wordy)
    assert seen >= 8
    dashboard = client.get("/").text
    assert 'aria-label="Open the queue"' in dashboard
    assert 'aria-label="Open the recommendations"' in dashboard
    assert 'aria-label="Change in the settings"' in client.get("/recommendations").text
