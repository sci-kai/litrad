"""Versioned SQLite schema: numbered, tested upgrade steps.

Every profile database carries its schema version in SQLite's
``PRAGMA user_version`` (0 = written before versions existed, 0.1 to 0.5).
``init_db`` calls ``migrate`` before it opens a database:

- a new, empty file gets the current tables and ``SCHEMA_VERSION``;
- an older database is first copied (``backup.snapshot``) to
  ``<database folder>/upgrade-backups/`` (the newest ``UPGRADE_BACKUPS_KEEP``
  copies are kept), then upgraded by the steps it has
  not had yet, one transaction per step; the version is raised with the step,
  so an interrupted upgrade resumes at the step that failed;
- a database with a *newer* version than this app knows is refused
  (``SchemaTooNew``): an older litresearch never changes a newer database.

Rules for new steps (the 1.0 compatibility promise): append a ``Step`` with
the next number and raise ``SCHEMA_VERSION``; never change or remove a
released step. Additive model changes (new tables, nullable columns or
columns with a default, new indexes) need a step that only calls
``sync_schema``; a test fails when the models change without a new version
(``tests/data/compat/schema.json``).
"""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import JSON, create_engine, event, inspect, text
from sqlalchemy.engine import Connection
from sqlmodel import SQLModel

SCHEMA_VERSION = 2
BACKUP_FOLDER = "upgrade-backups"
# copies from before an upgrade that are kept per profile (the newest; older ones
# are deleted after an upgrade succeeded, so a failed series keeps all of them)
UPGRADE_BACKUPS_KEEP = 3


class SchemaTooNew(RuntimeError):
    """The database was written by a newer litresearch."""


class MigrationError(RuntimeError):
    """A step failed; the database stays at the last completed version."""


@dataclass(frozen=True)
class Step:
    version: int
    description: str
    apply: Callable[[Connection], None]


def sync_schema(connection: Connection) -> list[str]:
    """Create missing tables, columns and indexes (additive changes only).

    Only nullable columns or columns with a scalar default can be added this
    way. Returns the ``table.column`` names that were added.
    """
    from litresearch.db import empty_dict, empty_list

    SQLModel.metadata.create_all(connection)
    inspector = inspect(connection)
    models = {m.__tablename__: m for m in SQLModel.__subclasses__() if hasattr(m, "__table__")}
    added = []
    for table in SQLModel.metadata.sorted_tables:
        existing = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing:
                continue
            kind = column.type.compile(connection.dialect)
            default = ""
            scalar = getattr(column.default, "arg", None)
            if isinstance(scalar, bool | int | float):
                default = f" DEFAULT {int(scalar) if isinstance(scalar, bool) else scalar}"
            elif isinstance(scalar, str):
                default = " DEFAULT '" + scalar.replace("'", "''") + "'"
            elif isinstance(column.type, JSON):
                # existing rows would stay NULL, which the models do not expect
                factory = models.get(table.name).model_fields[column.name].default_factory
                if factory is empty_list:
                    default = " DEFAULT '[]'"
                elif factory is empty_dict:
                    default = " DEFAULT '{}'"
            connection.execute(
                text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {kind}{default}')
            )
            added.append(f"{table.name}.{column.name}")
    for table in SQLModel.metadata.sorted_tables:
        for index in table.indexes:
            index.create(connection, checkfirst=True)
    return added


def _baseline(connection: Connection) -> None:
    sync_schema(connection)


# Never change or remove a step once released; append new ones.
STEPS: tuple[Step, ...] = (
    Step(
        1,
        "Versioned schema: databases of 0.1.0-alpha.1 to 0.5 get every table, column and "
        "index of 0.6 (additive only, nothing is removed)",
        _baseline,
    ),
    Step(
        2,
        "Read earlier: status events get 'earlier' (read before using litresearch) and "
        "'read_on' (when, if known); existing events are not earlier",
        sync_schema,
    ),
)


@dataclass
class MigrationResult:
    path: Path
    from_version: int
    to_version: int
    created: bool = False
    snapshot: Path | None = None
    applied: list[str] = field(default_factory=list)


