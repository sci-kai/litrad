import json
import re
import shutil
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

import litresearch.web.app as web
from conftest import SAMPLE_BIB, FakeApis, make_work
from litresearch.config import load_config
from litresearch.db import Paper, QueueItem, XpEvent, init_db
from litresearch.ingest.pipeline import ingest_library
from litresearch.web.settings import SettingsError, parse_form, save_settings

CONFIG = """\
# my litresearch config
paths:
  bibtex: {bib}
  database: {db}
  cache_dir: {cache}

topics:
  # tumour evolution is my main topic
  - name: evo
    weight: 1.0
    queries: ["clonal evolution"]
    mesh_terms: ["Clonal Evolution"]

selection:
  n_detail: 2  # two careful reads per week
  n_skim: 3
"""


@pytest.fixture
def app_env(tmp_path, monkeypatch, make_http):
    bib = tmp_path / "library.bib"
    shutil.copy(SAMPLE_BIB, bib)
    path = tmp_path / "config.yaml"
    path.write_text(
        CONFIG.format(bib=bib, db=tmp_path / "db.sqlite", cache=tmp_path / "cache"),
        encoding="utf-8",
    )
    apis = FakeApis()
    apis.sources.append(
        {"id": "https://openalex.org/S1", "summary_stats": {"2yr_mean_citedness": 20.0}}
    )
    fresh = date.today().isoformat()
    apis.search_results["fresh"] = [
        make_work("10.9/a", "Clonal evolution of tumours", fresh, abstract="clonal evolution"),
        make_work("10.9/b", "Lineage tracing of clones", fresh, abstract="lineage tracing clones"),
    ]
    monkeypatch.setattr(web, "make_http", lambda cfg: make_http(apis))
    cfg = load_config(path)
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        ingest_library(cfg, session, make_http(apis), use_s2=False)
    engine.dispose()
    client = TestClient(web.create_app(path))
    yield client, path, apis
    client.close()


def celebration(response) -> dict | None:
    match = re.search(
        r'<script id="celebration" type="application/json">(.*?)</script>', response.text, re.S
    )
    return json.loads(match.group(1)) if match else None


def post(client, url, data=None):
    return client.post(url, data=data or {}, follow_redirects=True)


def db(path):
    cfg = load_config(path)
    return init_db(cfg.paths.database)


def paper_id(path, **where) -> int:
    engine = db(path)
    with Session(engine) as session:
        query = select(Paper.id)
        for key, value in where.items():
            query = query.where(getattr(Paper, key) == value)
        result = session.exec(query).first()
    engine.dispose()
    return result


# --- pages -------------------------------------------------------------------


@pytest.mark.parametrize(
    "url, text",
    [
        ("/", "Weekly quest"),
        ("/recommendations", "No recommendations yet"),
        ("/queue", "Inbox zero"),
        ("/library?q=clonal", "result"),
        ("/progress", "Badges"),
        ("/weekly", "Week of"),
        ("/ledger", "No transactions yet"),
        ("/settings", "Topics"),
        ("/how", "How recommendations are made"),
    ],
)
def test_pages_render(app_env, url, text):
    client, _, _ = app_env
    response = client.get(url)
    assert response.status_code == 200 and text in response.text
    assert "/static/app.css" in response.text


def test_static_files_and_unknown_paper(app_env):
    client, _, _ = app_env
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/favicon.svg").status_code == 200
    assert client.get("/papers/9999").status_code == 404


def test_paper_page(app_env):
    client, path, _ = app_env
    pid = paper_id(path, bibtex_key="Barkley2022-ri")
    response = client.get(f"/papers/{pid}")
    assert "Cancer cell states recur" in response.text and "Not read yet" in response.text


# --- recommendations ---------------------------------------------------------


def test_build_and_show_evidence(app_env):
    client, _, _ = app_env
    response = post(client, "/recommendations/build", {"s2": "false"})
    assert response.status_code == 200
    assert "New batch" in celebration(response)["message"]
    page = client.get("/recommendations").text
    assert "Clonal evolution of tumours" in page
    assert "Why was this recommended" in page and "OpenAlex fresh search" in page
    assert "“clonal evolution”" in page and "Eligible as fresh" in page
    assert "Found by searches" in page  # funnel
    assert "Settings used" in page


def test_add_recommendation_then_drop_costs_xp(app_env):
    client, path, _ = app_env
    post(client, "/recommendations/build", {"s2": "false"})
    pid = paper_id(path, doi="10.9/a")
    response = post(client, "/queue/add", {"ref": f"#{pid}", "next_url": "/recommendations"})
    assert "Added to your queue" in celebration(response)["message"]
    queue = client.get("/queue").text
    assert "from recommendations" in queue and "−15 XP if removed unread" in queue
    assert "data-confirm" in queue
    response = post(client, f"/queue/{pid}/drop")
    payload = celebration(response)
    assert payload["kind"] == "warning" and payload["points"] == -15 and payload["penalty"]
    ledger = client.get("/ledger?kind=penalty").text
    assert "Picked recommendation left the queue unread" in ledger


