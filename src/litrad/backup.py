"""Backups, restore, merging data from archives, and comparing profiles.

Recommended setup: keep the live database (``paths.database``) on local disk
and point ``backup.dir`` to a cloud-synced folder. Syncing the live SQLite
file itself is unsafe: a sync client may copy it in the middle of a write, and
two machines using it create conflict copies.

Two complementary files are written into ``backup.dir`` per backup:

- ``litrad_YYYYMMDD_HHMMSS.sqlite``: a complete, consistent snapshot
  made with SQLite's online backup API (safe while the web UI is running).
  Restore it with ``litrad restore``.
- ``litrad_backup_YYYYMMDD_HHMMSS.zip``: the documented archive of
  "export everything" (``archive.py``): your data in open formats with the note
  files, loadable as a new profile or mergeable into another installation.
  Papers are matched by DOI, PMID, citation key or title, because internal
  ids differ between machines.
"""

import os
import re
import shutil
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import yaml
from sqlmodel import Session, insert, select

from litrad import __version__
from litrad.config import Config
from litrad.db import (
    Achievement,
    Category,
    ExampleChoice,
    Library,
    LibraryPaper,
    Paper,
    Profile,
    QueueItem,
    Reading,
    ReadStatus,
    Recommendation,
    RecommendationBatch,
    StatusEvent,
    XpEvent,
    now,
    readonly_uri,
)
from litrad.rules import GameRules

SNAPSHOT_PREFIX = "litrad_"
BACKUP_ARCHIVE_PREFIX = "litrad_backup_"
_STAMP = re.compile(r"(\d{8}_\d{6})")


def files_with(directory: Path, prefix: str, suffix: str) -> list[Path]:
    """Files ``<prefix>*<suffix>`` in ``directory``."""
    return list(Path(directory).glob(f"{prefix}*{suffix}"))


def stamp_order(path: Path) -> tuple[str, str]:
    """Sort key: the file's time stamp, then its name."""
    found = _STAMP.search(path.name)
    return (found.group(1) if found else "", path.name)


class BackupError(RuntimeError):
    pass


def stamp(moment: datetime) -> str:
    """File-name-safe ISO 8601 timestamp: YYYYMMDD_HHMMSS (CUBI convention)."""
    return moment.strftime("%Y%m%d_%H%M%S")


