#!/usr/bin/env python3
"""Generate "golden" compatibility fixtures from released litresearch versions.

For every git tag the script checks the tag out into a temporary worktree,
installs it into a fresh virtual environment and runs a builder snippet inside
that environment. The snippet uses *that version's own* Python API to build a
small profile completely offline (BibTeX import, readings with XP and badges,
queue, recommendations, ...) and writes the files that version produces:

    litresearch.sqlite  consistent snapshot of the profile database
    settings.yaml       the profile's settings file
    progress.json       progress export (backup.export_progress)
    profile.json        saved profile file (backup.save_profile_file)
    expected.json       counts computed with that version, for the tests

They are copied to ``<out>/<version>/`` (default ``tests/data/compat``). Fixtures
of a released version must NEVER be regenerated: only new versions get new
folders. The current app is then tested to still open and upgrade them.

Usage (from the repository root)::

    tools/make_compat_fixtures.py                      # both released tags
    tools/make_compat_fixtures.py --tags v0.2.0-dev    # one tag
    tools/make_compat_fixtures.py --source . --name 0.3.0 --out /tmp/compat
    tools/make_compat_fixtures.py --keep               # keep worktree/venv

Temporary files live under ``$TMPDIR`` (``tempfile.gettempdir()``); the
builder runs in an empty directory with HOME and XDG folders redirected there,
and every network access raises an error, so no real data and no literature API
is ever touched.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_TAGS = ["v0.1.0-alpha.1", "v0.2.0-dev"]
BIB_FILE = REPO / "tests" / "data" / "paperpile_sample.bib"
FIXTURE_FILES = ["litresearch.sqlite", "settings.yaml", "progress.json", "profile.json"]
# written by versions that have them (export.zip: "export everything", 0.6 and later)
OPTIONAL_FILES = ["export.zip"]

README = """# Compatibility fixtures

Golden files written by released litresearch versions. The current app is tested
to still open, read and upgrade them (see `tests/test_compat.py`).

Each folder `<version>/` was produced by `tools/make_compat_fixtures.py`: the
script checks out the git tag, installs it into a fresh virtual environment and
builds a small profile offline with **that version's own code** (BibTeX import of
`tests/data/paperpile_sample.bib`, readings at several depths with XP and badges,
ratings, notes, queue, one recommendation batch; from 0.2 a second library).
Network access is blocked while building.

| File | Content |
| --- | --- |
| `litresearch.sqlite` | SQLite snapshot of the profile database (`backup.snapshot`) |
| `settings.yaml` | the profile's settings file |
| `progress.json` | progress export (`backup.export_progress`) |
| `profile.json` | saved profile file (`backup.save_profile_file`) |
| `expected.json` | counts computed by that version (papers, readings, XP, ...) |
| `export.zip` | "export everything" archive (`archive.export_archive`, from 0.6 on) |

`dev-<YYYYMMDD>/` folders hold what the development branch wrote at that date
(e.g. the first data format version before its release); they were made with
`--source . --name dev-<YYYYMMDD>`.

**Never regenerate the fixtures of a released version**: they are the proof of
what that version wrote. Only a new version gets a new folder:

    tools/make_compat_fixtures.py --tags v0.3.0

Every release adds its fixtures (see CONTRIBUTING.md).
"""

BUILDER = r"""
import json
import shutil
import socket
import sys
from datetime import date, datetime
from pathlib import Path

import httpx
import yaml
from sqlmodel import Session, func, select

args = json.loads(sys.argv[1])
work = Path(args["work"])
out = Path(args["out"])
name = args["name"]
out.mkdir(parents=True, exist_ok=True)

# --- no network, whatever happens ---------------------------------------------------------
attempts = []


def _blocked(*a, **k):
    attempts.append(repr(a)[:200])
    raise RuntimeError("network access is blocked while building fixtures")


socket.socket.connect = _blocked
socket.create_connection = _blocked
socket.getaddrinfo = _blocked
httpx.Client.send = _blocked
httpx.HTTPTransport.handle_request = _blocked

from litresearch import backup, db, profiles, reading  # noqa: E402
from litresearch.config import load_config  # noqa: E402
from litresearch.db import (  # noqa: E402
    Achievement,
    IngestRun,
    Paper,
    QueueItem,
    Reading,
    ReadStatus,
    Recommendation,
    RecommendationBatch,
    StatusEvent,
    XpEvent,
)
from litresearch.ingest import pipeline  # noqa: E402
from litresearch.ingest.bibtex import parse_bibtex  # noqa: E402