def test_manual_add_and_free_drop(app_env):
    client, path, _ = app_env
    response = post(client, "/queue/add", {"title": "Preprint from the lab", "url": "https://x"})
    assert "Preprint from the lab" in celebration(response)["message"]
    pid = paper_id(path, title="Preprint from the lab")
    payload = celebration(post(client, f"/queue/{pid}/drop"))
    assert payload["points"] == 0 and payload["kind"] == "info"


def test_add_unknown_doi_and_errors(app_env):
    client, _, apis = app_env
    apis.works.append(make_work("10.9/new", "A brand new paper", "2026-09-30"))
    response = post(client, "/queue/add", {"ref": "10.9/NEW"})
    assert "A brand new paper" in celebration(response)["message"]
    assert celebration(post(client, "/queue/add", {"ref": "10.9/missing"}))["kind"] == "error"
    assert celebration(post(client, "/queue/add", {}))["kind"] == "error"
    assert (
        "Nothing matches"
        in celebration(post(client, "/queue/add", {"ref": "zebrafish xyz"}))["message"]
    )


def test_queue_limit_in_ui(app_env):
    client, path, _ = app_env
    text = path.read_text() + "queue:\n  max_size: 1\n"
    path.write_text(text)
    post(client, "/queue/add", {"ref": "Cheung2016-vl"})
    payload = celebration(post(client, "/queue/add", {"ref": "Barkley2022-ri"}))
    assert payload["kind"] == "error" and "Queue is full (1/1)" in payload["message"]


def test_move_and_read_with_celebration(app_env):
    client, path, _ = app_env
    post(client, "/queue/add", {"ref": "Cheung2016-vl"})
    post(client, "/queue/add", {"ref": "Barkley2022-ri"})
    barkley = paper_id(path, bibtex_key="Barkley2022-ri")
    post(client, f"/queue/{barkley}/move", {"position": "1"})
    assert client.get("/queue").text.index("Cancer cell states") < client.get("/queue").text.index(
        "A collective route"
    )
    response = post(
        client,
        f"/papers/{barkley}/read",
        {"level": "read", "rating": "5", "note": "great", "next_url": "/"},
    )
    payload = celebration(response)
    kinds = [e["kind"] for e in payload["events"]]
    assert "frog" in kinds and payload["badges"][0]["name"] == "First Steps"
    assert payload["points"] > 40
    # the token is used once: a reload shows no celebration
    assert celebration(client.get("/")) is None
    assert "Marked as read" in payload["message"]
    page = client.get(f"/papers/{barkley}").text
    assert "great" in page and "★★★★★" in page
    # "XP from this paper": base points and the frog bonus (half of 40)
    assert "Ate the frog" in page and "+40" in page and "+20" in page


def test_invalid_read_and_note(app_env):
    client, path, _ = app_env
    pid = paper_id(path, bibtex_key="Cheung2016-vl")
    assert (
        celebration(post(client, f"/papers/{pid}/read", {"level": "nonsense"}))["kind"] == "error"
    )
    assert (
        celebration(post(client, f"/papers/{pid}/read", {"level": "dismissed"}))["kind"] == "error"
    )
    payload = celebration(post(client, f"/papers/{pid}/note", {"text": "x"}))
    assert payload["kind"] == "error" and "Mark the paper first" in payload["message"]
    post(client, f"/papers/{pid}/read", {"level": "abstract"})
    assert celebration(post(client, f"/papers/{pid}/note", {"text": "  "}))["kind"] == "error"
    assert celebration(post(client, f"/papers/{pid}/note", {"text": "y" * 150}))["points"] == 10


def test_dismiss(app_env):
    client, path, _ = app_env
    pid = paper_id(path, bibtex_key="Cheung2016-vl")
    payload = celebration(post(client, f"/papers/{pid}/dismiss", {"next_url": "/"}))
    assert "not relevant" in payload["message"]
    assert "dismissed" in client.get(f"/papers/{pid}").text


def test_open_redirects_are_blocked(app_env):
    client, path, _ = app_env
    pid = paper_id(path, bibtex_key="Cheung2016-vl")
    response = client.post(
        f"/papers/{pid}/read",
        data={"level": "abstract", "next_url": "//evil.example"},
        follow_redirects=False,
    )
    assert response.headers["location"].startswith("/?c=")


def test_cross_origin_posts_are_rejected(app_env):
    client, _, _ = app_env
    response = client.post(
        "/queue/add", data={"title": "x"}, headers={"Origin": "https://evil.example"}
    )
    assert response.status_code == 403
    ok = client.post(
        "/queue/add",
        data={"title": "x"},
        headers={"Origin": "http://testserver"},
        follow_redirects=False,
    )
    assert ok.status_code == 303


