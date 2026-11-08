"""The web UI used like a person over many weeks, with large data, and with hostile input."""

import json
import re
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

import litresearch.web.app as web
from conftest import SAMPLE_BIB, FakeApis, enter_profile, make_work, profile_cfg, rules_form
from litresearch.db import Paper, Reading, ReadStatus, StatusEvent, XpEvent, init_db
from web_bigdata import populate

# every page of the menu (and the pages one click away)
MENU = [
    "/",
    "/queue",
    "/read",
    "/recommendations",
    "/recommendations?topics=1",
    "/progress",
    "/library",
    "/library/import",
    "/search",
    "/search?q=clonal&where=library",
    "/settings",
    "/docs",
    "/profile",
    "/profile/compare",
]


def celebration(response) -> dict | None:
    match = re.search(
        r'<script id="celebration" type="application/json">(.*?)</script>', response.text, re.S
    )
    return json.loads(match.group(1)) if match else None


def post(client, url, data=None, **kwargs):
    return client.post(url, data=data or {}, follow_redirects=True, **kwargs)


@pytest.fixture
def apis():
    apis = FakeApis()
    apis.sources.append(
        {"id": "https://openalex.org/S1", "summary_stats": {"2yr_mean_citedness": 20.0}}
    )
    fresh = date.today().isoformat()
    apis.search_results["fresh"] = [
        make_work("10.9/a", "Clonal evolution of tumours", fresh, abstract="clonal evolution"),
        make_work("10.9/b", "Lineage tracing of clones", fresh, abstract="lineage tracing clones"),
    ]
    return apis


