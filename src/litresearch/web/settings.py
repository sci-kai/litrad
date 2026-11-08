"""Editable settings: the schema behind the settings form and the rules form.

``GROUPS`` describes every recommendation setting (label, help, range) for
the Settings page; ``RULE_GROUPS`` the game rules chosen when a profile is
created. The parse functions turn submitted forms into nested dicts, which
``profiles.write_overlay`` validates and saves in the profile's settings.yaml.
"""

import math
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from litresearch.config import Config
from litresearch.rules import BADGE_CODES, GameRules, LevelDef
from litresearch.yamlfile import update_yaml


@dataclass(frozen=True)
class Setting:
    path: tuple[str, ...]
    label: str
    help: str
    kind: str = "float"  # float | int | bool
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None

    @property
    def key(self) -> str:
        return ".".join(self.path)

    def value(self, cfg: Config):
        node = cfg
        for part in self.path:
            node = getattr(node, part)
        return node


@dataclass(frozen=True)
class Group:
    title: str
    intro: str
    settings: tuple[Setting, ...]
    advanced: bool = False


GROUPS = (
    Group(
        "Recommendation list",
        "",
        (
            Setting(
                ("selection", "n_recommendations"),
                "Papers per list",
                "Size of each recommendation list, ranked by score (1-5).",
                "int",
                1,
                5,
                1,
            ),
            Setting(
                ("selection", "reminder_days"),
                "Reminder (days)",
                "Remind me to get new recommendations this many days after the last list "
                "(0 = never).",
                "int",
                0,
                365,
                1,
            ),
        ),
    ),
    Group(
        "Eligibility: current or important",
        "Only fresh papers or landmarks are considered.",
        (
            Setting(
                ("selection", "fresh_window_months"),
                "Fresh window (months)",
                "Published within this many months counts as fresh.",
                "int",
                1,
                36,
                1,
            ),
            Setting(
                ("selection", "landmark_min_percentile"),
                "Landmark citation percentile",
                "Older papers must be at least this highly cited (OpenAlex percentile, "
                "normalised by field and year).",
                "float",
                50,
                100,
                0.5,
            ),
            Setting(
                ("selection", "landmark_max_age_years"),
                "Landmark maximum age (years)",
                "Landmarks older than this are ignored.",
                "int",
                1,
                100,
                1,
            ),
        ),
    ),
    Group(
        "Score weights",
        "score = w_relevance × relevance + w_impact × impact + w_timeliness × timeliness. "
        "Only the ratio between the weights matters.",
        (
            Setting(
                ("selection", "weights", "relevance"),
                "Weight: relevance",
                "How much similarity to your library and topics counts.",
                "float",
                0,
                1,
                0.05,
            ),
            Setting(
                ("selection", "weights", "impact"),
                "Weight: impact",
                "How much citations / journal standing count.",
                "float",
                0,
                1,
                0.05,
            ),
            Setting(
                ("selection", "weights", "timeliness"),
                "Weight: timeliness",
                "How much recency counts.",
                "float",
                0,
                1,
                0.05,
            ),
        ),
    ),
    Group(
        "Selection",
        "",
        (
            Setting(
                ("selection", "mmr_lambda"),
                "Relevance vs. diversity (λ)",
                "1 = strictly by score; lower values prefer papers unlike the ones already picked.",
                "float",
                0,
                1,
                0.05,
            ),
            Setting(
                ("selection", "min_relative_score"),
                "Score floor (share of best)",
                "Only candidates with at least this share of the best score can be picked, "
                "so diversity never pulls in weak papers.",
                "float",
                0,
                1,
                0.05,
            ),
            Setting(
                ("selection", "cooldown_weeks"),
                "Cooldown (weeks)",
                "A recommendation you did not pick can come back after this many weeks.",
                "int",
                0,
                52,
                1,
            ),
            Setting(
                ("selection", "results_per_query"),
                "Results per topic query",
                "OpenAlex results per query and kind (fresh, landmark). More = broader, "
                "slower, uses more of the free API budget.",
                "int",
                5,
                100,
                5,
            ),
        ),
    ),
    Group(
        "Fine-tuning",
        "Constants of the score formulas; the defaults are sensible.",
        (
            Setting(
                ("selection", "similarity_share"),
                "Relevance: similarity share",
                "relevance = share × library-similarity rank + (1 − share) × topic bonus.",
                "float",
                0,
                1,
                0.05,
            ),
            Setting(
                ("selection", "knn"),
                "Relevance: nearest library papers (k)",
                "Library similarity is the mean similarity to the k most similar library papers.",
                "int",
                1,
                50,
                1,
            ),
            Setting(
                ("selection", "s2_only_topic_bonus"),
                "Relevance: bonus for library-based picks",
                "Topic bonus for papers found only by Semantic Scholar's recommendations "
                "for your library (topic papers get weight / max weight).",
                "float",
                0,
                1,
                0.05,
            ),
            Setting(
                ("selection", "top_venue_citedness"),
                "Impact: top journal citedness",
                "Journal 2-year mean citedness that counts as impact 1.0 for fresh papers "
                "(log scale).",
                "float",
                1,
                200,
                1,
            ),
            Setting(
                ("selection", "unknown_venue_impact"),
                "Impact: unknown journal",
                "Impact of fresh papers without a known journal.",
                "float",
                0,
                1,
                0.05,
            ),
            Setting(
                ("selection", "early_citation_bonus"),
                "Impact: early-citation bonus (max)",
                "Extra impact for fresh papers that are already cited.",
                "float",
                0,
                1,
                0.05,
            ),
            Setting(
                ("selection", "early_citation_saturation"),
                "Impact: early citations for full bonus",
                "Number of citations at which the early-citation bonus is complete.",
                "int",
                1,
                1000,
                1,
            ),
            Setting(
                ("selection", "citations_per_year_saturation"),
                "Impact: citations/year for 1.0 (fallback)",
                "Used for landmark papers without a citation percentile.",
                "int",
                1,
                5000,
                1,
            ),
            Setting(
                ("selection", "landmark_timeliness"),
                "Timeliness of landmark papers",
                "Fresh papers get 0.5–1.0 depending on age; landmarks get this constant.",
                "float",
                0,
                1,
                0.05,
            ),
        ),
        advanced=True,
    ),
    Group(
        "Data sources",
        "Both free; Semantic Scholar without a key may be rate-limited.",
        (
            Setting(
                ("sources", "openalex"),
                "OpenAlex topic searches",
                "Fresh and landmark searches for every topic query.",
                "bool",
            ),
            Setting(
                ("sources", "semanticscholar"),
                "Semantic Scholar",
                "Recommendations based on your library, SPECTER2 embeddings and TL;DRs. "
                "Sends DOIs of library papers and of the example papers to Semantic Scholar.",
                "bool",
            ),
        ),
    ),
    Group(
        "Reading queue",
        "Game rules are locked per profile (Profile page).",
        (
            Setting(
                ("queue", "max_size"),
                "Queue size",
                "Maximum number of queued papers.",
                "int",
                1,
                20,
                1,
            ),
        ),
    ),
)

