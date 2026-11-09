import json
from datetime import date, datetime, timedelta

import pytest
from sqlmodel import Session, select
from typer.testing import CliRunner

from litresearch.backup import (
    BackupError,
    export_progress,
    import_progress,
    load_profile,
    reset_progress,
    save_profile_file,
    summarize,
    verify_checksum,
)
from litresearch.config import Config
from litresearch.db import (
    Achievement,
    Category,
    Paper,
    Profile,
    QueueItem,
    Reading,
    ReadStatus,
    Recommendation,
    RecommendationBatch,
    StatusEvent,
    XpEvent,
    init_db,
)
from litresearch.game import level_for, stats, total_xp
from litresearch.profile import (
    ProfileError,
    active_rules,
    ensure_profile,
    get_profile,
    start_profile,
)
from litresearch.reading import drop_from_queue, queue_add, record_reading
from litresearch.rules import BADGE_CATALOG, GameRules, LevelDef

MONDAY = datetime(2026, 10, 5, 10, 0, 0)
runner = CliRunner()


@pytest.fixture
def db(cfg):
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        yield cfg, session
    engine.dispose()


def add(session, title="Paper", **kwargs) -> Paper:
    paper = Paper(title=title, **kwargs)
    session.add(paper)
    session.commit()
    return paper


def with_game(cfg: Config, **rules) -> Config:
    return Config.model_validate({**cfg.model_dump(mode="json"), "game": rules})


# --- rules ---------------------------------------------------------------------------


def test_default_rules_are_rich():
    rules = GameRules()
    assert len(rules.levels) == 20 and len(rules.badges()) == len(BADGE_CATALOG) >= 40
    assert rules.levels[0].title == "Curious Undergrad"
    assert rules.levels[-1].title == "Hallmarks Author"
    codes = [b.code for b in rules.badges()]
    assert len(codes) == len(set(codes))
    assert all("{target}" not in b.description for b in rules.badges())


@pytest.mark.parametrize(
    "values, message",
    [
        ({"levels": [{"xp": 10, "title": "A"}]}, "start at 0"),
        ({"levels": [{"xp": 0, "title": "A"}, {"xp": 0, "title": "B"}]}, "strictly increasing"),
        ({"levels": [{"xp": 0, "title": "A"}, {"xp": 5, "title": "A"}]}, "unique"),
        ({"badge_targets": {"nonsense": 3}}, "unknown badge"),
        ({"badge_targets": {"bookworm": 0}}, "at least 1"),
        ({"xp_read": 100, "xp_thorough": 50}, "deeper reading levels"),
        ({"xp_read": -1}, "greater than or equal"),
    ],
)
def test_invalid_rules(values, message):
    with pytest.raises(ValueError, match=message):
        GameRules.model_validate(values)


def test_fingerprint():
    assert GameRules().fingerprint() == GameRules().fingerprint()
    assert GameRules(xp_read=41).fingerprint() != GameRules().fingerprint()
    assert GameRules(badge_targets={"bookworm": 25}).fingerprint() == GameRules().fingerprint()
    assert GameRules(badge_targets={"bookworm": 26}).fingerprint() != GameRules().fingerprint()


def _fingerprint_until_0_6(rules: GameRules) -> str:
    """The fingerprint algorithm of 0.1 to 0.6 (hash of the complete dump)."""
    import hashlib

    data = rules.model_dump(mode="json")
    data["badge_targets"] = {badge.code: badge.target for badge in rules.badges()}
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:16]


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"xp_read": 41, "frog_bonus": 0.75},
        {"badge_targets": {"bookworm": 30, "critic": 5}},
        {"levels": [{"xp": 0, "title": "Start"}, {"xp": 10, "title": "Ünïcode 🎓", "icon": "🔬"}]},
    ],
)
def test_fingerprint_is_unchanged_for_existing_rules(values):
    rules = GameRules.model_validate(values)
    assert rules.fingerprint() == _fingerprint_until_0_6(rules)


