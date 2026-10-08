"""Editable settings: one schema for the settings form and the criteria page.

Values are written back to ``config.yaml`` with ruamel.yaml so that comments
and key order survive; the previous file is kept as ``config.yaml.bak``.
"""

import io
import shutil
from dataclasses import dataclass
from pathlib import Path

from ruamel.yaml import YAML

from litresearch.backup import atomic_write_text
from litresearch.config import Config


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
        "Weekly list",
        "How many papers you get and how they are split.",
        (
            Setting(
                ("selection", "n_detail"),
                "Papers to read in detail",
                "The papers with the highest relevance + impact among the picks.",
                "int",
                0,
                5,
                1,
            ),
            Setting(
                ("selection", "n_skim"),
                "Papers to skim",
                "The remaining picks. Detail + skim must not exceed 5.",
                "int",
                0,
                5,
                1,
            ),
        ),
    ),
    Group(
        "Eligibility: current or important",
        "A paper is only considered if it is fresh or a landmark.",
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
        "How the final papers are picked from the scored candidates.",
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
        "Constants inside the score formulas. The defaults are sensible; change them "
        "only if you understand the effect (see How it works).",
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
                10,
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
        "Where candidates come from. Both are free; Semantic Scholar works without a key "
        "but may be rate-limited.",
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
                "Sends DOIs of library, liked and dismissed papers to Semantic Scholar.",
                "bool",
            ),
        ),
    ),
    Group(
        "Reading queue and game",
        "Limits and rules of the reading queue and the points system.",
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
            Setting(
                ("game", "weekly_detail_goal"),
                "Weekly quest: deep reads",
                "Papers read at least at 'read' depth per week.",
                "int",
                0,
                20,
                1,
            ),
            Setting(
                ("game", "weekly_skim_goal"),
                "Weekly quest: further skims",
                "Further papers at least skimmed per week.",
                "int",
                0,
                20,
                1,
            ),
            Setting(
                ("game", "drop_penalty"),
                "Penalty for dropping a picked recommendation",
                "XP lost when a paper you took from the recommendations leaves the queue "
                "without being at least skimmed.",
                "int",
                0,
                200,
                1,
            ),
        ),
    ),
)

ALL_SETTINGS = tuple(s for g in GROUPS for s in g.settings)


class SettingsError(ValueError):
    pass


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
            put(setting.path, int(raw) if setting.kind == "int" else float(raw))
        except ValueError as error:
            raise SettingsError(f"'{setting.label}': '{raw}' is not a number") from error

    topics = []
    names = form.get("topic_name", [])
    weights = form.get("topic_weight", [])
    queries = form.get("topic_queries", [])
    for name, weight, query_text in zip(names, weights, queries, strict=True):
        name = name.strip()
        if not name:
            continue
        try:
            weight = float(weight)
        except ValueError as error:
            raise SettingsError(f"Topic '{name}': weight must be a number") from error
        topics.append(
            {
                "name": name,
                "weight": weight,
                "queries": [q.strip() for q in query_text.splitlines() if q.strip()],
            }
        )
    updates["topics"] = topics
    updates["excluded_terms"] = [
        t.strip() for t in form.get("excluded_terms", "").splitlines() if t.strip()
    ]
    return updates


def _merge(base: dict, updates: dict) -> dict:
    merged = dict(base)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _apply(node, updates: dict, existing_topics) -> None:
    """Write updates into a ruamel round-trip node, keeping comments."""
    for key, value in updates.items():
        if key == "topics":
            node["topics"] = _topics_node(value, existing_topics)
        elif isinstance(value, dict):
            if key not in node or node[key] is None:
                node[key] = {}
            _apply(node[key], value, existing_topics)
        else:
            node[key] = value


def _topics_node(topics: list[dict], existing):
    """Keep fields of existing topics that the form does not edit (mesh terms, seeds)."""
    by_name = {t.get("name"): t for t in (existing or [])}
    result = []
    for topic in topics:
        node = by_name.get(topic["name"])
        if node is None:
            node = {"name": topic["name"]}
        node["weight"] = topic["weight"]
        node["queries"] = topic["queries"]
        result.append(node)
    return result


def _write_document(config_path: Path, yaml: YAML, document) -> None:
    """Back up the old file, then replace it atomically (never half-written)."""
    buffer = io.StringIO()
    yaml.dump(document, buffer)
    shutil.copy2(config_path, config_path.with_name(config_path.name + ".bak"))
    atomic_write_text(config_path, buffer.getvalue())


def write_values(config_path: Path, updates: dict) -> None:
    """Write plain values (no validation) keeping comments, with a backup."""
    yaml = YAML()
    yaml.preserve_quotes = True
    with open(config_path, encoding="utf-8") as handle:
        document = yaml.load(handle) or {}
    _apply(document, updates, document.get("topics"))
    _write_document(config_path, yaml, document)


def save_settings(config_path: Path, updates: dict) -> Config:
    """Validate and write; raises SettingsError without touching the file on errors."""
    yaml = YAML()
    yaml.preserve_quotes = True
    with open(config_path, encoding="utf-8") as handle:
        document = yaml.load(handle) or {}
    plain = YAML(typ="safe")
    with open(config_path, encoding="utf-8") as handle:
        current = plain.load(handle) or {}
    merged = _merge(current, updates)
    try:
        validated = Config.model_validate(merged)
    except ValueError as error:
        raise SettingsError(str(error)) from error
    w = validated.selection.weights
    if w.relevance + w.impact + w.timeliness <= 0:
        raise SettingsError("At least one score weight must be above 0")
    _apply(document, updates, document.get("topics"))
    _write_document(config_path, yaml, document)
    return validated
