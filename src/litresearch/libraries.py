"""Libraries: collections of papers with a name and a description.

- **Imported libraries** come from a reference manager (Paperpile, Zotero,
  Mendeley, EndNote …) through an export file; a profile can have several.
- **Recommended** collects the recommendations you put on your reading queue
  (only those); litresearch creates it on the first one.
- The **reading queue** is shown as a library too, but it is not stored: it
  always contains exactly the papers currently on the queue.

A paper exists once, however many libraries contain it (``LibraryPaper``
links it to each, with that library's citation key, folders and labels).
``Paper.in_library`` means "in at least one imported library";
``refresh_flags`` keeps it and the combined folders and labels up to date.
Which papers count as *your library* for the recommendation similarity is
chosen per library (``use_for_recommendations``).
"""

import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlmodel import Session, func, select

from litresearch.config import Config
from litresearch.db import IngestRun, Library, LibraryPaper, Paper, Reading, ReadStatus, now

IMPORTED = "imported"
ID_CHUNK = 500  # SQLite limits the number of variables in one statement
RECOMMENDED = "recommended"
QUEUE_NAME = "Reading queue"
RECOMMENDED_NAME = "Recommended"
DEFAULT_NAME = "My library"
MAX_NAME = 80


class LibraryError(ValueError):
    pass


# --- finding and changing libraries -------------------------------------------------------


def all_libraries(session: Session) -> list[Library]:
    """Imported libraries by name, then the Recommended library."""
    libraries = session.exec(select(Library)).all()
    return sorted(libraries, key=lambda lib: (lib.kind != IMPORTED, lib.name.lower()))


def find(session: Session, name_or_id: str | int) -> Library:
    """A library by id or name (case-insensitive)."""
    if isinstance(name_or_id, int) or str(name_or_id).isdigit():
        library = session.get(Library, int(name_or_id))
        if library is not None:
            return library
    for library in session.exec(select(Library)).all():
        if library.name.lower() == str(name_or_id).strip().lower():
            return library
    raise LibraryError(f"No library '{name_or_id}'")


def check_name(session: Session, name: str, library: Library | None = None) -> str:
    name = re.sub(r"\s+", " ", name or "").strip()
    if not name:
        raise LibraryError("Give the library a name")
    if len(name) > MAX_NAME:
        raise LibraryError(f"Use a name of at most {MAX_NAME} characters")
    if name.lower() == QUEUE_NAME.lower():
        raise LibraryError(f"'{QUEUE_NAME}' is the name of your queue")
    for other in session.exec(select(Library)).all():
        if other.name.lower() == name.lower() and (library is None or other.id != library.id):
            raise LibraryError(f"There is already a library called '{other.name}'")
    return name


def create(
    session: Session,
    name: str,
    description: str = "",
    kind: str = IMPORTED,
    use_for_recommendations: bool = True,
) -> Library:
    library = Library(
        name=check_name(session, name),
        description=(description or "").strip(),
        kind=kind,
        use_for_recommendations=use_for_recommendations,
    )
    session.add(library)
    session.flush()
    return library


def update(
    session: Session,
    library: Library,
    name: str | None = None,
    description: str | None = None,
    use_for_recommendations: bool | None = None,
) -> Library:
    if name is not None:
        library.name = check_name(session, name, library)
    if description is not None:
        library.description = description.strip()
    if use_for_recommendations is not None:
        library.use_for_recommendations = use_for_recommendations
    library.updated_at = now()
    session.add(library)
    return library


def delete(session: Session, library: Library) -> int:
    """Remove a library; its papers stay in your other libraries and your history.

    Returns the number of papers it contained.
    """
    links = session.exec(select(LibraryPaper).where(LibraryPaper.library_id == library.id)).all()
    ids = {link.paper_id for link in links}
    for link in links:
        session.delete(link)
    # its import history goes with it (otherwise ``migrate`` would see an old database)
    for run in session.exec(select(IngestRun).where(IngestRun.library_id == library.id)).all():
        session.delete(run)
    session.delete(library)
    session.flush()
    refresh_flags(session, ids)
    return len(ids)


