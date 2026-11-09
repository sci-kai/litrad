"""Game rules: points, bonuses, penalty, levels and badges.

The ``game`` section of ``config.yaml`` holds the defaults for a *new*
profile. When a profile starts, its rules are copied into the database and
locked: later changes to the config do not affect a running game, so points
cannot be inflated after the fact. The rules carry a fingerprint, so two
profiles can be compared fairly only when they played by the same rules.
"""

import hashlib
import json
from dataclasses import dataclass

from pydantic import BaseModel, Field, field_validator, model_validator


class LevelDef(BaseModel):
    model_config = {"extra": "forbid"}

    xp: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=60)
    icon: str = Field("⭐", max_length=8)


DEFAULT_LEVELS = [
    (0, "Curious Undergrad", "🎓"),
    (60, "Bachelor Student", "📘"),
    (140, "Lab Rotation Student", "🧪"),
    (250, "Master's Student", "📗"),
    (400, "Research Assistant", "🧫"),
    (600, "PhD Candidate", "📚"),
    (850, "Journal Club Regular", "☕"),
    (1150, "Poster Presenter", "🪧"),
    (1500, "Conference Speaker", "🎤"),
    (1900, "Doctor of Philosophy", "📜"),
    (2400, "Postdoc", "🔬"),
    (3000, "Senior Postdoc", "🧬"),
    (3700, "Research Fellow", "🧑‍🔬"),
    (4500, "Junior Group Leader", "🌱"),
    (5400, "Group Leader", "👩‍🔬"),
    (6500, "Associate Professor", "🏫"),
    (7800, "Professor", "🎩"),
    (9300, "Department Head", "🏛️"),
    (11000, "Review Editor", "🖋️"),
    (13000, "Hallmarks Author", "🏆"),
]


@dataclass(frozen=True)
class BadgeDef:
    code: str
    name: str
    description: str  # may contain {target}
    stat: str
    target: int
    icon: str
    group: str


