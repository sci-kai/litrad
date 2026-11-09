"""Notes as Markdown files, one per paper, readable and editable in Obsidian.

Format ``litrad-note/1`` (documented in docs/documentation.md, "Notes
and Obsidian"): a UTF-8 file with LF line endings, YAML front matter that
litrad keeps up to date (``MANAGED_KEYS``) and the note itself as the
body. Keys you add to the front matter are kept; the body is only written
when you save a note in litrad (or log a reading with a note, which
appends to it).

The file is the source of truth. ``Reading.notes`` is a copy of the body for
search, history, XP and backups; ``NoteFile`` remembers the body both had at
the last sync. A change on one side is copied to the other; a change on both
sides is a *conflict*: nothing is overwritten until you choose a version.

Folder: ``<profile folder>/notes``, or ``<paths.obsidian_vault>/<paths.notes_subfolder>``.
File name: the paper's citation key, made safe for file names (``a-z A-Z 0-9
- _ .``), else ``doi-<doi>`` or ``paper-<id>``; Obsidian ``[[key]]`` links
resolve by file name.
"""

import hashlib
import io
import json
import os
import re
import tempfile
import unicodedata
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from xml.etree import ElementTree

import markdown
import yaml
from markdown.extensions import Extension
from markdown.inlinepatterns import InlineProcessor
from markdown.treeprocessors import Treeprocessor
from markupsafe import Markup
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import YAMLError as RuamelError
from sqlmodel import Session, select

from litrad.config import Config
from litrad.db import (
    READ_LEVELS,
    Library,
    LibraryPaper,
    NoteFile,
    Paper,
    Reading,
    ReadStatus,
    StatusEvent,
    depth,
    now,
)

FORMAT = "litrad-note/1"
ID_KEY = "litrad_id"
DEFAULT_FOLDER = "notes"
TEMPLATE_FILE = "note_template.md"
# front matter keys litrad writes, in this order
MANAGED_KEYS = (
    ID_KEY,
    "title",
    "authors",
    "year",
    "journal",
    "doi",
    "pmid",
    "citekey",
    "status",
    "rating",
    "tags",
    "updated",
    "format",
)
BUILTIN_TEMPLATE = "## Summary\n\n\n## Notes\n"
PLACEHOLDERS = ("title", "authors", "year", "journal", "doi", "pmid", "citekey", "date")
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {
    f"{p}{n}" for p in ("COM", "LPT") for n in "123456789"
}


class NotesError(ValueError):
    """A user-facing problem with notes."""


class NotesUnavailable(NotesError):
    """The notes folder cannot be used (e.g. the vault is not mounted)."""


class NoteConflict(NotesError):
    """The file changed after the editor was opened: both versions are kept."""

    def __init__(self, theirs: str, mine: str, base: str, path: Path | None):
        super().__init__("The note file changed meanwhile; nothing was overwritten")
        self.theirs, self.mine, self.base, self.path = theirs, mine, base, path


class _Changed(Exception):
    """The file changed between reading and writing it."""


# --- text ------------------------------------------------------------------------------


def normalize_text(text: str) -> str:
    """LF line endings, no byte order mark."""
    return text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")


def normalize_body(text: str | None) -> str:
    """The note as compared and stored: LF, no leading blank lines or trailing space."""
    return normalize_text(text or "").lstrip("\n").rstrip()


def body_hash(text: str | None) -> str:
    return hashlib.sha256(normalize_body(text).encode("utf-8")).hexdigest()


FRONT_MATTER = re.compile(r"\A---[ \t]*\n(.*?)^(?:---|\.\.\.)[ \t]*(?:\n|\Z)", re.S | re.M)


def split(text: str) -> tuple[str | None, str]:
    """(front matter YAML or None, raw body) of a normalized file text."""
    match = FRONT_MATTER.match(text)
    if not match:
        return None, text
    return match.group(1), text[match.end() :]


def safe_name(text: str | None) -> str | None:
    """A file name stem of a-z A-Z 0-9 - _ . only (accents folded), or None."""
    folded = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", folded)
    name = re.sub(r"-{2,}", "-", name).strip("-._")[:120].rstrip("-._")
    if not name:
        return None
    if name.upper() in WINDOWS_RESERVED:
        name += "-"
    return name


# --- the note file ------------------------------------------------------------------------


@dataclass
class Note:
    path: Path
    text: str
    front: str | None
    raw_body: str
    meta: dict | None
    error: str | None
    stat: tuple[int, int]

    @property
    def body(self) -> str:
        return normalize_body(self.raw_body)


