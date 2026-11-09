"""Several profiles side by side: folders, settings, the welcome screen and the CLI."""

import json
import zipfile
from datetime import date, datetime, timedelta

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlmodel import Session, select
from typer.testing import CliRunner

import litresearch.cli as cli
from conftest import SAMPLE_BIB, enter_profile, profile_cfg, rules_form
from litresearch import profiles
from litresearch.backup import BackupError, export_progress
from litresearch.config import ConfigError, load_config
from litresearch.db import Paper, Profile, QueueItem, RecommendationBatch, init_db
from litresearch.profile import profile_rules
from litresearch.reading import queue_add
from litresearch.rules import GameRules
from litresearch.web.app import PROFILE_COOKIE, create_app

runner = CliRunner()

CONFIG = """\
paths:
  bibtex: {bib}
  database: {db}
  cache_dir: {cache}
topics:
  - name: evo
    queries: ["clonal evolution"]
    mesh_terms: ["Clonal Evolution"]
excluded_terms: [mouse]
backup:
  dir: {backups}
"""


@pytest.fixture
def config_path(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        CONFIG.format(
            bib=SAMPLE_BIB,
            db=tmp_path / "legacy.sqlite",
            cache=tmp_path / "cache",
            backups=tmp_path / "backups",
        )
    )
    return path


@pytest.fixture
def base(config_path):
    return load_config(config_path)


def legacy_data(base, title="Old library paper"):
    """A database from before profiles existed."""
    engine = init_db(base.paths.database)
    with Session(engine) as session:
        session.add(Paper(title=title, doi="10.1/old", in_library=True))
        session.commit()
    engine.dispose()


def stored(session, title: str) -> Paper:
    paper = Paper(title=title)
    session.add(paper)
    session.commit()
    session.refresh(paper)
    return paper


def papers(cfg) -> list[str]:
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        titles = list(session.exec(select(Paper.title)).all())
    engine.dispose()
    return titles


# --- profile folders --------------------------------------------------------------------


def test_create_profile_with_locked_rules(base, tmp_path):
    rules = GameRules(drop_penalty=30)
    slug = profiles.create_profile(base, "Ada Lovelace", rules)
    assert slug == "ada-lovelace"
    home = tmp_path / "data/profiles/ada-lovelace"
    assert (home / "litresearch.sqlite").exists() and (home / "settings.yaml").exists()
    cfg = profiles.profile_config(base, slug)
    assert cfg.paths.database.resolve() == home / "litresearch.sqlite"
    assert cfg.paths.bibtex.resolve() == home / "library.bib"
    assert cfg.backup.dir == tmp_path / "backups/ada-lovelace"
    # a new profile does not inherit the topics of config.yaml: they are asked for
    assert cfg.topics == [] and cfg.excluded_terms == []
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        profile = session.exec(select(Profile)).one()
        assert profile.name == "Ada Lovelace" and profile_rules(profile).drop_penalty == 30
    engine.dispose()


@pytest.mark.parametrize(
    "name, message", [("", "name"), ("x" * 81, "at most 80"), ("ADA", "already exists")]
)
def test_profile_names(base, name, message):
    profiles.create_profile(base, "Ada")
    with pytest.raises(profiles.ProfilesError, match=message):
        profiles.create_profile(base, name)
    assert [i.name for i in profiles.list_profiles(base)] == ["Ada"]


def test_similar_names_get_their_own_folder(base):
    assert profiles.create_profile(base, "Ada") == "ada"
    assert profiles.create_profile(base, "Ada!") == "ada-2"
    assert profiles.create_profile(base, "🧬") == "profile"
    with pytest.raises(profiles.ProfilesError, match="Invalid profile id"):
        profiles.folder(base, "../escape")


