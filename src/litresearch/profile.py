"""The game profile: name, start date and the locked rule set.

Each profile folder (see ``profiles.py``) has its own database with exactly
one profile row. Once it exists, all points are computed with its stored
rules; the ``game`` section of ``config.yaml`` only provides the defaults for
the *next* profile. Playing by other rules means creating another profile.
"""

from datetime import datetime

from sqlmodel import Session, select

from litresearch import __version__
from litresearch.config import Config
from litresearch.db import Profile, now
from litresearch.rules import GameRules

DEFAULT_NAME = "My reading journey"


class ProfileError(ValueError):
    pass


def get_profile(session: Session) -> Profile | None:
    return session.exec(select(Profile).order_by(Profile.id)).first()


def profile_rules(profile: Profile) -> GameRules:
    return GameRules.model_validate(profile.rules)


def active_rules(session: Session, cfg: Config) -> GameRules:
    """The rules that count: the locked profile rules, else the configured defaults."""
    profile = get_profile(session)
    return profile_rules(profile) if profile else cfg.game


def start_profile(
    session: Session,
    cfg: Config,
    name: str = DEFAULT_NAME,
    rules: GameRules | None = None,
    at: datetime | None = None,
    commit: bool = True,
) -> Profile:
    """Start the game. The rules are copied and can no longer be changed."""
    if get_profile(session) is not None:
        raise ProfileError("A profile is already running; its rules are locked.")
    name = (name or "").strip() or DEFAULT_NAME
    if len(name) > 80:
        raise ProfileError("Use a profile name of at most 80 characters")
    rules = rules or cfg.game
    profile = Profile(
        name=name,
        created_at=at or now(),
        rules=rules.model_dump(mode="json"),
        fingerprint=rules.fingerprint(),
        app_version=__version__,
    )
    session.add(profile)
    if commit:
        session.commit()
        session.refresh(profile)
    else:
        session.flush()
    return profile


def ensure_profile(session: Session, cfg: Config, at: datetime | None = None):
    """The active profile; started with the configured defaults if there is none.

    Returns ``(profile, started_now)``.
    """
    profile = get_profile(session)
    if profile is not None:
        return profile, False
    return start_profile(session, cfg, at=at, commit=False), True