def read_note(path: Path) -> Note:
    data = path.read_bytes()
    st = path.stat()
    text = normalize_text(data.decode("utf-8", errors="replace"))
    front, raw_body = split(text)
    meta, error = None, None
    if front is not None:
        try:
            loaded = yaml.safe_load(front)
            if loaded is None:
                meta = {}
            elif isinstance(loaded, dict):
                meta = loaded
            else:
                error = "front matter is not a list of key: value pairs"
        except yaml.YAMLError as exc:
            error = f"front matter is not valid YAML ({str(exc).splitlines()[0]})"
    return Note(path, text, front, raw_body, meta, error, (st.st_mtime_ns, st.st_size))


def _yaml() -> YAML:
    handler = YAML()
    handler.width = 4096
    handler.indent(mapping=2, sequence=4, offset=2)
    handler.preserve_quotes = True
    return handler


def update_front_matter(
    front: str | None, values: dict, at: datetime, touch: bool = False
) -> tuple[str, bool]:
    """Front matter with litrad's values; user keys, comments and order stay.

    Returns (YAML text, changed). Tags are only added, never removed.
    ``updated`` changes with any value, or with ``touch`` (the body was written).
    """
    handler = _yaml()
    data = handler.load(front) if front and front.strip() else None
    if data is None:
        data = CommentedMap()
    if not isinstance(data, dict):
        raise NotesError("front matter is not a list of key: value pairs")
    changed = False
    for key, value in values.items():
        if key == "tags":
            current = data.get("tags")
            if current is None:
                current = []
            elif isinstance(current, str):
                current = [current]
            missing = [t for t in value if t not in current]
            if missing or "tags" not in data:
                if isinstance(current, CommentedSeq):
                    current.extend(missing)
                else:
                    data["tags"] = list(current) + missing
                changed = True
        elif not (key in data and data[key] == value):
            data[key] = value
            changed = True
    if changed or touch or "updated" not in data:
        data["updated"] = at.replace(microsecond=0).isoformat()
        changed = True
    out = io.StringIO()
    handler.dump(data, out)
    return out.getvalue(), changed


def note_id(meta: dict | None):
    """The paper id in a note's front matter (``litrad_id``)."""
    return (meta or {}).get(ID_KEY)


def compose(front: str, body: str) -> str:
    text = f"---\n{front}---\n"
    return f"{text}\n{body}\n" if body else text


