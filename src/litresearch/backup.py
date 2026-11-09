"""Backups, portable progress export/import and restore.

Recommended setup: keep the live database (``paths.database``) on local disk
and point ``backup.dir`` to a cloud-synced folder. Syncing the live SQLite
file itself is unsafe: a sync client may copy it in the middle of a write, and
two machines using it create conflict copies.

Two complementary formats are written into ``backup.dir``:

- ``litresearch_YYYYMMDD_HHMMSS.sqlite``: a complete, consistent snapshot
  made with SQLite's online backup API (safe while the web UI is running).
  Restore it with ``litresearch restore``.
- ``litresearch_notes_YYYYMMDD_HHMMSS.zip``: the note files (Markdown), if
  there are any; ``restore`` writes back the ones that are missing.
- ``litresearch_progress_YYYYMMDD_HHMMSS.json``: only *your* data (reading
  statuses, notes, ratings, history, queue, XP, badges), readable and
  mergeable into any other installation with ``litresearch import-progress``.
  Papers are matched by DOI, PMID, citation key or title, because internal
  ids differ between machines.
"""

import hashlib
import hmac
import json
import os
import re
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import yaml
from sqlmodel import Session, delete, insert, select

from litresearch import __version__
from litresearch.config import Config
from litresearch.db import (
    Achievement,
    Category,
    ExampleChoice,
    Library,
    LibraryPaper,
    NoteFile,
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
)
from litresearch.rules import GameRules

FORMAT = "litresearch-progress"
FORMAT_VERSION = 2  # 2: profile with locked rules, recommendations, checksum
SNAPSHOT_PREFIX = "litresearch_"
PROGRESS_PREFIX = "litresearch_progress_"
NOTES_PREFIX = "litresearch_notes_"


class BackupError(RuntimeError):
    pass


def stamp(moment: datetime) -> str:
    """File-name-safe ISO 8601 timestamp: YYYYMMDD_HHMMSS (CUBI convention)."""
    return moment.strftime("%Y%m%d_%H%M%S")


def atomic_write_text(path: Path, text: str) -> None:
    """Write via a temporary file and rename, so a crash never leaves half a file."""
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


def rotate(directory: Path, keep: int) -> list[Path]:
    """Delete the oldest snapshots and progress exports beyond ``keep`` each."""
    removed = []
    snapshots = sorted(
        p
        for p in directory.glob(f"{SNAPSHOT_PREFIX}*.sqlite")
        if not p.name.startswith(PROGRESS_PREFIX)
    )
    exports = sorted(directory.glob(f"{PROGRESS_PREFIX}*.json"))
    archives = sorted(directory.glob(f"{NOTES_PREFIX}*.zip"))
    for files in (snapshots, exports, archives):
        for old in files[: max(len(files) - keep, 0)]:
            old.unlink()
            removed.append(old)
    return removed


@dataclass
class BackupResult:
    snapshot: Path
    progress: Path
    removed: list[Path]
    notes: Path | None = None


def run_backup(
    cfg: Config, session: Session, moment: datetime | None = None, settings: dict | None = None
) -> BackupResult:
    from litresearch import notes

    moment = moment or now()
    directory = cfg.backup.dir
    notes.refresh_quietly(session, cfg)  # edits made in note files go into the backup
    snap = snapshot(cfg.paths.database, directory / f"{SNAPSHOT_PREFIX}{stamp(moment)}.sqlite")
    progress = directory / f"{PROGRESS_PREFIX}{stamp(moment)}.json"
    data = export_progress(session, moment, settings)
    atomic_write_text(progress, json.dumps(data, indent=1))
    archive = notes.archive(cfg, directory / f"{NOTES_PREFIX}{stamp(moment)}.zip")
    return BackupResult(snap, progress, rotate(directory, cfg.backup.keep), archive)


def latest_backup(cfg: Config) -> datetime | None:
    directory = cfg.backup.dir
    if not directory.exists():
        return None
    files = sorted(directory.glob(f"{PROGRESS_PREFIX}*.json"))
    if not files:
        return None
    try:
        return datetime.strptime(files[-1].stem.removeprefix(PROGRESS_PREFIX), "%Y%m%d_%H%M%S")
    except ValueError:
        return None


