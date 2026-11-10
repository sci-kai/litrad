"""Example papers for Semantic Scholar: rules, your choices, lists, engine, backups, CLI."""

import json
from datetime import date, datetime, timedelta

import pytest
from sqlmodel import Session, select
from typer.testing import CliRunner

import litrad.cli as cli
from conftest import SAMPLE_BIB, FakeApis, archive_data
from litrad import examples, libraries
from litrad.backup import merge
from litrad.config import Config, Selection
from litrad.db import (
    ExampleChoice,
    Library,
    LibraryPaper,
    Paper,
    Reading,
    ReadStatus,
    init_db,
)
from litrad.recommend.engine import build_recommendations
from test_recommend import TODAY, build, setup  # noqa: F401  (fixture)

runner = CliRunner()
T0 = datetime(2026, 10, 1, 8, 0, 0)


@pytest.fixture
def db(cfg):
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        yield cfg, session
    engine.dispose()


def with_selection(cfg: Config, **selection) -> Config:
    data = cfg.model_dump(mode="json")
    data["selection"].update(selection)
    return Config.model_validate(data)


def paper(session, name: str, doi: bool = True, **fields) -> Paper:
    item = Paper(title=f"Paper {name}", doi=f"10.1/{name}" if doi else None, **fields)
    session.add(item)
    session.flush()
    return item


def read(session, item: Paper, status=ReadStatus.read, rating=None, minutes=0) -> None:
    session.add(
        Reading(
            paper_id=item.id,
            status=status,
            rating=rating,
            updated_at=T0 + timedelta(minutes=minutes),
        )
    )
    session.flush()


def library_of(session, *papers, name="Lib", use=True) -> Library:
    library = libraries.create(session, name, use_for_recommendations=use)
    for item in papers:
        libraries.link(session, library, item)
    session.flush()
    libraries.refresh_flags(session)
    return library


def titles(example_list) -> list[str]:
    return [p.title.removeprefix("Paper ") for p in example_list.papers]


# --- configuration ---------------------------------------------------------------------


def test_defaults_and_validation():
    sel = Selection()
    assert sel.s2_positive_rules == ["liked", "recent"] and sel.s2_positive_count == 100
    assert sel.s2_negative_rules == ["dismissed"] and sel.s2_negative_count == 50
    assert Selection(s2_positive_rules=["Recent", "liked", "recent"]).s2_positive_rules == [
        "recent",
        "liked",
    ]
    assert Selection(s2_negative_rules="n/a").s2_negative_rules == []
    with pytest.raises(ValueError, match="unknown rule 'dismissed'; choose from liked"):
        Selection(s2_positive_rules=["dismissed"])
    with pytest.raises(ValueError, match="unknown rule 'liked'"):
        Selection(s2_negative_rules=["liked"])
    for bad in ({"s2_positive_count": 0}, {"s2_negative_count": 501}):
        with pytest.raises(ValueError):
            Selection(**bad)
    assert Selection(s2_negative_count=0).s2_negative_count == 0


# --- rules -----------------------------------------------------------------------------


def test_each_rule_orders_its_papers(db):
    cfg, session = db
    a = paper(session, "a", pub_date=date(2020, 1, 1), citation_percentile=90, fwci=1.0)
    b = paper(session, "b", pub_date=date(2024, 1, 1), citation_percentile=99, fwci=0.5)
    c = paper(session, "c", year=2022, citation_percentile=99, fwci=3.0, citation_count=5)
    d = paper(session, "d", citation_count=50, fwci=2.0)
    library_of(session, a, b, c, d)
    read(session, a, ReadStatus.thorough, minutes=1)
    read(session, b, ReadStatus.skimmed, rating=5, minutes=3)
    read(session, c, ReadStatus.abstract, rating=2, minutes=2)
    read(session, d, ReadStatus.dismissed, minutes=4)

    def names(rule):
        return [p.title.removeprefix("Paper ") for p in examples.rule_papers(session, rule)]

    assert names("liked") == ["b", "a"]  # most recently read first
    assert names("read") == ["b", "a"]  # skimmed or deeper; abstract does not count
    assert names("recent") == ["b", "c", "a", "d"]  # no date: oldest
    assert names("impact") == ["c", "b", "a"]  # percentile, then FWCI
    assert names("fwci") == ["c", "d", "a", "b"]
    assert names("cited") == ["d", "c"]
    assert names("dismissed") == ["d"]
    assert names("low_rated") == ["c"]
    with pytest.raises(ValueError):
        examples.rule_papers(session, "nonsense")