def test_fingerprint_survives_a_rule_added_later():
    """A 1.x rule with a default must not change fingerprints locked before it."""
    from pydantic import Field

    from litresearch import rules as rules_module
    from litresearch.rules import BadgeDef, LevelDef

    class FutureLevel(LevelDef):
        colour: str = "blue"

    class FutureRules(GameRules):
        coffee_xp: int = Field(0, ge=0)
        levels: list[FutureLevel] = Field(
            default_factory=lambda: [FutureLevel(xp=0, title="Start")]
        )

    locked = GameRules(xp_read=50, levels=[LevelDef(xp=0, title="Start")])
    stored = locked.model_dump(mode="json")  # as an older version locked it
    future = FutureRules.model_validate(stored)
    assert future.fingerprint() == locked.fingerprint()
    # setting the new rule to something else is a real difference
    assert future.model_copy(update={"coffee_xp": 5}).fingerprint() != locked.fingerprint()
    other_level = FutureRules.model_validate(
        {**stored, "levels": [{"xp": 0, "title": "Start", "colour": "red"}]}
    )
    assert other_level.fingerprint() != locked.fingerprint()

    # a badge added to the catalogue later counts only with a non-default target
    extra = BadgeDef("future", "Future", "x", "papers_read", 7, "🔮", "Reading")
    catalogue, targets = rules_module.BADGE_CATALOG, rules_module._CATALOG_TARGETS
    try:
        rules_module.BADGE_CATALOG = [*catalogue, extra]
        rules_module._CATALOG_TARGETS = {**targets, "future": 7}
        assert locked.fingerprint() == _fingerprint_until_0_6_without("future", locked)
        changed = locked.model_copy(update={"badge_targets": {"future": 8}})
        assert changed.fingerprint() != locked.fingerprint()
    finally:
        rules_module.BADGE_CATALOG, rules_module._CATALOG_TARGETS = catalogue, targets


def _fingerprint_until_0_6_without(code: str, rules: GameRules) -> str:
    import hashlib

    from litresearch.rules import BADGE_CODES_V1

    data = rules.model_dump(mode="json")
    data["badge_targets"] = {b.code: b.target for b in rules.badges() if b.code in BADGE_CODES_V1}
    assert code not in data["badge_targets"]
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:16]


def test_fingerprint_baseline_covers_every_current_rule():
    """Every rule of format 1 is in the baseline; new rules must not be added to it."""
    from litresearch.rules import BADGE_CODES, BADGE_CODES_V1, FINGERPRINT_FIELDS, LevelDef

    assert FINGERPRINT_FIELDS[GameRules] == set(GameRules.model_fields)
    assert FINGERPRINT_FIELDS[LevelDef] == set(LevelDef.model_fields)
    assert BADGE_CODES_V1 == BADGE_CODES


@pytest.mark.parametrize("version", ["0.1.0-alpha.1", "0.2.0-dev", "dev-20261109"])
def test_stored_fingerprints_of_older_versions_verify(version):
    from pathlib import Path

    folder = Path(__file__).parent / "data" / "compat" / version
    for name in ("profile.json", "progress.json"):
        profile = json.loads((folder / name).read_text(encoding="utf-8"))["profile"]
        assert GameRules.model_validate(profile["rules"]).fingerprint() == profile["fingerprint"]


def test_merge_compares_rules_not_stored_fingerprint_strings(db, tmp_path):
    """A stale fingerprint string (e.g. written by another version) is not a difference."""
    cfg, session = db
    played(session, cfg)
    data = export_progress(session)
    data["profile"]["fingerprint"] = "0000000000000000"
    data.pop("checksum", None)
    engine = init_db(tmp_path / "other.sqlite")
    with Session(engine) as fresh:
        start_profile(fresh, cfg, "Same rules")
        report = import_progress(fresh, cfg, data, allow_modified=True)
        assert report.xp_added > 0
        assert summarize(data, cfg)["fingerprint"] == cfg.game.fingerprint()
    engine.dispose()


def test_custom_ladder_and_badge_targets():
    rules = GameRules(
        levels=[LevelDef(xp=0, title="Rookie", icon="🐣"), LevelDef(xp=10, title="Pro")],
        badge_targets={"page_turner": 3},
    )
    assert level_for(9, rules).title == "Rookie" and level_for(10, rules).title == "Pro"
    page_turner = next(b for b in rules.badges() if b.code == "page_turner")
    assert page_turner.target == 3 and page_turner.description == "Read 3 papers"


# --- locking ---------------------------------------------------------------------------


