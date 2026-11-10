""" "Export everything": one zip archive per profile, and importing it again.

The archive (format ``litrad-export``, version ``dataformat.DATA_FORMAT_VERSION``)
holds plain, documented files; ``README.md`` inside describes each one:

    manifest.json              format, versions, counts, SHA-256 of every file
    README.md                  what each file and field means
    papers.json                every paper referenced anywhere, key "p<id>"
    libraries.json             libraries, flags, members (by paper key), keys, folders, tags
    libraries/<name>.bib/.json each library as BibTeX and CSL-JSON (views, not imported)
    readings.json              status, rating and notes per paper
    status_events.json         every status change (the reading log)
    xp_ledger.json             every XP transaction with running balance; total
    badges.json                unlocked badges
    queue.json                 the reading queue
    recommendations.json       batches with funnel, parameters and per-paper breakdown
    example_choices.json       your choices for the Semantic Scholar example lists
    profile.json               the profile with its locked game rules
    settings.yaml              the profile's settings (its settings.yaml)
    reading_log.tsv, xp_ledger.tsv   the same tables for spreadsheets (not imported)
    notes/*.md                 the note files as they are
    schemas/*.schema.json      JSON Schema (draft 2020-12) of every JSON file

Importing checks everything first (``check``): checksums, JSON Schema,
consistency between the files. Then it writes in one database transaction,
all or nothing; ``partial`` imports only the consistent records. Into an
existing profile, papers are matched by identifiers (DOI, PMID,
citation key, title + year) and a second import of the same archive changes
nothing.

Not included, because litrad recomputes them or they belong to one
machine: embeddings, the example lists themselves (only your choices), the
import log of libraries, the sync state of note files, file paths.
"""

import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml
from sqlmodel import Session, select

from litrad import __version__
from litrad.backup import (
    BackupError,
    ImportReport,
    _iso,
    earlier_fields,
    merge,
)
from litrad.config import Config
from litrad.dataformat import (
    ARCHIVE_FORMAT,
    DATA_FORMAT_VERSION,
    CheckReport,
    Naming,
    Problem,
    check_data,
    compact,
    drop,
    load_schema,
    unknown_fields,
    validate_schema,
)
from litrad.db import (
    Achievement,
    ExampleChoice,
    Library,
    LibraryPaper,
    NoteFile,
    Paper,
    Profile,
    QueueItem,
    Reading,
    Recommendation,
    RecommendationBatch,
    StatusEvent,
    XpEvent,
    now,
)

MANIFEST = "manifest.json"
README = "README.md"
SETTINGS = "settings.yaml"
NOTES = "notes/"
SCHEMAS = "schemas/"
# the JSON files: schema name, main list, section of the merged data
JSON_FILES = {
    "papers.json": ("papers", "papers", "papers"),
    "libraries.json": ("libraries", "libraries", "libraries"),
    "readings.json": ("readings", "readings", "readings"),
    "status_events.json": ("status_events", "status_events", "status_events"),
    "xp_ledger.json": ("xp_ledger", "events", "xp_events"),
    "badges.json": ("badges", "badges", "achievements"),
    "queue.json": ("queue", "queue", "queue"),
    "recommendations.json": ("recommendations", "batches", "recommendations"),
    "example_choices.json": ("example_choices", "example_choices", "example_choices"),
    "profile.json": ("profile", None, "profile"),
}
NAMING = {section: name for name, (_, _, section) in JSON_FILES.items()} | {
    "xp_total": "xp_ledger.json.total"
}
LIBRARY_KINDS_EXPORTED = ("imported", "recommended")
LIST_FIELDS = ("authors", "topics", "folders", "tags")
# limits against damaged or hostile archives
MAX_MEMBER = 512 * 2**20
MAX_TOTAL = 2 * 2**30
MAX_FILES = 200_000


class ArchiveError(BackupError):
    """The archive cannot be imported; ``report`` lists every problem."""

    def __init__(self, message: str, report: CheckReport | None = None):
        super().__init__(message)
        self.report = report or CheckReport()


def key(paper_id: int) -> str:
    return f"p{paper_id}"


def _json(data) -> bytes:
    return (json.dumps(data, indent=1, ensure_ascii=False) + "\n").encode("utf-8")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- export --------------------------------------------------------------------------------


def _paper_ids(session: Session, libraries: list[Library]) -> set[int]:
    kept = [lib.id for lib in libraries]
    ids = set()
    for model in (Reading, StatusEvent, QueueItem, Recommendation, ExampleChoice, NoteFile):
        ids |= set(session.exec(select(model.paper_id)).all())
    ids |= {p for p in session.exec(select(XpEvent.paper_id)).all() if p is not None}
    if kept:
        ids |= {
            pid
            for lib_id, pid in session.exec(select(LibraryPaper.library_id, LibraryPaper.paper_id))
            if lib_id in kept
        }
    return ids


def _paper_record(paper: Paper) -> dict:
    record = {"key": key(paper.id)}
    for name in Paper.model_fields:
        if name == "id":
            continue
        value = getattr(paper, name)
        if value is None and name in LIST_FIELDS:
            value = []  # rows of old databases or bulk inserts may hold NULL
        record[name] = _iso(value) if hasattr(value, "isoformat") else value
    return record


def _safe_file_names(libraries: list[Library]) -> dict[int, str]:
    used, names = set(), {}
    for lib in libraries:
        stem = re.sub(r"[^a-z0-9]+", "-", lib.name.lower()).strip("-")[:60] or "library"
        candidate, number = stem, 2
        while candidate in used:
            candidate, number = f"{stem}-{number}", number + 1
        used.add(candidate)
        names[lib.id] = candidate
    return names