ALL_SETTINGS = tuple(s for g in GROUPS for s in g.settings)


class SettingsError(ValueError):
    pass


def to_number(raw, kind: str = "float") -> int | float:
    """A form value as int or finite float; ValueError otherwise ("nan", "inf", "abc")."""
    value = int(raw) if kind == "int" else float(raw)
    if not math.isfinite(value):
        raise ValueError(f"not a finite number: {raw}")
    return value


def parse_form(form: dict) -> dict:
    """Turn submitted form fields into a nested update dict."""
    updates: dict = {}

    def put(path, value):
        node = updates
        for part in path[:-1]:
            node = node.setdefault(part, {})
        node[path[-1]] = value

    for setting in ALL_SETTINGS:
        raw = form.get(setting.key)
        if setting.kind == "bool":
            put(setting.path, raw in ("on", "true", "1"))
            continue
        if raw is None or str(raw).strip() == "":
            raise SettingsError(f"'{setting.label}' is empty")
        try:
            put(setting.path, to_number(raw, setting.kind))
        except ValueError as error:
            raise SettingsError(f"'{setting.label}': '{raw}' is not a number") from error

    updates.update(parse_topics(form))
    return updates


def parse_topics(form: dict) -> dict:
    """Topics and excluded terms from the topic editor (lists of form values)."""
    topics = []
    names = form.get("topic_name", [])
    weights = form.get("topic_weight", [])
    queries = form.get("topic_queries", [])
    if not len(names) == len(weights) == len(queries):
        raise SettingsError(
            "The topic form is incomplete (each topic needs a name, a weight and queries). "
            "Reload the page and enter the topics again."
        )
    for name, weight, query_text in zip(names, weights, queries, strict=True):
        name = name.strip()
        if not name:
            continue
        try:
            weight = to_number(weight)
        except ValueError as error:
            raise SettingsError(f"Topic '{name}': weight must be a number") from error
        if weight < 0:
            raise SettingsError(f"Topic '{name}': weight must be at least 0")
        topics.append(
            {
                "name": name,
                "weight": weight,
                "queries": [q.strip() for q in query_text.splitlines() if q.strip()],
            }
        )
    return {
        "topics": topics,
        "excluded_terms": [
            t.strip() for t in form.get("excluded_terms", "").splitlines() if t.strip()
        ],
    }