def test_profile_locks_the_rules(db):
    cfg, session = db
    profile = start_profile(session, cfg, "Kai", at=MONDAY)
    assert profile.fingerprint == cfg.game.fingerprint()
    with pytest.raises(ProfileError, match="locked"):
        start_profile(session, cfg, "again")
    # editing the config afterwards must not change the points of the running game
    cheat = with_game(cfg, xp_read=1000, xp_thorough=2000, badge_xp=1000)
    assert active_rules(session, cheat).xp_read == 40
    award = record_reading(session, cheat, add(session), ReadStatus.read, at=MONDAY).award
    assert {e.kind: e.points for e in award.events}["read"] == 40


def test_custom_rules_are_used(db):
    cfg, session = db
    rules = GameRules(xp_read=100, xp_thorough=200, badge_xp=0, drop_penalty=33, quest_xp=0)
    start_profile(session, cfg, "Custom", rules=rules, at=MONDAY)
    award = record_reading(session, cfg, add(session), ReadStatus.read, at=MONDAY).award
    kinds = {e.kind: e.points for e in award.events}
    assert kinds["read"] == 100 and "badge" not in kinds  # badges unlock without XP
    assert [b.code for b in award.badges] == ["first_read"]
    paper = add(session, "Picked")
    queue_add(session, cfg, paper, source="recommendation")
    assert drop_from_queue(session, cfg, paper, at=MONDAY).points == -33


def test_first_action_starts_the_profile(db):
    cfg, session = db
    assert get_profile(session) is None
    outcome = record_reading(session, cfg, add(session), ReadStatus.skimmed, at=MONDAY)
    assert outcome.award.profile_started
    profile = get_profile(session)
    assert profile.created_at == MONDAY and profile.fingerprint == cfg.game.fingerprint()
    second = record_reading(session, cfg, add(session, "B"), ReadStatus.skimmed, at=MONDAY)
    assert not second.award.profile_started
    assert ensure_profile(session, cfg)[1] is False


def test_profile_name_rules(db):
    cfg, session = db
    with pytest.raises(ProfileError, match="80"):
        start_profile(session, cfg, "x" * 81)
    assert start_profile(session, cfg, "   ").name == "My reading journey"


# --- save, load, merge, reset ----------------------------------------------------------------


def played(session, cfg):
    """A small game: profile, readings, queue, a recommendation batch."""
    start_profile(session, cfg, "Kai's journey", at=MONDAY)
    a, b, c = add(session, "A", doi="10.1/a"), add(session, "B", doi="10.1/b"), add(session, "C")
    batch = RecommendationBatch(week_start=MONDAY.date(), funnel={"gathered": 9})
    session.add(batch)
    session.flush()
    session.add(
        Recommendation(
            batch_id=batch.id,
            paper_id=b.id,
            rank=1,
            category=Category.open,
            score=0.8,
            breakdown={"evidence": {"x": 1}},
            reason="fresh",
        )
    )
    session.commit()
    queue_add(session, cfg, c)
    record_reading(session, cfg, a, ReadStatus.thorough, rating=5, note="key", at=MONDAY)
    record_reading(session, cfg, b, ReadStatus.read, at=MONDAY + timedelta(hours=2))
    return a, b, c


def test_saved_profile_contains_everything(db):
    cfg, session = db
    played(session, cfg)
    data = export_progress(session, MONDAY + timedelta(days=1))
    assert data["profile"]["name"] == "Kai's journey"
    assert data["profile"]["fingerprint"] == cfg.game.fingerprint()
    assert data["recommendations"][0]["items"][0]["reason"] == "fresh"
    assert data["recommendations"][0]["funnel"] == {"gathered": 9}
    assert {p["title"] for p in data["papers"].values()} == {"A", "B", "C"}
    assert verify_checksum(data) is True
    data["xp_events"][0]["points"] += 1000  # tampering
    assert verify_checksum(data) is False


def test_load_replaces_progress(db, tmp_path):
    cfg, session = db
    played(session, cfg)
    path = save_profile_file(session, tmp_path, MONDAY + timedelta(days=1))
    assert path.name == "litresearch_profile_kai-s-journey_20261006_100000.json"
    data = json.loads(path.read_text())
    xp = total_xp(session)
    # play on, then go back to the saved state
    record_reading(session, cfg, add(session, "D"), ReadStatus.read, at=MONDAY + timedelta(days=2))
    assert total_xp(session) > xp
    load_profile(session, cfg, data)
    assert total_xp(session) == xp
    assert session.exec(select(Paper).where(Paper.title == "D")).one()  # library stays
    assert (
        session.get(Reading, session.exec(select(Paper.id).where(Paper.title == "D")).one()) is None
    )
    assert len(session.exec(select(RecommendationBatch)).all()) == 1
    assert get_profile(session).name == "Kai's journey"