def _write(path: Path, text: str, expect: tuple[int, int] | None = None) -> tuple[int, int]:
    """Write via a temporary file and rename; refuse if the file changed since ``expect``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = None
    if path.exists():
        st = path.stat()
        mode = st.st_mode & 0o777
        if expect is not None and (st.st_mtime_ns, st.st_size) != expect:
            raise _Changed(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(text.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    st = path.stat()
    return st.st_mtime_ns, st.st_size


# --- folder and names -------------------------------------------------------------------


def notes_dir(cfg: Config, create: bool = True) -> Path:
    """The notes folder of this configuration (profile)."""
    vault = cfg.paths.obsidian_vault
    if vault is not None:
        vault = Path(vault).expanduser()
        if not vault.is_dir():
            raise NotesUnavailable(f"Obsidian vault not found: {vault} (paths.obsidian_vault)")
        folder = vault / cfg.paths.notes_subfolder if cfg.paths.notes_subfolder else vault
    else:
        folder = Path(cfg.paths.database).parent / DEFAULT_FOLDER
    if create:
        folder.mkdir(parents=True, exist_ok=True)
    return folder


def citation_keys(session: Session, paper: Paper) -> list[str]:
    """The paper's citation keys, the first library's first."""
    keys = [paper.bibtex_key] if paper.bibtex_key else []
    rows = session.exec(
        select(LibraryPaper.citation_key)
        .join(Library, Library.id == LibraryPaper.library_id)
        .where(LibraryPaper.paper_id == paper.id, LibraryPaper.citation_key.is_not(None))
        .order_by(Library.id)
    ).all()
    return keys + [k for k in rows if k and k not in keys]


def citekey(session: Session, paper: Paper) -> str | None:
    keys = citation_keys(session, paper)
    return keys[0] if keys else None


def stem_for(session: Session, paper: Paper) -> str:
    return (
        safe_name(citekey(session, paper))
        or (paper.doi and safe_name(f"doi-{paper.doi}"))
        or f"paper-{paper.id}"
    )


def _rel(folder: Path, path: Path) -> str:
    return path.relative_to(folder).as_posix()


def scan(folder: Path) -> list[Path]:
    """All note files below the folder (not hidden ones, not the template)."""
    if not folder.is_dir():
        return []
    found = []
    for path in sorted(folder.rglob("*.md")):
        parts = path.relative_to(folder).parts
        if any(p.startswith(".") for p in parts) or path.name == TEMPLATE_FILE:
            continue
        if path.is_file():
            found.append(path)
    return found


# --- front matter values -----------------------------------------------------------------


def deepest_status(session: Session, paper_id: int, reading: Reading | None) -> str | None:
    levels = session.exec(select(StatusEvent.status).where(StatusEvent.paper_id == paper_id)).all()
    deepest = max((depth(s) for s in levels), default=0)
    if reading is not None:
        deepest = max(deepest, depth(reading.status))
    if deepest:
        return READ_LEVELS[deepest - 1].value
    if reading is not None and reading.status == ReadStatus.dismissed:
        return ReadStatus.dismissed.value
    return None


def tag_name(label: str) -> str:
    """Obsidian tags cannot contain spaces."""
    return re.sub(r"\s+", "-", label.strip())


def managed_values(session: Session, paper: Paper, reading: Reading | None) -> dict:
    """What litrad writes into the front matter (``updated`` is added on writing)."""
    return {
        ID_KEY: paper.id,
        "title": paper.title,
        "authors": list(paper.authors or []),
        "year": paper.year,
        "journal": paper.venue,
        "doi": paper.doi,
        "pmid": paper.pmid,
        "citekey": citekey(session, paper),
        "status": deepest_status(session, paper.id, reading),
        "rating": reading.rating if reading else None,
        "tags": [t for t in (tag_name(label) for label in paper.tags or []) if t],
        "format": FORMAT,
    }


def values_hash(values: dict) -> str:
    """Hash of the managed values, as stored in ``NoteFile.meta``."""
    return hashlib.sha256(json.dumps(values, sort_keys=True, default=str).encode()).hexdigest()


def new_front_matter(values: dict, at: datetime) -> str:
    data = CommentedMap()
    for key in MANAGED_KEYS:
        data[key] = at.replace(microsecond=0).isoformat() if key == "updated" else values[key]
    out = io.StringIO()
    _yaml().dump(data, out)
    return out.getvalue()


# --- finding a paper's file ---------------------------------------------------------------


def _consistent(paper: Paper, meta: dict) -> bool:
    from litrad.ingest.bibtex import normalize_doi

    doi = normalize_doi(str(meta.get("doi") or ""))
    if doi and paper.doi and doi != paper.doi:
        return False
    pmid = str(meta.get("pmid") or "").strip()
    return not (pmid and paper.pmid and pmid != paper.pmid)


def _belongs(session: Session, paper: Paper, note: Note, trusted: bool = False) -> bool:
    """Is this file the note of this paper? ``trusted``: no identifying keys suffice."""
    meta = note.meta or {}
    lid = note_id(meta)
    if lid is not None:
        try:
            return int(lid) == paper.id and _consistent(paper, meta)
        except (TypeError, ValueError):
            return False
    from litrad.ingest.bibtex import normalize_doi

    doi = normalize_doi(str(meta.get("doi") or ""))
    if doi and paper.doi:
        return doi == paper.doi
    key = meta.get("citekey")
    if key:
        return str(key) in citation_keys(session, paper)
    return trusted or note.path.stem == stem_for(session, paper)


def free_path(session: Session, folder: Path, paper: Paper) -> Path:
    """Where a new note file for the paper goes (a suffix avoids collisions)."""
    base = stem_for(session, paper)
    taken = {
        p
        for p, pid in session.exec(select(NoteFile.path, NoteFile.paper_id)).all()
        if pid != paper.id
    }
    number = 1
    while True:
        name = f"{base}.md" if number == 1 else f"{base}-{number}.md"
        path = folder / name
        if name not in taken:
            if not path.exists():
                return path
            try:
                if _belongs(session, paper, read_note(path)):
                    return path
            except OSError:
                pass
        number += 1


def _locate(session, folder, paper, row, hint) -> Path | None:
    if row is not None:
        path = folder / row.path
        if path.is_file():
            if _belongs(session, paper, read_note(path), trusted=True):
                return path
    if hint is not None and hint.is_file():
        return hint
    expected = free_path(session, folder, paper)
    if expected.is_file():
        return expected
    if row is not None:  # renamed or moved: look for its id
        for path in scan(folder):
            note = read_note(path)
            if note_id(note.meta) == paper.id and _consistent(paper, note.meta):
                return path
    return None


# --- sync -----------------------------------------------------------------------------


@dataclass
class NoteState:
    """A paper's note after a refresh, for the paper page."""

    available: bool = True
    problem: str | None = None
    # not blocking, e.g. invalid front matter (the body is still used)
    warning: str | None = None
    folder: Path | None = None
    path: Path | None = None
    exists: bool = False
    body: str = ""
    file_body: str | None = None
    base: str = ""
    conflict: bool = False
    waiting: bool = False
    actions: list[str] = field(default_factory=list)