def write_values(config_path: Path, updates: dict) -> None:
    """Write plain values into ``config.yaml`` (no validation), keeping comments."""
    update_yaml(config_path, updates)


# --- game rules (profile creation form) ------------------------------------------------------

RULE_GROUPS = (
    (
        "Points per reading level",
        "Your marking after reading decides the points; upgrading later pays only the difference.",
        (
            ("xp_abstract", "👀 abstract", "int", 0, 1000, 1),
            ("xp_skimmed", "📄 skimmed", "int", 0, 1000, 1),
            ("xp_read", "📖 read", "int", 0, 1000, 1),
            ("xp_thorough", "🔬 thorough", "int", 0, 1000, 1),
        ),
    ),
    (
        "Bonuses",
        "Paid on top of the base points.",
        (
            ("frog_bonus", "🐸 top paper first: share of base points", "float", 0, 5, 0.05),
            ("streak_step", "🔥 streak: extra share per consecutive week", "float", 0, 1, 0.01),
            ("streak_max_steps", "🔥 streak: maximum number of steps", "int", 0, 100, 1),
            ("classic_percentile", "🏛️ landmark: minimum citation percentile", "float", 0, 100, 0.5),
            ("classic_xp", "🏛️ landmark read (≥ read)", "int", 0, 1000, 1),
            ("hot_days", "📰 fresh paper: days after publication", "int", 0, 365, 1),
            ("hot_xp", "📰 fresh paper read", "int", 0, 1000, 1),
            ("recommendation_xp", "🎯 read a recommended paper", "int", 0, 1000, 1),
            ("rating_xp", "⭐ first rating", "int", 0, 1000, 1),
            ("reflection_chars", "✍️ notes: characters needed", "int", 1, 100000, 1),
            ("reflection_xp", "✍️ substantial notes", "int", 0, 1000, 1),
            ("inbox_zero_xp", "📭 queue emptied by reading", "int", 0, 1000, 1),
            ("badge_xp", "🏅 per badge", "int", 0, 1000, 1),
        ),
    ),
    (
        "Weekly quest",
        "A target for every week (Monday to Sunday).",
        (
            ("weekly_detail_goal", "papers read at least at 'read' depth", "int", 0, 50, 1),
            ("weekly_skim_goal", "further papers at least skimmed", "int", 0, 50, 1),
            ("quest_xp", "🗓️ quest reward", "int", 0, 5000, 1),
        ),
    ),
    (
        "Penalty",
        "Discourages picking recommendations and abandoning them.",
        (("drop_penalty", "⚠️ queued paper leaves the queue unread", "int", 0, 1000, 1),),
    ),
)


def levels_text(rules) -> str:
    return "\n".join(f"{level.xp} | {level.title} | {level.icon}" for level in rules.levels)