def restore(cfg: Config, source: Path, moment: datetime | None = None) -> Path:
    """Replace the live database with a snapshot; the current one is saved first.

    Stop ``litresearch serve`` before restoring.
    """
    if not integrity_ok(source):
        raise BackupError(f"{source} is not a valid litresearch database")
    connection = sqlite3.connect(source)
    try:
        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master")}
    finally:
        connection.close()
    if not {"paper", "reading", "xpevent"} <= tables:
        raise BackupError(f"{source} does not look like a litresearch database")
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
    """After a restore: write back missing note files from the snapshot's notes archive.

    The sync state is forgotten, so a note whose file and restored copy differ
    becomes a conflict (both kept) instead of one side overwriting the other.
    """
    from litresearch import notes
    from litresearch.db import init_db

    archive = source.with_name(
        NOTES_PREFIX + source.name.removeprefix(SNAPSHOT_PREFIX).removesuffix(".sqlite") + ".zip"
    )
    engine = init_db(cfg.paths.database)
    try:
        with Session(engine) as session:
            notes.forget_sync_state(session)
            try:
                if archive.is_file():
                    notes.restore_archive(cfg, archive)
            except (notes.NotesError, OSError, ValueError):
                pass  # the copies in the database stay; `notes sync` writes the files
            notes.refresh_quietly(session, cfg)
    finally:
        engine.dispose()


# --- portable progress export ---------------------------------------------------------


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def _canonical(data: dict) -> str:
    return json.dumps({k: v for k, v in data.items() if k != "checksum"}, sort_keys=True)


def checksum(data: dict) -> str:
    """SHA-256 over the file content; detects edits made outside the tool."""
    return hashlib.sha256(_canonical(data).encode()).hexdigest()


def verify_checksum(data: dict) -> bool | None:
    """True/False, or None for files without a checksum (format version 1)."""
    if "checksum" not in data:
        return None
    return hmac.compare_digest(str(data["checksum"]), checksum(data))


PAPER_FIELDS = (
    "doi",
    "pmid",
    "bibtex_key",
    "title",
    "authors",
    "year",
    "venue",
    "url",
    "citation_percentile",
    "topics",
    "folders",
    "in_library",
)