# --- members -----------------------------------------------------------------------------


def members(session: Session, library: Library) -> list[tuple[Paper, LibraryPaper]]:
    rows = session.exec(
        select(Paper, LibraryPaper)
        .join(LibraryPaper, LibraryPaper.paper_id == Paper.id)
        .where(LibraryPaper.library_id == library.id)
        .order_by(Paper.title)
    ).all()
    return list(rows)


def size(session: Session, library: Library) -> int:
    return session.exec(
        select(func.count()).select_from(LibraryPaper).where(LibraryPaper.library_id == library.id)
    ).one()


def libraries_of(session: Session, paper_id: int) -> list[Library]:
    return list(
        session.exec(
            select(Library)
            .join(LibraryPaper, LibraryPaper.library_id == Library.id)
            .where(LibraryPaper.paper_id == paper_id)
            .order_by(Library.name)
        ).all()
    )


def link(
    session: Session,
    library: Library,
    paper: Paper,
    citation_key: str | None = None,
    folders=(),
    tags=(),
) -> LibraryPaper:
    """Put a paper into a library (or update what the library says about it)."""
    entry = session.get(LibraryPaper, (library.id, paper.id))
    if entry is None:
        entry = LibraryPaper(library_id=library.id, paper_id=paper.id)
    entry.citation_key = citation_key or entry.citation_key
    # None: JSON columns that a migration added to an old database are empty
    entry.folders = list(folders or ())
    entry.tags = list(tags or ())
    session.add(entry)
    return entry


def unlink(session: Session, library: Library, paper_id: int) -> bool:
    entry = session.get(LibraryPaper, (library.id, paper_id))
    if entry is None:
        return False
    session.delete(entry)
    session.flush()
    refresh_flags(session, {paper_id})
    return True


def recommended_library(session: Session) -> Library:
    """The library of recommendations you queued (created on first use)."""
    library = session.exec(select(Library).where(Library.kind == RECOMMENDED)).first()
    if library is None:
        library = create(
            session,
            _free_name(session, RECOMMENDED_NAME),
            "Recommendations you put on your reading queue.",
            kind=RECOMMENDED,
            # off by default: your picks should not narrow future recommendations
            use_for_recommendations=False,
        )
    return library


def add_recommended(session: Session, paper: Paper) -> None:
    """Called when a recommendation goes on the queue."""
    library = recommended_library(session)
    link(session, library, paper)
    session.flush()


def _free_name(session: Session, name: str) -> str:
    taken = {lib.name.lower() for lib in session.exec(select(Library)).all()}
    candidate, number = name, 2
    while candidate.lower() in taken:
        candidate, number = f"{name} {number}", number + 1
    return candidate


# --- derived data -----------------------------------------------------------------------


def refresh_flags(session: Session, paper_ids: set[int] | None = None) -> None:
    """Recompute ``in_library``, folders, labels and the citation key of papers.

    ``paper_ids=None`` refreshes every paper that is or was in a library.
    """
    imported = {lib.id for lib in session.exec(select(Library)).all() if lib.kind == IMPORTED}
    links: dict[int, list[LibraryPaper]] = {}
    for entry in session.exec(select(LibraryPaper)).all():
        links.setdefault(entry.paper_id, []).append(entry)
    if paper_ids is None:
        flagged = session.exec(select(Paper.id).where(Paper.in_library)).all()
        wanted = sorted(set(links) | set(flagged))
    else:
        wanted = sorted(paper_ids)
    taken = set(session.exec(select(Paper.bibtex_key).where(Paper.bibtex_key.is_not(None))).all())
    papers = []
    for start in range(0, len(wanted), ID_CHUNK):
        chunk = wanted[start : start + ID_CHUNK]
        papers.extend(session.exec(select(Paper).where(Paper.id.in_(chunk))).all())
    for paper in papers:
        mine = sorted(links.get(paper.id, []), key=lambda e: e.added_at)
        in_imported = [e for e in mine if e.library_id in imported]
        paper.in_library = bool(in_imported)
        paper.folders = list(dict.fromkeys(f for e in in_imported for f in e.folders))
        paper.tags = list(dict.fromkeys(t for e in in_imported for t in e.tags))
        keys = [e.citation_key for e in in_imported if e.citation_key]
        if keys and paper.bibtex_key not in keys:
            # Paper.bibtex_key is unique: use a key that no other paper has yet
            free = next((k for k in keys if k not in taken), None)
            if free:
                taken.discard(paper.bibtex_key)
                paper.bibtex_key = free
                taken.add(free)
        session.add(paper)
    session.flush()