def _ledger_tsv(rows: list[dict], titles: dict[str, str]) -> str:
    from litrad.history import _cell

    lines = ["at\tpoints\tbalance\tkind\treason\tpaper\ttitle"]
    for row in rows:
        values = (
            row["at"],
            row["points"],
            row["balance"],
            row["kind"],
            row["reason"],
            row["paper"],
            titles.get(row["paper"]) if row["paper"] else None,
        )
        lines.append("\t".join(_cell(v if v not in (None, "") else "n/a") for v in values))
    return "\n".join(lines) + "\n"


def collect(session: Session, cfg: Config, settings_text: str | None = None) -> dict[str, bytes]:
    """Every file of the archive except the manifest, as bytes."""
    from litrad import export, history, notes
    from litrad.rules import GameRules

    files: dict[str, bytes] = {}
    libraries = [
        lib
        for lib in session.exec(select(Library).order_by(Library.id)).all()
        if lib.kind in LIBRARY_KINDS_EXPORTED
    ]
    ids = _paper_ids(session, libraries)
    papers = [p for p in session.exec(select(Paper).order_by(Paper.id)).all() if p.id in ids]
    files["papers.json"] = _json({"papers": [_paper_record(p) for p in papers]})
    titles = {key(p.id): p.title for p in papers}

    members: dict[int, list[LibraryPaper]] = {}
    for link in session.exec(
        select(LibraryPaper).order_by(LibraryPaper.library_id, LibraryPaper.paper_id)
    ).all():
        members.setdefault(link.library_id, []).append(link)
    names = _safe_file_names(libraries)
    entries = []
    for lib in libraries:
        stem = f"libraries/{names[lib.id]}"
        items = export.library_items(session, lib)
        files[f"{stem}.bib"] = export.to_bibtex(items).encode("utf-8")
        files[f"{stem}.json"] = export.to_csl_json(items).encode("utf-8")
        entries.append(
            {
                "name": lib.name,
                "description": lib.description,
                "kind": lib.kind,
                "format": lib.format,
                "use_for_recommendations": lib.use_for_recommendations,
                "created_at": _iso(lib.created_at),
                "updated_at": _iso(lib.updated_at),
                "files": {"bibtex": f"{stem}.bib", "csl_json": f"{stem}.json"},
                "papers": [
                    {
                        "paper": key(m.paper_id),
                        "citation_key": m.citation_key,
                        "folders": m.folders or [],
                        "tags": m.tags or [],
                        "added_at": _iso(m.added_at),
                    }
                    for m in members.get(lib.id, [])
                ],
            }
        )
    files["libraries.json"] = _json({"libraries": entries})

    files["readings.json"] = _json(
        {
            "readings": [
                {
                    "paper": key(r.paper_id),
                    "status": r.status.value,
                    "rating": r.rating,
                    "notes": r.notes,
                    "updated_at": _iso(r.updated_at),
                }
                for r in session.exec(select(Reading).order_by(Reading.paper_id)).all()
            ]
        }
    )
    files["status_events.json"] = _json(
        {
            "status_events": [
                {
                    "paper": key(e.paper_id),
                    "status": e.status.value,
                    "was_queue_top": e.was_queue_top,
                    "at": _iso(e.at),
                    **earlier_fields(e),
                }
                for e in session.exec(
                    select(StatusEvent).order_by(StatusEvent.at, StatusEvent.id)
                ).all()
            ]
        }
    )
    balance, ledger = 0, []
    for e in session.exec(select(XpEvent).order_by(XpEvent.at, XpEvent.id)).all():
        balance += e.points
        ledger.append(
            {
                "paper": key(e.paper_id) if e.paper_id is not None else None,
                "points": e.points,
                "kind": e.kind,
                "reason": e.reason,
                "at": _iso(e.at),
                "balance": balance,
            }
        )
    files["xp_ledger.json"] = _json({"total": balance, "events": ledger})
    profile = session.exec(select(Profile).order_by(Profile.id)).first()
    badge_names = {}
    if profile:
        try:
            badge_names = {b.code: b.name for b in GameRules.model_validate(profile.rules).badges()}
        except ValueError:
            badge_names = {}
    files["badges.json"] = _json(
        {
            "badges": [
                {
                    "code": a.code,
                    "name": badge_names.get(a.code),
                    "unlocked_at": _iso(a.unlocked_at),
                }
                for a in session.exec(
                    select(Achievement).order_by(Achievement.unlocked_at, Achievement.code)
                ).all()
            ]
        }
    )
    files["queue.json"] = _json(
        {
            "queue": [
                {
                    "paper": key(q.paper_id),
                    "position": q.position,
                    "source": q.source,
                    "added_at": _iso(q.added_at),
                }
                for q in session.exec(select(QueueItem).order_by(QueueItem.position)).all()
            ]
        }
    )
    items: dict[int, list] = {}
    for rec in session.exec(
        select(Recommendation).order_by(Recommendation.batch_id, Recommendation.rank)
    ).all():
        items.setdefault(rec.batch_id, []).append(
            {
                "paper": key(rec.paper_id),
                "rank": rec.rank,
                "category": rec.category.value,
                "score": rec.score,
                "breakdown": rec.breakdown,
                "reason": rec.reason,
            }
        )
    files["recommendations.json"] = _json(
        {
            "batches": [
                {
                    "week_start": _iso(b.week_start),
                    "created_at": _iso(b.created_at),
                    "n_candidates": b.n_candidates,
                    "warnings": b.warnings,
                    "funnel": b.funnel,
                    "params": b.params,
                    "items": items.get(b.id, []),
                }
                for b in session.exec(
                    select(RecommendationBatch).order_by(RecommendationBatch.id)
                ).all()
            ]
        }
    )
    files["example_choices.json"] = _json(
        {
            "example_choices": [
                {"paper": key(c.paper_id), "kind": c.kind, "action": c.action, "at": _iso(c.at)}
                for c in session.exec(
                    select(ExampleChoice).order_by(ExampleChoice.at, ExampleChoice.paper_id)
                ).all()
            ]
        }
    )
    files["profile.json"] = _json(
        {
            "profile": {
                "name": profile.name,
                "created_at": _iso(profile.created_at),
                "rules": profile.rules,
                "fingerprint": profile.fingerprint,
                "app_version": profile.app_version,
            }
            if profile
            else None,
            "xp_total": balance,
        }
    )
    files[SETTINGS] = _without_paths(settings_text or "").encode("utf-8")
    log = history.rows(session, history.HistoryFilter(sort="first", dismissed=True))
    files["reading_log.tsv"] = history.to_tsv(history.entries(session, log)).encode("utf-8")
    files["xp_ledger.tsv"] = _ledger_tsv(ledger, titles).encode("utf-8")
    try:
        folder = notes.notes_dir(cfg, create=False)
    except notes.NotesUnavailable:
        folder = None
    if folder is not None and folder.is_dir():
        paths = notes.scan(folder)
        if (folder / notes.TEMPLATE_FILE).is_file():
            paths.append(folder / notes.TEMPLATE_FILE)
        for path in paths:
            files[NOTES + path.relative_to(folder).as_posix()] = path.read_bytes()
    for name in JSON_FILES.values():
        schema = name[0] + ".schema.json"
        files[SCHEMAS + schema] = _json(load_schema(name[0]))
    files[SCHEMAS + "manifest.schema.json"] = _json(load_schema("manifest"))
    files[README] = readme(files).encode("utf-8")
    return files


