"""Backups, portable progress export/import and restore.

Recommended setup: keep the live database (``paths.database``) on local disk
and point ``backup.dir`` to a cloud-synced folder. Syncing the live SQLite
file itself is unsafe: a sync client may copy it in the middle of a write, and
two machines using it create conflict copies.

Two complementary formats are written into ``backup.dir``:

- ``litresearch_YYYYMMDD_HHMMSS.sqlite``: a complete, consistent snapshot
  made with SQLite's online backup API (safe while the web UI is running).
  Restore it with ``litresearch restore``.
- ``litresearch_progress_YYYYMMDD_HHMMSS.json``: only *your* data (reading
  statuses, notes, ratings, history, queue, XP, badges), readable and
  mergeable into any other installation with ``litresearch import-progress``.
  Papers are matched by DOI, PMID, citation key or title, because internal
  ids differ between machines.
"""

import json
import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlmodel import Session, func, select

from litresearch import __version__
from litresearch.config import Config
from litresearch.db import (
    Achievement,
    Paper,
    QueueItem,
    Reading,
    ReadStatus,
    StatusEvent,
    XpEvent,
    now,
)

FORMAT = "litresearch-progress"
FORMAT_VERSION = 1
SNAPSHOT_PREFIX = "litresearch_"
PROGRESS_PREFIX = "litresearch_progress_"


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
    for files in (snapshots, exports):
        for old in files[: max(len(files) - keep, 0)]:
            old.unlink()
            removed.append(old)
    return removed


@dataclass
class BackupResult:
    snapshot: Path
    progress: Path
    removed: list[Path]


def run_backup(cfg: Config, session: Session, moment: datetime | None = None) -> BackupResult:
    moment = moment or now()
    directory = cfg.backup.dir
    snap = snapshot(cfg.paths.database, directory / f"{SNAPSHOT_PREFIX}{stamp(moment)}.sqlite")
    progress = directory / f"{PROGRESS_PREFIX}{stamp(moment)}.json"
    atomic_write_text(progress, json.dumps(export_progress(session, moment), indent=1))
    return BackupResult(snap, progress, rotate(directory, cfg.backup.keep))


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
    os.replace(tmp, cfg.paths.database)
    return safety


# --- portable progress export ---------------------------------------------------------


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def export_progress(session: Session, moment: datetime | None = None) -> dict:
    """All personal data, independent of internal paper ids."""
    readings = session.exec(select(Reading)).all()
    events = session.exec(select(StatusEvent).order_by(StatusEvent.at, StatusEvent.id)).all()
    queue = session.exec(select(QueueItem).order_by(QueueItem.position)).all()
    xp = session.exec(select(XpEvent).order_by(XpEvent.at, XpEvent.id)).all()
    achievements = session.exec(select(Achievement)).all()

    ids = (
        {r.paper_id for r in readings}
        | {e.paper_id for e in events}
        | {q.paper_id for q in queue}
        | {e.paper_id for e in xp if e.paper_id is not None}
    )
    papers = {}
    for paper_id in sorted(ids):
        paper = session.get(Paper, paper_id)
        if paper is None:
            continue
        papers[f"p{paper_id}"] = {
            "doi": paper.doi,
            "pmid": paper.pmid,
            "bibtex_key": paper.bibtex_key,
            "title": paper.title,
            "authors": paper.authors,
            "year": paper.year,
            "venue": paper.venue,
            "url": paper.url,
        }

    def ref(paper_id):
        return f"p{paper_id}" if paper_id is not None and f"p{paper_id}" in papers else None

    return {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "app_version": __version__,
        "exported_at": _iso(moment or now()),
        "papers": papers,
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
        "achievements": [
            {"code": a.code, "unlocked_at": _iso(a.unlocked_at)} for a in achievements
        ],
    }


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
        ]
        if self.queue_skipped:
            out.append(f"Not queued (queue full):   {', '.join(self.queue_skipped)}")
        return out


def _dt(text: str | None) -> datetime | None:
    return datetime.fromisoformat(text) if text else None


def _find_paper(session: Session, info: dict) -> Paper | None:
    for column in ("doi", "pmid", "bibtex_key"):
        value = info.get(column)
        if value:
            paper = session.exec(select(Paper).where(getattr(Paper, column) == value)).first()
            if paper:
                return paper
    title = (info.get("title") or "").strip().lower()
    if title:
        return session.exec(select(Paper).where(func.lower(Paper.title) == title)).first()
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