# The statistics behind ``stat`` are computed in ``game.stats``.
BADGE_CATALOG = [
    # reading volume
    BadgeDef(
        "first_read",
        "First Steps",
        "Read your first paper (skimmed or deeper)",
        "papers_read",
        1,
        "🌱",
        "Reading",
    ),
    BadgeDef(
        "page_turner", "Page Turner", "Read {target} papers", "papers_read", 10, "📄", "Reading"
    ),
    BadgeDef("bookworm", "Bookworm", "Read {target} papers", "papers_read", 25, "🐛", "Reading"),
    BadgeDef(
        "bibliophile", "Bibliophile", "Read {target} papers", "papers_read", 50, "📚", "Reading"
    ),
    BadgeDef("centurion", "Centurion", "Read {target} papers", "papers_read", 100, "💯", "Reading"),
    BadgeDef(
        "living_library",
        "Living Library",
        "Read {target} papers",
        "papers_read",
        250,
        "🏛️",
        "Reading",
    ),
    # depth
    BadgeDef(
        "deep_diver", "Deep Diver", "Read {target} papers thoroughly", "thorough", 5, "🤿", "Depth"
    ),
    BadgeDef(
        "abyss_explorer",
        "Abyss Explorer",
        "Read {target} papers thoroughly",
        "thorough",
        25,
        "🐙",
        "Depth",
    ),
    BadgeDef(
        "scribe",
        "Scribe",
        "Write substantial notes on {target} papers",
        "reflections",
        10,
        "✍️",
        "Depth",
    ),
    BadgeDef(
        "novelist",
        "Novelist",
        "Write {target} characters of notes in total",
        "notes_chars",
        20000,
        "📝",
        "Depth",
    ),
    BadgeDef("critic", "Critic", "Rate {target} papers", "ratings", 20, "⭐", "Depth"),
    BadgeDef(
        "fan_club", "Fan Club", "Give {target} five-star ratings", "five_star", 10, "🌟", "Depth"
    ),
    # discipline
    BadgeDef(
        "frog_eater",
        "Frog Eater",
        "Read your top paper first, {target} times",
        "frogs",
        10,
        "🐸",
        "Discipline",
    ),
    BadgeDef(
        "frog_prince",
        "Frog Prince",
        "Read your top paper first, {target} times",
        "frogs",
        50,
        "🤴",
        "Discipline",
    ),
    BadgeDef(
        "inbox_zero",
        "Inbox Zero",
        "Empty your reading queue by reading it",
        "inbox_zero",
        1,
        "📭",
        "Discipline",
    ),
    BadgeDef(
        "minimalist",
        "Minimalist",
        "Empty your reading queue {target} times",
        "inbox_zero",
        5,
        "🧘",
        "Discipline",
    ),
    BadgeDef(
        "curator",
        "Curator",
        "Mark {target} papers as not relevant (teaches the recommender)",
        "dismissed",
        10,
        "🗂️",
        "Discipline",
    ),
    # streaks and quests
    BadgeDef(
        "streak_4",
        "Month of Momentum",
        "Read something {target} weeks in a row",
        "best_streak",
        4,
        "🔥",
        "Consistency",
    ),
    BadgeDef(
        "streak_12",
        "Quarter Marathon",
        "Read something {target} weeks in a row",
        "best_streak",
        12,
        "🏃",
        "Consistency",
    ),
    BadgeDef(
        "streak_26",
        "Half-Year Hero",
        "Read something {target} weeks in a row",
        "best_streak",
        26,
        "🦸",
        "Consistency",
    ),
    BadgeDef(
        "streak_52",
        "Year of Reading",
        "Read something {target} weeks in a row",
        "best_streak",
        52,
        "🗓️",
        "Consistency",
    ),
    BadgeDef(
        "questing", "Questing", "Complete your first weekly quest", "quests", 1, "🧭", "Consistency"
    ),
    BadgeDef(
        "quest_master",
        "Quest Master",
        "Complete {target} weekly quests",
        "quests",
        10,
        "🗺️",
        "Consistency",
    ),
    BadgeDef(
        "quest_legend",
        "Quest Legend",
        "Complete {target} weekly quests",
        "quests",
        25,
        "🐉",
        "Consistency",
    ),
    BadgeDef(
        "straight_a",
        "Straight A",
        "Get grade A or S in {target} weeks",
        "a_weeks",
        5,
        "🅰️",
        "Consistency",
    ),
    BadgeDef(
        "perfectionist",
        "Perfectionist",
        "Get grade S in {target} weeks",
        "s_weeks",
        3,
        "💎",
        "Consistency",
    ),
    BadgeDef(
        "comeback",
        "Comeback Kid",
        "Read again after a break of 4 weeks or more",
        "comebacks",
        1,
        "🔄",
        "Consistency",
    ),
    # pace
    BadgeDef(
        "marathon", "Marathon Day", "Read {target} papers in one day", "max_day", 4, "🏅", "Pace"
    ),
    BadgeDef(
        "binge_week", "Binge Week", "Read {target} papers in one week", "max_week", 8, "🍿", "Pace"
    ),
    BadgeDef(
        "night_owl",
        "Night Owl",
        "Read {target} different papers between 22:00 and 04:00",
        "night_reads",
        5,
        "🦉",
        "Pace",
    ),
    BadgeDef(
        "early_riser",
        "Early Riser",
        "Read {target} different papers between 05:00 and 08:00",
        "early_reads",
        5,
        "🌅",
        "Pace",
    ),
    BadgeDef(
        "weekend_warrior",
        "Weekend Warrior",
        "Read {target} different papers on weekends",
        "weekend_reads",
        5,
        "🏖️",
        "Pace",
    ),
    # what you read
    BadgeDef(
        "classicist",
        "Classicist",
        "Read {target} landmark papers (top 1 % cited)",
        "classics",
        5,
        "🏺",
        "Breadth",
    ),
    BadgeDef(
        "canon_keeper",
        "Canon Keeper",
        "Read {target} landmark papers",
        "classics",
        20,
        "📜",
        "Breadth",
    ),
    BadgeDef(
        "early_bird",
        "Early Bird",
        "Read {target} papers within 30 days of publication",
        "hot",
        5,
        "🐦",
        "Breadth",
    ),
    BadgeDef(
        "scoop",
        "Scoop",
        "Read {target} papers within 7 days of publication",
        "scoops",
        3,
        "📰",
        "Breadth",
    ),
    BadgeDef(
        "archaeologist",
        "Archaeologist",
        "Read a paper that is 25+ years old",
        "old_papers",
        1,
        "🦴",
        "Breadth",
    ),
    BadgeDef(
        "polymath",
        "Polymath",
        "Read papers from {target} different research topics",
        "topics",
        8,
        "🧩",
        "Breadth",
    ),
    BadgeDef(
        "renaissance",
        "Renaissance Mind",
        "Read papers from {target} different topics",
        "topics",
        20,
        "🎨",
        "Breadth",
    ),
    BadgeDef(
        "trailblazer",
        "Trailblazer",
        "Read {target} papers from your recommendations",
        "from_recommendations",
        10,
        "🧗",
        "Breadth",
    ),
    BadgeDef(
        "library_diver",
        "Library Diver",
        "Read {target} papers from your libraries",
        "from_library",
        10,
        "📁",
        "Breadth",
    ),
    BadgeDef(
        "free_spirit",
        "Free Spirit",
        "Read {target} papers you added yourself",
        "manual_read",
        5,
        "🪁",
        "Breadth",
    ),
    # experience
    BadgeDef("thousand_club", "Thousand Club", "Earn {target} XP", "xp", 1000, "💰", "Experience"),
    BadgeDef("high_five", "High Five", "Earn {target} XP", "xp", 5000, "🖐️", "Experience"),
]
BADGE_CODES = {b.code for b in BADGE_CATALOG}
_CATALOG_TARGETS = {b.code: b.target for b in BADGE_CATALOG}