def counts(files: dict[str, bytes]) -> dict[str, int]:
    out = {}
    for name, (_, list_key, _) in JSON_FILES.items():
        if list_key and name in files:
            out[name.removesuffix(".json")] = len(json.loads(files[name])[list_key])
    out["notes"] = sum(
        1 for n in files if n.startswith(NOTES) and not n.endswith("/note_template.md")
    )
    return out


def manifest(files: dict[str, bytes], profile_name: str | None, moment: datetime) -> dict:
    from litrad.migrations import SCHEMA_VERSION

    return {
        "format": ARCHIVE_FORMAT,
        "format_version": DATA_FORMAT_VERSION,
        "app_version": __version__,
        "schema_version": SCHEMA_VERSION,
        "created": _iso(moment.replace(microsecond=0)),
        "profile": profile_name,
        "counts": counts(files),
        "files": {
            name: {"sha256": sha256(data), "size": len(data)}
            for name, data in sorted(files.items())
        },
    }


@dataclass
class ExportResult:
    path: Path
    counts: dict
    size: int


def _without_paths(text: str) -> str:
    """settings.yaml without ``paths``: an archive holds no file paths of this machine."""
    import yaml

    try:
        data = yaml.safe_load(text) if text.strip() else None
    except yaml.YAMLError:
        return text  # the archive check reports it
    if not isinstance(data, dict) or "paths" not in data:
        return text
    data.pop("paths")
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True) if data else ""