def award_notes(session: Session, cfg: Config, paper: Paper, at: datetime | None = None):
    """The notes bonus (✍️ reflection) when the notes are long enough; never twice."""
    from litrad.game import award_reading
    from litrad.profile import ensure_profile, profile_rules

    at = at or now()
    reading = session.get(Reading, paper.id)
    levels = session.exec(select(StatusEvent.status).where(StatusEvent.paper_id == paper.id)).all()
    profile, started = ensure_profile(session, cfg, at)
    award = award_reading(
        session,
        profile_rules(profile),
        paper,
        max((depth(s) for s in levels), default=0),
        reading.status,
        False,
        False,
        False,
        at,
    )
    award.profile_started = started
    return award


def _store(row: NoteFile | None, paper_id: int, folder: Path, path: Path, **values) -> NoteFile:
    row = row or NoteFile(paper_id=paper_id, path=_rel(folder, path))
    row.path = _rel(folder, path)
    for key, value in values.items():
        setattr(row, key, value)
    row.synced_at = now()
    return row


def refresh_paper(
    session: Session,
    cfg: Config,
    paper: Paper,
    *,
    metadata: bool = True,
    award: bool = True,
    dry_run: bool = False,
    commit: bool = True,
    hint: Path | None = None,
    at: datetime | None = None,
) -> NoteState:
    """Bring file and ``Reading.notes`` of one paper in line (see the module doc)."""
    at = at or now()
    reading = session.get(Reading, paper.id)
    cache = normalize_body(reading.notes) if reading else ""
    cache_hash = body_hash(cache)
    state = NoteState(body=cache, base=cache_hash)
    try:
        folder = notes_dir(cfg, create=not dry_run)
    except (NotesUnavailable, OSError) as exc:
        state.available, state.problem = False, str(exc)
        return state
    state.folder = folder
    row = session.get(NoteFile, paper.id)
    values = managed_values(session, paper, reading)
    vhash = values_hash(values)

    # unchanged on both sides: no need to read the file
    if row is not None and not row.conflict and cache_hash == row.sha256:
        path = folder / row.path
        try:
            st = path.stat()
        except OSError:
            st = None
        if st and (st.st_mtime_ns, st.st_size) == (row.mtime_ns, row.size):
            if not metadata or row.meta == vhash:
                state.path, state.exists, state.file_body = path, True, cache
                return state

    try:
        path = _locate(session, folder, paper, row, hint)
        if path is None:
            state.path = free_path(session, folder, paper)
            if row is None and not cache:
                return state
            if reading is None or not cache:
                if row is not None and not dry_run:
                    session.delete(row)  # bookkeeping only: the file and its text are gone
                return state
            state.actions.append("created" if row is None else "recreated")
            if not dry_run:
                stat = _write(state.path, compose(new_front_matter(values, at), cache))
                session.add(
                    _store(
                        row,
                        paper.id,
                        folder,
                        state.path,
                        sha256=cache_hash,
                        mtime_ns=stat[0],
                        size=stat[1],
                        meta=vhash,
                        conflict=False,
                    )
                )
            state.exists, state.file_body = not dry_run, cache
            return state

        note = read_note(path)
        state.path, state.exists = path, True
        file_hash = body_hash(note.body)
        body = None  # new body to write into the file
        imported = False
        if row is not None and row.path != _rel(folder, path):
            state.actions.append("relocated")
        if row is None:
            if reading is None:
                state.waiting, state.file_body, state.base = True, note.body, file_hash
                return state
            if not cache:
                imported = True
            elif file_hash == cache_hash:
                state.actions.append("linked")
            else:
                state.conflict = True
        elif row.conflict:
            state.conflict = file_hash != cache_hash
            if not state.conflict:
                state.actions.append("resolved")
        else:
            file_changed, cache_changed = file_hash != row.sha256, cache_hash != row.sha256
            if file_changed and not cache_changed:
                imported = True
            elif cache_changed and not file_changed:
                body = cache
            elif file_changed and cache_changed:
                state.conflict = file_hash != cache_hash
        if imported:
            state.actions.append("imported")
            cache, cache_hash = note.body, file_hash
        if state.conflict:
            state.actions.append("conflict")
        if body is not None:
            state.actions.append("exported")

        text = note.text
        meta_done = False
        rel = _rel(folder, path)
        if note.error:
            state.warning = f"{rel}: {note.error}; front matter not updated"
            if body is not None:  # the front matter stays as you wrote it
                text = f"---\n{note.front}---\n" + (f"\n{body}\n" if body else "")
        elif body is not None or (metadata and (row is None or row.meta != vhash)):
            try:
                front, meta_changed = update_front_matter(
                    note.front, values, at, touch=body is not None
                )
                meta_done = True
            except (NotesError, RuamelError) as exc:
                state.warning = f"{rel}: {exc}; front matter not updated"
                front, meta_changed = None, False
            if front is not None and (meta_changed or body is not None):
                if body is not None:
                    text = compose(front, body)
                else:
                    raw = note.raw_body if note.front is not None else "\n" + note.raw_body
                    text = f"---\n{front}---\n" + raw
                    state.actions.append("metadata")
        stat = note.stat
        if text != note.text and not dry_run:
            stat = _write(path, text, expect=note.stat)
        state.file_body = note.body if body is None else body
        state.body = cache
        state.base = body_hash(state.file_body)
        if not dry_run:
            if imported:
                reading.notes = cache
                reading.updated_at = at
                session.add(reading)
            session.add(
                _store(
                    row,
                    paper.id,
                    folder,
                    path,
                    sha256=""
                    if state.conflict and row is None
                    else (row.sha256 if state.conflict else body_hash(state.file_body)),
                    mtime_ns=stat[0],
                    size=stat[1],
                    meta=vhash if meta_done else (row.meta if row else ""),
                    conflict=state.conflict,
                )
            )
            if imported and award:
                session.flush()
                award_notes(session, cfg, paper, at)
    except _Changed:
        state.problem = "The file changed while litrad was updating it; try again"
        return state
    except OSError as exc:
        state.problem = f"Cannot use the note file: {exc}"
        return state
    finally:
        if not dry_run:
            if commit:
                session.commit()
            else:
                session.flush()
    return state