def recommendation_library(session: Session) -> list[Paper]:
    """The papers that stand for *your library* when scoring recommendations."""
    every = session.exec(select(Library)).all()
    if not every:  # a database that has not been migrated yet
        return list(session.exec(select(Paper).where(Paper.in_library)).all())
    chosen = [lib.id for lib in every if lib.use_for_recommendations]
    if not chosen:
        return []
    return list(
        session.exec(
            select(Paper)
            .join(LibraryPaper, LibraryPaper.paper_id == Paper.id)
            .where(LibraryPaper.library_id.in_(chosen))
            .distinct()
        ).all()
    )


@dataclass
class Summary:
    library: Library
    papers: int
    read: int
    last_run: IngestRun | None
    file_modified: datetime | None


def summaries(session: Session) -> list[Summary]:
    """What the libraries overview shows."""
    read_ids = {
        r.paper_id
        for r in session.exec(select(Reading)).all()
        if r.status in (ReadStatus.skimmed, ReadStatus.read, ReadStatus.thorough)
    }
    result = []
    for library in all_libraries(session):
        ids = set(
            session.exec(
                select(LibraryPaper.paper_id).where(LibraryPaper.library_id == library.id)
            ).all()
        )
        last = session.exec(
            select(IngestRun)
            .where(IngestRun.library_id == library.id)
            .order_by(IngestRun.id.desc())
        ).first()
        result.append(
            Summary(library, len(ids), len(ids & read_ids), last, source_modified(library))
        )
    return result


def source_modified(library: Library) -> datetime | None:
    if not library.source_path:
        return None
    path = Path(library.source_path)
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime).replace(microsecond=0)


# --- files ---------------------------------------------------------------------------


def files_dir(cfg: Config) -> Path:
    """Where uploaded exports are kept: next to the profile's database."""
    return cfg.paths.database.parent / "libraries"


def store_upload(cfg: Config, library: Library, raw: bytes, fmt: str) -> Path:
    """Keep an uploaded export as the library's file (the previous one as .bak)."""
    ext = {"bibtex": ".bib", "ris": ".ris", "csl-json": ".json"}.get(fmt, ".txt")
    slug = re.sub(r"[^a-z0-9]+", "-", library.name.lower()).strip("-")[:40] or "library"
    target = files_dir(cfg) / f"{library.id}_{slug}{ext}"
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".upload.")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        if target.exists():
            shutil.copy2(target, target.with_name(target.name + ".bak"))
        os.replace(tmp, target)
    finally:
        Path(tmp).unlink(missing_ok=True)
    return target


# --- migration ---------------------------------------------------------------------------


def migrate(session: Session, cfg: Config) -> Library | None:
    """Databases from before libraries existed: their library becomes "My library".

    Runs once (only while no library exists); nothing is lost: citation keys,
    folders, labels and the import history are kept.
    """
    if session.exec(select(Library)).first() is not None:
        return None
    papers = session.exec(select(Paper).where(Paper.in_library)).all()
    runs = session.exec(select(IngestRun)).all()
    if not papers and not runs:
        return None
    source = cfg.paths.bibtex
    library = create(
        session,
        DEFAULT_NAME,
        "Your library from before litresearch had several libraries.",
    )
    library.format = "bibtex"
    library.source_path = str(source.resolve()) if source.exists() else None
    for paper in papers:
        link(session, library, paper, paper.bibtex_key, paper.folders, paper.tags)
    for run in runs:
        run.library_id = library.id
        session.add(run)
    session.commit()
    return library