def test_rule_order_dedupe_cap_and_doi(db):
    cfg, session = db
    old = paper(session, "old", pub_date=date(2010, 1, 1))
    new = paper(session, "new", pub_date=date(2025, 1, 1))
    nodoi = paper(session, "nodoi", doi=False, pub_date=date(2026, 1, 1))
    library_of(session, old, new, nodoi)
    read(session, old, ReadStatus.thorough)
    cfg = with_selection(cfg, s2_positive_rules=["liked", "recent"], s2_positive_count=2)
    lists = examples.refresh(session, cfg)
    assert titles(lists.positive) == ["old", "new"]  # liked first; "old" not twice
    assert lists.positive.origins[old.id] == "rule: liked"
    assert lists.positive.origins[new.id] == "rule: newest"
    assert lists.positive.without_doi == 1  # the newest paper has no DOI
    assert lists.positive.ids == ["DOI:10.1/old", "DOI:10.1/new"]
    cfg = with_selection(cfg, s2_positive_rules=["recent", "liked"], s2_positive_count=1)
    assert titles(examples.refresh(session, cfg).positive) == ["new"]
    cfg = with_selection(cfg, s2_negative_count=0)
    read(session, new, ReadStatus.dismissed)
    assert examples.refresh(session, cfg).negative.papers == []


def test_manual_choices_come_first_and_win(db):
    cfg, session = db
    a, b, c = (paper(session, n, pub_date=date(2020 + i, 1, 1)) for i, n in enumerate("abc"))
    outside = paper(session, "outside")
    library_of(session, a, b, c)
    cfg = with_selection(cfg, s2_positive_rules=["recent"], s2_positive_count=3)
    assert titles(examples.refresh(session, cfg).positive) == ["c", "b", "a"]

    examples.include(session, outside, "positive")
    assert examples.remove(session, cfg, c.id, "positive")  # rule-chosen: excluded
    lists = examples.refresh(session, cfg)
    assert titles(lists.positive) == ["outside", "b", "a"]
    assert lists.positive.origins[outside.id] == "added by you"
    assert [p.id for p, _ in lists.positive.removed] == [c.id]
    assert lists.positive.manual == 1

    # removing your own addition drops it (no exclusion needed: no rule chooses it)
    assert examples.remove(session, cfg, outside.id, "positive")
    assert session.get(ExampleChoice, (outside.id, "positive")) is None
    assert not examples.remove(session, cfg, outside.id, "positive")
    # an addition that a rule also chooses is excluded when removed
    examples.include(session, b, "positive")
    examples.remove(session, cfg, b.id, "positive")
    assert session.get(ExampleChoice, (b.id, "positive")).action == "exclude"

    assert examples.restore(session, c.id, "positive")
    assert not examples.restore(session, c.id, "positive")
    assert titles(examples.refresh(session, cfg).positive) == ["c", "a"]
    assert examples.reset(session, "positive") == 1
    assert titles(examples.refresh(session, cfg).positive) == ["c", "b", "a"]
    with pytest.raises(ValueError):
        examples.include(session, a, "neutral")