def parse_rules_form(form: dict, base: GameRules) -> GameRules:
    """Build GameRules from the start form; raises SettingsError on bad input."""
    values = base.model_dump()
    for _, _, fields in RULE_GROUPS:
        for name, label, kind, *_ in fields:
            raw = str(form.get(name, "")).strip()
            if raw == "":
                raise SettingsError(f"'{label}' is empty")
            try:
                values[name] = to_number(raw, kind)
            except ValueError as error:
                raise SettingsError(f"'{label}': '{raw}' is not a number") from error
    levels = []
    for number, line in enumerate(str(form.get("levels", "")).splitlines(), start=1):
        if not line.strip():
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) < 2:
            raise SettingsError(f"Level line {number}: use 'XP | title | icon'")
        try:
            levels.append(
                LevelDef(
                    xp=int(parts[0]),
                    title=parts[1],
                    icon=parts[2] if len(parts) > 2 and parts[2] else "⭐",
                )
            )
        except ValueError as error:
            raise SettingsError(f"Level line {number}: {error}") from error
    values["levels"] = [level.model_dump() for level in levels] or values["levels"]
    targets = {}
    for code in BADGE_CODES:
        raw = str(form.get(f"badge_{code}", "")).strip()
        if raw:
            try:
                targets[code] = int(raw)
            except ValueError as error:
                raise SettingsError(f"Badge target '{code}': '{raw}' is not a number") from error
    values["badge_targets"] = targets or values["badge_targets"]
    try:
        return GameRules.model_validate(values)
    except ValueError as error:
        raise SettingsError(friendly_error(error)) from error


# --- readable validation errors --------------------------------------------------------------

# field paths (as pydantic reports them) and the labels the forms show
FIELD_LABELS: dict[tuple, str] = {
    **{s.path: s.label for s in ALL_SETTINGS},
    **{(name,): label for _, _, fields in RULE_GROUPS for name, label, *_ in fields},
    **{("game", name): label for _, _, fields in RULE_GROUPS for name, label, *_ in fields},
    # set on the pages of the example lists (Library → Positive / Negative examples)
    ("selection", "s2_positive_rules"): "Rules of the positive examples",
    ("selection", "s2_positive_count"): "Number of positive examples",
    ("selection", "s2_negative_rules"): "Rules of the negative examples",
    ("selection", "s2_negative_count"): "Number of negative examples",
}
_PART_NAMES = {"topics": "Topic", "levels": "Level"}


def _field_name(loc: tuple) -> str:
    """'Score weight: relevance', 'Topic 2: weight', 'Level 3: title' or 'hot_days'."""
    loc = tuple(part for part in loc if part not in ("body", "query", "path"))
    if loc in FIELD_LABELS:
        return FIELD_LABELS[loc]
    for start in range(len(loc)):
        part = loc[start]
        if part in _PART_NAMES and start + 1 < len(loc) and isinstance(loc[start + 1], int):
            rest = " ".join(str(p) for p in loc[start + 2 :])
            name = f"{_PART_NAMES[part]} {loc[start + 1] + 1}"
            return f"{name}: {rest}" if rest else name
        if part == "badge_targets":
            return "Badge target " + " ".join(str(p) for p in loc[start + 1 :])
    return " ".join(str(p) for p in loc) or "A value"


def describe_problem(error: dict) -> str:
    kind, ctx = error.get("type", ""), error.get("ctx") or {}
    if kind in ("less_than_equal", "less_than"):
        return f"must be at most {ctx.get('le', ctx.get('lt'))}"
    if kind in ("greater_than_equal", "greater_than"):
        return f"must be at least {ctx.get('ge', ctx.get('gt'))}"
    if kind == "string_too_short":
        return (
            "must not be empty"
            if ctx.get("min_length") == 1
            else (f"needs at least {ctx.get('min_length')} characters")
        )
    if kind == "string_too_long":
        return f"may have at most {ctx.get('max_length')} characters"
    if kind in ("int_parsing", "int_from_float", "int_type"):
        return "must be a whole number"
    if kind in ("float_parsing", "float_type", "finite_number"):
        return "must be a number"
    if kind == "missing":
        return "is missing"
    message = str(error.get("msg", "is not valid"))
    return message.removeprefix("Value error, ").removeprefix("Assertion failed, ")


def friendly_error(error: Exception) -> str:
    """A short sentence for a toast; pydantic's reports name the form's labels instead.

    ``'🐸 top paper first: share of base points' must be at most 10`` instead of
    "1 validation error for GameRules … errors.pydantic.dev …".
    """
    cause: BaseException | None = error
    while cause is not None and not isinstance(cause, ValidationError):
        cause = cause.__cause__
    if cause is None:
        return str(error)
    problems = []
    for item in cause.errors():
        loc = tuple(item.get("loc") or ())
        problem = describe_problem(item)
        problems.append(
            f"'{_field_name(loc)}' {problem}" if loc else problem[:1].upper() + problem[1:]
        )
    return "; ".join(dict.fromkeys(problems))