# --- progress, ledger, weekly ------------------------------------------------------


def test_ledger_and_tsv(app_env):
    client, path, _ = app_env
    pid = paper_id(path, bibtex_key="Cheung2016-vl")
    post(client, f"/papers/{pid}/read", {"level": "thorough"})
    page = client.get("/ledger").text
    assert "thorough: A collective route" in page and "Badge: First Steps" in page
    tsv = client.get("/ledger.tsv")
    assert tsv.headers["content-type"].startswith("text/tab-separated-values")
    lines = tsv.text.strip().split("\n")
    assert lines[0] == "timestamp\tpoints\tbalance\tkind\treason\tpaper_doi"
    assert lines[1].split("\t")[3] == "read"
    assert lines[-1].split("\t")[2] == str(sum(int(line.split("\t")[1]) for line in lines[1:]))


def test_progress_and_weekly_after_reading(app_env):
    client, path, _ = app_env
    pid = paper_id(path, bibtex_key="Cheung2016-vl")
    post(client, f"/papers/{pid}/read", {"level": "read"})
    progress = client.get("/progress").text
    assert "unlocked" in progress and "First Steps" in progress and "Personal bests" in progress
    weekly = client.get("/weekly").text
    assert "this week" in weekly and "A collective route" in weekly


# --- settings ------------------------------------------------------------------


def settings_form(client) -> dict:
    page = client.get("/settings").text
    data = {}
    for name, value in re.findall(
        r'<input type="number" id="[^"]+" name="([^"]+)" value="([^"]*)"', page
    ):
        data[name] = value
    for name in re.findall(r'<input type="checkbox" name="([^"]+)" checked', page):
        data[name] = "on"
    data["topic_name"] = ["evo"]
    data["topic_weight"] = ["1.0"]
    data["topic_queries"] = ["clonal evolution"]
    data["excluded_terms"] = ""
    return data


def test_settings_round_trip_keeps_comments(app_env):
    client, path, _ = app_env
    data = settings_form(client)
    data["selection.mmr_lambda"] = "0.55"
    data["game.drop_penalty"] = "25"
    data["topic_name"] = ["evo", "ctdna"]
    data["topic_weight"] = ["1.0", "0.6"]
    data["topic_queries"] = ["clonal evolution\ntumour phylogeny", "circulating tumor DNA"]
    data["excluded_terms"] = "veterinary\nmouse only"
    response = post(client, "/settings", data)
    assert "Settings saved" in celebration(response)["message"]
    text = path.read_text()
    assert "# my litresearch config" in text and "# tumour evolution is my main topic" in text
    assert "# two careful reads per week" in text
    cfg = load_config(path)
    assert cfg.selection.mmr_lambda == 0.55 and cfg.game.drop_penalty == 25
    assert [t.name for t in cfg.topics] == ["evo", "ctdna"]
    assert cfg.topics[0].mesh_terms == ["Clonal Evolution"]  # untouched field kept
    assert cfg.topics[0].queries == ["clonal evolution", "tumour phylogeny"]
    assert cfg.excluded_terms == ["veterinary", "mouse only"]
    assert path.with_name("config.yaml.bak").exists()
    how = client.get("/how").text
    assert "−25" in how and "0.55" in how and "ctdna" in how


def test_invalid_settings_change_nothing(app_env):
    client, path, _ = app_env
    before = path.read_text()
    data = settings_form(client)
    data["selection.n_detail"] = "4"  # 4 + 3 > 5
    payload = celebration(post(client, "/settings", data))
    assert payload["kind"] == "error" and "Not saved" in payload["message"]
    data = settings_form(client)
    data["selection.knn"] = "abc"
    assert "not a number" in celebration(post(client, "/settings", data))["message"]
    data = settings_form(client)
    for key in ("relevance", "impact", "timeliness"):
        data[f"selection.weights.{key}"] = "0"
    assert "weight must be above 0" in celebration(post(client, "/settings", data))["message"]
    assert path.read_text() == before


def test_parse_form_units():
    form = {s: "1" for s in ("selection.n_detail",)}
    with pytest.raises(SettingsError, match="is empty"):
        parse_form(form)


def test_save_settings_disabling_sources(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text("paths:\n  bibtex: x.bib\nsources:\n  openalex: true  # keep\n")
    cfg = save_settings(path, {"sources": {"openalex": False}})
    assert cfg.sources.openalex is False and "# keep" in path.read_text()


def test_xp_events_unchanged_by_page_views(app_env):
    client, path, _ = app_env
    for url in ("/", "/progress", "/weekly", "/ledger", "/how"):
        client.get(url)
    engine = db(path)
    with Session(engine) as session:
        assert session.exec(select(XpEvent)).all() == []
        assert session.exec(select(QueueItem)).all() == []
    engine.dispose()