def test_a_paper_is_never_in_both_lists(db):
    cfg, session = db
    liked = paper(session, "liked")
    read(session, liked, ReadStatus.dismissed, rating=5)  # liked and dismissed
    other = paper(session, "other")
    read(session, other, ReadStatus.dismissed)
    cfg = with_selection(cfg, s2_negative_rules=["dismissed"])
    lists = examples.refresh(session, cfg)
    # rated 5 but marked not relevant later: the negative rule wins
    assert lists.positive.papers == [] and titles(lists.negative) == ["liked", "other"]

    examples.include(session, other, "positive")  # rule-chosen negative becomes positive
    lists = examples.refresh(session, cfg)
    assert titles(lists.positive) == ["other"] and titles(lists.negative) == ["liked"]
    examples.include(session, other, "negative")  # the positive addition is dropped
    assert session.get(ExampleChoice, (other.id, "positive")) is None
    lists = examples.refresh(session, cfg)
    assert lists.positive.papers == [] and titles(lists.negative) == ["other", "liked"]
    assert examples.role(lists, other.id) == ("negative", "added by you")
    assert examples.role(lists, liked.id) == ("negative", "rule: not relevant")
    examples.include(session, liked, "positive")
    assert examples.role(examples.refresh(session, cfg), liked.id) == (
        "positive",
        "added by you",
    )

    assert examples.not_an_example(session, cfg, other.id)
    lists = examples.refresh(session, cfg)
    assert examples.role(lists, liked.id) == ("positive", "added by you")
    assert examples.role(lists, other.id) is None
    assert session.get(ExampleChoice, (other.id, "negative")).action == "exclude"


def test_too_many_additions_keep_the_newest(db):
    cfg, session = db
    cfg = with_selection(cfg, s2_positive_rules=[], s2_positive_count=2)
    added = [paper(session, str(i)) for i in range(3)]
    for number, item in enumerate(added):
        examples.include(session, item, "positive")
        session.get(ExampleChoice, (item.id, "positive")).at = T0 + timedelta(hours=number)
    session.flush()
    lists = examples.refresh(session, cfg)
    assert titles(lists.positive) == ["2", "1"]
    warning = lists.positive.warnings[0]
    assert "You added 3 papers to the positive examples, but at most 2" in warning


def test_empty_rules_without_additions_warn(db):
    cfg, session = db
    cfg = with_selection(cfg, s2_positive_rules=[])
    lists = examples.refresh(session, cfg)
    assert lists.positive.papers == [] and "recommendations are skipped" in " ".join(
        lists.positive.warnings
    )


def test_refresh_is_idempotent_and_labels_the_origin(db):
    cfg, session = db
    a, b = paper(session, "a"), paper(session, "b")
    library_of(session, a, b)
    examples.include(session, b, "negative")
    first = examples.refresh(session, cfg)
    entries = session.exec(select(LibraryPaper).order_by(LibraryPaper.paper_id)).all()
    snapshot = [(e.library_id, e.paper_id, list(e.tags), e.added_at) for e in entries]
    second = examples.refresh(session, cfg)
    entries = session.exec(select(LibraryPaper).order_by(LibraryPaper.paper_id)).all()
    assert snapshot == [(e.library_id, e.paper_id, list(e.tags), e.added_at) for e in entries]
    assert titles(first.positive) == titles(second.positive) == ["a"]
    tags = {
        (e.library_id, e.paper_id): e.tags
        for e in entries
        if e.library_id in (first.positive.library.id, first.negative.library.id)
    }
    assert tags == {
        (first.positive.library.id, a.id): ["rule: newest"],
        (first.negative.library.id, b.id): ["added by you"],
    }
    assert len(session.exec(select(Library)).all()) == 3  # created once


def test_only_libraries_used_for_recommendations(db):
    cfg, session = db
    used, unused = paper(session, "used"), paper(session, "unused")
    library_of(session, used, name="Used")
    library_of(session, unused, name="Other", use=False)
    cfg = with_selection(cfg, s2_positive_rules=["recent", "impact", "fwci", "cited"])
    assert titles(examples.refresh(session, cfg).positive) == ["used"]
    libraries.set_used(session, set())
    assert examples.refresh(session, cfg).positive.papers == []
    # "liked" and your additions do not depend on the libraries
    read(session, unused, ReadStatus.thorough)
    cfg = with_selection(cfg, s2_positive_rules=["liked", "recent"])
    assert titles(examples.refresh(session, cfg).positive) == ["unused"]