@dataclass
class SyncReport:
    folder: Path | None = None
    problem: str | None = None
    counts: dict = field(default_factory=dict)
    conflicts: list[tuple[int, str, str]] = field(default_factory=list)
    orphans: list[str] = field(default_factory=list)
    waiting: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def add(self, paper: Paper, state: NoteState, folder: Path) -> None:
        for action in state.actions:
            self.counts[action] = self.counts.get(action, 0) + 1
        if state.conflict:
            rel = _rel(folder, state.path) if state.path else "?"
            self.conflicts.append((paper.id, paper.title, rel))
        self.problems += [p for p in (state.problem, state.warning) if p]

    def lines(self) -> list[str]:
        out = [f"Notes folder: {self.folder or self.problem}"]
        labels = (
            ("created", "files created"),
            ("recreated", "missing files written again"),
            ("imported", "edits imported from files"),
            ("exported", "files updated from litrad"),
            ("linked", "files linked"),
            ("relocated", "renamed or moved files found"),
            ("metadata", "front matter updated"),
            ("resolved", "conflicts resolved"),
        )
        out += [f"{label}: {self.counts[key]}" for key, label in labels if self.counts.get(key)]
        out.append(f"conflicts: {len(self.conflicts)}")
        out += [f"  #{pid} {path}  ({title[:60]})" for pid, title, path in self.conflicts]
        out.append(f"orphans (no matching paper): {len(self.orphans)}")
        out += [f"  {path}" for path in self.orphans]
        if self.waiting:
            out.append(f"waiting for a reading (log one to link them): {len(self.waiting)}")
            out += [f"  {path}" for path in self.waiting]
        if self.duplicates:
            out.append(f"second files for a paper (not used): {len(self.duplicates)}")
            out += [f"  {path}" for path in self.duplicates]
        out += [f"problem: {p}" for p in self.problems]
        return out


def _note_ids(session: Session) -> set[int]:
    rows = set(session.exec(select(NoteFile.paper_id)).all())
    return rows | set(session.exec(select(Reading.paper_id).where(Reading.notes != "")).all())


def refresh_all(
    session: Session, cfg: Config, metadata: bool = False, award: bool = True
) -> SyncReport:
    """Refresh every note litrad knows (no folder scan): migration and external edits."""
    report = SyncReport()
    try:
        report.folder = notes_dir(cfg)
    except (NotesUnavailable, OSError) as exc:
        report.problem = str(exc)
        return report
    for paper_id in sorted(_note_ids(session)):
        paper = session.get(Paper, paper_id)
        if paper is not None:
            state = refresh_paper(session, cfg, paper, metadata=metadata, award=award, commit=False)
            report.add(paper, state, report.folder)
    session.commit()
    return report