try:
    from litresearch import libraries  # noqa: E402
    from litresearch.db import Library, LibraryPaper  # noqa: E402
except ImportError:  # 0.1: a profile has one library (Paper.in_library)
    libraries = None

try:
    from litresearch import examples  # noqa: E402
except ImportError:
    examples = None

# --- config -------------------------------------------------------------------------------
example = yaml.safe_load(Path(args["source"], "config", "config.example.yaml").read_text())
data = work / "data"
example["paths"].update(
    {
        "profiles_dir": str(data / "profiles"),
        "bibtex": str(data / "paperpile" / "library.bib"),
        "database": str(data / "litresearch.sqlite"),
        "cache_dir": str(data / "cache"),
        "obsidian_vault": "n/a",
    }
)
example["backup"]["dir"] = str(work / "backups")
config_path = work / "config.yaml"
config_path.write_text(yaml.safe_dump(example, sort_keys=False), encoding="utf-8")
base = load_config(config_path)

# --- profile (rules are locked in the DB) ---------------------------------------------------
topics = [
    {
        "name": "tumor-evolution",
        "weight": 1.0,
        "queries": ["clonal evolution cancer"],
        "mesh_terms": ["Clonal Evolution"],
        "seed_dois": [],
    },
    {"name": "liquid-biopsy", "weight": 0.8, "queries": ["circulating tumor DNA"]},
]
slug = profiles.create_profile(
    base,
    f"Compat {name}",
    settings={"topics": topics, "excluded_terms": ["veterinary"]},
    at=datetime(2026, 9, 1, 9, 0),
)
cfg = profiles.profile_config(base, slug)
engine = db.init_db(cfg.paths.database)