def test_tampered_files_are_refused(db):
    cfg, session = db
    played(session, cfg)
    data = export_progress(session)
    data["xp_events"][0]["points"] = 99999
    before = total_xp(session)
    with pytest.raises(BackupError, match="modified outside"):
        load_profile(session, cfg, data)
    with pytest.raises(BackupError, match="modified outside"):
        import_progress(session, cfg, data)
    assert total_xp(session) == before


def test_merge_requires_the_same_rules(db, tmp_path):
    cfg, session = db
    played(session, cfg)
    data = export_progress(session)
    other = with_game(cfg, xp_read=79)
    other_cfg = Config.model_validate(
        {
            **other.model_dump(mode="json"),
            "paths": {
                **other.model_dump(mode="json")["paths"],
                "database": str(tmp_path / "other.sqlite"),
            },
        }
    )
    engine = init_db(other_cfg.paths.database)
    with Session(engine) as fresh:
        start_profile(fresh, other_cfg, "Other rules")
        with pytest.raises(BackupError, match="different game rules"):
            import_progress(fresh, other_cfg, data)
        assert total_xp(fresh) == 0
        report = import_progress(fresh, other_cfg, data, allow_different_rules=True)
        assert report.xp_added > 0 and not report.profile_adopted
    engine.dispose()


def test_merge_into_empty_database_adopts_the_profile(db, tmp_path):
    cfg, session = db
    played(session, cfg)
    data = export_progress(session)
    engine = init_db(tmp_path / "empty.sqlite")
    with Session(engine) as fresh:
        report = import_progress(fresh, cfg, data)
        assert report.profile_adopted and report.batches_added == 1
        assert get_profile(fresh).fingerprint == cfg.game.fingerprint()
        # idempotent, also for recommendation batches
        again = import_progress(fresh, cfg, data)
        assert again.batches_added == 0 and again.xp_added == 0
    engine.dispose()


def test_version_one_files_still_import(db):
    cfg, session = db
    paper = add(session, "Old", doi="10.1/old")
    data = {
        "format": "litresearch-progress",
        "format_version": 1,
        "papers": {"p1": {"doi": "10.1/old", "title": "Old"}},
        "readings": [
            {
                "paper": "p1",
                "status": "read",
                "rating": None,
                "notes": "",
                "updated_at": "2026-01-01T10:00:00",
            }
        ],
    }
    report = import_progress(session, cfg, data)
    assert report.readings_added == 1 and session.get(Reading, paper.id).status == ReadStatus.read


def test_reset_keeps_the_library(db):
    cfg, session = db
    played(session, cfg)
    papers = len(session.exec(select(Paper)).all())
    reset_progress(session)
    for model in (
        Profile,
        Reading,
        StatusEvent,
        QueueItem,
        XpEvent,
        Achievement,
        Recommendation,
        RecommendationBatch,
    ):
        assert session.exec(select(model)).all() == []
    assert len(session.exec(select(Paper)).all()) == papers
    start_profile(session, cfg, "Season 2")  # a new game can start


def test_summary_for_comparison(db):
    cfg, session = db
    played(session, cfg)
    data = export_progress(session, MONDAY + timedelta(days=1))
    row = summarize(data, cfg, "saved")
    assert row["name"] == "Kai's journey" and row["xp"] == total_xp(session)
    assert row["papers_read"] == 2 and row["thorough"] == 1 and row["checksum_ok"] is True
    assert row["fingerprint"] == cfg.game.fingerprint()
    assert row["level"].number >= 2 and 0 <= row["discipline"] <= 1
    data["readings"][0]["notes"] = "edited"
    assert summarize(data, cfg, "edited")["checksum_ok"] is False  # shown, not refused


# --- new badges -------------------------------------------------------------------------


def codes(award):
    return {b.code for b in award.badges}