def refresh_quietly(session: Session, cfg: Config) -> SyncReport | None:
    """``refresh_all`` for callers that must go on if notes fail (backups, exports)."""
    try:
        return refresh_all(session, cfg)
    except Exception:  # a broken note never blocks a backup
        session.rollback()
        return None


class _KeyIndex:
    def __init__(self, session: Session):
        self.keys: dict[str, int] = {}
        for key, pid in session.exec(select(Paper.bibtex_key, Paper.id)).all():
            if key:
                self.keys.setdefault(key, pid)
        for key, pid in session.exec(
            select(LibraryPaper.citation_key, LibraryPaper.paper_id).order_by(
                LibraryPaper.library_id
            )
        ).all():
            if key:
                self.keys.setdefault(key, pid)
        self.safe = {}
        for key, pid in self.keys.items():
            self.safe.setdefault(safe_name(key), pid)

    def get(self, key: str) -> int | None:
        return self.keys.get(key) or self.safe.get(key) or self.safe.get(safe_name(key))


def _match(session: Session, note: Note, index: _KeyIndex) -> int | None:
    from litrad.ingest.bibtex import normalize_doi

    meta = note.meta or {}
    lid = note_id(meta)
    if lid is not None:
        try:
            paper = session.get(Paper, int(lid))
        except (TypeError, ValueError):
            paper = None
        if paper and _consistent(paper, meta):
            return paper.id
    if doi := normalize_doi(str(meta.get("doi") or "")):
        paper = session.exec(select(Paper).where(Paper.doi == doi)).first()
        if paper:
            return paper.id
    if pmid := str(meta.get("pmid") or "").strip():
        paper = session.exec(select(Paper).where(Paper.pmid == pmid)).first()
        if paper:
            return paper.id
    if meta.get("citekey") and (pid := index.get(str(meta["citekey"]))):
        return pid
    if pid := index.get(note.path.stem):
        return pid
    if match := re.fullmatch(r"paper-(\d+)", note.path.stem):
        if session.get(Paper, int(match.group(1))):
            return int(match.group(1))
    return None


def sync(session: Session, cfg: Config, dry_run: bool = False) -> SyncReport:
    """Scan the notes folder: import edits, write missing files, list conflicts and orphans.

    ``dry_run`` (``notes check``) changes nothing and reports what sync would do.
    """
    report = SyncReport()
    try:
        folder = report.folder = notes_dir(cfg, create=not dry_run)
    except (NotesUnavailable, OSError) as exc:
        report.problem = str(exc)
        return report
    rows = {r.paper_id: r for r in session.exec(select(NoteFile)).all()}
    claimed = {r.path for r in rows.values()}
    hints: dict[int, Path] = {}
    index = None
    for path in scan(folder):
        rel = _rel(folder, path)
        if rel in claimed:
            continue
        note = read_note(path)
        if note.error:
            report.problems.append(f"{rel}: {note.error}")
        index = index or _KeyIndex(session)
        paper_id = _match(session, note, index)
        if paper_id is None:
            report.orphans.append(rel)
        elif paper_id in hints or (paper_id in rows and (folder / rows[paper_id].path).is_file()):
            report.duplicates.append(rel)
        elif session.get(Reading, paper_id) is None:
            report.waiting.append(rel)
        else:
            hints[paper_id] = path
    for paper_id in sorted(_note_ids(session) | set(hints)):
        paper = session.get(Paper, paper_id)
        if paper is None:
            continue
        state = refresh_paper(
            session, cfg, paper, dry_run=dry_run, commit=False, hint=hints.get(paper_id)
        )
        report.add(paper, state, folder)
    if dry_run:
        session.rollback()
    else:
        session.commit()
    return report