@dataclass(frozen=True)
class Badge:
    code: str
    name: str
    description: str
    stat: str
    target: int
    icon: str
    group: str


class GameRules(BaseModel):
    """Every reward and penalty. Locked into a profile when the game starts."""

    model_config = {"extra": "forbid", "allow_inf_nan": False}

    # base points per reading level (paid up to the deepest level ever reached)
    xp_abstract: int = Field(5, ge=0, le=10000)
    xp_skimmed: int = Field(15, ge=0, le=10000)
    xp_read: int = Field(40, ge=0, le=10000)
    xp_thorough: int = Field(80, ge=0, le=10000)
    # bonus: share of the base points for reading the top paper of the queue
    frog_bonus: float = Field(0.5, ge=0, le=10)
    # bonus: +step per consecutive reading week, up to max_steps steps
    streak_step: float = Field(0.05, ge=0, le=1)
    streak_max_steps: int = Field(10, ge=0, le=100)
    # bonus: landmark paper read at least at "read" depth
    classic_percentile: float = Field(99.0, ge=0, le=100)
    classic_xp: int = Field(20, ge=0, le=10000)
    # bonus: read soon after publication
    hot_days: int = Field(30, ge=0, le=3650)
    hot_xp: int = Field(10, ge=0, le=10000)
    # bonus: read a paper from the recommendations (any real depth)
    recommendation_xp: int = Field(15, ge=0, le=10000)
    # bonus: first rating of a paper
    rating_xp: int = Field(2, ge=0, le=10000)
    # bonus: notes on a paper reach this length
    reflection_chars: int = Field(140, ge=1, le=100000)
    reflection_xp: int = Field(10, ge=0, le=10000)
    # bonus: the queue becomes empty because you read its last paper (once per day)
    inbox_zero_xp: int = Field(10, ge=0, le=10000)
    # weekly quest: papers at least "read" + further papers at least skimmed
    weekly_detail_goal: int = Field(1, ge=0, le=100)
    weekly_skim_goal: int = Field(2, ge=0, le=100)
    quest_xp: int = Field(50, ge=0, le=10000)
    # per unlocked badge
    badge_xp: int = Field(25, ge=0, le=10000)
    # penalty: a queued paper leaves the queue before it was at least skimmed
    drop_penalty: int = Field(15, ge=0, le=10000)
    levels: list[LevelDef] = Field(
        default_factory=lambda: [LevelDef(xp=x, title=t, icon=i) for x, t, i in DEFAULT_LEVELS]
    )
    # badge code -> target; missing codes use the catalog default
    badge_targets: dict[str, int] = Field(default_factory=dict)

    @field_validator("levels")
    @classmethod
    def _levels_ascending(cls, levels):
        if not levels or levels[0].xp != 0:
            raise ValueError("the first level must start at 0 XP")
        xps = [level.xp for level in levels]
        if xps != sorted(set(xps)):
            raise ValueError("level XP thresholds must be strictly increasing")
        if len({level.title for level in levels}) != len(levels):
            raise ValueError("level titles must be unique")
        return levels

    @field_validator("badge_targets")
    @classmethod
    def _known_badges(cls, targets):
        unknown = set(targets) - BADGE_CODES
        if unknown:
            raise ValueError(f"unknown badge(s): {', '.join(sorted(unknown))}")
        if any(value < 1 for value in targets.values()):
            raise ValueError("badge targets must be at least 1")
        return targets

    @model_validator(mode="after")
    def _depth_pays_more(self):
        values = [self.xp_abstract, self.xp_skimmed, self.xp_read, self.xp_thorough]
        if values != sorted(values):
            raise ValueError("deeper reading levels must not earn fewer points")
        return self

    def base_xp(self) -> dict[str, int]:
        return {
            "abstract": self.xp_abstract,
            "skimmed": self.xp_skimmed,
            "read": self.xp_read,
            "thorough": self.xp_thorough,
        }

    def badges(self) -> list[Badge]:
        """The badge catalogue with this profile's targets."""
        result = []
        for definition in BADGE_CATALOG:
            target = self.badge_targets.get(definition.code, definition.target)
            result.append(
                Badge(
                    definition.code,
                    definition.name,
                    definition.description.format(target=target),
                    definition.stat,
                    target,
                    definition.icon,
                    definition.group,
                )
            )
        return result

    def fingerprint(self) -> str:
        """Short hash of the complete rule set (equal rules = equal fingerprint).

        Stable across versions: see ``FINGERPRINT_FIELDS``.
        """
        return rules_fingerprint(self)