def read_version(path: Path) -> tuple[int, bool]:
    """(schema version, has tables) of a database file, without changing it."""
    connection = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True, timeout=30)
    try:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        tables = connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()[0]
    finally:
        connection.close()
    return version, tables > 0


def too_new_message(path: Path, version: int) -> str:
    return (
        f"{path} was written by a newer litresearch (database schema version {version}; "
        f"this version understands up to {SCHEMA_VERSION}). It was not opened or changed. "
        "Update litresearch, or restore a backup made with this version."
    )


def check_version(path: Path) -> int:
    """The schema version of an existing database; raises ``SchemaTooNew``."""
    version, _ = read_version(path)
    if version > SCHEMA_VERSION:
        raise SchemaTooNew(too_new_message(path, version))
    return version


def _engine(path: Path):
    """An engine whose transactions also cover DDL (``BEGIN IMMEDIATE``)."""
    from litresearch.db import BUSY_TIMEOUT

    engine = create_engine(
        f"sqlite:///{path}", connect_args={"timeout": BUSY_TIMEOUT, "isolation_level": None}
    )

    @event.listens_for(engine, "begin")
    def _begin(connection):
        # one writer at a time: a second process waits and then sees the new version
        connection.exec_driver_sql("BEGIN IMMEDIATE")

    return engine


def _version(connection: Connection) -> int:
    return connection.exec_driver_sql("PRAGMA user_version").scalar()


def _set_version(connection: Connection, version: int) -> None:
    connection.exec_driver_sql(f"PRAGMA user_version = {int(version)}")


def snapshot_path(path: Path, old: int, moment: datetime | None = None) -> Path:
    from litresearch.backup import stamp
    from litresearch.db import now

    name = f"litresearch_{stamp(moment or now())}_before-schema-{old}-to-{SCHEMA_VERSION}.sqlite"
    return Path(path).parent / BACKUP_FOLDER / name


def migrate(path: Path, steps: tuple[Step, ...] | None = None) -> MigrationResult:
    """Bring the database at ``path`` to ``SCHEMA_VERSION`` (see the module doc)."""
    from litresearch.backup import snapshot

    steps = STEPS if steps is None else steps
    path = Path(path)
    if path.exists():
        version, has_tables = read_version(path)
    else:
        version, has_tables = 0, False
    if version > SCHEMA_VERSION:
        raise SchemaTooNew(too_new_message(path, version))
    result = MigrationResult(path, version, version)
    if version == SCHEMA_VERSION:
        return result
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = _engine(path)
    try:
        if not has_tables:
            with engine.begin() as connection:
                if _version(connection) == 0 and not inspect(connection).get_table_names():
                    SQLModel.metadata.create_all(connection)
                    _set_version(connection, SCHEMA_VERSION)
                    result.created, result.to_version = True, SCHEMA_VERSION
                    return result
        # an existing database: keep a copy of it as it was before the upgrade
        result.snapshot = snapshot(path, snapshot_path(path, version))
        for step in sorted(steps, key=lambda s: s.version):
            with engine.begin() as connection:
                current = _version(connection)
                if current > SCHEMA_VERSION:
                    raise SchemaTooNew(too_new_message(path, current))
                if step.version <= current:
                    continue  # done earlier, or by another process meanwhile
                if step.version != current + 1:
                    raise MigrationError(f"Migration step {current + 1} is missing")
                try:
                    step.apply(connection)
                except Exception as error:
                    raise MigrationError(
                        f"Upgrading {path} to schema version {step.version} failed: {error}. "
                        f"The database stays at version {current}; a copy from before the "
                        f"upgrade is {result.snapshot}"
                    ) from error
                _set_version(connection, step.version)
                result.applied.append(f"{step.version}: {step.description}")
                result.to_version = step.version
    finally:
        engine.dispose()
    rotate_upgrade_backups(path.parent / BACKUP_FOLDER)
    return result


def rotate_upgrade_backups(folder: Path, keep: int = UPGRADE_BACKUPS_KEEP) -> list[Path]:
    """Delete all but the newest ``keep`` copies made before upgrades."""
    copies = sorted(Path(folder).glob("litresearch_*_before-schema-*.sqlite"))
    removed = copies[: max(len(copies) - keep, 0)]
    for old in removed:
        old.unlink(missing_ok=True)
    return removed