def export_progress(
    session: Session, moment: datetime | None = None, settings: dict | None = None
) -> dict:
    """The whole profile: rules, progress and recommendations, independent of ids."""
    readings = session.exec(select(Reading)).all()
    events = session.exec(select(StatusEvent).order_by(StatusEvent.at, StatusEvent.id)).all()
    queue = session.exec(select(QueueItem).order_by(QueueItem.position)).all()
    xp = session.exec(select(XpEvent).order_by(XpEvent.at, XpEvent.id)).all()
    achievements = session.exec(select(Achievement)).all()
    batches = session.exec(select(RecommendationBatch).order_by(RecommendationBatch.id)).all()
    recommendations = session.exec(
        select(Recommendation).order_by(Recommendation.batch_id, Recommendation.rank)
    ).all()
    profile = session.exec(select(Profile).order_by(Profile.id)).first()
    # the example lists are regenerated from rules and your choices, not saved
    library_rows = [
        lib
        for lib in session.exec(select(Library).order_by(Library.id)).all()
        if lib.kind not in ("positive", "negative")
    ]
    kept = {lib.id for lib in library_rows}
    memberships = [m for m in session.exec(select(LibraryPaper)).all() if m.library_id in kept]
    example_choices = session.exec(
        select(ExampleChoice).order_by(ExampleChoice.at, ExampleChoice.paper_id)
    ).all()

    ids = (
        {m.paper_id for m in memberships}
        | {r.paper_id for r in readings}
        | {e.paper_id for e in events}
        | {q.paper_id for q in queue}
        | {e.paper_id for e in xp if e.paper_id is not None}
        | {r.paper_id for r in recommendations}
        | {c.paper_id for c in example_choices}
    )
    columns = [getattr(Paper, name) for name in PAPER_FIELDS]
    rows = session.exec(select(Paper.id, Paper.pub_date, *columns).order_by(Paper.id)).all()
    papers = {}
    for paper_id, pub_date, *values in rows:  # one query, not one per paper
        if paper_id in ids:
            info = dict(zip(PAPER_FIELDS, values, strict=True))
            info["pub_date"] = _iso(pub_date)
            papers[f"p{paper_id}"] = info

    def ref(paper_id):
        return f"p{paper_id}" if paper_id is not None and f"p{paper_id}" in papers else None

    items_by_batch: dict[int, list] = {}
    for rec in recommendations:
        if ref(rec.paper_id):
            items_by_batch.setdefault(rec.batch_id, []).append(
                {
                    "paper": ref(rec.paper_id),
                    "rank": rec.rank,
                    "category": rec.category.value,
                    "score": rec.score,
                    "breakdown": rec.breakdown,
                    "reason": rec.reason,
                }
            )

    data = {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "app_version": __version__,
        "exported_at": _iso(moment or now()),
        "profile": {
            "name": profile.name,
            "created_at": _iso(profile.created_at),
            "rules": profile.rules,
            "fingerprint": profile.fingerprint,
        }
        if profile
        else None,
        "papers": papers,
        # libraries and who is in them; their files are machine-specific and not included
        "libraries": [
            {
                "name": lib.name,
                "description": lib.description,
                "kind": lib.kind,
                "format": lib.format,
                "use_for_recommendations": lib.use_for_recommendations,
                "created_at": _iso(lib.created_at),
                "papers": [
                    {
                        "paper": ref(m.paper_id),
                        "key": m.citation_key,
                        "folders": m.folders,
                        "tags": m.tags,
                    }
                    for m in memberships
                    if m.library_id == lib.id and ref(m.paper_id)
                ],
            }
            for lib in library_rows
        ],
        "readings": [
            {
                "paper": ref(r.paper_id),
                "status": r.status.value,
                "rating": r.rating,
                "notes": r.notes,
                "updated_at": _iso(r.updated_at),
            }
            for r in readings
            if ref(r.paper_id)
        ],
        "status_events": [
            {
                "paper": ref(e.paper_id),
                "status": e.status.value,
                "was_queue_top": e.was_queue_top,
                "at": _iso(e.at),
            }
            for e in events
            if ref(e.paper_id)
        ],
        "queue": [
            {
                "paper": ref(q.paper_id),
                "position": q.position,
                "source": q.source,
                "added_at": _iso(q.added_at),
            }
            for q in queue
            if ref(q.paper_id)
        ],
        "xp_events": [
            {
                "paper": ref(e.paper_id),
                "points": e.points,
                "kind": e.kind,
                "reason": e.reason,
                "at": _iso(e.at),
            }
            for e in xp
        ],
        "settings": settings or {},
        # papers you added to or removed from the example lists for Semantic Scholar
        "example_choices": [
            {"paper": ref(c.paper_id), "kind": c.kind, "action": c.action, "at": _iso(c.at)}
            for c in example_choices
            if ref(c.paper_id)
        ],
        "achievements": [
            {"code": a.code, "unlocked_at": _iso(a.unlocked_at)} for a in achievements
        ],
        "recommendations": [
            {
                "week_start": _iso(b.week_start),
                "created_at": _iso(b.created_at),
                "n_candidates": b.n_candidates,
                "warnings": b.warnings,
                "funnel": b.funnel,
                "params": b.params,
                "items": items_by_batch.get(b.id, []),
            }
            for b in batches
        ],
    }
    data["checksum"] = checksum(data)
    return data


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
    """Finds the local paper of a paper in a progress file: by DOI, PMID, citation
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


class ConsistencyError(BackupError):
    """The file failed the schema or consistency check; ``report`` lists why."""

    def __init__(self, report, what: str = "Invalid progress file"):
        self.report = report
        lines = [str(p) for p in report.errors[:20]]
        more = len(report.errors) - len(lines)
        super().__init__(
            f"{what} (nothing was changed):\n  "
            + "\n  ".join(lines)
            + (f"\n  ... and {more} more" if more > 0 else "")
        )


def check_file(data: dict, allow_modified: bool = False):
    """Format, version, checksum, JSON Schema and consistency of a progress file.

    Returns the check report (notes); raises ``BackupError`` before anything
    is changed. Files of format version 1 (no checksum) are still accepted.
    """
    from litresearch.dataformat import check_progress

    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise BackupError("Not a litresearch progress export")
    version = data.get("format_version", 0)
    if not isinstance(version, int) or version > FORMAT_VERSION:
        raise BackupError("This export was made by a newer litresearch version")
    if verify_checksum(data) is False and not allow_modified:
        raise BackupError(
            "The file was modified outside litresearch (checksum mismatch); refusing to load it"
        )
    report = check_progress(data)
    if report.errors:
        raise ConsistencyError(report)
    return report


def import_progress(
    session: Session,
    cfg: Config,
    data: dict,
    allow_different_rules: bool = False,
    allow_modified: bool = False,
    commit: bool = True,
) -> ImportReport:
    """Merge an export into this database. Importing the same file twice is a no-op.

    - readings: the newer ``updated_at`` wins; notes from both sides are kept;
    - history, XP, badges and recommendation batches: added when not present;
    - queue: entries are appended while there is room;
    - profile: adopted if this database has none. XP earned under different
      locked rules is not mixed unless ``allow_different_rules``.
    Nothing is written if the file is invalid.
    """
    check_file(data, allow_modified)
    return merge(session, cfg, data, allow_different_rules, commit)


def _value(name: str, value):
    """A paper field from JSON as the model stores it."""
    if value is None:
        return None
    if name == "pub_date":
        return date.fromisoformat(value)
    if name in ("enriched_at", "created_at", "updated_at"):
        return datetime.fromisoformat(value)
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
            if incoming_profile["fingerprint"] != local_profile.fingerprint:
                if not allow_different_rules:
                    raise BackupError(
                        "The file was played with different game rules "
                        f"({incoming_profile['fingerprint']} vs. {local_profile.fingerprint}); "
                        "merging would mix incomparable points. Load it as a profile instead."
                    )
        elif incoming_profile:
            rules = GameRules.model_validate(incoming_profile["rules"])
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
        raise BackupError(f"Invalid progress file: {error}") from error
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
    from litresearch import libraries

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


PERSONAL_TABLES = (
    NoteFile,
    ExampleChoice,
    Recommendation,
    RecommendationBatch,
    QueueItem,
    StatusEvent,
    XpEvent,
    Achievement,
    Reading,
    Profile,
)


def reset_progress(session: Session, commit: bool = True) -> None:
    """Delete the profile and all personal data (the library stays)."""
    for model in PERSONAL_TABLES:
        session.exec(delete(model))
    if commit:
        session.commit()


def load_profile(session: Session, cfg: Config, data: dict, allow_modified: bool = False):
    """Replace the current profile and progress with a saved one (one transaction)."""
    check_file(data, allow_modified)
    try:
        reset_progress(session, commit=False)
        report = merge(session, cfg, data, commit=False)
        session.commit()
    except BaseException:
        session.rollback()
        raise
    return report


# --- saved profiles and comparison ---------------------------------------------------------

PROFILE_PREFIX = "litresearch_profile_"


def slug(text: str) -> str:
    """File-name-safe version of a profile name (a-z, 0-9 and -)."""
    cleaned = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return cleaned[:40].strip("-") or "profile"


def save_profile_file(
    session: Session,
    directory: Path,
    moment: datetime | None = None,
    settings: dict | None = None,
) -> Path:
    moment = moment or now()
    data = export_progress(session, moment, settings)
    name = (data["profile"] or {}).get("name", "no-profile")
    path = directory / f"{PROFILE_PREFIX}{slug(name)}_{stamp(moment)}.json"
    atomic_write_text(path, json.dumps(data, indent=1))
    return path


def saved_files(directory: Path) -> list[Path]:
    """Saved profiles and progress exports in a folder, newest first."""
    if not directory.exists():
        return []
    files = list(directory.glob(f"{PROFILE_PREFIX}*.json")) + list(
        directory.glob(f"{PROGRESS_PREFIX}*.json")
    )
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def memory_engine():
    """An empty in-memory litresearch database (for comparisons)."""
    from sqlalchemy.pool import StaticPool
    from sqlmodel import SQLModel, create_engine

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    return engine


def summarize(data: dict, cfg: Config, label: str = "") -> dict:
    """Key figures of a saved profile, computed in a throw-away database."""
    check_file(data, allow_modified=True)
    engine = memory_engine()
    try:
        with Session(engine) as session:
            merge(session, cfg, data)
            moment = _dt(data.get("exported_at")) or now()
            return summarize_session(session, cfg, moment, label, verify_checksum(data))
    finally:
        engine.dispose()


def summarize_session(
    session: Session,
    cfg: Config,
    moment: datetime | None = None,
    label: str = "",
    checksum_ok: bool | None = None,
) -> dict:
    """Key figures of the profile in ``session`` at ``moment`` (default: now)."""
    from litresearch.game import level_for, reading_impact_factor, stats

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
        "fingerprint": profile.fingerprint if profile else None,
        "checksum_ok": checksum_ok,
        "xp": figures["xp"],
        "level": level,
        "papers_read": read,
        "thorough": figures["thorough"],
        "best_streak": figures["best_streak"],
        "quests": figures["quests"],
        "badges": badges,
        "discipline": figures["frogs"] / read if read else 0.0,
        "depth": figures["deep_reads"] / read if read else 0.0,
        "notes_chars": figures["notes_chars"],
        "rif": reading_impact_factor(session, moment.date()),
        "weeks": len(
            {e.at.date().isocalendar()[:2] for e in session.exec(select(StatusEvent)).all()}
        ),
    }


# --- automatic backups while the web UI runs ---------------------------------------------


def backup_targets(base: Config) -> list[tuple[str, Config]]:
    """Every profile, plus the data from before profiles existed (until adopted)."""
    from litresearch import profiles

    def target(slug: str) -> Config:
        try:
            return profiles.profile_config(base, slug)
        except ValueError:  # an invalid settings.yaml: still back up the database
            data = base.model_dump(mode="json")
            data["paths"]["database"] = str(profiles.folder(base, slug) / profiles.PROFILE_DB)
            data["backup"]["dir"] = str(Path(base.backup.dir) / slug)
            return Config.model_validate(data)

    targets = [(info.slug, target(info.slug)) for info in profiles.list_profiles(base)]
    if (
        base.paths.database.exists()
        and not (base.paths.profiles_dir / profiles.LEGACY_MARKER).exists()
    ):
        targets.append(("(no profile)", base))
    return targets


def backup_all(base: Config, log=print) -> int:
    """Back up every profile; returns the number of failures."""
    from litresearch.db import init_db

    failures = 0
    for label, cfg in backup_targets(base):
        engine = init_db(cfg.paths.database)
        try:
            with Session(engine) as session:
                settings = None
                if label != "(no profile)":
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


def backup_loop(config_path: Path, stop, log) -> None:
    """Back up all profiles now and then every ``backup.interval_hours``."""
    from litresearch.config import ConfigError, load_config

    while not stop.is_set():
        try:
            base = load_config(config_path)
        except ConfigError as error:  # e.g. while config.yaml is being edited
            log(f"WARNING: no backup, the configuration cannot be read: {error}")
            stop.wait(3600)
            continue
        if base.backup.interval_hours <= 0:
            return
        backup_all(base, log)
        stop.wait(base.backup.interval_hours * 3600)


def start_backup_thread(config_path: Path, log=print):
    import threading

    stop = threading.Event()
    thread = threading.Thread(
        target=backup_loop, args=(config_path, stop, log), name="litresearch-backup", daemon=True
    )
    thread.start()
    return stop, thread