# Rules format 1: the fields every fingerprint covers, as of litresearch 1.0 (and
# unchanged since 0.1). A rule added later (with a default) enters the hash only
# when a profile sets it to a value other than its default, so rules locked by
# an older version keep their fingerprint in every newer one. Never remove or
# rename a name here (that would change all fingerprints: 2.0 only).
FINGERPRINT_FIELDS = {
    GameRules: frozenset(
        {
            "xp_abstract",
            "xp_skimmed",
            "xp_read",
            "xp_thorough",
            "frog_bonus",
            "streak_step",
            "streak_max_steps",
            "classic_percentile",
            "classic_xp",
            "hot_days",
            "hot_xp",
            "recommendation_xp",
            "rating_xp",
            "reflection_chars",
            "reflection_xp",
            "inbox_zero_xp",
            "weekly_detail_goal",
            "weekly_skim_goal",
            "quest_xp",
            "badge_xp",
            "drop_penalty",
            "levels",
            "badge_targets",
        }
    ),
    LevelDef: frozenset({"xp", "title", "icon"}),
}
# the badge catalogue of rules format 1: a badge added later counts only with a
# target other than its catalogue default
BADGE_CODES_V1 = frozenset(
    """first_read page_turner bookworm bibliophile centurion living_library deep_diver
    abyss_explorer scribe novelist critic fan_club frog_eater frog_prince inbox_zero
    minimalist curator streak_4 streak_12 streak_26 streak_52 questing quest_master
    quest_legend straight_a perfectionist comeback marathon binge_week night_owl
    early_riser weekend_warrior classicist canon_keeper early_bird scoop archaeologist
    polymath renaissance trailblazer library_diver free_spirit thousand_club
    high_five""".split()
)


def _canonical(model: BaseModel):
    """The model as JSON data without later-added fields that hold their default."""
    baseline = next(
        (fields for cls, fields in FINGERPRINT_FIELDS.items() if isinstance(model, cls)),
        None,
    )
    data = {}
    for name, info in type(model).model_fields.items():
        value = getattr(model, name)
        if baseline is not None and name not in baseline:
            default = info.get_default(call_default_factory=True)
            if value == default:
                continue
        if isinstance(value, BaseModel):
            value = _canonical(value)
        elif isinstance(value, list):
            value = [_canonical(v) if isinstance(v, BaseModel) else v for v in value]
        data[name] = value
    return data


def rules_fingerprint(rules: "GameRules") -> str:
    """The fingerprint of ``rules``; see ``FINGERPRINT_FIELDS``."""
    data = _canonical(rules)
    # resolved targets: writing a default explicitly must not change the fingerprint;
    # a badge added to the catalogue later enters only with a non-default target
    resolved = {badge.code: badge.target for badge in rules.badges()}
    data["badge_targets"] = {
        code: target
        for code, target in resolved.items()
        if code in BADGE_CODES_V1 or target != _CATALOG_TARGETS[code]
    }
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]
