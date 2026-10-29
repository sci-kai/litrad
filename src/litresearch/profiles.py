"""Several profiles side by side, each in its own folder.

    data/profiles/<slug>/
        litresearch.sqlite   progress, queue, recommendations, library metadata
        library.bib          the Paperpile export of this profile
        settings.yaml        topics and recommendation settings of this profile

Everything personal is therefore linked to exactly one profile. The global
``config.yaml`` provides machine settings (APIs, web server, backups) and the
defaults that ``settings.yaml`` can override (see ``OVERLAY_KEYS``). Backups go
to ``backup.dir/<slug>``.

A database from before profiles existed (``paths.database``) is "legacy data";
``adopt_legacy`` turns it into the first profile without losing anything.
"""

import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml
from sqlmodel import Session, func, select

from litresearch.backup import atomic_write_text, load_profile, snapshot
from litresearch.backup import slug as slug_of
from litresearch.config import Config
from litresearch.db import Paper, Profile, Reading, XpEvent, init_db, now
from litresearch.game import Level, level_for
from litresearch.profile import profile_rules, start_profile
from litresearch.rules import GameRules
from litresearch.yamlfile import deep_merge, update_yaml

PROFILE_DB = "litresearch.sqlite"
PROFILE_BIB = "library.bib"
PROFILE_SETTINGS = "settings.yaml"
CURRENT_FILE = ".current"
LEGACY_MARKER = ".legacy_adopted"
# settings a profile may override; everything else comes from config.yaml
OVERLAY_KEYS = ("topics", "excluded_terms", "selection", "sources", "queue", "library", "paths")
# profile bookkeeping stored in the same file, not part of the configuration
META_KEYS = ("onboarding",)


class ProfilesError(ValueError):
    pass


def profiles_dir(base: Config) -> Path:
    return base.paths.profiles_dir


def folder(base: Config, slug: str) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,40}", slug or ""):
        raise ProfilesError(f"Invalid profile id '{slug}'")
    return profiles_dir(base) / slug


def exists(base: Config, slug: str) -> bool:
    try:
        return (folder(base, slug) / PROFILE_DB).exists()
    except ProfilesError:
        return False


def unique_slug(base: Config, name: str) -> str:
    stem = slug_of(name)
    slug, number = stem, 2
    while (profiles_dir(base) / slug).exists():
        slug = f"{stem[:36]}-{number}"
        number += 1
    return slug


# --- settings overlay --------------------------------------------------------------


def _settings_file(base: Config, slug: str) -> dict:
    path = folder(base, slug) / PROFILE_SETTINGS
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as error:
        raise ProfilesError(f"{path} is not valid YAML: {error}") from error
    if not isinstance(data, dict):
        raise ProfilesError(f"{path} must contain settings (key: value)")
    return data


def overlay(base: Config, slug: str) -> dict:
    """The configuration settings this profile overrides."""
    return {k: v for k, v in _settings_file(base, slug).items() if k in OVERLAY_KEYS}


def meta(base: Config, slug: str) -> dict:
    """Profile bookkeeping, e.g. ``{"onboarding": {"topics_done": True}}``."""
    return {k: v for k, v in _settings_file(base, slug).items() if k in META_KEYS}


def profile_config(base: Config, slug: str, extra: dict | None = None) -> Config:
    """The effective configuration of one profile."""
    home = folder(base, slug)
    settings = deep_merge(overlay(base, slug), extra or {})
    data = deep_merge(base.model_dump(mode="json"), settings)
    data["paths"]["database"] = str(home / PROFILE_DB)
    data["paths"]["bibtex"] = str((settings.get("paths") or {}).get("bibtex") or home / PROFILE_BIB)
    data["backup"]["dir"] = str(Path(base.backup.dir) / slug)
    return Config.model_validate(data)


def write_overlay(base: Config, slug: str, updates: dict) -> Config:
    """Change this profile's settings; validated before writing.

    Comments and key order survive, topic fields that ``updates`` does not
    mention (mesh terms, seed DOIs) are kept, and the previous file is kept as
    ``settings.yaml.bak``.
    """
    unknown = set(updates) - set(OVERLAY_KEYS) - set(META_KEYS)
    if unknown:
        raise ProfilesError(f"Not a profile setting: {', '.join(sorted(unknown))}")
    config_part = {k: v for k, v in updates.items() if k in OVERLAY_KEYS}
    cfg = profile_config(base, slug, config_part)  # raises on invalid values
    update_yaml(folder(base, slug) / PROFILE_SETTINGS, updates)
    return cfg


# --- registry --------------------------------------------------------------------------


@dataclass
class ProfileInfo:
    slug: str
    name: str
    created_at: datetime | None
    fingerprint: str
    xp: int
    level: Level
    papers_read: int
    library: int
    last_activity: datetime | None


def describe(base: Config, slug: str) -> ProfileInfo:
    """Name, level, XP, papers read and activity of a profile (for the welcome screen)."""
    # only the database: a profile with an invalid settings.yaml is still listed
    engine = init_db(folder(base, slug) / PROFILE_DB)
    try:
        with Session(engine) as session:
            profile = session.exec(select(Profile)).first()
            rules = profile_rules(profile) if profile else base.game
            xp = session.exec(select(func.coalesce(func.sum(XpEvent.points), 0))).one()
            last = session.exec(select(func.max(XpEvent.at))).one()
            read = session.exec(
                select(func.count()).select_from(Reading).where(Reading.status != "dismissed")
            ).one()
            library = session.exec(
                select(func.count()).select_from(Paper).where(Paper.in_library)
            ).one()
            return ProfileInfo(
                slug=slug,
                name=profile.name if profile else slug,
                created_at=profile.created_at if profile else None,
                fingerprint=profile.fingerprint if profile else "",
                xp=xp,
                level=level_for(xp, rules),
                papers_read=read,
                library=library,
                last_activity=last,
            )
    finally:
        engine.dispose()