@pytest.fixture
def env(tmp_path, monkeypatch, make_http, apis):
    """A fresh installation: config.yaml only, no profile yet."""
    path = tmp_path / "config.yaml"
    path.write_text(
        f"paths:\n  database: {tmp_path / 'db.sqlite'}\n  cache_dir: {tmp_path / 'cache'}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(web, "make_http", lambda cfg: make_http(apis))
    client = TestClient(web.create_app(path))
    yield client, path
    client.close()


def database(path, slug=None):
    return init_db(profile_cfg(path, slug).paths.database)


def assert_pages_render(client, urls=MENU):
    sizes = {}
    for url in urls:
        response = client.get(url)
        assert response.status_code == 200, (url, response.text[:300])
        assert "/static/app.css" in response.text, url
        sizes[url] = len(response.content)
    return sizes


# --- many weeks of use -----------------------------------------------------------------


def test_brand_new_profile_pages(env):
    client, _ = env
    assert client.get("/", follow_redirects=False).headers["location"] == "/welcome"
    enter_profile(client, "Newbie", adopt=False)
    assert_pages_render(client)
    assert "No recommendations yet" in client.get("/recommendations").text or (
        "topics" in client.get("/recommendations").text
    )
    assert "Inbox zero" in client.get("/queue").text


def test_weeks_of_use(env, apis):
    client, path = env
    enter_profile(client, "Kai", adopt=False)

    # import the library
    with open(SAMPLE_BIB, "rb") as handle:
        response = client.post(
            "/library/import",
            data={"target": "new", "name": "Paperpile", "action": "import"},
            files={"file": ("my export.bib", handle.read())},
            follow_redirects=True,
        )
    assert "entries imported" in celebration(response)["message"]
    library_id = int(re.search(r"/library/(\d+)/export", response.text).group(1))
    assert "Paperpile" in client.get("/library").text

    # topics, then recommendations
    payload = celebration(
        post(
            client,
            "/recommendations/topics",
            {"topic_name": "evo", "topic_weight": "1", "topic_queries": "clonal evolution"},
        )
    )
    assert payload["message"] == "Saved 1 topic for this profile."
    assert (
        "New batch"
        in celebration(post(client, "/recommendations/build", {"s2": "false"}))["message"]
    )
    engine = database(path)
    with Session(engine) as session:
        fresh = session.exec(select(Paper).where(Paper.doi == "10.9/a")).one().id
        other = session.exec(select(Paper).where(Paper.doi == "10.9/b")).one().id
    engine.dispose()

    # queue twice (double submit / back button): the second one is refused
    add = {"ref": f"#{fresh}", "source": "recommendation", "next_url": "/recommendations"}
    assert celebration(post(client, "/queue/add", add))["kind"] == "success"
    again = celebration(post(client, "/queue/add", add))
    assert again["kind"] == "error" and "Already on the queue" in again["message"]

    # mark read twice: points only once
    first = celebration(post(client, f"/papers/{fresh}/read", {"level": "read", "rating": "4"}))
    assert first["points"] > 0
    second = celebration(post(client, f"/papers/{fresh}/read", {"level": "read"}))
    assert second["points"] == 0
    post(client, f"/papers/{other}/dismiss")
    assert celebration(post(client, f"/papers/{other}/dismiss"))["points"] <= 0
    engine = database(path)
    with Session(engine) as session:
        assert session.exec(select(Reading).where(Reading.paper_id == fresh)).one().rating == 4
        xp = sum(e.points for e in session.exec(select(XpEvent)).all())
    engine.dispose()
    assert f"<b>{xp}</b>" in client.get("/").text

    # progress, weekly reports, ledger
    assert "Clonal evolution of tumours" in client.get("/weekly", follow_redirects=True).text
    assert "Badges" in client.get("/progress").text
    tsv = client.get("/ledger.tsv").text.strip().split("\n")
    assert tsv[-1].split("\t")[2] == str(xp)

    # exports
    bib = client.get(f"/library/{library_id}/export?format=bibtex&status=true")
    assert bib.status_code == 200 and "@" in bib.text
    assert client.get("/library/queue/export?format=ris").status_code == 200
    assert client.get("/profile/download").json()["profile"]["name"] == "Kai"
    saved = celebration(post(client, "/profile/save"))["message"].removeprefix("Profile saved as ")

    # a second profile, then back and compare
    assert celebration(post(client, "/welcome/leave")) is None
    enter_profile(client, "Colleague", adopt=False)
    assert "Colleague" in client.get("/").text
    assert_pages_render(client)
    kai = client.post("/welcome/select", data={"slug": "kai"}, follow_redirects=True)
    assert "Kai" in kai.text
    compared = client.post("/profile/compare", data={"saved": saved})
    assert compared.status_code == 200 and compared.text.count("Kai") >= 2
    assert_pages_render(client)


def test_current_profile_figures_match_saved_file(env):
    """The compare page reads the open profile directly; it must agree with summarize()."""
    from litresearch.backup import export_progress, summarize

    client, path = env
    enter_profile(client, "Kai", adopt=False)
    post(client, "/queue/add", {"title": "Manual paper"})
    pid = 1
    post(client, f"/papers/{pid}/read", {"level": "thorough", "note": "n" * 200})
    page = client.get("/profile/compare").text
    engine = database(path)
    with Session(engine) as session:
        expected = summarize(export_progress(session), profile_cfg(path), "current")
    engine.dispose()
    labels = {
        "XP": "xp",
        "Papers read": "papers_read",
        "Thorough reads": "thorough",
        "Badges": "badges",
        "Active weeks": "weeks",
        r"Notes \(characters\)": "notes_chars",
        "Weekly quests": "quests",
    }
    assert expected["xp"] > 0 and expected["badges"] > 0
    for label, key in labels.items():
        shown = re.search(rf"<td>{label}</td>\s*<td[^>]*>\s*([^<]*?)\s*</td>", page).group(1)
        assert shown == str(expected[key]), label


# --- large data ------------------------------------------------------------------------


@pytest.fixture
def big(env):
    client, path = env
    enter_profile(client, "Veteran", adopt=False)
    ids = populate(
        profile_cfg(path).paths.database,
        n_papers=2500,
        n_libraries=6,
        n_batches=120,
        n_readings=900,
        n_xp=2500,
    )
    return client, path, ids


def test_large_profile_pages_are_bounded(big, monkeypatch):
    client, path, ids = big
    # the open profile is never exported and re-imported for the comparison
    monkeypatch.setattr(web, "summarize", lambda *a, **k: pytest.fail("summarize() called"))
    library = ids["libraries"][0]
    urls = MENU + [
        f"/library/{library}",
        f"/library/{library}?q=paper&status=read",
        f"/recommendations?batch={ids['batches'][3]}",
        f"/papers/{ids['papers'][0]}",
        "/?wpage=3",
    ]
    sizes = assert_pages_render(client, urls)
    for url, size in sizes.items():
        assert size < 250_000, (url, size)


def test_large_library_is_paginated(big):
    client, _, ids = big
    library = ids["libraries"][0]
    page = client.get(f"/library/{library}").text
    assert page.count('action="/library/') and "page 1/40" in page  # 2000 papers, 50 per page
    assert len(re.findall(r'<tr>\s*<td><a href="/papers/', page)) == 50
    last = client.get(f"/library/{library}?page=999").text
    assert "page 40/40" in last and "No papers match" not in last
    filtered = client.get(f"/library/{library}?folder=folder3&status=unread").text
    assert "No papers match" not in filtered
    assert "<b>2000</b> papers" in page


def test_large_library_search_is_paginated(big):
    client, _, _ = big
    first = client.get("/search?q=clonal&where=library").text
    assert "2500 papers in your library" in first and "page 1/50" in first
    assert first.count('class="card paper-card"') == 50
    second = client.get("/search?q=clonal&where=library&page=2").text
    assert "page 2/50" in second and 'where=library&page=1"' in second
    assert "page 50/50" in client.get("/search?q=clonal&where=library&page=9999").text


def test_large_ledger_is_paginated(big):
    client, _, _ = big
    page = client.get("/progress").text
    assert "page 1/50" in page
    ledger = page.split('id="ledger"')[1]
    assert len(re.findall(r'<td style="white-space: nowrap">', ledger)) == 50
    last = client.get("/progress?page=99999").text
    assert "page 50/50" in last and "No transactions yet" not in last
    lines = client.get("/ledger.tsv").text.strip().split("\n")
    assert len(lines) == 2501


# --- hostile and broken input ----------------------------------------------------------


@pytest.fixture
def user(env):
    client, path = env
    enter_profile(client, "Kai", adopt=False)
    post(client, "/queue/add", {"title": "Manual paper"})
    return client, path


def test_html_in_user_text_is_escaped(user):
    client, _ = user
    evil = '<img src=x onerror="alert(1)">'
    post(client, "/queue/add", {"title": evil})
    post(
        client,
        "/recommendations/topics",
        {"topic_name": evil, "topic_weight": "1", "topic_queries": evil},
    )
    for url in (
        "/",
        "/queue",
        "/read",
        "/recommendations",
        "/settings",
        "/search?q=img&where=library",
    ):
        assert "<img src=x" not in client.get(url).text, url
    payload = post(client, "/queue/add", {"title": evil + "2"})
    raw = re.search(r'<script id="celebration"[^>]*>(.*?)</script>', payload.text, re.S).group(1)
    assert "<img" not in raw  # tojson escapes < and >


def test_rating_and_numbers_are_validated(user):
    client, _ = user
    for rating in ("abc", "3.5", "9", "-1"):
        payload = celebration(post(client, "/papers/1/read", {"level": "read", "rating": rating}))
        assert payload["kind"] == "error" and payload["message"] == "Rating must be between 1 and 5"
    for weight in ("nan", "inf", "-inf"):
        payload = celebration(
            post(
                client,
                "/recommendations/topics",
                {"topic_name": "t", "topic_weight": weight, "topic_queries": "q"},
            )
        )
        assert payload["kind"] == "error", weight
    response = post(client, "/welcome/create", {**rules_form(), "name": "X", "quest_xp": "inf"})
    assert celebration(response)["kind"] == "error"


def test_to_number():
    from litresearch.web.settings import to_number

    assert to_number("3", "int") == 3 and to_number("0.5") == 0.5
    for raw in ("nan", "inf", "-Infinity", "abc"):
        with pytest.raises(ValueError):
            to_number(raw)


def test_upload_names_and_sizes(user, monkeypatch):
    client, _ = user
    bib = SAMPLE_BIB.read_bytes()
    for name in ("..", ".", "../../etc/passwd"):
        response = client.post(
            "/library/import",
            data={"target": "new", "name": "L", "action": "check"},
            files={"file": (name, bib)},
        )
        assert response.status_code == 200, name
    monkeypatch.setattr(web, "MAX_UPLOAD", 100)
    payload = celebration(
        client.post(
            "/library/import",
            data={"target": "new", "name": "L"},
            files={"file": ("x.bib", bib)},
            follow_redirects=True,
        )
    )
    assert "larger than" in payload["message"]


def test_huge_requests_are_refused_early(user):
    client, _ = user
    response = client.post(
        "/library/import",
        content=b"x",
        headers={"content-length": str(web.MAX_UPLOAD + 2**21), "content-type": "text/plain"},
    )
    assert response.status_code == 413


def test_profile_files_must_be_objects(user):
    client, _ = user
    for url in ("/welcome/load", "/profile/compare"):
        response = client.post(url, files={"file": ("p.json", b"[1, 2]")}, follow_redirects=True)
        assert response.status_code == 200
        assert "is not a litresearch profile file" in (
            (celebration(response) or {}).get("message", "") + response.text
        )


def test_unknown_ids_never_fail(user):
    client, _ = user
    for url in (
        "/papers/9999",
        "/library/9999",
        "/recommendations?batch=9999",
        "/library/import?to=9999",
        "/library/9999/export",
        "/progress?page=-5",
        "/?weeks=-3",
    ):
        assert client.get(url).status_code in (200, 404), url
    for url in ("/queue/9999/drop", "/queue/9999/move", "/papers/9999/read", "/papers/9999/note"):
        data = {"position": "1", "level": "read", "text": "x"}
        response = client.post(url, data=data, follow_redirects=True)
        assert response.status_code == 200 and celebration(response)["kind"] == "error", url


def test_dashboard_weeks_are_capped(user):
    client, _ = user
    page = client.get("/?weeks=500&wpage=99").text  # one week of history: no pager
    assert page.count('class="card week-card') == 1 and "older →" not in page


def test_dashboard_weekly_reports_are_paged(big):
    client, _, _ = big
    first = client.get("/").text
    assert first.count('class="card week-card') == 4
    assert "page 1/26" in first and 'href="/?wpage=2#weekly"' in first  # 104 weeks, 4 per page
    second = client.get("/?wpage=2").text
    assert "page 2/26" in second and 'href="/?wpage=1#weekly"' in second
    last = client.get("/?wpage=999").text
    assert "page 26/26" in last and "older →" not in last
    assert len(last) < 250_000


def test_streak_multiplier_counts_this_week(user):
    """'Next reads ×' is what a read logged today earns: last week's streak plus this week."""
    client, path = user
    engine = database(path)
    with Session(engine) as session:
        session.add(
            StatusEvent(paper_id=1, status=ReadStatus.read, at=datetime.now() - timedelta(days=7))
        )
        session.commit()
    engine.dispose()
    page = client.get("/").text
    assert "next reads ×1.05" in page
    assert "×1.05" in client.get("/progress").text