def save(
    session: Session,
    cfg: Config,
    paper: Paper,
    text: str,
    base: str,
    at: datetime | None = None,
):
    """Save the note body from the editor; ``base`` = hash of the version it was opened from.

    Raises ``NoteConflict`` (nothing written) if the file changed meanwhile.
    Returns the XP award (notes bonus).
    """
    at = at or now()
    reading = session.get(Reading, paper.id)
    if reading is None:
        raise NotesError("Log a reading first, then write notes")
    state = refresh_paper(session, cfg, paper, award=False, commit=False, at=at)
    if not state.available:
        raise NotesUnavailable(state.problem)
    if state.problem:
        raise NotesError(state.problem)
    if base != state.base:
        theirs = state.file_body if state.file_body is not None else state.body
        raise NoteConflict(theirs, text, state.base, state.path)
    body = normalize_body(text)
    reading.notes = body
    reading.updated_at = at
    session.add(reading)
    row = session.get(NoteFile, paper.id)
    values = managed_values(session, paper, reading)
    try:
        if state.exists:
            note = read_note(state.path)
            if note.error:  # keep the front matter as you wrote it
                text = f"---\n{note.front}---\n" + (f"\n{body}\n" if body else "")
            else:
                text = compose(update_front_matter(note.front, values, at, touch=True)[0], body)
            stat = _write(state.path, text, expect=note.stat)
        elif body:
            stat = _write(state.path, compose(new_front_matter(values, at), body))
        else:
            stat = None
    except _Changed as exc:
        session.rollback()
        raise NotesError("The file changed while saving; open the paper again") from exc
    if stat is not None:
        session.add(
            _store(
                row,
                paper.id,
                state.folder,
                state.path,
                sha256=body_hash(body),
                mtime_ns=stat[0],
                size=stat[1],
                meta=values_hash(values),
                conflict=False,
            )
        )
    session.flush()
    award = award_notes(session, cfg, paper, at)
    session.commit()
    return award


def merge(theirs: str, mine: str) -> str:
    """A starting point for a merged note: the file's paragraphs, then new ones of yours."""
    paragraphs = [p for p in normalize_body(theirs).split("\n\n") if p.strip()]
    for paragraph in normalize_body(mine).split("\n\n"):
        if paragraph.strip() and paragraph not in paragraphs:
            paragraphs.append(paragraph)
    return "\n\n".join(paragraphs)


def path_for(session: Session, cfg: Config, paper: Paper) -> Path:
    """The paper's note file (where it would be created if it does not exist yet)."""
    folder = notes_dir(cfg, create=False)
    row = session.get(NoteFile, paper.id)
    if row is not None and (folder / row.path).is_file():
        return folder / row.path
    return free_path(session, folder, paper)


def status(session: Session, cfg: Config) -> dict:
    """Folder, number of files and conflicts (for the Settings page)."""
    try:
        folder = notes_dir(cfg, create=False)
    except NotesUnavailable as exc:
        return {"folder": None, "problem": str(exc), "files": 0, "conflicts": []}
    rows = session.exec(select(NoteFile)).all()
    conflicts = [
        (r.paper_id, (session.get(Paper, r.paper_id) or Paper(title="?")).title)
        for r in rows
        if r.conflict
    ]
    return {"folder": folder, "problem": None, "files": len(rows), "conflicts": conflicts}


# --- template ---------------------------------------------------------------------------


def template(cfg: Config, session: Session, paper: Paper, today: date | None = None) -> str:
    """Text for a new note: ``note_template.md`` in the notes folder, or a built-in one."""
    text = BUILTIN_TEMPLATE
    try:
        custom = notes_dir(cfg, create=False) / TEMPLATE_FILE
        if custom.is_file():
            text = normalize_text(custom.read_text(encoding="utf-8", errors="replace"))
    except NotesUnavailable:
        pass
    values = {
        "title": paper.title or "",
        "authors": "; ".join(paper.authors or []),
        "year": str(paper.year or ""),
        "journal": paper.venue or "",
        "doi": paper.doi or "",
        "pmid": paper.pmid or "",
        "citekey": citekey(session, paper) or "",
        "date": (today or date.today()).isoformat(),
    }
    return re.sub(r"\{(" + "|".join(PLACEHOLDERS) + r")\}", lambda m: values[m.group(1)], text)


# --- Markdown preview -------------------------------------------------------------------

ALLOWED_TAGS = {
    "p", "br", "strong", "em", "b", "i", "code", "pre", "blockquote", "ul", "ol", "li",
    "h1", "h2", "h3", "h4", "h5", "h6", "hr", "a", "table", "thead", "tbody", "tr", "th",
    "td", "span", "div", "del",
}  # fmt: skip
SAFE_HREF = re.compile(r"^(https?://|mailto:|/(?!/)|#)", re.I)
WIKILINK = r"\[\[([^\[\]|#\n]+)(#[^\[\]|\n]*)?(?:\|([^\[\]\n]+))?\]\]"


class _WikiLinks(InlineProcessor):
    def __init__(self, pattern, resolve):
        super().__init__(pattern)
        self.resolve = resolve

    def handleMatch(self, m, data):
        key = m.group(1).strip()
        label = (m.group(3) or key).strip()
        paper_id = self.resolve(key)
        if paper_id is None:
            element = ElementTree.Element("span", {"class": "wikilink missing"})
            element.set("title", "No paper with this citation key")
            element.text = f"{label} (?)"
        else:
            element = ElementTree.Element("a", {"class": "wikilink", "href": f"/papers/{paper_id}"})
            element.text = label
        return element, m.start(0), m.end(0)