def list_profiles(base: Config) -> list[ProfileInfo]:
    home = profiles_dir(base)
    if not home.exists():
        return []
    slugs = sorted(p.parent.name for p in home.glob(f"*/{PROFILE_DB}"))
    infos = [describe(base, slug) for slug in slugs]
    return sorted(
        infos, key=lambda i: i.last_activity or i.created_at or datetime.min, reverse=True
    )


def _new_folder(base: Config, name: str) -> tuple[str, Path]:
    name = (name or "").strip()
    if not name:
        raise ProfilesError("Give your profile a name")
    if len(name) > 80:
        raise ProfilesError("Use a profile name of at most 80 characters")
    if any(info.name.lower() == name.lower() for info in list_profiles(base)):
        raise ProfilesError(f"A profile called '{name}' already exists")
    slug = unique_slug(base, name)
    home = profiles_dir(base) / slug
    home.mkdir(parents=True)
    return slug, home


def create_profile(
    base: Config,
    name: str,
    rules: GameRules | None = None,
    settings: dict | None = None,
    at: datetime | None = None,
) -> str:
    """A new, empty profile whose rules are locked from the start."""
    slug, home = _new_folder(base, name)
    try:
        # a new profile starts without topics: they are asked for on first use
        write_overlay(base, slug, {"topics": [], "excluded_terms": [], **(settings or {})})
        engine = init_db(home / PROFILE_DB)
        try:
            with Session(engine) as session:
                start_profile(
                    session, profile_config(base, slug), name.strip(), rules or base.game, at=at
                )
        finally:
            engine.dispose()
    except BaseException:
        shutil.rmtree(home, ignore_errors=True)
        raise
    return slug


def legacy_available(base: Config) -> dict | None:
    """Data from before profiles existed that can become a profile."""
    database = base.paths.database
    if not database.exists() or (profiles_dir(base) / LEGACY_MARKER).exists():
        return None
    engine = init_db(database)
    try:
        with Session(engine) as session:
            library = session.exec(
                select(func.count()).select_from(Paper).where(Paper.in_library)
            ).one()
            readings = session.exec(select(func.count()).select_from(Reading)).one()
            profile = session.exec(select(Profile)).first()
    finally:
        engine.dispose()
    if not (library or readings or profile):
        return None
    return {
        "library": library,
        "readings": readings,
        "name": profile.name if profile else None,
        "path": database,
    }


def adopt_legacy(base: Config, name: str, rules: GameRules | None = None) -> str:
    """Turn the legacy database into a profile (a copy; the original stays)."""
    info = legacy_available(base)
    if info is None:
        raise ProfilesError("There is no existing data to take over")
    slug, home = _new_folder(base, name or info["name"] or "")
    try:
        snapshot(base.paths.database, home / PROFILE_DB)
        settings = {
            "topics": [t.model_dump(mode="json") for t in base.topics],
            "excluded_terms": list(base.excluded_terms),
        }
        if base.paths.bibtex.exists():
            # keep using the same export (e.g. kept up to date by rclone)
            settings["paths"] = {"bibtex": str(base.paths.bibtex.resolve())}
        write_overlay(base, slug, settings)
        engine = init_db(home / PROFILE_DB)
        try:
            with Session(engine) as session:
                if session.exec(select(Profile)).first() is None:
                    start_profile(
                        session, profile_config(base, slug), name.strip(), rules or base.game
                    )
        finally:
            engine.dispose()
        (profiles_dir(base) / LEGACY_MARKER).write_text(
            f"{now().isoformat()} {base.paths.database} -> {slug}\n"
        )
    except BaseException:
        shutil.rmtree(home, ignore_errors=True)
        raise
    return slug


def import_profile_file(base: Config, data: dict, name: str | None = None) -> str:
    """A new profile from a saved profile file (rules, progress, settings)."""
    saved = data.get("profile") or {}
    slug, home = _new_folder(base, name or saved.get("name") or "Imported profile")
    try:
        settings = {k: v for k, v in (data.get("settings") or {}).items() if k in OVERLAY_KEYS}
        settings.pop("paths", None)  # paths of another machine do not apply here
        if settings:
            write_overlay(base, slug, settings)
        cfg = profile_config(base, slug)
        engine = init_db(cfg.paths.database)
        try:
            with Session(engine) as session:
                load_profile(session, cfg, data)
                profile = session.exec(select(Profile)).first()
                if profile is None:
                    start_profile(session, cfg, name or "Imported profile")
                elif name:
                    profile.name = name
                    session.add(profile)
                    session.commit()
        finally:
            engine.dispose()
    except BaseException:
        shutil.rmtree(home, ignore_errors=True)
        raise
    return slug


# --- current profile for the command line -----------------------------------------------


def get_current(base: Config) -> str | None:
    marker = profiles_dir(base) / CURRENT_FILE
    if marker.exists():
        slug = marker.read_text().strip()
        if exists(base, slug):
            return slug
    slugs = [info.slug for info in list_profiles(base)]
    return slugs[0] if len(slugs) == 1 else None


def set_current(base: Config, slug: str) -> None:
    if not exists(base, slug):
        raise ProfilesError(f"No profile '{slug}'")
    atomic_write_text(profiles_dir(base) / CURRENT_FILE, slug + "\n")


def find(base: Config, name_or_slug: str) -> str:
    """Accept a slug or a profile name."""
    if exists(base, name_or_slug):
        return name_or_slug
    for info in list_profiles(base):
        if info.name.lower() == name_or_slug.lower():
            return info.slug
    raise ProfilesError(f"No profile '{name_or_slug}'")