def export_archive(
    session: Session,
    cfg: Config,
    target: Path,
    settings_text: str | None = None,
    moment: datetime | None = None,
) -> ExportResult:
    """Write the archive (via a temporary file; readable only by you)."""
    files = collect(session, cfg, settings_text)
    profile = json.loads(files["profile.json"])["profile"]
    info = manifest(files, profile["name"] if profile else None, moment or now())
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            with zipfile.ZipFile(handle, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(MANIFEST, _json(info))
                for name in sorted(files):
                    archive.writestr(name, files[name])
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return ExportResult(target, info["counts"], target.stat().st_size)


def archive_name(profile_name: str, moment: datetime | None = None) -> str:
    from litrad.backup import slug, stamp

    return f"litrad_export_{slug(profile_name)}_{stamp(moment or now())}.zip"


# --- README ---------------------------------------------------------------------------------

FILE_DOCS = {
    "papers.json": 'Every paper any other file refers to. `key` ("p<id>") is how the other '
    "files refer to it; identifiers (doi, pmid, pmcid, arxiv_id, s2_id, openalex_id, "
    "bibtex_key) and metadata (title, authors, year, pub_date, venue, abstract, metrics such "
    "as citation_count, fwci, citation_percentile, topics); `in_library`, `folders` and `tags` "
    "follow the libraries.",
    "libraries.json": "Your libraries: name, description, kind (imported, recommended), "
    "format of the last import, use_for_recommendations, and `papers`: every member with its "
    "`paper` key, `citation_key`, `folders`, `tags` and `added_at`. `files` names the "
    "library's BibTeX and CSL-JSON copy.",
    "libraries/": "Each library as BibTeX (`.bib`) and CSL-JSON (`.json`) for reference "
    "managers. Views of libraries.json: not read on import, but checked against it.",
    "readings.json": "Per paper: current `status` (abstract, skimmed, read, thorough, "
    "dismissed), `rating` (1-5), `notes` (the note body) and `updated_at`.",
    "status_events.json": "The reading log: every status change with `at` and "
    "`was_queue_top` (it was the top of the queue); optional, not in older files: "
    "`earlier: true` marks a paper read before litrad, `read_on` when it was read.",
    "xp_ledger.json": "Every XP transaction in time order: `points`, `kind`, `reason`, `at`, "
    "the `paper` if any and the running `balance`; `total` is the sum.",
    "badges.json": "Unlocked badges: `code`, `name`, `unlocked_at`.",
    "queue.json": "The reading queue: `position` (1 = read next), `source` and `added_at`.",
    "recommendations.json": "Recommendation batches: `week_start`, `created_at`, "
    "`n_candidates`, `warnings`, `funnel` (candidates removed per step), `params` (settings "
    "used) and `items` (paper, rank, category, score, `breakdown` with the evidence, reason).",
    "example_choices.json": "Papers you added to (`include`) or removed from (`exclude`) the "
    "positive or negative example list for Semantic Scholar.",
    "profile.json": "The profile: `name`, `created_at`, the locked game `rules`, their "
    "`fingerprint`, the `app_version` that created it; `xp_total`.",
    "settings.yaml": "The profile's settings (topics, excluded terms, selection, sources, "
    "queue, library), without `paths`: no file paths of this machine are included.",
    "reading_log.tsv": "The reading log as a table for spreadsheets (as Reading → Read "
    "papers exports it). Not read on import.",
    "xp_ledger.tsv": "The XP ledger as a table for spreadsheets. Not read on import.",
    "notes/": "The note files as they are (Markdown with YAML front matter, format "
    "litrad-note/1; `litrad_id: N` is paper key pN).",
    "schemas/": "JSON Schema (draft 2020-12) of every JSON file.",
}


def readme(files: dict[str, bytes]) -> str:
    lines = [
        "# litrad export",
        "",
        f"Format `{ARCHIVE_FORMAT}`, version {DATA_FORMAT_VERSION}, written by litrad "
        f"{__version__}. All text is UTF-8; dates and times are ISO 8601 (local time); JSON "
        "files are described by the JSON Schemas in `schemas/`. `manifest.json` lists the "
        "SHA-256 checksum of every file.",
        "",
        "Import with `litrad import-all FILE.zip` (a new profile) or "
        "`litrad import-all FILE.zip --into PROFILE` (merge). Everything is checked "
        "before anything is changed.",
        "",
        "## Files",
        "",
    ]
    for name, text in FILE_DOCS.items():
        present = name in files or any(f.startswith(name) for f in files)
        if present or name in ("notes/",):
            lines.append(f"- `{name}`: {text}")
    lines += [
        "",
        "Not included (recomputed or machine-specific): embeddings, the example lists "
        "themselves, the import log, the sync state of note files, file paths.",
        "",
    ]
    return "\n".join(lines)


# --- reading and checking -------------------------------------------------------------------


@dataclass
class Archive:
    path: Path
    manifest: dict
    files: dict[str, bytes]


def read_archive(path: Path) -> Archive:
    """The files of an archive; raises ``ArchiveError`` if it cannot be one."""
    path = Path(path)
    try:
        handle = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ArchiveError(f"{path.name} is not a zip archive ({exc})") from exc
    files = {}
    with handle:
        infos = handle.infolist()
        if len(infos) > MAX_FILES:
            raise ArchiveError(f"{path.name} has too many files ({len(infos)})")
        total = 0
        for info in infos:
            if info.is_dir():
                continue
            name = info.filename
            if not safe_member(name):
                raise ArchiveError(f"{path.name}: unsafe file name {name!r}")
            if info.file_size > MAX_MEMBER:
                raise ArchiveError(f"{path.name}: {name} is too large")
            total += info.file_size
            if total > MAX_TOTAL:
                raise ArchiveError(f"{path.name} is too large when unpacked")
            try:
                files[name] = handle.read(info)
            except (zipfile.BadZipFile, OSError, ValueError) as exc:
                raise ArchiveError(f"{path.name}: {name} cannot be read ({exc})") from exc
    if MANIFEST not in files:
        raise ArchiveError(f"{path.name} is not a litrad export (no {MANIFEST})")
    try:
        info = json.loads(files[MANIFEST])
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArchiveError(f"{MANIFEST} is not valid JSON ({exc})") from exc
    if not isinstance(info, dict) or info.get("format") != ARCHIVE_FORMAT:
        raise ArchiveError(f"{path.name} is not a litrad export (format)")
    version = str(info.get("format_version", ""))
    if not version.isdigit():
        raise ArchiveError(f"{MANIFEST}: format_version {version!r} is not a number")
    if int(version) > int(DATA_FORMAT_VERSION):
        raise ArchiveError(
            f"{path.name} was written by a newer litrad (data format {version}; this "
            f"version reads up to {DATA_FORMAT_VERSION}). Update litrad to import it."
        )
    problems = validate_schema("manifest", info, MANIFEST)
    if problems:
        report = CheckReport(fatal=True)
        report.extend(problems)
        raise ArchiveError(f"{MANIFEST} is not valid", report)
    return Archive(path, info, files)


@dataclass
class NoteRecord:
    name: str  # path inside notes/
    text: str
    paper: str | None  # paper key from litrad_id
    meta: dict | None


@dataclass
class Checked:
    """The result of ``check``: the data to import and what was found."""

    report: CheckReport
    data: dict
    notes: list[NoteRecord] = field(default_factory=list)
    settings: dict = field(default_factory=dict)
    template: str | None = None


def _note_record(name: str, raw: bytes) -> tuple[NoteRecord, list[Problem]]:
    from litrad.notes import normalize_text, note_id, split

    location = NOTES + name
    text = normalize_text(raw.decode("utf-8", errors="replace"))
    front, _ = split(text)
    meta, problems, paper = None, [], None
    if front is not None:
        try:
            loaded = yaml.safe_load(front)
            meta = loaded if isinstance(loaded, dict) else None
        except yaml.YAMLError:
            problems.append(Problem(location, "front matter is not valid YAML", "note"))
    lid = note_id(meta)
    if lid is not None:
        if isinstance(lid, int) and not isinstance(lid, bool) and lid > 0:
            paper = key(lid)
        else:
            problems.append(
                Problem(location, f"litrad_id {lid!r} is not a paper id", "error", ("note", name))
            )
    return NoteRecord(name, text, paper, meta), problems


def _library_files(files, libraries, papers, report: CheckReport) -> None:
    """The .bib and .json copies of each library must list the same papers."""
    from litrad.ingest.bibtex import normalize_doi, parse_bibtex
    from litrad.ingest.formats import parse_csl_json

    def title(text):
        return " ".join(str(text or "").lower().split())

    for i, library in enumerate(libraries):
        if library is None:
            continue
        members = [m for m in library.get("papers") or [] if m is not None]
        wanted_keys = {m.get("citation_key") for m in members if m.get("citation_key")}
        wanted_dois = {
            (papers.get(m["paper"]) or {}).get("doi", "").lower()
            for m in members
            if (papers.get(m["paper"]) or {}).get("doi")
        }
        wanted_titles = {title((papers.get(m["paper"]) or {}).get("title")) for m in members}
        for fmt, name in (library.get("files") or {}).items():
            if name not in files:
                report.add(
                    Problem(
                        f"libraries.json[{i}].files.{fmt}",
                        f"{name} is not in the archive",
                        "error",
                        ("file", name),
                    )
                )
                continue
            try:
                if fmt == "bibtex":
                    with tempfile.TemporaryDirectory() as folder:
                        bib = Path(folder) / "library.bib"
                        bib.write_bytes(files[name])
                        entries = parse_bibtex(bib)
                else:
                    entries = parse_csl_json(files[name].decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as exc:
                report.add(Problem(name, f"cannot be read: {exc}", "error", ("file", name)))
                continue
            if len(entries) != len(members):
                report.add(
                    Problem(
                        name,
                        f"{len(entries)} entries, but libraries.json[{i}] lists "
                        f"{len(members)} papers",
                        "error",
                        ("file", name),
                    )
                )
            for entry in entries:
                doi = (normalize_doi(entry.doi) or "").lower()
                if (
                    entry.key not in wanted_keys
                    and not (doi and doi in wanted_dois)
                    and title(entry.title) not in wanted_titles
                ):
                    report.add(
                        Problem(
                            name,
                            f"entry '{entry.key}' is not a paper of libraries.json[{i}]",
                            "error",
                            ("file", name),
                        )
                    )


def check(archive: Archive, base: Config | None = None, partial: bool = False) -> Checked:
    """Checksums, JSON Schema and consistency; nothing is changed.

    With ``partial`` the records with errors are dropped (``report.skipped``);
    otherwise ``report.errors`` lists every problem.
    """
    report = CheckReport()
    files = archive.files
    listed = archive.manifest.get("files") or {}
    damaged = set()
    for name, info in sorted(listed.items()):
        if name not in files:
            report.add(
                Problem(name, "listed in manifest.json but missing", "error", ("file", name))
            )
            damaged.add(name)
        elif sha256(files[name]) != info.get("sha256"):
            report.add(
                Problem(
                    name,
                    "checksum mismatch: the file was changed or damaged",
                    "error",
                    ("file", name),
                )
            )
            damaged.add(name)
    for name in sorted(set(files) - set(listed) - {MANIFEST}):
        report.add(Problem(name, "not listed in manifest.json; ignored", "note"))

    contents: dict[str, object] = {}
    for name, (schema, list_key, _) in JSON_FILES.items():
        if name not in files or name not in listed:
            if name in ("papers.json", "profile.json", "xp_ledger.json"):
                report.add(Problem(name, "missing", "error", ("file", name)))
            continue
        if name in damaged and partial:
            continue
        try:
            content = json.loads(files[name])
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            report.add(Problem(name, f"not valid JSON ({exc})", "error", ("file", name)))
            continue
        problems = validate_schema(schema, content, name, list_key)
        fatal = [p for p in problems if p.record == ("file", name)]
        report.extend(problems)
        if fatal:
            continue
        report.extend(unknown_fields(schema, content, name, list_key))
        if list_key:
            items = content[list_key]
            for problem in problems:
                if problem.record and problem.record[0] == name:
                    items[problem.record[1]] = None
        contents[name] = content

    # all sections merged into one dict for ``backup.merge`` (None = record dropped)
    paper_list = (contents.get("papers.json") or {}).get("papers") or []
    papers, index = {}, {}
    for i, record in enumerate(paper_list):
        if record is None:
            continue
        if record["key"] in papers:
            report.add(
                Problem(
                    f"papers.json[{i}]",
                    f"key {record['key']} is used twice (also papers.json[{index[record['key']]}])",
                    "error",
                    ("papers.json", i),
                )
            )
            continue
        papers[record["key"]] = {k: v for k, v in record.items() if k != "key"}
        index[record["key"]] = i

    def items(name):
        content = contents.get(name) or {}
        return content.get(JSON_FILES[name][1]) or []

    libraries = []
    for library in items("libraries.json"):
        if library is None:
            libraries.append(None)
            continue
        entry = dict(library)
        entry["papers"] = [
            None if m is None else {**m, "key": m.get("citation_key")} for m in library["papers"]
        ]
        libraries.append(entry)
    profile_file = contents.get("profile.json") or {}
    ledger = contents.get("xp_ledger.json") or {}
    data = {
        "papers": papers,
        "profile": profile_file.get("profile"),
        "libraries": libraries,
        "readings": list(items("readings.json")),
        "status_events": list(items("status_events.json")),
        "queue": list(items("queue.json")),
        "xp_events": list(items("xp_ledger.json")),
        "achievements": list(items("badges.json")),
        "recommendations": list(items("recommendations.json")),
        "example_choices": list(items("example_choices.json")),
    }
    if "total" in ledger:
        data["xp_total"] = ledger["total"]
    if (
        "xp_total" in profile_file
        and "total" in ledger
        and profile_file["xp_total"] != ledger["total"]
    ):
        report.add(
            Problem(
                "profile.json.xp_total",
                f"{profile_file['xp_total']} XP, but xp_ledger.json states {ledger['total']} XP",
                "error",
                ("xp_total", None),
            )
        )
    naming = Naming(NAMING, papers="papers.json", paper_index=index)
    report.extend(check_data(data, naming, "papers.json"))
    if "libraries.json" in contents and "papers.json" in contents:
        _library_files(files, libraries, papers, report)

    checked = Checked(report, data)
    for name in sorted(n for n in files if n.startswith(NOTES) and n.endswith(".md")):
        if name not in listed or (partial and name in damaged):
            continue
        rel = name.removeprefix(NOTES)
        if rel == "note_template.md":
            checked.template = files[name].decode("utf-8", errors="replace")
            continue
        record, problems = _note_record(rel, files[name])
        report.extend(problems)
        if record.paper is not None and record.paper not in papers:
            report.add(
                Problem(
                    name,
                    f"litrad_id points to paper {record.paper}, not in papers.json",
                    "error",
                    ("note", rel),
                )
            )
        checked.notes.append(record)

    if SETTINGS in files and SETTINGS in listed and SETTINGS not in damaged:
        checked.settings = _settings(files[SETTINGS], base, report)

    if partial:
        _drop_partially(checked, naming)
    return checked


def _settings(raw: bytes, base: Config | None, report: CheckReport) -> dict:
    from litrad.profiles import META_KEYS, OVERLAY_KEYS
    from litrad.yamlfile import deep_merge

    try:
        loaded = yaml.safe_load(raw.decode("utf-8")) or {}
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        report.add(Problem(SETTINGS, f"not valid YAML ({exc})", "error", ("settings", None)))
        return {}
    if not isinstance(loaded, dict):
        report.add(Problem(SETTINGS, "must contain settings (key: value)", "error", ("settings",)))
        return {}
    for name in sorted(set(loaded) - set(OVERLAY_KEYS) - set(META_KEYS)):
        report.add(Problem(f"{SETTINGS}.{name}", "not a profile setting; ignored", "note"))
    settings = {k: v for k, v in loaded.items() if k in OVERLAY_KEYS and k != "paths"}
    if "paths" in loaded:
        report.add(Problem(f"{SETTINGS}.paths", "paths of another machine are not used", "note"))
    if base is not None and settings:
        try:
            Config.model_validate(deep_merge(base.model_dump(mode="json"), settings))
        except ValueError as exc:
            lines = str(exc).splitlines()
            report.add(
                Problem(
                    SETTINGS,
                    f"not valid settings ({'; '.join(lines[1:3]) or lines[0]})",
                    "error",
                    ("settings", None),
                )
            )
            return {}
    return settings


def _drop_partially(checked: Checked, naming: Naming) -> None:
    """Drop every record with an error, then what referred to dropped ones, until clean."""
    report, data = checked.report, checked.data
    pending = report.errors
    report.errors = []
    while pending:
        report.skipped += pending
        drop(data, [p for p in pending if p.record and p.record[0] in _SECTIONS])
        for problem in pending:
            record = problem.record or ()
            if record[:1] == ("note",):
                checked.notes = [n for n in checked.notes if n.name != record[1]]
            elif record[:1] == ("settings",):
                checked.settings = {}
            elif record[:1] == ("papers.json",):
                pass  # a duplicate key: the first record stays
        problems = check_data(data, naming, "papers.json")
        pending = [p for p in problems if p.level == "error"]
        for note in list(checked.notes):
            if note.paper is not None and note.paper not in data["papers"]:
                checked.notes.remove(note)
                report.skipped.append(
                    Problem(NOTES + note.name, f"paper {note.paper} was skipped", "error")
                )


_SECTIONS = {
    "papers",
    "profile",
    "xp_total",
    "libraries",
    "readings",
    "status_events",
    "queue",
    "xp_events",
    "achievements",
    "recommendations",
    "example_choices",
}


# --- import ---------------------------------------------------------------------------------


@dataclass
class ImportResult:
    report: CheckReport
    merge: ImportReport | None = None
    slug: str | None = None
    notes_written: int = 0
    notes_kept: int = 0
    notes_failed: list[Path] = field(default_factory=list)
    dry_run: bool = False
    backup: Path | None = None

    def lines(self) -> list[str]:
        out = []
        if self.dry_run:
            out.append("Dry run: nothing was changed.")
        if self.merge is not None:
            out += self.merge.lines()
        out.append(f"Note files written:        {self.notes_written}")
        if self.notes_kept:
            out.append(f"Note files kept (exist):   {self.notes_kept}")
        if self.notes_failed:
            out.append(
                f"Note files NOT written:    {len(self.notes_failed)} (the notes are in the "
                "database; run 'litrad notes sync' to write them)"
            )
        if self.backup is not None:
            out.append(f"Backup before the import:  {self.backup}")
        return out + self.report.lines()


def _rewrite_id(text: str, paper_id: int) -> str:
    """The note with ``litrad_id`` set to this installation's paper id."""
    from litrad.notes import ID_KEY, _yaml, note_id, split

    front, body = split(text)
    if front is None:
        return text
    handler = _yaml()
    try:
        data = handler.load(front)
    except Exception:  # an invalid front matter is copied as it is
        return text
    if not hasattr(data, "__setitem__") or note_id(data) is None:
        return text
    data[ID_KEY] = paper_id
    out = io.StringIO()
    handler.dump(data, out)
    return f"---\n{out.getvalue()}---\n{body}"


def safe_member(name: str) -> bool:
    """A relative path of plain names, safe on every system (no drive, ``:``, ``\\``, ``..``)."""
    from pathlib import PurePosixPath, PureWindowsPath

    if not name or any(c in name for c in ":\\") or any(ord(c) < 32 for c in name):
        return False
    if PurePosixPath(name).is_absolute() or PureWindowsPath(name).drive:
        return False
    return all(part not in ("", ".", "..") for part in name.split("/"))


def _stage_notes(
    session: Session, cfg: Config, checked: Checked, ids: dict[str, int], staging: Path | None
) -> tuple[list[tuple[Path, Path]], int]:
    """Write the note files into ``staging`` and record them; returns (moves, kept)."""
    from litrad import notes

    try:
        folder = notes.notes_dir(cfg, create=staging is not None)
    except notes.NotesUnavailable as exc:
        if checked.notes:
            checked.report.add(Problem("notes/", f"not written: {exc}", "note"))
        return [], len(checked.notes)
    rows = {r.paper_id: r for r in session.exec(select(NoteFile)).all()}
    taken = {r.path for r in rows.values()}
    moves, kept = [], 0
    inside = folder.resolve()
    for note in checked.notes:
        paper_id = ids.get(note.paper) if note.paper else None
        target = folder / note.name
        if not safe_member(note.name) or not target.resolve().is_relative_to(inside):
            checked.report.add(Problem(NOTES + note.name, "unsafe file name, not written", "note"))
            continue
        if paper_id is not None:
            paper = session.get(Paper, paper_id)
            reading = session.get(Reading, paper_id)
            if paper_id in rows:  # this paper has its note file here already
                kept += 1
                continue
            if target.exists() or note.name in taken:
                target = notes.free_path(session, folder, paper)
                if target.exists():
                    kept += 1
                    continue
            text = _rewrite_id(note.text, paper_id)
            rel = target.relative_to(folder).as_posix()
            if reading is not None:
                # the body the database has now: equal → linked, different → a conflict
                same = notes.body_hash(notes.split(text)[1]) == notes.body_hash(reading.notes)
                row = NoteFile(
                    paper_id=paper_id,
                    path=rel,
                    sha256=notes.body_hash(reading.notes) if same else "",
                )
                session.add(row)
                rows[paper_id] = row
            taken.add(rel)
        else:
            if target.exists() or note.name in taken:
                kept += 1
                continue
            text = note.text
            taken.add(note.name)
        if staging is not None:
            staged = staging / f"{len(moves)}.md"
            staged.write_text(text, encoding="utf-8", newline="\n")
            moves.append((staged, target))
        else:
            moves.append((target, target))
    if checked.template is not None and not (folder / notes.TEMPLATE_FILE).exists():
        if staging is not None:
            staged = staging / "template.md"
            staged.write_text(checked.template, encoding="utf-8", newline="\n")
            moves.append((staged, folder / notes.TEMPLATE_FILE))
    session.flush()
    return moves, kept


def _place(moves: list[tuple[Path, Path]], failed: list[Path] | None = None) -> int:
    """Move staged note files into place; an existing file is never overwritten.

    Runs after the database transaction was committed, so a file that cannot be
    written (disk full, permissions) is collected in ``failed`` instead of raising:
    the note stays in the database and ``notes sync`` writes the file later.
    """
    written = 0
    for staged, target in moves:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                continue
            try:
                os.link(staged, target)  # fails instead of overwriting a file created meanwhile
            except FileExistsError:
                continue
            except OSError:
                if target.exists():
                    continue
                shutil.copy2(staged, target)
        except OSError:
            if failed is not None:
                failed.append(target)
            continue
        written += 1
    return written


def _keys(checked: Checked) -> dict:
    """The data to merge (``backup.merge``)."""
    data = compact(checked.data)
    data["settings"] = checked.settings
    return data


def _merge_ids(session: Session, data: dict) -> dict[str, int]:
    from litrad.backup import _PaperIndex

    index = _PaperIndex(session)
    ids = {}
    for paper_key, info in data["papers"].items():
        found = index.find(info)
        if found is not None:
            ids[paper_key] = found if isinstance(found, int) else found.id
    return ids


def _run(
    session: Session, cfg: Config, checked: Checked, dry_run: bool, staging: Path | None
) -> tuple[ImportReport, list, int]:
    data = _keys(checked)
    report = merge(session, cfg, data, commit=False)
    ids = _merge_ids(session, data)
    moves, kept = _stage_notes(session, cfg, checked, ids, None if dry_run else staging)
    return report, moves, kept


def _fail_if_errors(checked: Checked) -> None:
    if checked.report.errors or checked.report.fatal:
        errors = checked.report.errors
        shown = [str(p) for p in errors[:20]]
        more = f"\n  ... and {len(errors) - 20} more" if len(errors) > 20 else ""
        raise ArchiveError(
            "The archive is not consistent; nothing was imported:\n  "
            + "\n  ".join(shown)
            + more
            + "\nFix the files, or import only the consistent part with --partial.",
            checked.report,
        )


def import_new(
    base: Config,
    path: Path,
    name: str | None = None,
    dry_run: bool = False,
    partial: bool = False,
) -> ImportResult:
    """A new profile from an archive; on any problem nothing is left behind."""
    from litrad import notes, profiles
    from litrad.backup import memory_engine
    from litrad.db import init_db
    from litrad.profile import start_profile

    archive = read_archive(path)
    checked = check(archive, base, partial)
    _fail_if_errors(checked)
    profile = checked.data.get("profile") or {}
    name = (name or profile.get("name") or archive.manifest.get("profile") or "").strip()
    name = name or "Imported profile"
    result = ImportResult(checked.report, dry_run=dry_run)
    if dry_run:
        if any(i.name.lower() == name.lower() for i in profiles.list_profiles(base)):
            raise ArchiveError(f"A profile called '{name}' already exists; choose --new NAME")
        engine = memory_engine()
        try:
            with tempfile.TemporaryDirectory() as folder:
                data = base.model_dump(mode="json")
                data["paths"]["database"] = str(Path(folder) / "litrad.sqlite")
                data["paths"]["obsidian_vault"] = None
                cfg = Config.model_validate(data)
                with Session(engine) as session:
                    result.merge, moves, result.notes_kept = _run(session, cfg, checked, True, None)
                    result.notes_written = len(moves)
                    session.rollback()
        finally:
            engine.dispose()
        return result

    slug, home = profiles._new_folder(base, name)
    try:
        settings = dict(checked.settings)
        profiles.write_overlay(base, slug, settings or {"topics": [], "excluded_terms": []})
        cfg = profiles.profile_config(base, slug)
        engine = init_db(cfg.paths.database)
        staging = None
        try:
            with Session(engine) as session:
                folder = notes.notes_dir(cfg)
                staging = Path(tempfile.mkdtemp(prefix=".litrad-import-", dir=folder))
                result.merge, moves, result.notes_kept = _run(session, cfg, checked, False, staging)
                if session.exec(select(Profile)).first() is None:
                    session.commit()
                    start_profile(session, cfg, name)
                else:
                    stored = session.exec(select(Profile)).first()
                    stored.name = name
                    session.add(stored)
                    session.commit()
                result.notes_written = _place(moves, result.notes_failed)
                notes.refresh_quietly(session, cfg)
        finally:
            if staging is not None:
                shutil.rmtree(staging, ignore_errors=True)
            engine.dispose()
    except BaseException:
        shutil.rmtree(home, ignore_errors=True)
        raise
    result.slug = slug
    return result


def import_into(
    cfg: Config,
    path: Path,
    dry_run: bool = False,
    partial: bool = False,
    backup: bool = True,
) -> ImportResult:
    """Merge an archive into the profile of ``cfg`` (idempotent), in one transaction."""
    from litrad import notes
    from litrad.backup import run_backup
    from litrad.db import init_db

    archive = read_archive(path)
    checked = check(archive, cfg, partial)
    _fail_if_errors(checked)
    result = ImportResult(checked.report, dry_run=dry_run)
    if checked.settings:
        checked.report.add(
            Problem(SETTINGS, "not applied when merging into an existing profile", "note")
        )
    engine = init_db(cfg.paths.database)
    staging = None
    try:
        with Session(engine) as session:
            if not dry_run and backup:
                result.backup = run_backup(cfg, session).snapshot  # safety net
            if not dry_run:
                folder = notes.notes_dir(cfg)
                staging = Path(tempfile.mkdtemp(prefix=".litrad-import-", dir=folder))
            try:
                result.merge, moves, result.notes_kept = _run(
                    session, cfg, checked, dry_run, staging
                )
            except BaseException:
                session.rollback()
                raise
            if dry_run:
                result.notes_written = len(moves)
                session.rollback()
                return result
            session.commit()
            result.notes_written = _place(moves, result.notes_failed)
            notes.refresh_quietly(session, cfg)
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        engine.dispose()
    return result


def check_file(path: Path, base: Config | None = None) -> Checked:
    """Only the check (``litrad import-all --dry-run`` shows the rest)."""
    return check(read_archive(path), base)


def is_archive(path: Path) -> bool:
    """Whether ``path`` is a zip file (an archive)."""
    try:
        return zipfile.is_zipfile(path)
    except OSError:
        return False


def summarize_file(path: Path, cfg: Config, label: str = "") -> dict:
    """``backup.summarize_session`` for an archive: key figures, nothing is changed."""
    from litrad.backup import memory_engine, summarize_session

    archive = read_archive(path)
    checked = check(archive, cfg)
    _fail_if_errors(checked)
    engine = memory_engine()
    try:
        with Session(engine) as session:
            merge(session, cfg, _keys(checked), commit=False)
            moment = None
            try:
                moment = datetime.fromisoformat(str(archive.manifest.get("created")))
            except ValueError:
                pass
            return summarize_session(session, cfg, moment, label)
    finally:
        engine.dispose()