def import_progress(session: Session, cfg: Config, data: dict) -> ImportReport:
    """Merge an export into this database. Importing the same file twice is a no-op.

    - readings: the newer ``updated_at`` wins; notes from both sides are kept;
    - history, XP and badges: added when not present yet;
    - queue: entries are appended while there is room.
    Nothing is written if the file is invalid.
    """
    if data.get("format") != FORMAT:
        raise BackupError("Not a litresearch progress export")
    if data.get("format_version", 0) > FORMAT_VERSION:
        raise BackupError("This export was made by a newer litresearch version")
    report = ImportReport()
    try:
        ids: dict[str, int] = {}
        for key, info in data.get("papers", {}).items():
            paper = _find_paper(session, info)
            if paper is None:
                paper = Paper(
                    title=info.get("title") or "(untitled)",
                    doi=info.get("doi"),
                    pmid=info.get("pmid"),
                    authors=info.get("authors") or [],
                    year=info.get("year"),
                    venue=info.get("venue"),
                    url=info.get("url"),
                )
                session.add(paper)
                session.flush()
                report.papers_created += 1
            else:
                report.papers_matched += 1
            ids[key] = paper.id

        for item in data.get("readings", []):
            paper_id = ids[item["paper"]]
            incoming_time = _dt(item.get("updated_at")) or now()
            local = session.get(Reading, paper_id)
            if local is None:
                session.add(
                    Reading(
                        paper_id=paper_id,
                        status=ReadStatus(item["status"]),
                        rating=item.get("rating"),
                        notes=item.get("notes") or "",
                        updated_at=incoming_time,
                    )
                )
                report.readings_added += 1
                continue
            merged = _merge_notes(local.notes, item.get("notes") or "")
            if merged != local.notes:
                local.notes = merged
                report.notes_merged += 1
            if incoming_time > local.updated_at:
                local.status = ReadStatus(item["status"])
                local.rating = (
                    item.get("rating") if item.get("rating") is not None else local.rating
                )
                local.updated_at = incoming_time
                report.readings_updated += 1
            session.add(local)

        for item in data.get("status_events", []):
            paper_id, at = ids[item["paper"]], _dt(item["at"])
            exists = session.exec(
                select(StatusEvent).where(
                    StatusEvent.paper_id == paper_id,
                    StatusEvent.at == at,
                    StatusEvent.status == item["status"],
                )
            ).first()
            if not exists:
                session.add(
                    StatusEvent(
                        paper_id=paper_id,
                        status=ReadStatus(item["status"]),
                        was_queue_top=item.get("was_queue_top", False),
                        at=at,
                    )
                )
                report.events_added += 1

        for item in data.get("xp_events", []):
            paper_id = ids.get(item["paper"]) if item.get("paper") else None
            at = _dt(item["at"])
            query = select(XpEvent).where(
                XpEvent.at == at,
                XpEvent.kind == item["kind"],
                XpEvent.points == item["points"],
                XpEvent.reason == item["reason"],
            )
            query = query.where(
                XpEvent.paper_id == paper_id if paper_id is not None else XpEvent.paper_id.is_(None)
            )
            if not session.exec(query).first():
                session.add(
                    XpEvent(
                        paper_id=paper_id,
                        points=item["points"],
                        kind=item["kind"],
                        reason=item["reason"],
                        at=at,
                    )
                )
                report.xp_added += 1

        for item in data.get("achievements", []):
            if session.get(Achievement, item["code"]) is None:
                session.add(Achievement(code=item["code"], unlocked_at=_dt(item["unlocked_at"])))
                report.achievements_added += 1

        queued = session.exec(select(QueueItem).order_by(QueueItem.position)).all()
        position = len(queued)
        present = {q.paper_id for q in queued}
        for item in sorted(data.get("queue", []), key=lambda q: q["position"]):
            paper_id = ids[item["paper"]]
            if paper_id in present:
                continue
            reading = session.get(Reading, paper_id)
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
        session.commit()
    except (KeyError, ValueError, TypeError) as error:
        session.rollback()
        raise BackupError(f"Invalid progress file: {error}") from error
    except BaseException:
        session.rollback()
        raise
    return report


# --- automatic backups while the web UI runs ---------------------------------------------


def backup_loop(config_path: Path, stop, log) -> None:
    """Back up now and then every ``backup.interval_hours`` until ``stop`` is set."""
    from litresearch.config import load_config
    from litresearch.db import init_db

    while not stop.is_set():
        cfg = load_config(config_path)
        if cfg.backup.interval_hours <= 0:
            return
        engine = init_db(cfg.paths.database)
        try:
            with Session(engine) as session:
                result = run_backup(cfg, session)
            log(f"backup written: {result.snapshot.name}")
        except Exception as error:  # a failed backup must never stop the server
            log(f"WARNING: backup failed: {error}")
        finally:
            engine.dispose()
        stop.wait(cfg.backup.interval_hours * 3600)


def start_backup_thread(config_path: Path, log=print):
    import threading

    stop = threading.Event()
    thread = threading.Thread(
        target=backup_loop, args=(config_path, stop, log), name="litresearch-backup", daemon=True
    )
    thread.start()
    return stop, thread
