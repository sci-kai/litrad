"""Configuration models, loaded from a YAML file."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

# value used in config files for "not set"
NA = "n/a"


class _Base(BaseModel):
    model_config = {"extra": "forbid"}

    @field_validator("*", mode="before")
    @classmethod
    def _na_to_none(cls, value):
        if isinstance(value, str) and value.strip().lower() == NA:
            return None
        return value


class Paths(_Base):
    bibtex: Path
    database: Path = Path("data/litresearch.sqlite")
    cache_dir: Path = Path("data/cache")
    obsidian_vault: Path | None = None


class Topic(_Base):
    name: str
    weight: float = Field(1.0, ge=0)
    queries: list[str] = []
    mesh_terms: list[str] = []
    seed_dois: list[str] = []


class Sources(_Base):
    semanticscholar: bool = True
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
    # an unread recommendation may come back after this many weeks
    cooldown_weeks: int = Field(8, ge=0)
    # --- fine-tuning (see docs/recommendation-criteria.md) ---
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

    @model_validator(mode="before")
    @classmethod
    def _old_split_keys(cls, data):
        """Configs before v0.1.0 had n_detail + n_skim instead of n_recommendations."""
        if isinstance(data, dict) and ("n_detail" in data or "n_skim" in data):
            data = dict(data)
            old = (data.pop("n_detail", None) or 0) + (data.pop("n_skim", None) or 0)
            data.setdefault("n_recommendations", min(max(old, 1), 5))
        return data


class Queue(_Base):
    # deliberately small: finish or drop papers before adding new ones
    max_size: int = Field(5, ge=1, le=20)


class Game(_Base):
    # weekly quest: papers read at least "read" depth / at least "skimmed" depth
    weekly_detail_goal: int = Field(1, ge=0)
    weekly_skim_goal: int = Field(2, ge=0)
    # XP lost when a paper picked from the recommendations leaves the queue unread
    drop_penalty: int = Field(15, ge=0)


class Backup(_Base):
    # put this in a cloud-synced folder (Nextcloud, Dropbox, Syncthing, rclone mount ...)
    dir: Path = Path("data/backups")
    # number of snapshots (and progress exports) to keep
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
    host: str = "127.0.0.1"
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
    game: Game = Game()
    backup: Backup = Backup()
    llm: Llm = Llm()
    schedule: Schedule = Schedule()
    web: Web = Web()


class ConfigError(ValueError):
    """A missing or invalid config file, with a message for humans."""


def load_config(path: Path) -> Config:
    try:
        with open(path, encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
    except FileNotFoundError as error:
        raise ConfigError(
            f"No config file at {path}. Copy config/config.example.yaml to config/config.yaml."
        ) from error
    except yaml.YAMLError as error:
        raise ConfigError(f"{path} is not valid YAML: {error}") from error
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must contain settings (key: value), not {type(raw).__name__}")
    try:
        return Config.model_validate(raw)
    except ValueError as error:
        raise ConfigError(f"{path} has invalid settings:\n{error}") from error