def old_selection(session) -> tuple[list[str], list[str]]:
    """The selection of gather_s2_candidates before the rules existed (v0.2.0-dev)."""
    library = [p for p in libraries.recommendation_library(session) if p.doi]
    liked = session.exec(
        select(Paper)
        .join(Reading, Reading.paper_id == Paper.id)
        .where(Paper.doi.is_not(None), (Reading.rating >= 4) | (Reading.status == "thorough"))
    ).all()
    dismissed = session.exec(
        select(Paper)
        .join(Reading, Reading.paper_id == Paper.id)
        .where(Paper.doi.is_not(None), Reading.status == ReadStatus.dismissed)
    ).all()
    recent = sorted(library, key=lambda p: p.pub_date or date(p.year or 1900, 7, 1), reverse=True)
    positives = list(dict.fromkeys(f"DOI:{p.doi}" for p in liked + recent))[:100]
    negatives = [f"DOI:{p.doi}" for p in dismissed][:50]
    return positives, negatives


def old_selection_without(session, skip: set[str]) -> list[str]:
    """The old positives, computed without the papers in ``skip``."""
    library = [p for p in libraries.recommendation_library(session) if p.doi]
    liked = session.exec(
        select(Paper)
        .join(Reading, Reading.paper_id == Paper.id)
        .where(Paper.doi.is_not(None), (Reading.rating >= 4) | (Reading.status == "thorough"))
    ).all()
    recent = sorted(library, key=lambda p: p.pub_date or date(p.year or 1900, 7, 1), reverse=True)
    ids = [f"DOI:{p.doi}" for p in liked + recent]
    return list(dict.fromkeys(i for i in ids if i not in skip))[:100]


def test_defaults_reproduce_the_old_selection(db):
    cfg, session = db
    papers = [
        paper(session, f"p{i}", doi=i % 7 != 0, pub_date=date(2000 + i % 25, 1 + i % 12, 1))
        for i in range(160)
    ]
    library_of(session, *papers[:130])
    library_of(session, *papers[100:], name="Unused", use=False)
    for number, item in enumerate(papers[::9]):
        read(session, item, ReadStatus.thorough if number % 3 else ReadStatus.read, rating=4)
    for item in papers[1::11]:
        if session.get(Reading, item.id) is None:
            read(session, item, ReadStatus.dismissed)
    positives, negatives = old_selection(session)
    lists = examples.refresh(session, cfg)
    assert len(positives) == 100
    # Semantic Scholar does not care about the order of the examples
    assert sorted(lists.negative.ids) == sorted(negatives)
    # the only difference: dismissed library papers were sent in both lists; now they
    # are negative examples only, and the next newest library papers take their place
    both = set(positives) & set(negatives)
    assert both and not both & set(lists.positive.ids)
    assert set(positives) - both <= set(lists.positive.ids)
    old_without = old_selection_without(session, both)
    assert sorted(lists.positive.ids) == sorted(old_without)


def test_example_libraries_never_count_as_your_library(db):
    cfg, session = db
    item = paper(session, "liked")
    read(session, item, ReadStatus.thorough)
    lists = examples.refresh(session, cfg)
    assert titles(lists.positive) == ["liked"]
    libraries.refresh_flags(session)
    assert not session.get(Paper, item.id).in_library
    assert libraries.recommendation_library(session) == []
    positive = lists.positive.library
    assert positive.kind == "positive" and not positive.use_for_recommendations
    with pytest.raises(libraries.LibraryError, match="cannot be deleted"):
        libraries.delete(session, positive)
    with pytest.raises(libraries.LibraryError, match="cannot be used"):
        libraries.update(session, positive, use_for_recommendations=True)
    libraries.set_used(session, {positive.id})
    assert not positive.use_for_recommendations
    assert [lib.kind for lib in libraries.all_libraries(session)] == ["positive", "negative"]
    # an imported library is listed before the example libraries
    libraries.create(session, "Imported papers")
    order = [lib.kind for lib in libraries.all_libraries(session)]
    assert order == ["imported", "positive", "negative"]


def test_an_existing_name_is_kept(db):
    cfg, session = db
    libraries.create(session, "Positive examples")
    lists = examples.refresh(session, cfg)
    assert lists.positive.library.name == "Positive examples 2"


# --- engine ------------------------------------------------------------------------------


def s2_request(apis):
    return json.loads(
        [r for r in apis.requests if r.url.path.startswith("/recommendations/")][-1].content
    )