with Session(engine) as session:
    # --- BibTeX import without any enrichment (sync step only) -------------------------------
    entries = parse_bibtex(Path(args["bib"]), cfg.library.ignore_labels)
    report = pipeline.IngestReport()
    if libraries is not None:
        library = libraries.create(session, libraries.DEFAULT_NAME)
        papers = pipeline.sync_library(session, library, entries, report)
        library.format = "bibtex"
        library.updated_at = datetime(2026, 9, 1, 9, 5)
        session.add(library)
    else:
        papers = pipeline.sync_library(session, entries, report)
    raw = Path(args["bib"]).read_bytes()
    run = dict(
        at=datetime(2026, 9, 1, 9, 5),
        source="cli",
        file_name=Path(args["bib"]).name,
        sha256="0" * 64,
        size=len(raw),
        entries=report.entries or len(entries),
        added=report.added,
        updated=report.updated,
        removed=report.removed,
    )
    if "mode" in IngestRun.model_fields:
        run.update(mode="new", format="bibtex", library_id=library.id)
    session.add(IngestRun(**run))
    session.commit()
    papers = [session.get(Paper, p.id) for p in papers]
    p_rec, p_frog, p_skim, p_abs, p_drop, p_q1, p_q2 = papers[:7]

    # --- one recommendation batch (no network), before reading ------------------------------
    batch = RecommendationBatch(
        created_at=datetime(2026, 9, 7, 6, 0),
        week_start=date(2026, 9, 7),
        n_candidates=40,
        warnings=[],
        funnel={"candidates": 40, "after_filters": 12, "selected": 2},
        params={"n_recommendations": 2},
    )
    session.add(batch)
    session.flush()
    for rank, (paper, score) in enumerate(((p_rec, 0.91), (p_q2, 0.74)), start=1):
        session.add(
            Recommendation(
                batch_id=batch.id,
                paper_id=paper.id,
                rank=rank,
                category="open",
                score=score,
                breakdown={"relevance": score, "impact": 0.5, "timeliness": 0.3},
                reason="Fixture recommendation",
            )
        )
    session.commit()

    long_note = "Reflection on the study design and what it means for my own work. " * 3
    reading.record_reading(  # recommended paper, thorough, rated, long note
        session, cfg, p_rec, ReadStatus.thorough, rating=5, note=long_note,
        at=datetime(2026, 9, 8, 12, 0),
    )  # fmt: skip

    # --- queue: three papers, the top one is read first ("frog") ------------------------------
    reading.queue_add(session, cfg, p_frog)
    reading.queue_add(session, cfg, p_q1)
    reading.queue_add(session, cfg, p_q2, source="recommendation")
    reading.record_reading(
        session, cfg, p_frog, ReadStatus.read, rating=4, note="Short note.",
        at=datetime(2026, 9, 15, 12, 0),
    )  # fmt: skip
    reading.record_reading(
        session, cfg, p_skim, ReadStatus.skimmed, rating=3, at=datetime(2026, 9, 22, 12, 0)
    )
    reading.record_reading(
        session, cfg, p_abs, ReadStatus.abstract, at=datetime(2026, 9, 29, 12, 0)
    )
    reading.record_reading(
        session, cfg, p_drop, ReadStatus.dismissed, at=datetime(2026, 10, 6, 12, 0)
    )

    # --- second library and example choice (0.2) --------------------------------------------
    if libraries is not None:
        from litresearch.ingest.formats import read_references

        overlap = next(p.doi for p in papers if p.doi)
        ris = work / "second.ris"
        ris.write_text(
            "TY  - JOUR\nTI  - Overlapping paper with the BibTeX library\nAU  - Doe, Jane\n"
            f"PY  - 2020\nDO  - {overlap}\nER  - \n\n"
            "TY  - JOUR\nTI  - A paper only in the second library\nAU  - Roe, Richard\n"
            "PY  - 2021\nDO  - 10.1234/compat.second\nT2  - Journal of Fixtures\nER  - \n",
            encoding="utf-8",
        )
        second = libraries.create(session, "Compat second library")
        dups = []
        fmt, entries2 = read_references(ris, cfg.library.ignore_labels, dups)
        report2 = pipeline.IngestReport(library=second.name, format=fmt)
        pipeline.sync_library(session, second, entries2, report2, replace=False)
        second.format = fmt
        session.add(second)
        session.add(
            IngestRun(
                at=datetime(2026, 9, 2, 10, 0), library_id=second.id, mode="new", format=fmt,
                source="cli", file_name=ris.name, sha256="1" * 64, size=ris.stat().st_size,
                entries=report2.entries or len(entries2), added=report2.added,
                updated=report2.updated, removed=report2.removed,
            )
        )  # fmt: skip
        session.commit()
    if examples is not None and hasattr(examples, "include"):  # 0.3 and later
        examples.include(session, papers[0], "positive")
        session.commit()
    elif examples is not None:
        for fn in ("choose", "set_choice", "record_choice"):
            if hasattr(examples, fn):
                getattr(examples, fn)(session, cfg, papers[0], "include")
                break

    # --- expected counts (computed by this version) ------------------------------------------
    def count(model, *where):
        return session.exec(select(func.count()).select_from(model).where(*where)).one()

    queue_rows = session.exec(select(QueueItem).order_by(QueueItem.position)).all()
    expected = {
        "version": name,
        "tag": args["tag"],
        "app_version": __import__("litresearch").__version__,
        "papers": count(Paper),
        "library_papers": count(Paper, Paper.in_library == True),  # noqa: E712
        "readings": count(Reading),
        "readings_with_notes": count(Reading, Reading.notes != ""),
        "status_events": count(StatusEvent),
        "xp_events": count(XpEvent),
        "xp_total": session.exec(select(func.sum(XpEvent.points))).one() or 0,
        "xp_by_kind": {
            kind: pts
            for kind, pts in session.exec(
                select(XpEvent.kind, func.sum(XpEvent.points)).group_by(XpEvent.kind)
            )
        },
        "achievements": sorted(a.code for a in session.exec(select(Achievement))),
        "queue": [session.get(Paper, q.paper_id).title for q in queue_rows],
        "batches": count(RecommendationBatch),
        "recommendations": count(Recommendation),
    }  # fmt: skip
    if libraries is not None:
        expected["libraries"] = count(Library)
        expected["library_memberships"] = count(LibraryPaper)
        expected["example_choices"] = (
            count(examples.ExampleChoice) if examples and hasattr(examples, "ExampleChoice") else 0
        )

    # --- outputs written by this version's own functions ----------------------------------------
    moment = datetime(2026, 11, 1, 8, 0)
    settings = backup.profile_settings(cfg)
    progress = backup.export_progress(session, moment, settings)
    (out / "progress.json").write_text(json.dumps(progress, indent=1), encoding="utf-8")
    saved = backup.save_profile_file(session, work / "saved", moment, settings)
    shutil.copyfile(saved, out / "profile.json")
    try:  # the "export everything" archive exists from 0.6 on
        from litresearch import archive, notes
    except ImportError:
        archive = None
    if archive is not None:
        notes.refresh_quietly(session, cfg)
        settings_text = (cfg.paths.database.parent / "settings.yaml").read_text("utf-8")
        archive.export_archive(session, cfg, out / "export.zip", settings_text, moment)
        expected["note_files"] = len(notes.scan(notes.notes_dir(cfg)))

