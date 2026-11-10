"""Configuration models, loaded from a YAML file."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from litrad.locations import base_dir, resolve_path
from litrad.rules import GameRules

# value used in config files for "not set"
NA = "n/a"

# rules that choose the example papers for Semantic Scholar's recommendations
# (see examples.py; their labels and descriptions are there)
S2_POSITIVE_RULES = ("liked", "read", "recent", "impact", "fwci", "cited")
S2_NEGATIVE_RULES = ("dismissed", "low_rated")


class _Base(BaseModel):
    # inf and nan are valid YAML floats but no valid setting
    model_config = {"extra": "forbid", "allow_inf_nan": False}

    @field_validator("*", mode="before")
    @classmethod
    def _na_to_none(cls, value):
        if isinstance(value, str) and value.strip().lower() == NA:
            return None
        return value


class Paths(_Base):
    # relative paths below (and backup.dir) are resolved against this folder;
    # not set: see locations.base_dir (set by load_config to the folder in use)
    base_dir: Path | None = None
    # set per profile (profiles.profile_config), not in config.yaml: the profile's
    # Paperpile export (settings.yaml may name another file) and its database
    bibtex: Path = Path("data/paperpile/library.bib")
    database: Path = Path("data/litrad.sqlite")
    # one folder per profile: database, Paperpile export, settings
    profiles_dir: Path = Path("data/profiles")
    cache_dir: Path = Path("data/cache")
    # notes: Markdown files in <profile folder>/notes, or in this Obsidian vault
    obsidian_vault: Path | None = None
    # folder inside the vault for the note files ("" = the vault itself)
    notes_subfolder: str = "litrad"

    @field_validator("notes_subfolder")
    @classmethod
    def _relative_subfolder(cls, value: str) -> str:
        value = (value or "").strip().strip("/")
        if Path(value).is_absolute() or ".." in Path(value).parts or "\\" in value:
            raise ValueError("use a folder inside the vault, e.g. 'litrad'")
        return value


class Topic(_Base):
    name: str
    weight: float = Field(1.0, ge=0)
    queries: list[str] = []
    mesh_terms: list[str] = []
    seed_dois: list[str] = []


class Sources(_Base):
    semanticscholar: bool = True
    # literature search (Library → Find & add, `litrad search`)
    europepmc: bool = True
    pubmed: bool = True
    biorxiv: bool = True
    openalex: bool = True


class Library(_Base):
    # Paperpile labels that carry no topical information
    ignore_labels: list[str] = ["Import Mendeley"]


class Api(_Base):
    semanticscholar_key: str | None = None
    openalex_key: str | None = None
    contact_email: str | None = None
    # optional free NCBI key: PubMed allows 10 instead of 3 requests per second
    ncbi_key: str | None = None


class Embeddings(_Base):
    backend: Literal["s2", "local", "hybrid"] = "hybrid"
    local_url: str | None = None

    @model_validator(mode="after")
    def _local_needs_url(self):
        # hybrid without a URL simply falls back to S2 only
        if self.backend == "local" and not self.local_url:
            raise ValueError("embeddings.local_url is required for backend 'local'")
        return self


class Weights(_Base):
    relevance: float = 0.5
    impact: float = 0.3
    timeliness: float = 0.2


class Selection(_Base):
    # papers per weekly list; how deeply to read them is your choice
    n_recommendations: int = Field(5, ge=1, le=5)
    fresh_window_months: int = Field(6, ge=1)
    landmark_min_percentile: float = Field(95, ge=0, le=100)
    mmr_lambda: float = Field(0.7, ge=0, le=1)
    weights: Weights = Weights()
    # landmark papers: published within this many years
    landmark_max_age_years: int = Field(15, ge=1)
    # results per topic query and kind (fresh / landmark); each query costs API budget
    results_per_query: int = Field(25, ge=1, le=100)
    # a recommended paper you did not pick may come back after this many weeks
    cooldown_weeks: int = Field(8, ge=0)
    # remind me to get new recommendations after this many days (0 = never)
    reminder_days: int = Field(7, ge=0, le=365)
    # --- fine-tuning (see docs/documentation.md, "How papers are recommended") ---
    # relevance = similarity_share * library similarity + (1 - share) * topic bonus
    similarity_share: float = Field(0.7, ge=0, le=1)
    # number of most similar library papers averaged for the similarity
    knn: int = Field(5, ge=1, le=50)
    # topic bonus for papers found only by Semantic Scholar's library-based recommendations
    s2_only_topic_bonus: float = Field(0.5, ge=0, le=1)
    # journal 2-year mean citedness that counts as a top journal (impact 1.0)
    top_venue_citedness: float = Field(30.0, gt=0)
    # impact of fresh papers whose journal is unknown
    unknown_venue_impact: float = Field(0.05, ge=0, le=1)
    # maximal impact bonus of fresh papers for early citations, reached at this many
    early_citation_bonus: float = Field(0.2, ge=0, le=1)
    early_citation_saturation: int = Field(50, ge=1)
    # citations per year that count as maximal impact when no percentile is known
    citations_per_year_saturation: int = Field(200, ge=1)
    # timeliness of landmark papers (fresh papers: 0.5-1.0)
    landmark_timeliness: float = Field(0.4, ge=0, le=1)
    # picks need at least this share of the best score (diversity must not pull in weak papers)
    min_relative_score: float = Field(0.6, ge=0, le=1)
    # example papers for Semantic Scholar's recommendations: rules in order, and how many
    s2_positive_rules: list[str] = ["liked", "recent"]
    s2_positive_count: int = Field(100, ge=1, le=500)
    s2_negative_rules: list[str] = ["dismissed"]
    s2_negative_count: int = Field(50, ge=0, le=500)

    @field_validator("s2_positive_rules", "s2_negative_rules", mode="before")
    @classmethod
    def _known_rules(cls, value, info):
        if value is None or (isinstance(value, str) and value.strip().lower() == NA):
            return []
        if isinstance(value, str):
            value = [value]
        known = S2_POSITIVE_RULES if info.field_name == "s2_positive_rules" else S2_NEGATIVE_RULES
        names = []
        for name in value:
            name = str(name).strip().lower()
            if name not in known:
                raise ValueError(f"unknown rule '{name}'; choose from {', '.join(known)}")
            if name not in names:
                names.append(name)
        return names


class Queue(_Base):
    # deliberately small: finish or drop papers before adding new ones
    max_size: int = Field(5, ge=1, le=20)


class Backup(_Base):
    # put this in a cloud-synced folder (Nextcloud, Dropbox, Syncthing, rclone mount ...)
    dir: Path = Path("data/backups")
    # number of snapshots (and archives) to keep
    keep: int = Field(14, ge=1, le=1000)
    # the web server makes a backup at start and then every this many hours (0 = off)
    interval_hours: float = Field(24, ge=0)


class Llm(_Base):
    enabled: bool = False
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None


class Schedule(_Base):
    cron: str = "0 6 * * 1"


class Web(_Base):
    # local | all | interface name (eth0, wlan0) | IPv4 address; see web/network.py
    host: str = "local"
    port: int = 8080
    password_hash: str | None = None


class Config(_Base):
    paths: Paths
    library: Library = Library()
    topics: list[Topic] = []
    excluded_terms: list[str] = []
    sources: Sources = Sources()
    api: Api = Api()
    embeddings: Embeddings = Embeddings()
    selection: Selection = Selection()
    queue: Queue = Queue()
    # defaults for a new game profile; a running profile keeps its own locked copy
    game: GameRules = GameRules()
    backup: Backup = Backup()
    llm: Llm = Llm()
    schedule: Schedule = Schedule()
    web: Web = Web()

    @model_validator(mode="after")
    def _resolve_paths(self):
        """Make relative paths absolute against ``paths.base_dir`` (when absolute)."""
        base = self.paths.base_dir
        if base is None or not base.is_absolute():
            return self
        fields = ("bibtex", "database", "profiles_dir", "cache_dir", "obsidian_vault")
        updates = {
            name: resolve_path(base, getattr(self.paths, name))
            for name in fields
            if getattr(self.paths, name) is not None
        }
        self.paths = self.paths.model_copy(update=updates)
        self.backup = self.backup.model_copy(update={"dir": resolve_path(base, self.backup.dir)})
        return self


class ConfigError(ValueError):
    """A missing or invalid config file, with a message for humans."""


# paths that belong to a profile (profiles.profile_config sets them), never to config.yaml
PROFILE_PATHS = {"database": "database", "bibtex": "Paperpile export (library.bib)"}


def config_base_dir(path: Path, raw_paths: dict | None = None) -> tuple[Path, str]:
    """Base folder of relative paths for the config file at ``path`` and the rule used.

    ``raw_paths`` is the ``paths`` section as read from the file.
    """
    explicit = (raw_paths or {}).get("base_dir")
    if isinstance(explicit, str) and explicit.strip().lower() in ("", NA):
        explicit = None
    if explicit is not None:
        explicit = Path(str(explicit)).expanduser()
    base, rule = base_dir(Path(path).absolute(), explicit)
    return Path(base), rule


def load_config(path: Path) -> Config:
    """Read and validate ``config.yaml``; problems become a readable ConfigError.

    Relative paths become absolute, resolved against the base folder (see
    ``litrad.locations``), so they do not depend on the current folder.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
    except FileNotFoundError as error:
        raise ConfigError(
            f"No config file at {path}. Copy config/config.example.yaml there, or run "
            "'litrad where' to see where litrad looks for it."
        ) from error
    except yaml.YAMLError as error:
        raise ConfigError(f"{path} is not valid YAML: {error}") from error
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must contain settings (key: value), not {type(raw).__name__}")
    if isinstance(raw.get("paths"), dict):
        for key in PROFILE_PATHS:
            if key in raw["paths"]:
                raise ConfigError(
                    f"{path}: paths.{key} is not a setting of config.yaml (each profile keeps "
                    f"its {PROFILE_PATHS[key]} in its own folder); delete that line"
                )
        raw["paths"] = dict(raw["paths"], base_dir=str(config_base_dir(path, raw["paths"])[0]))
    try:
        return Config.model_validate(raw)
    except ValueError as error:
        raise ConfigError(f"{path} has invalid settings:\n{error}") from error