def test_engine_sends_the_lists_and_records_them(setup):  # noqa: F811
    cfg, engine, http, apis = setup
    with Session(engine) as session:
        dismissed = session.exec(select(Paper).where(Paper.doi.is_not(None))).first()
        dismissed_doi = dismissed.doi
        session.add(Reading(paper_id=dismissed.id, status=ReadStatus.dismissed))
        added = Paper(title="Chosen by hand", doi="10.9/by-hand")
        session.add(added)
        session.flush()
        examples.include(session, added, "positive")
        session.commit()
        lists = examples.refresh(session, cfg)
        expected = (lists.positive.ids, lists.negative.ids)
        session.commit()
    batch, items = build(setup)
    body = s2_request(apis)
    assert (body["positivePaperIds"], body["negativePaperIds"]) == expected
    assert body["positivePaperIds"][0] == "DOI:10.9/by-hand"
    assert body["negativePaperIds"] == [f"DOI:{dismissed_doi}"]
    snapshot = batch.params["s2_examples"]
    assert snapshot["positive_rules"] == ["liked", "recent"]
    assert snapshot["positives"] == len(expected[0]) and snapshot["negatives"] == 1
    assert snapshot["positives_added_by_you"] == 1
    assert batch.funnel["s2_positives"] == len(expected[0])
    assert batch.params["selection"]["s2_positive_count"] == 100
    found = [
        f
        for rec, _ in items
        for f in rec.breakdown["evidence"]["found_by"]
        if f["source"] == "s2-recs"
    ]
    assert found and found[0]["positives"] == len(expected[0]) and found[0]["negatives"] == 1


def test_engine_skips_s2_without_positives(cfg, make_http):
    apis = FakeApis()
    cfg = with_selection(cfg, s2_positive_rules=[])
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        library_of(session, paper(session, "lib"))
        batch = build_recommendations(cfg, session, make_http(apis), today=TODAY)
        assert batch.params["s2_examples"] is None and batch.funnel["s2_positives"] == 0
        assert any("recommendations are skipped" in w for w in batch.warnings)
    engine.dispose()
    assert not [r for r in apis.requests if r.url.path.startswith("/recommendations/")]


def test_engine_warns_when_no_library_is_used(cfg, make_http):
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        library_of(session, paper(session, "lib"), use=False)
        batch = build_recommendations(cfg, session, make_http(FakeApis()), today=TODAY)
        assert any("No library is used for recommendations" in w for w in batch.warnings)
        libraries.set_used(session, {session.exec(select(Library)).first().id})
        session.commit()
        batch = build_recommendations(
            cfg, session, make_http(FakeApis()), today=TODAY + timedelta(days=1)
        )
        assert not any("No library is used" in w for w in batch.warnings)
    engine.dispose()


# --- backups -----------------------------------------------------------------------------


def test_archive_data_keeps_choices(db, tmp_path):
    cfg, session = db
    a, b, c = paper(session, "a"), paper(session, "b"), paper(session, "c")
    library_of(session, a, b, c)
    examples.include(session, a, "negative")
    examples.remove(session, cfg, b.id, "positive")
    examples.refresh(session, cfg)
    session.commit()
    data = archive_data(session, cfg)
    assert {lib["kind"] for lib in data["libraries"]} == {"imported"}
    assert sorted((c["kind"], c["action"]) for c in data["example_choices"]) == [
        ("negative", "include"),
        ("positive", "exclude"),
    ]

    def lists_of(other_session):
        found = examples.refresh(other_session, cfg)
        return titles(found.positive), titles(found.negative)

    expected = lists_of(session)
    # a fresh profile, twice
    other = init_db(tmp_path / "fresh.sqlite")
    with Session(other) as fresh:
        report = merge(fresh, cfg, data)
        assert report.example_choices == 2
        assert merge(fresh, cfg, data).example_choices == 0
        assert len(fresh.exec(select(ExampleChoice)).all()) == 2
        assert lists_of(fresh) == expected
        kinds = [lib.kind for lib in fresh.exec(select(Library)).all()]
        assert sorted(kinds) == ["imported", "negative", "positive"]
    other.dispose()
    # merging the data back restores a reset choice
    examples.reset(session, "negative")
    merge(session, cfg, data)
    assert len(session.exec(select(ExampleChoice)).all()) == 2