engine.dispose()
backup.snapshot(cfg.paths.database, out / "litresearch.sqlite")
shutil.copyfile(cfg.paths.database.parent / "settings.yaml", out / "settings.yaml")
expected["network_attempts"] = len(attempts)
assert not attempts, attempts
(out / "expected.json").write_text(json.dumps(expected, indent=2) + "\n", encoding="utf-8")
print(json.dumps(expected, indent=2))
"""


def run(cmd, **kwargs):
    """Run a command, echoing it; raise on failure."""
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, **kwargs)


def tmp_root() -> Path:
    """Folder for temporary worktrees and venvs (never the small /tmp tmpfs)."""
    root = Path(tempfile.gettempdir())
    root.mkdir(parents=True, exist_ok=True)
    return root


def build(source: Path, name: str, tag: str, out_dir: Path, keep: bool, python: str) -> None:
    """Build the fixtures for one source tree and copy them to ``out_dir/name``."""
    work_root = Path(tempfile.mkdtemp(prefix=f"compat-{name}-", dir=tmp_root()))
    worktree = None
    try:
        if tag:
            worktree = work_root / "src"
            run(["git", "-C", REPO, "worktree", "add", "--detach", worktree, tag])
            source = worktree
        venv = work_root / "venv"
        run([python, "-m", "venv", venv])
        pip = venv / "bin" / "pip"
        run([pip, "install", "--quiet", source])
        sandbox = work_root / "run"  # fresh and empty; the builder's cwd
        out = work_root / "out"
        for folder in (sandbox, sandbox / "home", sandbox / "config", sandbox / "share"):
            folder.mkdir(parents=True, exist_ok=True)
        env = {
            **os.environ,
            "HOME": str(sandbox / "home"),
            "XDG_CONFIG_HOME": str(sandbox / "config"),
            "XDG_DATA_HOME": str(sandbox / "share"),
            "XDG_CACHE_HOME": str(sandbox / "home" / ".cache"),
            "PYTHONHASHSEED": "0",
        }
        arguments = {
            "work": str(sandbox),
            "out": str(out),
            "name": name,
            "tag": tag or "",
            "source": str(source),
            "bib": str(BIB_FILE),
        }
        run([venv / "bin" / "python", "-c", BUILDER, json.dumps(arguments)], cwd=sandbox, env=env)
        target = out_dir / name
        target.mkdir(parents=True, exist_ok=True)
        for fixture in [*FIXTURE_FILES, "expected.json", *OPTIONAL_FILES]:
            if fixture in OPTIONAL_FILES and not (out / fixture).exists():
                continue
            shutil.copyfile(out / fixture, target / fixture)
        print(f"fixtures written to {target}")
    finally:
        if keep:
            print(f"kept {work_root} (remove it and `git worktree prune` when done)")
        else:
            if worktree is not None:
                subprocess.run(
                    ["git", "-C", str(REPO), "worktree", "remove", "--force", str(worktree)],
                    check=False,
                )
            shutil.rmtree(work_root, ignore_errors=True)
            subprocess.run(["git", "-C", str(REPO), "worktree", "prune"], check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--tags", nargs="+", default=DEFAULT_TAGS, help="git tags to build")
    parser.add_argument("--out", type=Path, default=REPO / "tests" / "data" / "compat")
    parser.add_argument("--source", type=Path, help="build from this checkout instead of tags")
    parser.add_argument("--name", help="folder name for --source (e.g. 0.3.0)")
    parser.add_argument("--keep", action="store_true", help="keep worktree and venv for debugging")
    parser.add_argument("--python", default="/usr/bin/python3", help="base interpreter (>= 3.11)")
    args = parser.parse_args()

    out_dir = args.out.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.source:
        if not args.name:
            parser.error("--source needs --name")
        build(args.source.resolve(), args.name, "", out_dir, args.keep, args.python)
    else:
        for tag in args.tags:
            build(REPO, tag.removeprefix("v"), tag, out_dir, args.keep, args.python)
    (out_dir / "README.md").write_text(README, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