class _Sanitize(Treeprocessor):
    def run(self, root):
        for parent in list(root.iter()):
            for element in list(parent):
                if element.tag == "img":  # no external requests: show a link instead
                    src, alt = element.get("src", ""), element.get("alt")
                    element.tag = "a" if SAFE_HREF.match(src) else "span"
                    element.attrib = {"href": src} if element.tag == "a" else {}
                    element.text = "🖼 " + (alt or src or "image")
                if element.tag not in ALLOWED_TAGS:
                    element.tag = "span"
                kept = {}
                for name, value in element.attrib.items():
                    if name == "href" and element.tag == "a" and SAFE_HREF.match(value.strip()):
                        kept[name] = value.strip()
                    elif name == "class" and value in ("wikilink", "wikilink missing"):
                        kept[name] = value
                    elif name == "title" or (name == "align" and element.tag in ("td", "th")):
                        kept[name] = value
                element.attrib = kept
                if element.tag == "a" and "href" in kept and kept["href"].startswith("http"):
                    element.set("rel", "noopener noreferrer")


class _NoteExtension(Extension):
    def __init__(self, resolve):
        super().__init__()
        self.resolve = resolve

    def extendMarkdown(self, md):
        md.preprocessors.deregister("html_block")
        md.inlinePatterns.deregister("html")
        md.inlinePatterns.register(_WikiLinks(WIKILINK, self.resolve), "wikilink", 175)
        md.treeprocessors.register(_Sanitize(md), "sanitize", 0)


def resolver(session: Session):
    """Citation key or note file name → paper id (None if unknown)."""
    cache: dict[str, int | None] = {}
    index: list[_KeyIndex] = []

    def resolve(key: str) -> int | None:
        if key in cache:
            return cache[key]
        found = session.exec(select(Paper.id).where(Paper.bibtex_key == key)).first()
        if found is None:
            found = session.exec(
                select(LibraryPaper.paper_id).where(LibraryPaper.citation_key == key)
            ).first()
        if found is None:
            found = session.exec(
                select(NoteFile.paper_id).where(
                    (NoteFile.path == f"{key}.md") | NoteFile.path.endswith(f"/{key}.md")
                )
            ).first()
        if found is None:
            if not index:
                index.append(_KeyIndex(session))
            found = index[0].get(key)
        cache[key] = found
        return found

    return resolve


def render_html(session: Session, text: str) -> Markup:
    """The note as HTML: Markdown without raw HTML, ``[[key]]`` as links to papers."""
    md = markdown.Markdown(
        extensions=[
            "fenced_code",
            "tables",
            "sane_lists",
            "nl2br",
            _NoteExtension(resolver(session)),
        ]
    )
    return Markup(md.convert(normalize_body(text)))  # raw HTML is disabled and sanitized


# --- backups ------------------------------------------------------------------------------


def archive(cfg: Config, destination: Path) -> Path | None:
    """Zip every note file (and the template) of this profile; None if there are none."""
    try:
        folder = notes_dir(cfg, create=False)
    except NotesUnavailable:
        return None
    files = scan(folder) + ([folder / TEMPLATE_FILE] if (folder / TEMPLATE_FILE).is_file() else [])
    if not files:
        return None
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_name(f".{destination.name}.tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as handle:
        for path in files:
            handle.write(path, _rel(folder, path))
    tmp.chmod(0o600)
    os.replace(tmp, destination)
    return destination


def restore_archive(cfg: Config, source: Path, prefix: str = "") -> int:
    """Write the files of a notes archive that are missing; never overwrites one.

    ``prefix``: only members below this folder (``notes/`` in an export archive).
    """
    folder = notes_dir(cfg)
    written = 0
    with zipfile.ZipFile(source) as handle:
        for member in handle.namelist():
            if not member.startswith(prefix):
                continue
            name = member[len(prefix) :]
            target = (folder / name).resolve()
            if not target.is_relative_to(folder.resolve()) or not name.endswith(".md"):
                continue
            if target.exists():
                continue
            _write(target, normalize_text(handle.read(member).decode("utf-8", errors="replace")))
            written += 1
    return written


def forget_sync_state(session: Session) -> None:
    """After a restore: differing file and copy become conflicts instead of one winning."""
    for row in session.exec(select(NoteFile)).all():
        row.sha256 = ""
        row.mtime_ns = 0
        session.add(row)
    session.commit()