def test_merging_choices_newer_wins(db, tmp_path):
    cfg, session = db
    a = paper(session, "a")
    examples.include(session, a, "positive")
    session.get(ExampleChoice, (a.id, "positive")).at = T0
    session.commit()
    data = archive_data(session, cfg)
    other = init_db(tmp_path / "other.sqlite")
    with Session(other) as existing:
        local = paper(existing, "a")
        examples.include(existing, local, "negative")  # newer than the file's choice
        existing.commit()
        merge(existing, cfg, data)
        choices = {c.kind: c.action for c in existing.exec(select(ExampleChoice)).all()}
        assert choices == {"negative": "include"}
        merge(existing, cfg, data)
        assert len(existing.exec(select(ExampleChoice)).all()) == 1
    other.dispose()


# --- command line --------------------------------------------------------------------------


@pytest.fixture
def plain_console(monkeypatch):
    from rich.console import Console

    import litrad.cli_library as cli_library

    console = Console(color_system=None, width=300, highlight=False)
    monkeypatch.setattr(cli, "console", console)
    monkeypatch.setattr(cli_library, "console", console)


def test_cli_examples(tmp_path, plain_console, monkeypatch, make_http):
    import litrad.cli_library as cli_library

    apis = FakeApis()
    monkeypatch.setattr(cli_library, "make_http", lambda cfg: make_http(apis))
    path = tmp_path / "config.yaml"
    path.write_text(f"paths:\n  cache_dir: {tmp_path / 'cache'}\n")
    c = ["-c", str(path)]

    def run(*args, code=0):
        result = runner.invoke(cli.app, [*args, *c])
        assert result.exit_code == code, result.stdout
        return result.stdout

    run("profile", "create", "Ada", "--yes")
    run("library", "import", str(SAMPLE_BIB), "--new", "Paperpile", "--no-s2")
    listing = run("examples")
    assert "Positive examples: 4/100 papers" in listing and "rule: newest" in listing
    assert "Negative examples: 0/50 papers" in run("examples", "negative")
    added = run("examples", "add", "negative", "10.1038/s41588-022-01141-9")
    assert "Added to the negative examples" in added
    assert "added by you" in run("examples", "negative")
    assert "Positive examples: 3/100" in run("examples", "positive")
    assert "is not a positive example" in run(
        "examples", "remove", "positive", "10.1038/s41588-022-01141-9", code=1
    )
    run("examples", "remove", "negative", "10.1038/s41588-022-01141-9")
    run("examples", "remove", "positive", "10.1038/s41591-021-01323-8")
    assert "removed by you" in run("examples", "positive")
    run("examples", "restore", "positive", "10.1038/s41591-021-01323-8")
    run("examples", "remove", "positive", "10.1038/s41591-021-01323-8")
    assert "Undid 1 changes" in run("examples", "reset", "positive", "--yes")
    assert "Choose 'positive' or 'negative'" in run("examples", "reset", "maybe", code=1)
    rules = run("examples", "rules", "positive", "cited", "liked", "--count", "20")
    assert "1. cited" in rules and "2. liked" in rules and "up to 20 papers" in rules
    assert "s2_positive_rules" in (tmp_path / "data/profiles/ada/settings.yaml").read_text()
    assert "unknown rule" in run("examples", "rules", "negative", "liked", code=1)
    assert "no rules" in run("examples", "rules", "negative", "--none")
    assert "No paper" in run("examples", "remove", "positive", "10.1/unknown", code=1)
    # libraries used for recommendations
    assert "none" in run("library", "use", "Paperpile", "--off")
    assert "Positive examples: 0/20" in run("examples", "positive")
    assert "Used for recommendations: Paperpile" in run("library", "use", "Paperpile")
    assert "cannot be used" in run("library", "use", "Positive examples", code=1)
    assert "cannot be deleted" in run("library", "delete", "Negative examples", "--yes", code=1)