def atomic_write_text(path: Path, text: str) -> None:
    """Write via a temporary file and rename, so a crash never leaves half a file.

    The file is readable only by you (mode 0600 from ``mkstemp``): config.yaml
    holds the password hash and API keys.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# --- database snapshots -----------------------------------------------------------


def integrity_ok(path: Path) -> bool:
    if not Path(path).is_file():
        return False  # connecting would create an empty database there
    connection = sqlite3.connect(path)
    try:
        return connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    except sqlite3.DatabaseError:
        return False
    finally:
        connection.close()


REQUIRED_TABLES = {"paper", "reading", "xpevent"}


def check_database(path: Path) -> list[str]:
    """Problems that make a database file unusable for this version (empty = fine).

    Integrity, a litrad database at all, a schema version this version
    understands, and no rows pointing to papers, libraries or batches that
    do not exist (foreign keys). The file is opened read-only.
    """
    from litrad.migrations import SCHEMA_VERSION, too_new_message

    if not integrity_ok(path):
        return [f"{path} is not a valid litrad database (integrity check failed)"]
    connection = sqlite3.connect(readonly_uri(path), uri=True)
    try:
        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master")}
        if not REQUIRED_TABLES <= tables:
            return [f"{path} does not look like a litrad database"]
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            return [too_new_message(path, version)]
        problems = []
        for table, rowid, parent, _ in connection.execute("PRAGMA foreign_key_check"):
            problems.append(f"{table} row {rowid}: refers to a missing {parent}")
        if len(problems) > 20:
            problems = problems[:20] + [f"... and {len(problems) - 20} more"]
        return problems
    finally:
        connection.close()


def snapshot(database: Path, destination: Path) -> Path:
    """Consistent copy of a (possibly busy) SQLite database."""
    if not database.exists():
        raise BackupError(f"No database at {database}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_name(f".{destination.name}.tmp")
    source = sqlite3.connect(database, timeout=30)
    target = sqlite3.connect(tmp)
    try:
        source.backup(target)
    except BaseException:
        target.close()
        tmp.unlink(missing_ok=True)  # a failed backup leaves nothing behind
        raise
    finally:
        target.close()
        source.close()
    if not integrity_ok(tmp):
        tmp.unlink(missing_ok=True)
        raise BackupError("The snapshot failed the integrity check")
    tmp.chmod(0o600)  # contains your notes: readable only by you
    os.replace(tmp, destination)
    return destination


# beyond ``backup.keep``: the newest backup of each of this many earlier weeks
WEEKLY_KEEP = 8
# safety copies made before a restore, rotated separately from the snapshots
RESTORE_KEEP = 5
_SNAPSHOT_NAME = re.compile(r"litrad_(\d{8}_\d{6})\.sqlite")
_SAFETY_NAME = re.compile(r"litrad_(\d{8}_\d{6})_before_restore\.sqlite")
_ARCHIVE_NAME = re.compile(r"litrad_backup_(\d{8}_\d{6})\.zip")


def _stamped(directory: Path, pattern: re.Pattern) -> dict[str, list[Path]]:
    """Files whose whole name matches ``pattern``, by time stamp (others are not ours)."""
    found: dict[str, list[Path]] = {}
    for path in Path(directory).glob("litrad_*"):
        match = pattern.fullmatch(path.name)
        if match and path.is_file():
            try:
                datetime.strptime(match.group(1), "%Y%m%d_%H%M%S")
            except ValueError:
                continue
            found.setdefault(match.group(1), []).append(path)
    return found


def _to_delete(stamps: list[str], keep: int, weekly: int) -> list[str]:
    """The newest ``keep`` stamps stay, then the newest of each of ``weekly`` older weeks."""
    ordered = sorted(stamps, reverse=True)
    weeks = {datetime.strptime(s, "%Y%m%d_%H%M%S").isocalendar()[:2] for s in ordered[:keep]}
    extra, delete = 0, []
    for old in ordered[keep:]:
        week = datetime.strptime(old, "%Y%m%d_%H%M%S").isocalendar()[:2]
        if week not in weeks and extra < weekly:
            weeks.add(week)
            extra += 1
        else:
            delete.append(old)
    return delete


def rotate(directory: Path, keep: int) -> list[Path]:
    """Delete old snapshots and backup archives.

    Of each kind the newest ``keep`` stay, plus the newest one of each of the
    ``WEEKLY_KEEP`` weeks before them, so frequent starts never wipe out older
    backups. ``*_before_restore.sqlite`` copies are kept apart (the newest
    ``RESTORE_KEEP``). Only files named exactly like litrad's backups are
    touched: saved profiles, exports and your own files are never deleted.
    """
    removed = []
    for pattern, count, weekly in (
        (_SNAPSHOT_NAME, keep, WEEKLY_KEEP),
        (_ARCHIVE_NAME, keep, WEEKLY_KEEP),
        (_SAFETY_NAME, RESTORE_KEEP, 0),
    ):
        by_stamp = _stamped(directory, pattern)
        for old_stamp in _to_delete(list(by_stamp), count, weekly):
            for old in sorted(by_stamp[old_stamp]):
                old.unlink()
                removed.append(old)
    return sorted(removed)


@dataclass
class BackupResult:
    snapshot: Path
    archive: Path
    removed: list[Path]


def settings_text(cfg: Config, settings: dict | None = None) -> str | None:
    """The profile's settings.yaml for an archive: ``settings`` if given, else the file.

    A file that is not valid YAML is left out (the archive check would refuse it).
    """
    if settings is None:
        try:
            settings = profile_settings(cfg)
        except (yaml.YAMLError, OSError, UnicodeDecodeError):
            return None
    if not settings:
        return None
    return yaml.safe_dump(settings, sort_keys=False, allow_unicode=True)


def run_backup(
    cfg: Config, session: Session, moment: datetime | None = None, settings: dict | None = None
) -> BackupResult:
    """A snapshot of the database and an archive (export everything) in ``backup.dir``."""
    from litrad import archive, notes

    moment = moment or now()
    directory = cfg.backup.dir
    notes.refresh_quietly(session, cfg)  # edits made in note files go into the backup
    snap = snapshot(cfg.paths.database, directory / f"{SNAPSHOT_PREFIX}{stamp(moment)}.sqlite")
    target = directory / f"{BACKUP_ARCHIVE_PREFIX}{stamp(moment)}.zip"
    written = archive.export_archive(session, cfg, target, settings_text(cfg, settings), moment)
    return BackupResult(snap, written.path, rotate(directory, cfg.backup.keep))


def latest_backup(cfg: Config) -> datetime | None:
    """The time of the newest backup archive."""
    directory = cfg.backup.dir
    if not directory.exists():
        return None
    times = []
    for path in files_with(directory, BACKUP_ARCHIVE_PREFIX, ".zip"):
        found = _STAMP.fullmatch(path.stem.split("_", 2)[-1])
        if found:
            try:
                times.append(datetime.strptime(found.group(1), "%Y%m%d_%H%M%S"))
            except ValueError:
                continue
    return max(times, default=None)


def restore(cfg: Config, source: Path, moment: datetime | None = None) -> Path:
    """Replace the live database with a snapshot; the current one is saved first.

    Stop ``litrad serve`` before restoring.
    """
    if source.is_file() and zipfile.is_zipfile(source):
        raise BackupError(
            f"{source} is an archive, not a database snapshot; nothing was changed. "
            "Load it as a new profile (litrad profile load) or merge it "
            "(litrad import-all --into PROFILE)."
        )
    problems = check_database(source)
    if problems:
        raise BackupError(
            f"{source} cannot be restored; nothing was changed:\n  " + "\n  ".join(problems)
        )
    moment = moment or now()
    safety = cfg.backup.dir / f"{SNAPSHOT_PREFIX}{stamp(moment)}_before_restore.sqlite"
    if cfg.paths.database.exists():
        snapshot(cfg.paths.database, safety)
    tmp = cfg.paths.database.with_name(f".{cfg.paths.database.name}.restore.tmp")
    cfg.paths.database.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, tmp)
    # a stale journal/WAL of the old file must not be replayed into the restored one
    for suffix in ("-journal", "-wal", "-shm"):
        cfg.paths.database.with_name(cfg.paths.database.name + suffix).unlink(missing_ok=True)
    os.replace(tmp, cfg.paths.database)
    restore_notes(cfg, source)
    return safety


def restore_notes(cfg: Config, source: Path) -> None:
    """After a restore: write back missing note files from the backup archive.

    The sync state is forgotten, so a note whose file and restored copy differ
    becomes a conflict (both kept) instead of one side overwriting the other.
    """
    from litrad import notes
    from litrad.db import init_db

    name = source.name.removesuffix(".sqlite")
    # the archive of the same backup
    archive = source.with_name(f"{BACKUP_ARCHIVE_PREFIX}{name.removeprefix(SNAPSHOT_PREFIX)}.zip")
    engine = init_db(cfg.paths.database)
    try:
        with Session(engine) as session:
            notes.forget_sync_state(session)
            try:
                if archive.is_file():
                    notes.restore_archive(cfg, archive, "notes/")
            except (notes.NotesError, OSError, ValueError, zipfile.BadZipFile):
                pass  # the copies in the database stay; `notes sync` writes the files
            notes.refresh_quietly(session, cfg)
    finally:
        engine.dispose()


# --- merging data from an archive -------------------------------------------------------


def earlier_fields(event) -> dict:
    """The optional "read earlier" fields of a status event (only when set)."""
    if not getattr(event, "earlier", False):
        return {}
    return {"earlier": True, "read_on": event.read_on.isoformat() if event.read_on else None}


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


@dataclass
class ImportReport:
    papers_matched: int = 0
    papers_created: int = 0
    readings_added: int = 0
    readings_updated: int = 0
    notes_merged: int = 0
    events_added: int = 0
    xp_added: int = 0
    achievements_added: int = 0
    queue_added: int = 0
    batches_added: int = 0
    libraries_added: int = 0
    example_choices: int = 0
    profile_adopted: bool = False
    queue_skipped: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        out = [
            f"Papers matched / created:  {self.papers_matched} / {self.papers_created}",
            f"Readings added / updated:  {self.readings_added} / {self.readings_updated}",
            f"Notes merged:              {self.notes_merged}",
            f"Reading history entries:   {self.events_added}",
            f"XP transactions:           {self.xp_added}",
            f"Badges:                    {self.achievements_added}",
            f"Queue entries:             {self.queue_added}",
            f"Recommendation batches:    {self.batches_added}",
            f"Libraries created:         {self.libraries_added}",
            f"Example choices:           {self.example_choices}",
        ]
        if self.profile_adopted:
            out.append("Profile and its locked rules taken over from the file.")
        if self.queue_skipped:
            out.append(f"Not queued (queue full):   {', '.join(self.queue_skipped)}")
        return out


def _dt(text: str | None) -> datetime | None:
    return datetime.fromisoformat(text) if text else None


def _title(text: str | None) -> str:
    return " ".join((text or "").lower().split())


class _PaperIndex:
    """Finds the local paper of an imported paper: by DOI, PMID, citation
    key, then title (+ year when both have one), never one with another DOI/PMID.
    Papers created during the import are kept as objects until they have an id.

    Built once per import, so large files need no query per paper.
    """

    COLUMNS = ("doi", "pmid", "bibtex_key")

    def __init__(self, session: Session):
        self.by: dict[str, dict] = {column: {} for column in self.COLUMNS}
        self.by_title: dict[str, list] = {}
        rows = session.exec(
            select(Paper.id, Paper.doi, Paper.pmid, Paper.bibtex_key, Paper.title, Paper.year)
        ).all()
        for paper_id, doi, pmid, key, title, year in rows:
            self.add(
                paper_id,
                {"doi": doi, "pmid": pmid, "bibtex_key": key, "title": title, "year": year},
            )

    def add(self, paper_id: "int | Paper", info: dict) -> None:
        for column in self.COLUMNS:
            if info.get(column):
                self.by[column].setdefault(info[column], (paper_id, info))
        self.by_title.setdefault(_title(info.get("title")), []).append((paper_id, info))

    @staticmethod
    def _same(local: dict, info: dict) -> bool:
        return not any(
            local.get(column) and info.get(column) and local[column] != info[column]
            for column in ("doi", "pmid", "year")
        )

    def find(self, info: dict) -> "int | Paper | None":
        for column in self.COLUMNS:
            found = self.by[column].get(info.get(column)) if info.get(column) else None
            if found and self._same(found[1], {**info, "year": None}):
                return found[0]
        if _title(info.get("title")):
            for paper_id, local in self.by_title.get(_title(info.get("title")), []):
                if self._same(local, info):
                    return paper_id
        return None


def _merge_notes(local: str, incoming: str) -> str:
    """Union of note entries (separated by blank lines), local order first."""
    if not incoming or incoming == local:
        return local
    if not local:
        return incoming
    entries = [e for e in local.split("\n\n") if e.strip()]
    for entry in incoming.split("\n\n"):
        if entry.strip() and entry not in entries:
            entries.append(entry)
    return "\n\n".join(entries)


def _value(name: str, value):
    """A paper field from JSON as the model stores it."""
    if value is None:
        return None
    if name == "pub_date":
        return date.fromisoformat(value)
    if name in ("enriched_at", "created_at", "updated_at"):
        return datetime.fromisoformat(value)
    if name == "url":
        from litrad.ingest.bibtex import web_url

        return web_url(value)
    return value


# copied from a file into a new paper; in_library, folders and tags follow the libraries
NEW_PAPER_FIELDS = tuple(
    name for name in Paper.model_fields if name not in ("id", "in_library", "bibtex_key")
)


def _new_paper(key: str, info: dict, index: "_PaperIndex") -> Paper:
    values = {name: _value(name, info.get(name)) for name in NEW_PAPER_FIELDS if name in info}
    values = {name: value for name, value in values.items() if value is not None}
    values["title"] = info.get("title") or "(untitled)"
    doi = values.get("doi")
    if doi and doi in index.by["doi"]:
        raise BackupError(
            f"paper {key}: its DOI {doi} belongs to a paper here whose PMID or year differs; "
            "correct one of them first"
        )
    paper = Paper(**values)
    key_value = info.get("bibtex_key")
    if key_value and key_value not in index.by["bibtex_key"]:
        paper.bibtex_key = key_value  # unique: only when no other paper has it
    return paper


def merge(
    session: Session,
    cfg: Config,
    data: dict,
    allow_different_rules: bool = False,
    commit: bool = True,
) -> ImportReport:
    """The merge of ``import_progress`` for checked data (see there)."""
    from collections import Counter

    report = ImportReport()
    try:
        incoming_profile = data.get("profile")
        local_profile = session.exec(select(Profile).order_by(Profile.id)).first()
        if incoming_profile and local_profile:
            # recomputed from the rules, not the stored strings: only a real rule
            # difference refuses the merge (see rules.FINGERPRINT_FIELDS)
            theirs = GameRules.model_validate(incoming_profile["rules"]).fingerprint()
            ours = GameRules.model_validate(local_profile.rules).fingerprint()
            if theirs != ours:
                if not allow_different_rules:
                    raise BackupError(
                        "The file was played with different game rules "
                        f"({theirs} vs. {ours}); "
                        "merging would mix incomparable points. Load it as a profile instead."
                    )
        elif incoming_profile:
            rules = GameRules.model_validate(incoming_profile["rules"]).locked()
            session.add(
                Profile(
                    name=incoming_profile["name"],
                    created_at=_dt(incoming_profile["created_at"]) or now(),
                    rules=rules.model_dump(mode="json"),
                    fingerprint=rules.fingerprint(),
                    app_version=__version__,
                )
            )
            report.profile_adopted = True

        # rows are looked up in dictionaries built once and written with one flush
        # or bulk inserts: a query per row made large files slow
        refs: dict[str, int | Paper] = {}
        index = _PaperIndex(session)
        for key, info in data.get("papers", {}).items():
            ref = index.find(info)
            if ref is None:
                ref = _new_paper(key, info, index)
                session.add(ref)
                index.add(ref, info)
                report.papers_created += 1
            else:
                report.papers_matched += 1
            refs[key] = ref
        session.flush()  # ids of the new papers
        ids = {key: ref if isinstance(ref, int) else ref.id for key, ref in refs.items()}

        readings = {r.paper_id: r for r in session.exec(select(Reading)).all()}
        for item in data.get("readings", []):
            paper_id = ids[item["paper"]]
            incoming_time = _dt(item.get("updated_at")) or now()
            local = readings.get(paper_id)
            if local is None:
                readings[paper_id] = Reading(
                    paper_id=paper_id,
                    status=ReadStatus(item["status"]),
                    rating=item.get("rating"),
                    notes=item.get("notes") or "",
                    updated_at=incoming_time,
                )
                session.add(readings[paper_id])
                report.readings_added += 1
                continue
            merged = _merge_notes(local.notes, item.get("notes") or "")
            if merged != local.notes:
                local.notes = merged
                report.notes_merged += 1
            if incoming_time > local.updated_at:
                local.status = ReadStatus(item["status"])
                if item.get("rating") is not None:
                    local.rating = item["rating"]
                local.updated_at = incoming_time
                report.readings_updated += 1
            session.add(local)

        # identical rows can exist (two clicks within a second): count them, so that a
        # file adds what is missing and a second import adds nothing
        present = Counter(
            (e.paper_id, e.at, str(e.status)) for e in session.exec(select(StatusEvent)).all()
        )
        # per entry of the file: two entries that are one paper here count once
        incoming = Counter()
        rows = []
        for item in data.get("status_events", []):
            paper_id, at = ids[item["paper"]], _dt(item["at"])
            status = ReadStatus(item["status"])
            identity = (paper_id, at, str(status))
            incoming[(item["paper"], identity)] += 1
            if incoming[(item["paper"], identity)] > present[identity]:
                present[identity] += 1
                rows.append(
                    {
                        "paper_id": paper_id,
                        "status": status,
                        "was_queue_top": item.get("was_queue_top", False),
                        "at": at,
                        "earlier": bool(item.get("earlier", False)),
                        "read_on": date.fromisoformat(item["read_on"])
                        if item.get("read_on")
                        else None,
                    }
                )
        if rows:
            session.exec(insert(StatusEvent), params=rows)
        report.events_added = len(rows)

        present = Counter(
            (e.at, e.kind, e.points, e.reason, e.paper_id)
            for e in session.exec(select(XpEvent)).all()
        )
        incoming = Counter()
        rows = []
        for item in data.get("xp_events", []):
            paper_id = ids.get(item["paper"]) if item.get("paper") else None
            row = {
                "paper_id": paper_id,
                "points": item["points"],
                "kind": item["kind"],
                "reason": item["reason"],
                "at": _dt(item["at"]),
            }
            identity = (row["at"], row["kind"], row["points"], row["reason"], paper_id)
            incoming[(item.get("paper"), identity)] += 1
            if incoming[(item.get("paper"), identity)] > present[identity]:
                present[identity] += 1
                rows.append(row)
        if rows:
            session.exec(insert(XpEvent), params=rows)
        report.xp_added = len(rows)

        unlocked = set(session.exec(select(Achievement.code)).all())
        for item in data.get("achievements", []):
            if item["code"] not in unlocked:
                unlocked.add(item["code"])
                session.add(Achievement(code=item["code"], unlocked_at=_dt(item["unlocked_at"])))
                report.achievements_added += 1

        present = {
            (b.week_start, b.created_at) for b in session.exec(select(RecommendationBatch)).all()
        }
        added = []
        for batch in data.get("recommendations", []):
            week, created = date.fromisoformat(batch["week_start"]), _dt(batch["created_at"])
            if (week, created) in present:
                continue
            present.add((week, created))
            stored = RecommendationBatch(
                week_start=week,
                created_at=created,
                n_candidates=batch.get("n_candidates", 0),
                warnings=batch.get("warnings") or [],
                funnel=batch.get("funnel"),
                params=batch.get("params"),
            )
            session.add(stored)
            added.append((stored, batch))
        session.flush()  # ids of the new batches
        rows = [
            {
                "batch_id": stored.id,
                "paper_id": ids[item["paper"]],
                "rank": item["rank"],
                "category": Category(item.get("category", "open")),
                "score": item["score"],
                "breakdown": item.get("breakdown") or {},
                "reason": item.get("reason", ""),
            }
            for stored, batch in added
            for item in batch.get("items", [])
        ]
        if rows:
            session.exec(insert(Recommendation), params=rows)
        report.batches_added = len(added)

        report.libraries_added = _merge_libraries(session, data.get("libraries", []), ids)
        report.example_choices = _merge_example_choices(
            session, data.get("example_choices", []), ids
        )

        queued = session.exec(select(QueueItem).order_by(QueueItem.position)).all()
        position = len(queued)
        present = {q.paper_id for q in queued}
        for item in sorted(data.get("queue", []), key=lambda q: q["position"]):
            paper_id = ids[item["paper"]]
            if paper_id in present:
                continue
            reading = readings.get(paper_id)
            if reading and reading.status != ReadStatus.abstract:
                continue  # read or dismissed in the meantime
            if position >= cfg.queue.max_size:
                report.queue_skipped.append(data["papers"][item["paper"]].get("title", "")[:40])
                continue
            position += 1
            session.add(
                QueueItem(
                    paper_id=paper_id,
                    position=position,
                    source=item.get("source", "manual"),
                    added_at=_dt(item.get("added_at")) or now(),
                )
            )
            present.add(paper_id)
            report.queue_added += 1
        if commit:
            session.commit()
        else:
            session.flush()
    except (KeyError, ValueError, TypeError) as error:
        session.rollback()
        raise BackupError(f"Invalid data: {error}") from error
    except BaseException:
        session.rollback()
        raise
    return report


def _merge_example_choices(session: Session, incoming: list[dict], ids: dict[str, int]) -> int:
    """Your example choices: the newer choice per paper and list wins.

    A paper you added to both lists (on two machines) stays in the list of
    the newer addition. Returns the number of choices added or changed.
    """
    local = {(c.paper_id, c.kind): c for c in session.exec(select(ExampleChoice)).all()}
    changed = 0
    for item in incoming:
        kind, action = item["kind"], item["action"]
        if kind not in ("positive", "negative") or action not in ("include", "exclude"):
            raise ValueError(f"unknown example choice {kind}/{action}")
        paper_id, at = ids[item["paper"]], _dt(item.get("at")) or now()
        choice = local.get((paper_id, kind))
        if choice is not None and (choice.at >= at or choice.action == action):
            continue
        if choice is None:
            choice = ExampleChoice(paper_id=paper_id, kind=kind, action=action, at=at)
            local[(paper_id, kind)] = choice
        choice.action, choice.at = action, at
        session.add(choice)
        changed += 1
    session.flush()
    for (paper_id, kind), choice in list(local.items()):
        other = local.get((paper_id, "negative" if kind == "positive" else "positive"))
        both = other is not None and choice.action == other.action == "include"
        if both and choice.at < other.at:
            session.delete(choice)
            del local[(paper_id, kind)]
    session.flush()
    return changed


def _numbered(name: str, number: int) -> str:
    return name if number == 1 else f"{name} {number}"


def _merge_libraries(session: Session, incoming: list[dict], ids: dict[str, int]) -> int:
    """Libraries of a profile file: created when missing (by name), papers added."""
    from litrad import libraries

    existing = {lib.name.lower(): lib for lib in session.exec(select(Library)).all()}
    members = set(session.exec(select(LibraryPaper.library_id, LibraryPaper.paper_id)).all())
    created, touched = 0, set()
    for entry in incoming:
        wanted = entry.get("kind") or libraries.IMPORTED
        if wanted in libraries.EXAMPLE_KINDS:
            continue  # regenerated from the rules and your choices
        library, number = None, 1
        while (found := existing.get(_numbered(entry["name"], number).lower())) is not None:
            # same name but another kind (e.g. an import called "Recommended" or
            # "Positive examples") is a different library: look for its renamed copy
            # ("Recommended 2") instead
            if found.kind == wanted:
                library = found
                break
            number += 1
        if library is None and wanted == libraries.RECOMMENDED:
            library = session.exec(
                select(Library).where(Library.kind == libraries.RECOMMENDED)
            ).first()
        if library is None:
            library = Library(
                name=libraries._free_name(session, entry["name"]),
                description=entry.get("description") or "",
                kind=entry.get("kind") or libraries.IMPORTED,
                format=entry.get("format"),
                use_for_recommendations=entry.get("use_for_recommendations", True),
                created_at=_dt(entry.get("created_at")) or now(),
                updated_at=_dt(entry.get("updated_at")) or _dt(entry.get("created_at")) or now(),
            )
            session.add(library)
            session.flush()
            existing[library.name.lower()] = library
            created += 1
        for member in entry.get("papers", []):
            paper_id = ids.get(member["paper"])
            if paper_id is None or (library.id, paper_id) in members:
                continue
            members.add((library.id, paper_id))
            session.add(
                LibraryPaper(
                    library_id=library.id,
                    paper_id=paper_id,
                    citation_key=member.get("key"),
                    folders=member.get("folders") or [],
                    tags=member.get("tags") or [],
                    added_at=_dt(member.get("added_at")) or now(),
                )
            )
            touched.add(paper_id)
    session.flush()
    if touched:
        libraries.refresh_flags(session, touched)
    return created


# --- saved profiles and comparison ---------------------------------------------------------

PROFILE_PREFIX = "litrad_profile_"


def slug(text: str) -> str:
    """File-name-safe version of a profile name (a-z, 0-9 and -)."""
    cleaned = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return cleaned[:40].strip("-") or "profile"


def save_profile_archive(
    session: Session,
    cfg: Config,
    directory: Path,
    moment: datetime | None = None,
    settings: dict | None = None,
) -> Path:
    """*Save profile*: the archive (export everything) as ``litrad_profile_*.zip``."""
    from litrad import archive

    moment = moment or now()
    profile = session.exec(select(Profile).order_by(Profile.id)).first()
    name = profile.name if profile else "no-profile"
    path = directory / f"{PROFILE_PREFIX}{slug(name)}_{stamp(moment)}.zip"
    return archive.export_archive(session, cfg, path, settings_text(cfg, settings), moment).path


EXPORT_PREFIX = "litrad_export_"
SAVED_PATTERNS = tuple(
    f"{prefix}*{suffix}"
    for prefix, suffix in (
        (PROFILE_PREFIX, ".zip"),
        (BACKUP_ARCHIVE_PREFIX, ".zip"),
        (EXPORT_PREFIX, ".zip"),
    )
)


def saved_files(directory: Path) -> list[Path]:
    """Saved profiles, backup archives and exports in a folder, newest first."""
    if not directory.exists():
        return []
    files = [path for pattern in SAVED_PATTERNS for path in directory.glob(pattern)]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def memory_engine():
    """An empty in-memory litrad database (for comparisons)."""
    from sqlalchemy.pool import StaticPool
    from sqlmodel import SQLModel, create_engine

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    return engine


def summarize_session(
    session: Session,
    cfg: Config,
    moment: datetime | None = None,
    label: str = "",
) -> dict:
    """Key figures of the profile in ``session`` at ``moment`` (default: now)."""
    from litrad.game import level_for, reading_impact_factor, stats

    moment = moment or now()
    profile = session.exec(select(Profile)).first()
    rules = GameRules.model_validate(profile.rules) if profile else cfg.game
    figures = stats(session, moment.date(), rules)
    level = level_for(figures["xp"], rules)
    badges = len(session.exec(select(Achievement)).all())
    read = figures["papers_read"]
    return {
        "label": label,
        "name": profile.name if profile else "(no profile)",
        "started": profile.created_at if profile else None,
        "exported_at": moment,
        "fingerprint": rules.fingerprint() if profile else None,
        "xp": figures["xp"],
        "level": level,
        "papers_read": read,
        "thorough": figures["thorough"],
        "best_streak": figures["best_streak"],
        "quests": figures["quests"],
        "badges": badges,
        "discipline": figures["cheeses"] / read if read else 0.0,
        "depth": figures["deep_reads"] / read if read else 0.0,
        "notes_chars": figures["notes_chars"],
        "rif": reading_impact_factor(session, moment.date()),
        "weeks": len(
            {e.at.date().isocalendar()[:2] for e in session.exec(select(StatusEvent)).all()}
        ),
    }


# --- automatic backups while the web UI runs ---------------------------------------------


def backup_targets(base: Config) -> list[tuple[str, Config]]:
    """Every profile."""
    from litrad import profiles

    def target(slug: str) -> Config:
        try:
            return profiles.profile_config(base, slug)
        except ValueError:  # an invalid settings.yaml: still back up the database
            data = base.model_dump(mode="json")
            data["paths"]["database"] = str(profiles.profile_db(profiles.folder(base, slug)))
            data["backup"]["dir"] = str(Path(base.backup.dir) / slug)
            return Config.model_validate(data)

    return [(info.slug, target(info.slug)) for info in profiles.list_profiles(base)]


def backup_due(cfg: Config, hours: float, moment: datetime | None = None) -> bool:
    """Whether the newest backup of this profile is at least ``hours`` old (or missing)."""
    latest = latest_backup(cfg)
    moment = moment or now()
    return latest is None or latest > moment or (moment - latest).total_seconds() >= hours * 3600


def backup_all(base: Config, log=print, due_hours: float | None = None) -> int:
    """Back up every profile; returns the number of failures.

    ``due_hours``: only profiles whose newest backup is at least this old.
    """
    from litrad.db import init_db

    failures = 0
    for label, cfg in backup_targets(base):
        if due_hours is not None and not backup_due(cfg, due_hours):
            continue
        engine = init_db(cfg.paths.database)
        try:
            with Session(engine) as session:
                settings = None
                try:
                    settings = profile_settings(cfg)
                except yaml.YAMLError as error:
                    log(f"WARNING: {label}: settings.yaml is not valid YAML, not included")
                    log(f"  {error}")
                result = run_backup(cfg, session, settings=settings)
            log(f"backup written: {label}: {result.snapshot.name}")
        except Exception as error:  # a failed backup must never stop the server
            failures += 1
            log(f"WARNING: backup of {label} failed: {error}")
        finally:
            engine.dispose()
    return failures


def profile_settings(cfg: Config) -> dict:
    """The settings.yaml of the profile that ``cfg`` belongs to (empty if none)."""
    path = cfg.paths.database.parent / "settings.yaml"
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


# how often the backup thread checks whether a profile's backup is due (seconds)
CHECK_SECONDS = 3600


def backup_loop(config_path: Path, stop, log) -> None:
    """Back up each profile whose newest backup is ``backup.interval_hours`` old.

    Checked at start and then hourly: restarting the server does not add a
    backup, and no profile goes much longer than the interval without one.
    """
    from litrad.config import ConfigError, load_config

    while not stop.is_set():
        try:
            base = load_config(config_path)
        except ConfigError as error:  # e.g. while config.yaml is being edited
            log(f"WARNING: no backup, the configuration cannot be read: {error}")
            stop.wait(CHECK_SECONDS)
            continue
        hours = base.backup.interval_hours
        if hours <= 0:
            return
        backup_all(base, log, due_hours=hours)
        stop.wait(min(hours * 3600, CHECK_SECONDS))


def start_backup_thread(config_path: Path, log=print):
    import threading

    stop = threading.Event()
    thread = threading.Thread(
        target=backup_loop, args=(config_path, stop, log), name="litrad-backup", daemon=True
    )
    thread.start()
    return stop, thread