def test_failed_creation_leaves_nothing(base, monkeypatch, tmp_path):
    def broken(*_args, **_kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(profiles, "start_profile", broken)
    with pytest.raises(RuntimeError):
        profiles.create_profile(base, "Ada")
    assert not (tmp_path / "data/profiles/ada").exists()


def test_settings_overlay(base):
    slug = profiles.create_profile(base, "Ada")
    profiles.write_overlay(
        base,
        slug,
        {
            "selection": {"n_recommendations": 3},
            "topics": [{"name": "ctdna", "weight": 0.5, "queries": ["ctDNA"]}],
            "onboarding": {"topics_done": True},
        },
    )
    cfg = profiles.profile_config(base, slug)
    assert cfg.selection.n_recommendations == 3 and cfg.selection.knn == 5
    assert [t.name for t in cfg.topics] == ["ctdna"]
    assert profiles.meta(base, slug) == {"onboarding": {"topics_done": True}}
    assert "onboarding" not in profiles.overlay(base, slug)
    before = (profiles.folder(base, slug) / "settings.yaml").read_text()
    with pytest.raises(ValueError):
        profiles.write_overlay(base, slug, {"selection": {"n_recommendations": 9}})
    with pytest.raises(profiles.ProfilesError, match="Not a profile setting: web"):
        profiles.write_overlay(base, slug, {"web": {"port": 1}})
    assert (profiles.folder(base, slug) / "settings.yaml").read_text() == before


def test_profiles_are_sorted_by_activity(base):
    profiles.create_profile(base, "Old", at=datetime(2026, 1, 1))
    profiles.create_profile(base, "New", at=datetime(2026, 9, 1))
    assert [i.name for i in profiles.list_profiles(base)] == ["New", "Old"]


def test_current_profile(base):
    assert profiles.get_current(base) is None
    profiles.create_profile(base, "Ada")
    assert profiles.get_current(base) == "ada"  # the only one
    profiles.create_profile(base, "Grace")
    assert profiles.get_current(base) is None  # ambiguous
    profiles.set_current(base, "grace")
    assert profiles.get_current(base) == "grace"
    assert profiles.find(base, "ADA") == "ada" and profiles.find(base, "grace") == "grace"
    with pytest.raises(profiles.ProfilesError):
        profiles.find(base, "nobody")
    with pytest.raises(profiles.ProfilesError):
        profiles.set_current(base, "nobody")


# --- existing data and saved files --------------------------------------------------------


def test_adopt_existing_data(base):
    assert profiles.legacy_available(base) is None  # nothing there
    legacy_data(base)
    info = profiles.legacy_available(base)
    assert info["library"] == 1 and info["readings"] == 0
    slug = profiles.adopt_legacy(base, "Kai")
    cfg = profiles.profile_config(base, slug)
    assert papers(cfg) == ["Old library paper"]
    # topics come along; the Paperpile export keeps being read where it is
    assert [t.name for t in cfg.topics] == ["evo"] and cfg.topics[0].mesh_terms
    assert cfg.excluded_terms == ["mouse"] and cfg.paths.bibtex == SAMPLE_BIB
    assert profiles.legacy_available(base) is None  # offered once
    assert papers(base) == ["Old library paper"]  # the original stays
    with pytest.raises(profiles.ProfilesError, match="no existing data"):
        profiles.adopt_legacy(base, "Again")


def test_load_a_saved_profile_as_a_new_one(base):
    slug = profiles.create_profile(base, "Ada")
    profiles.write_overlay(
        base,
        slug,
        {"topics": [{"name": "t", "queries": ["q"]}], "paths": {"bibtex": "/elsewhere.bib"}},
    )
    cfg = profiles.profile_config(base, slug)
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        queue_add(session, cfg, stored(session, "Queued"))
        data = export_progress(session, settings=profiles.overlay(base, slug))
    engine.dispose()
    copy = profiles.import_profile_file(base, data, name="Ada on the laptop")
    copied = profiles.profile_config(base, copy)
    assert profiles.describe(base, copy).name == "Ada on the laptop"
    assert "Queued" in papers(copied) and [t.name for t in copied.topics] == ["t"]
    assert copied.paths.bibtex.name == "library.bib"  # paths of another machine are dropped
    data["profile"]["name"] = "Edited by hand"
    with pytest.raises(BackupError, match="modified"):
        profiles.import_profile_file(base, data, name="Tampered")
    assert not profiles.exists(base, "tampered")


# --- web: choosing a profile --------------------------------------------------------------


@pytest.fixture
def client(config_path):
    with TestClient(create_app(config_path)) as client:
        yield client


def test_nothing_without_a_profile(client):
    response = client.get("/queue", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/welcome"
    assert client.post("/queue/add", data={"title": "x"}).status_code == 401
    page = client.get("/").text
    assert "Create a new profile" in page and "Load a saved profile" in page
    assert "Continue with your profile" not in page and "EXISTING DATA" not in page
    assert client.get("/static/app.css").status_code == 200


def test_forged_profile_cookie_is_ignored(client):
    enter_profile(client, "Ada", adopt=False)
    client.cookies.set(PROFILE_COOKIE, "ada.0000")
    assert client.get("/", follow_redirects=False).headers["location"] == "/welcome"


def test_invalid_rules_create_nothing(client, base):
    form = {"name": "Ada", **rules_form(), "levels": "5 | Wrong start"}
    page = client.post("/welcome/create", data=form).text
    assert "start at 0" in page
    form = {"name": "Ada", **rules_form(), "xp_read": "abc"}
    assert "not a number" in client.post("/welcome/create", data=form).text
    assert profiles.list_profiles(base) == []


def test_profiles_are_separate(client, config_path):
    enter_profile(client, "Ada", adopt=False, xp_read="55")
    client.post("/queue/add", data={"title": "Ada's paper"})
    client.post("/papers/1/read", data={"level": "read"})
    page = client.get("/").text
    assert "Ada" in page and "55" in page
    enter_profile(client, "Grace", adopt=False)
    queue = client.get("/queue").text
    assert "Grace" in queue and "Ada&#39;s paper" not in queue and "Inbox zero" in queue
    # switching back
    client.post("/welcome/leave")
    welcome = client.get("/welcome").text
    assert "Continue with your profile" in welcome and "Ada" in welcome and "Grace" in welcome
    client.post("/welcome/select", data={"slug": "ada"})
    assert "Ada&#39;s paper" in client.get("/progress").text
    assert profiles.get_current(load_config(config_path)) == "ada"  # the CLI follows
    assert "does not exist" in client.post("/welcome/select", data={"slug": "nobody"}).text


def test_adopt_existing_data_in_the_browser(client, base):
    legacy_data(base)
    page = client.get("/welcome").text
    assert "EXISTING DATA FOUND" in page and "1 library papers" in page
    enter_profile(client, "Kai")
    assert "Old library paper" in client.get("/library/1").text
    assert "EXISTING DATA FOUND" not in client.get("/welcome").text


def test_load_profile_in_the_browser(client, config_path, tmp_path):
    enter_profile(client, "Ada", adopt=False)
    client.post("/queue/add", data={"title": "Saved paper"})
    download = client.get("/profile/download")
    assert download.json()["settings"]["topics"] == []
    saved = client.post("/profile/save", follow_redirects=True)
    assert "Profile saved" in saved.text
    saves = list((tmp_path / "backups/ada").glob("litresearch_profile_ada_*.zip"))
    assert saves  # the archive format
    client.post("/welcome/leave")
    page = client.get("/welcome").text
    assert "ada/litresearch_profile_ada_" in page  # saved files are offered
    from_saved = client.post(
        "/welcome/load", data={"saved": saves[0].name, "name": "Ada archive"}, follow_redirects=True
    )
    assert "Loaded profile Ada archive" in from_saved.text
    client.post("/welcome/leave")
    uploaded = client.post(
        "/welcome/load",
        data={"name": "Ada upload"},
        files={"file": ("ada.zip", saves[0].read_bytes())},
        follow_redirects=True,
    )
    assert "Loaded profile Ada upload" in uploaded.text
    client.post("/welcome/leave")
    response = client.post(
        "/welcome/load",
        data={"name": "Ada copy"},
        files={"file": ("ada.json", download.content)},
        follow_redirects=True,
    )
    assert "Loaded profile Ada copy" in response.text
    assert "Saved paper" in client.get("/queue").text
    refused = client.post("/welcome/load", data={}, follow_redirects=True)
    assert "Choose a saved profile" in refused.text
    broken = client.post(
        "/welcome/load", files={"file": ("x.json", b"{nope")}, follow_redirects=True
    )
    assert "Not loaded" in broken.text


def test_profile_page(client):
    enter_profile(client, "Ada", adopt=False, xp_read="55")
    enter_profile(client, "Grace", adopt=False)
    page = client.get("/profile").text
    assert "RULES LOCKED" in page and "Grace" in page and "Ada" in page  # Ada: other profile
    assert "data/profiles/grace" in page and "Switch profile" in page
    compare = client.post(
        "/profile/compare", files={"file": ("me.json", client.get("/profile/download").content)}
    )
    assert "same rules" in compare.text and "unmodified" in compare.text
    # the removed actions are gone
    for url in ("/profile/start", "/profile/load", "/profile/reset"):
        assert client.post(url).status_code in (404, 405)


# --- web: topics, reminders and merged pages --------------------------------------------------


def test_topics_are_asked_for_first(client, config_path):
    enter_profile(client, "Ada", adopt=False)
    page = client.get("/recommendations").text
    assert "What are you interested in?" in page and "Skip: library only" in page
    bad = {"topic_name": "evo", "topic_weight": "lots", "topic_queries": "q"}
    assert "weight must be a number" in client.post("/recommendations/topics", data=bad).text
    form = {
        "topic_name": ["evo", ""],
        "topic_weight": ["1.0", "1.0"],
        "topic_queries": ["clonal evolution\ntumour phylogeny", ""],
        "excluded_terms": "veterinary",
    }
    saved = client.post("/recommendations/topics", data=form, follow_redirects=True).text
    assert "Saved 1 topic for this profile" in saved and "What are you interested in" not in saved
    cfg = profile_cfg(config_path)
    assert cfg.topics[0].queries == ["clonal evolution", "tumour phylogeny"]
    assert cfg.excluded_terms == ["veterinary"]
    assert "Get recommendations" in saved and "✏️ Edit topics" in saved
    assert "How recommendations are made" in saved and "veterinary" in saved


def test_topics_can_be_skipped(client, config_path):
    enter_profile(client, "Ada", adopt=False)
    page = client.post("/recommendations/topics/skip", follow_redirects=True).text
    assert "library" in page and "What are you interested in" not in page
    assert profile_cfg(config_path).topics == []


def add_batch(config_path, days_ago: int):
    engine = init_db(profile_cfg(config_path).paths.database)
    with Session(engine) as session:
        moment = datetime.now() - timedelta(days=days_ago)
        session.add(RecommendationBatch(created_at=moment, week_start=moment.date()))
        session.commit()
    engine.dispose()


def test_reminder_for_new_recommendations(client, config_path):
    enter_profile(client, "Ada", adopt=False)
    assert 'title="time for new recommendations"' in client.get("/queue").text
    assert "✨ No recommendations yet." in client.get("/").text
    add_batch(config_path, days_ago=2)
    assert "time for new recommendations" not in client.get("/queue").text
    add_batch(config_path, days_ago=0)
    engine = init_db(profile_cfg(config_path).paths.database)
    with Session(engine) as session:
        for batch in session.exec(select(RecommendationBatch)).all():
            batch.created_at = datetime.now() - timedelta(days=9)
            session.add(batch)
        session.commit()
    engine.dispose()
    assert "9 days old" in client.get("/").text
    client.post("/recommendations/topics/skip")
    assert "9 days old: time for new ones" in client.get("/recommendations").text
    profiles.write_overlay(load_config(config_path), "ada", {"selection": {"reminder_days": 0}})
    assert "time for new recommendations" not in client.get("/queue").text


def test_old_pages_redirect(client):
    enter_profile(client, "Ada", adopt=False)

    def location(url):
        return client.get(url, follow_redirects=False).headers["location"]

    # the weekly reports are paged on the dashboard now
    assert location("/weekly") == "/#weekly"
    assert location("/weekly?weeks=500") == "/#weekly"
    assert location("/ledger") == "/progress#ledger"
    assert location("/ledger?kind=read&page=2") == "/progress?kind=read&page=2#ledger"
    assert location("/how") == "/recommendations#how"


def test_merged_pages(client):
    enter_profile(client, "Ada", adopt=False)
    client.post("/queue/add", data={"title": "T"})
    client.post("/papers/1/read", data={"level": "read"})
    dashboard = client.get("/").text
    assert "Weekly reports" in dashboard and "Week of" in dashboard
    assert 'id="weekly"' in dashboard
    progress = client.get("/progress").text
    assert "XP ledger" in progress and "Points, penalties and levels" in progress
    assert "Badges" in progress and "/ledger.tsv" in progress
    assert "No transactions" in client.get("/progress?kind=penalty").text
    nav = client.get("/queue").text
    for label in (
        "Reading queue",
        "Recommendations",
        "Progress &amp; XP",
        "Libraries",
        "Find &amp; add",
    ):
        assert label in nav
    assert 'href="/how"' not in nav and 'href="/ledger"' not in nav


# --- command line -------------------------------------------------------------------------


@pytest.fixture
def plain_console(monkeypatch):
    from rich.console import Console

    import litresearch.cli_data as cli_data
    import litresearch.cli_reading as cli_reading

    console = Console(color_system=None, width=300, highlight=False)
    for module in (cli, cli_data, cli_reading):
        monkeypatch.setattr(module, "console", console)


def test_cli_profiles(config_path, plain_console, tmp_path):
    def run(*args, profile=None):
        prefix = ["--profile", profile] if profile else []
        return runner.invoke(cli.app, [*prefix, *args, "-c", str(config_path)])

    result = run("profile", "list")
    assert "No profiles yet" in result.stdout
    result = run("profile", "create", "Ada", "--yes")
    assert (
        result.exit_code == 0 and "Created 'Ada'" in result.stdout and "thorough" in result.stdout
    )
    assert "library.bib" in result.stdout
    assert run("profile", "create", "Ada", "--yes").exit_code == 1  # duplicate
    assert run("profile", "create", "Grace", "--yes").exit_code == 0
    out = run("profile", "list").stdout
    assert "* Grace" in out and "  Ada" in out
    assert run("queue", "add", "--title", "Grace's paper").exit_code == 0
    assert "Grace's paper" in run("queue").stdout
    assert "Grace's paper" not in run("queue", profile="Ada").stdout
    assert run("profile", "use", "ada").exit_code == 0
    assert "Ada" in run("profile", "show").stdout
    assert run("profile", "use", "nobody").exit_code == 1
    result = run("queue", profile="nobody")
    assert isinstance(result.exception, ConfigError) and "No profile 'nobody'" in str(
        result.exception
    )
    target = tmp_path / "grace.json"
    assert run("profile", "save", str(target), profile="Grace").exit_code == 0
    assert json.loads(target.read_text())["settings"]["topics"] == []
    result = run("profile", "load", str(target), "--name", "Grace 2")
    assert result.exit_code == 0 and "Created 'Grace 2'" in result.stdout
    assert "Grace's paper" in run("queue").stdout  # the loaded profile is current now
    result = run("profile", "compare", str(target))
    assert "same rules" in result.stdout
    # without a current profile, commands ask which one to use
    (tmp_path / "data/profiles/.current").unlink()
    result = run("queue")
    assert isinstance(result.exception, ConfigError) and "several profiles" in str(result.exception)


def test_cli_without_profiles_uses_existing_data(config_path, base, plain_console):
    legacy_data(base)
    result = runner.invoke(cli.app, ["status", "-c", str(config_path)])
    assert result.exit_code == 0 and "1" in result.stdout
    result = runner.invoke(cli.app, ["profile", "list", "-c", str(config_path)])
    assert "profile adopt NAME" in result.stdout
    result = runner.invoke(cli.app, ["profile", "adopt", "Kai", "--yes", "-c", str(config_path)])
    assert result.exit_code == 0 and "Created 'Kai'" in result.stdout
    assert papers(profile_cfg(config_path)) == ["Old library paper"]
    result = runner.invoke(cli.app, ["profile", "adopt", "Again", "--yes", "-c", str(config_path)])
    assert result.exit_code == 1 and "no existing data" in result.stdout


def test_cli_recommend_shows_the_latest_list(config_path, plain_console, monkeypatch):
    runner.invoke(cli.app, ["profile", "create", "Ada", "--yes", "-c", str(config_path)])
    add_batch(config_path, days_ago=10)
    built = []
    monkeypatch.setattr(
        "litresearch.recommend.engine.build_recommendations",
        lambda *args, **kwargs: built.append(1),
    )
    result = runner.invoke(cli.app, ["recommend", "-c", str(config_path)])
    assert result.exit_code == 0 and not built
    assert "10 days old" in result.stdout and "recommend --new" in result.stdout
    assert f"Recommendations from {(date.today() - timedelta(days=10)).isoformat()}" in (
        result.stdout
    )


def test_backup_all_profiles(base, tmp_path):
    from litresearch.backup import backup_all

    legacy_data(base)
    profiles.create_profile(base, "Ada")
    messages = []
    assert backup_all(base, messages.append) == 0
    assert [m.split(":")[1].strip() for m in messages] == ["ada", "(no profile)"]
    assert list((tmp_path / "backups/ada").glob("litresearch_*.sqlite"))
    archives = sorted((tmp_path / "backups/ada").glob("litresearch_backup_*.zip"))
    with zipfile.ZipFile(archives[-1]) as handle:
        assert yaml.safe_load(handle.read("settings.yaml"))["topics"] == []
    profiles.adopt_legacy(base, "Kai")
    messages.clear()
    backup_all(base, messages.append)
    assert len(messages) == 2 and all("(no profile)" not in m for m in messages)


def test_queue_items_stay_in_their_profile(base):
    a = profiles.profile_config(base, profiles.create_profile(base, "Ada"))
    b = profiles.profile_config(base, profiles.create_profile(base, "Grace"))
    engine = init_db(a.paths.database)
    with Session(engine) as session:
        queue_add(session, a, stored(session, "Only Ada"))
    engine.dispose()
    engine = init_db(b.paths.database)
    with Session(engine) as session:
        assert session.exec(select(QueueItem)).all() == []
    engine.dispose()


@pytest.mark.parametrize(
    "content, message",
    [("topics: [unclosed\n", "not valid YAML"), ("queue:\n  max_size: 999\n", "max_size")],
)
def test_invalid_profile_settings_are_reported(config_path, base, plain_console, content, message):
    """A hand-edited settings.yaml gives a message, and the profile is still listed."""
    slug = profiles.create_profile(base, "Ada")
    (profiles.folder(base, slug) / profiles.PROFILE_SETTINGS).write_text(content)
    result = runner.invoke(cli.app, ["queue", "-c", str(config_path)])
    assert isinstance(result.exception, ConfigError) and message in str(result.exception)
    assert [info.name for info in profiles.list_profiles(base)] == ["Ada"]


def test_cli_save_load_compare_and_merge_archives(config_path, plain_console, tmp_path):
    """Save profile writes an archive; load, compare and import-progress take it."""

    def run(*args, profile=None):
        prefix = ["--profile", profile] if profile else []
        return runner.invoke(cli.app, [*prefix, *args, "-c", str(config_path)])

    assert run("profile", "create", "Ada", "--yes").exit_code == 0
    saved = run("profile", "save", profile="ada")
    assert saved.exit_code == 0, saved.output
    archive_file = next((tmp_path / "backups/ada").glob("litresearch_profile_ada_*.zip"))
    assert zipfile.is_zipfile(archive_file)
    legacy = tmp_path / "ada.json"
    assert run("profile", "save", str(legacy), profile="ada").exit_code == 0
    assert json.loads(legacy.read_text())["format"] == "litresearch-progress"

    compared = run("profile", "compare", str(archive_file), str(legacy), profile="ada")
    assert compared.exit_code == 0, compared.output
    assert compared.stdout.count("same rules") == 3 and "unmodified" in compared.stdout

    loaded = run("profile", "load", str(archive_file), "--name", "Ada copy")
    assert loaded.exit_code == 0, loaded.output
    assert "Ada copy" in run("profile", "list").stdout
    merged = run("import-progress", str(archive_file), profile="ada")
    assert merged.exit_code == 0, merged.output  # idempotent merge of the same data

    restored = run("restore", str(archive_file), "--yes", profile="ada")
    assert restored.exit_code == 1 and "is an archive" in restored.stdout