def test_time_of_day_and_weekend_badges(db):
    cfg, session = db
    rules = GameRules(badge_targets={"night_owl": 2, "early_riser": 1, "weekend_warrior": 2})
    start_profile(session, cfg, rules=rules, at=MONDAY)
    record_reading(
        session, cfg, add(session, "N1"), ReadStatus.skimmed, at=datetime(2026, 10, 5, 23, 30)
    )
    unlocked = codes(
        record_reading(
            session, cfg, add(session, "N2"), ReadStatus.skimmed, at=datetime(2026, 10, 6, 1, 15)
        ).award
    )
    assert "night_owl" in unlocked
    assert "early_riser" in codes(
        record_reading(
            session, cfg, add(session, "E"), ReadStatus.skimmed, at=datetime(2026, 10, 7, 6, 0)
        ).award
    )
    record_reading(
        session, cfg, add(session, "W1"), ReadStatus.skimmed, at=datetime(2026, 10, 10, 12, 0)
    )  # Saturday
    assert "weekend_warrior" in codes(
        record_reading(
            session, cfg, add(session, "W2"), ReadStatus.skimmed, at=datetime(2026, 10, 11, 12, 0)
        ).award
    )  # Sunday


def test_pace_comeback_and_age_badges(db):
    cfg, session = db
    start_profile(session, cfg, rules=GameRules(badge_targets={"marathon": 3}), at=MONDAY)
    for i in range(2):
        record_reading(session, cfg, add(session, f"D{i}"), ReadStatus.skimmed, at=MONDAY)
    assert "marathon" in codes(
        record_reading(session, cfg, add(session, "D2"), ReadStatus.skimmed, at=MONDAY).award
    )
    later = MONDAY + timedelta(weeks=6)
    old = add(session, "Ancient", pub_date=date(1990, 1, 1))
    unlocked = codes(record_reading(session, cfg, old, ReadStatus.read, at=later).award)
    assert {"comeback", "archaeologist"} <= unlocked
    scoop = add(session, "Brand new", pub_date=later.date() - timedelta(days=2))
    figures_before = stats(session, later.date())["scoops"]
    record_reading(session, cfg, scoop, ReadStatus.skimmed, at=later)
    assert stats(session, later.date())["scoops"] == figures_before + 1


def test_source_badges_and_curation(db):
    cfg, session = db
    rules = GameRules(
        badge_targets={
            "library_diver": 1,
            "free_spirit": 1,
            "trailblazer": 1,
            "curator": 2,
            "fan_club": 1,
        }
    )
    start_profile(session, cfg, rules=rules, at=MONDAY)
    library = add(session, "Lib", in_library=True)
    assert "library_diver" in codes(
        record_reading(session, cfg, library, ReadStatus.skimmed, at=MONDAY).award
    )
    assert "free_spirit" in codes(
        record_reading(session, cfg, add(session, "Own"), ReadStatus.skimmed, at=MONDAY).award
    )
    recommended = add(session, "Rec")
    batch = RecommendationBatch(week_start=MONDAY.date())
    session.add(batch)
    session.flush()
    session.add(
        Recommendation(
            batch_id=batch.id, paper_id=recommended.id, rank=1, category=Category.open, score=1
        )
    )
    session.commit()
    unlocked = codes(
        record_reading(session, cfg, recommended, ReadStatus.read, rating=5, at=MONDAY).award
    )
    assert {"trailblazer", "fan_club"} <= unlocked
    record_reading(session, cfg, add(session, "X1"), ReadStatus.dismissed, at=MONDAY)
    assert "curator" in codes(
        record_reading(session, cfg, add(session, "X2"), ReadStatus.dismissed, at=MONDAY).award
    )


def test_inbox_zero_once_per_day_and_minimalist(db):
    cfg, session = db
    start_profile(session, cfg, rules=GameRules(badge_targets={"minimalist": 2}), at=MONDAY)
    for day in range(2):
        for repeat in range(2):
            paper = add(session, f"Q{day}{repeat}")
            queue_add(session, cfg, paper)
            at = MONDAY + timedelta(days=day, hours=repeat)
            record_reading(session, cfg, paper, ReadStatus.skimmed, at=at)
    paid = session.exec(select(XpEvent).where(XpEvent.kind == "inbox_zero")).all()
    assert len(paid) == 2  # once per day, not per emptying
    assert session.get(Achievement, "minimalist") is not None


def test_xp_badges_follow_badge_xp(db):
    cfg, session = db
    rules = GameRules(xp_thorough=990, badge_xp=25, badge_targets={"thousand_club": 1000})
    start_profile(session, cfg, rules=rules, at=MONDAY)
    award = record_reading(session, cfg, add(session), ReadStatus.thorough, at=MONDAY).award
    # 990 + 25 for "First Steps" crosses 1000 within the same action
    assert "thousand_club" in codes(award)


# --- web and CLI -----------------------------------------------------------------------------
