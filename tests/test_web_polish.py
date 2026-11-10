"""Usability of the web UI: labels, error pages, readable messages, paging."""

import html
import json
import re
import tempfile
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlmodel import Session

import litrad.game as game
import litrad.web.app as web
from conftest import enter_profile, profile_cfg, rules_form
from litrad.config import Config
from litrad.db import XpEvent, init_db
from litrad.rules import GameRules
from litrad.web.settings import SettingsError, friendly_error, parse_topics
from web_bigdata import populate

PAGES = [
    "/",
    "/queue",
    "/recommendations",
    "/recommendations?topics=1",
    "/progress",
    "/library",
    "/library/import",
    "/search",
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


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text(
        f"paths:\n  cache_dir: {tmp_path / 'cache'}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(web, "make_http", lambda cfg: pytest.fail("no network in these tests"))
    client = TestClient(web.create_app(path))
    yield client, path
    client.close()


@pytest.fixture
def user(env):
    client, path = env
    enter_profile(client, "Kai")
    client.post("/queue/add", data={"title": "Manual paper"})
    return client, path


# --- every form control has a name a screen reader can read ----------------------------


class Controls(HTMLParser):
    """Form controls without a label, aria-label or wrapping <label>."""

    def __init__(self):
        super().__init__()
        self.label_depth = 0
        self.label_for: set[str] = set()
        self.controls: list[dict] = []
        self.in_template = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "template":
            self.in_template += 1
        if tag == "label":
            self.label_depth += 1
            if attrs.get("for"):
                self.label_for.add(attrs["for"])
        if tag in ("input", "select", "textarea"):
            if attrs.get("type") in ("hidden", "submit", "button"):
                return
            self.controls.append({**attrs, "_tag": tag, "_wrapped": self.label_depth > 0})

    def handle_endtag(self, tag):
        if tag == "label":
            self.label_depth = max(self.label_depth - 1, 0)
        if tag == "template":
            self.in_template -= 1

    def unlabelled(self) -> list[dict]:
        return [
            c
            for c in self.controls
            if not c["_wrapped"]
            and not c.get("aria-label")
            and not c.get("aria-labelledby")
            and c.get("id") not in self.label_for
        ]


def unlabelled(html: str) -> list[dict]:
    parser = Controls()
    parser.feed(html)
    return parser.unlabelled()


def test_every_control_has_a_label(user):
    client, _ = user
    paper = client.get("/queue").text
    paper_id = re.search(r'href="/papers/(\d+)"', paper).group(1)
    for url in [*PAGES, f"/papers/{paper_id}", "/search?q=paper&where=library"]:
        assert unlabelled(client.get(url).text) == [], url
    client.post("/welcome/leave")
    assert unlabelled(client.get("/welcome").text) == []


def test_login_page_labels_the_password(env):
    client, _ = env
    assert unlabelled(client.get("/login").text) == []


def test_rating_stars_and_topic_fields_are_named(user):
    client, _ = user
    queue = client.get("/queue").text
    for star in ("1 star", "2 stars", "5 stars"):
        assert f'aria-label="{star}"' in queue
    editor = client.get("/recommendations?topics=1").text
    for label in ("Topic name", "Weight", "Search queries"):
        assert f"<span>{label}" in editor
    assert 'aria-label="Remove this topic"' in editor


def test_keyboard_shortcuts_are_explained(user):
    client, _ = user
    page = client.get("/").text
    assert "data-shortcuts" in page and 'id="shortcuts-help"' in page
    assert "keys: <code>n</code>" not in page


# --- error pages -----------------------------------------------------------------------


def test_unknown_pages_render_a_friendly_page(user):
    client, _ = user
    for url in ("/papers/999999", "/papers/abc", "/no/such/page"):
        response = client.get(url)
        assert response.status_code == 404, url
        assert response.headers["content-type"].startswith("text/html"), url
        assert "Page not found" in response.text and "/static/app.css" in response.text, url
        assert 'class="sidebar"' in response.text  # the normal layout with the menu
    assert "no paper with the number 999999" in client.get("/papers/999999").text


def test_invalid_values_render_a_friendly_page(user):
    client, _ = user
    response = client.get("/progress?page=abc")
    assert response.status_code == 422
    assert "Invalid address" in response.text and "&#39;page&#39; must be a whole number" in (
        response.text
    )
    assert "pydantic" not in response.text


def test_json_clients_keep_json_errors(user):
    client, _ = user
    response = client.get("/papers/abc", headers={"accept": "application/json"})
    assert response.status_code == 422 and "detail" in response.json()
    response = client.get("/papers/999999", headers={"accept": "application/json"})
    assert response.status_code == 404 and "detail" in response.json()


def test_error_page_without_a_profile(env):
    client, _ = env
    response = client.get("/welcome/nothing-here")
    assert response.status_code == 404 and "Page not found" in response.text
    assert 'class="sidebar"' not in response.text


# --- readable messages -----------------------------------------------------------------


def test_profile_creation_names_the_field(env):
    client, _ = env
    response = client.post(
        "/welcome/create", data={**rules_form(), "name": "X", "cheese_bonus": "20"}
    )
    message = celebration(response)["message"]
    assert "'🧀 top paper first: share of base points' must be at most 10" in message
    assert "pydantic" not in message and "validation error" not in message


def test_topic_errors_are_readable(user):
    client, _ = user
    for data, expected in (
        (
            {"topic_name": "evo", "topic_weight": "-1", "topic_queries": "q"},
            "Topic 'evo': weight must be at least 0",
        ),
        (
            {"topic_name": ["a", "b"], "topic_weight": "1", "topic_queries": "q"},
            "The topic form is incomplete",
        ),
    ):
        message = celebration(client.post("/recommendations/topics", data=data))["message"]
        assert expected in message and "zip()" not in message, message


def test_friendly_error_uses_labels():
    with pytest.raises(ValidationError) as caught:
        Config.model_validate({"paths": {}, "queue": {"max_size": 0}})
    assert friendly_error(caught.value) == "'Queue size' must be at least 1"
    with pytest.raises(ValidationError) as caught:
        GameRules.model_validate({"levels": [{"xp": 0, "title": ""}]})
    text = friendly_error(caught.value)
    assert "Level 1: title" in text and "must not be empty" in text
    wrapped = SettingsError("x")
    wrapped.__cause__ = caught.value
    assert friendly_error(wrapped) == text
    assert friendly_error(ValueError("plain")) == "plain"
    with pytest.raises(SettingsError):
        parse_topics({"topic_name": ["a"], "topic_weight": [], "topic_queries": []})


# --- paging and lists ------------------------------------------------------------------


@pytest.fixture
def big(env):
    client, path = env
    enter_profile(client, "Veteran")
    ids = populate(
        profile_cfg(path).paths.database,
        n_papers=600,
        n_libraries=3,
        n_batches=60,
        n_readings=200,
        n_xp=300,
        n_queue=3,
    )
    return client, path, ids


def test_ledger_kind_links_are_encoded(user):
    client, path = user
    engine = init_db(profile_cfg(path).paths.database)
    with Session(engine) as session:
        session.add(XpEvent(points=5, kind="odd kind&x=1", reason="test"))
        session.commit()
    engine.dispose()
    page = client.get("/progress").text
    assert 'href="/progress?kind=odd+kind%26x%3D1#ledger"' in page or (
        'href="/progress?kind=odd%20kind%26x%3D1#ledger"' in page
    )


def test_earlier_recommendation_sets_are_grouped_by_month(big):
    client, _, _ = big
    client.post("/recommendations/topics/skip")
    page = client.get("/recommendations").text
    groups = re.findall(r'<optgroup label="(\d{4}-\d{2})">', page)
    assert len(groups) >= 6 and groups == sorted(groups, reverse=True)
    assert page.count("<option ") == 60


def test_library_search_says_which_results_are_shown(big):
    client, _, _ = big
    page = client.get("/search?q=clonal&where=library&page=2").text
    assert "Showing 51–100 · page 2 of 12" in page


def test_library_deletion_asks_once(big):
    client, _, ids = big
    page = client.get(f"/library/{ids['libraries'][0]}").text
    form = re.search(r'<form method="post" action="/library/\d+/delete"[^>]*>', page).group(0)
    assert "data-confirm" not in form
    assert 'name="confirm_name"' in page


def test_compare_lists_saved_files_with_dates(user):
    client, path = user
    client.post("/profile/save")
    page = client.get("/profile/compare").text
    assert 'class="save-list"' in page
    assert re.search(r"<small class=\"muted\">\d{4}-\d{2}-\d{2} \d{2}:\d{2}</small>", page)
    # the saved file is an archive: compared from the list and as an upload
    name = re.search(r'name="saved" value="(litrad_profile_[^"]+\.zip)"', page).group(1)
    compared = client.post("/profile/compare", data={"saved": name}).text
    assert name in compared and "same rules" in compared and "notice error" not in compared
    saved = next(path.parent.rglob(name))
    uploaded = client.post("/profile/compare", files={"file": ("saved.zip", saved.read_bytes())})
    assert "saved.zip" in uploaded.text and "notice error" not in uploaded.text
    assert not list(Path(tempfile.gettempdir()).glob("litrad-upload-*"))  # removed


def test_new_profile_dashboard_shows_first_steps(env):
    client, _ = env
    enter_profile(client, "Newbie")
    page = client.get("/").text
    assert "Getting started" in page and "0/5 done" in page
    assert 'href="/library/import"' in page


def test_progress_computes_stats_once(big, monkeypatch):
    client, _, _ = big
    calls = []
    original = game.stats

    def counting(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(game, "stats", counting)
    monkeypatch.setattr(web, "stats", counting)
    assert client.get("/progress").status_code == 200
    assert len(calls) == 1
    calls.clear()
    assert client.get("/").status_code == 200
    assert len(calls) == 1


def test_career_ladder_is_a_rank_table(user):
    client, path = user
    engine = init_db(profile_cfg(path).paths.database)
    with Session(engine) as session:
        session.add(XpEvent(points=1500, kind="read", reason="test"))
        session.commit()
    engine.dispose()
    page = client.get("/progress").text
    ladder = page.split('id="ladder"')[1].split("XP ledger")[0]
    assert '<th scope="col" class="num">Lv</th>' in ladder and "XP needed" in ladder
    current = ladder.split('aria-current="true"')[1].split("</tr>")[0]
    rules = GameRules()
    level = next(i for i, step in enumerate(rules.levels, 1) if step.xp > 1500) - 1

    def esc(text):  # as Jinja escapes it
        return html.escape(text).replace("&#x27;", "&#39;")

    title = esc(rules.levels[level - 1].title)
    assert f">{level}</td>" in current and title in current
    assert "· you" in current and "XP to go" in ladder
    assert f"All {len(rules.levels)} levels" in ladder
    assert f"XP to <b>Lv {level + 1} · {esc(rules.levels[level].title)}</b>" in page
    assert ladder.count("<tr") == 1 + 6 + 1 + len(rules.levels)  # near you, then all
    profile = client.get("/profile").text
    assert "XP needed" in profile and esc(rules.levels[-1].title) in profile
