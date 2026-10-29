"""Large libraries: thousands of entries, messy duplicates, re-imports, exports, progress files.

Sizes are kept small enough for a Raspberry Pi; the work per entry is checked
by counting SQL statements and LaTeX decoder calls, which does not depend on
the speed of the machine.
"""

import json

import pytest
from bibtexparser import middlewares as mw
from sqlalchemy import event
from sqlmodel import Session, func, select

from conftest import FakeApis
from litresearch import export, libraries
from litresearch.backup import export_progress, import_progress
from litresearch.db import LibraryPaper, Paper, init_db
from litresearch.ingest.formats import read_references
from litresearch.ingest.pipeline import import_into

N = 400


@pytest.fixture
def db(cfg):
    cfg.sources.openalex = False  # no enrichment: these tests are about the database
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        yield cfg, session, engine
    engine.dispose()


class Statements:
    """Counts the SQL statements an engine runs."""

    def __init__(self, engine):
        self.count = 0
        event.listen(engine, "before_cursor_execute", self._seen)

    def _seen(self, *_args):
        self.count += 1

    def take(self) -> int:
        count, self.count = self.count, 0
        return count


def count(session, model) -> int:
    return session.exec(select(func.count()).select_from(model)).one()


def write_bib(path, numbers, abstract_words=0):
    abstract = " ".join(["plain words with 50% and A & B"] * abstract_words)
    with path.open("w", encoding="utf-8") as handle:
        for n in numbers:
            handle.write(
                f"@article{{Key{n},\n  title = {{Paper number {n} on tumour evolution}},\n"
                f"  author = {{Smith, Anna and Jones, Ben}},\n  year = {{{2000 + n % 25}}},\n"
                f"  doi = {{10.1000/x{n}}},\n  journal = {{Journal}},\n"
                f"  keywords = {{Projects/P{n % 5};label{n % 3}}},\n"
                + (f"  abstract = {{{abstract}}},\n" if abstract else "")
                + "}\n\n"
            )
    return path


def test_parsing_plain_values_skips_the_latex_decoder(tmp_path, monkeypatch):
    calls = []
    original = mw.LatexDecodingMiddleware._transform_python_value_string

    def counted(self, value):
        calls.append(value)
        return original(self, value)

    monkeypatch.setattr(mw.LatexDecodingMiddleware, "_transform_python_value_string", counted)
    path = write_bib(tmp_path / "big.bib", range(1000), abstract_words=60)
    with path.open("a") as handle:
        handle.write('@article{Latex, title = {{\\"U}ber {T}umour}, abstract = {25\\% of 50%}}\n')
    _, entries = read_references(path)
    assert len(entries) == 1001
    assert entries[0].abstract.startswith("plain words with 50% and A & B")
    assert entries[-1].title == "Über Tumour" and entries[-1].abstract == "25% of 50%"
    assert len(calls) == 2  # only the two values with LaTeX


def test_large_reimports_are_linear_and_keep_flags_right(db, make_http, tmp_path):
    cfg, session, engine = db
    http = make_http(FakeApis())
    statements = Statements(engine)
    lib = libraries.create(session, "Big")
    path = write_bib(tmp_path / "big.bib", range(N))
    report = import_into(cfg, session, http, lib, path, "new", use_s2=False)
    first = statements.take()
    assert report.added == N and count(session, Paper) == N
    assert first < 4 * N, first  # a few statements per entry, no query per pair of entries

    report = import_into(cfg, session, http, lib, path, "replace", use_s2=False)
    again = statements.take()
    assert (report.added, report.updated, report.removed) == (0, N, 0)
    assert again < 2 * N, again

    # an updated export: a third of the papers left, new ones arrived
    write_bib(path, range(N // 3, N + N // 3))
    report = import_into(cfg, session, http, lib, path, "replace", use_s2=False)
    assert (report.added, report.removed) == (N // 3, N // 3)
    session.expire_all()
    assert count(session, Paper) == N + N // 3
    assert count(session, LibraryPaper) == libraries.size(session, lib) == N
    in_library = session.exec(select(func.count()).where(Paper.in_library)).one()
    assert in_library == N
    gone = session.exec(select(Paper).where(Paper.doi == "10.1000/x0")).one()
    assert not gone.in_library and gone.folders == [] and gone.tags == []
    libraries.delete(session, lib)
    session.commit()
    assert count(session, LibraryPaper) == 0
    assert session.exec(select(func.count()).where(Paper.in_library)).one() == 0


def test_messy_duplicates_in_a_large_file(db, make_http, tmp_path):
    cfg, session, _ = db
    records = []
    for n in range(N):
        records.append({"id": f"k{n}", "title": f"Paper {n}", "DOI": f"10.1/D{n}"})
        if n % 10 == 0:  # the same paper again, DOI written differently
            records.append(
                {"id": f"copy{n}", "title": f"Paper {n}", "URL": f"https://doi.org/10.1/d{n}"}
            )
    path = tmp_path / "zotero.json"
    path.write_text(json.dumps(records))
    lib = libraries.create(session, "Zotero")
    report = import_into(cfg, session, make_http(FakeApis()), lib, path, "new", use_s2=False)
    assert report.entries == N + N // 10 and len(report.duplicates) == N // 10
    assert count(session, Paper) == N == libraries.size(session, lib)


def test_large_exports_round_trip(db, make_http, tmp_path):
    cfg, session, _ = db
    lib = libraries.create(session, "Big")
    path = write_bib(tmp_path / "big.bib", range(N))
    import_into(cfg, session, make_http(FakeApis()), lib, path, "new", use_s2=False)
    items = export.library_items(session, lib)
    for fmt, suffix in (("bibtex", "bib"), ("ris", "ris")):
        out = tmp_path / f"out.{suffix}"
        out.write_text(export.render(items, fmt))
        _, back = read_references(out)
        assert len(back) == N and {e.doi for e in back} == {f"10.1000/x{n}" for n in range(N)}


def test_large_progress_files_merge_without_a_query_per_paper(db, make_http, tmp_path):
    cfg, session, _ = db
    lib = libraries.create(session, "Big")
    path = write_bib(tmp_path / "big.bib", range(N))
    import_into(cfg, session, make_http(FakeApis()), lib, path, "new", use_s2=False)
    data = export_progress(session)
    other = init_db(tmp_path / "other.sqlite")
    statements = Statements(other)
    with Session(other) as fresh:
        report = import_progress(fresh, cfg, data)
        assert (
            report.papers_created == N
            and libraries.size(fresh, fresh.exec(select(libraries.Library)).one()) == N
        )
        statements.take()
        report = import_progress(fresh, cfg, data)  # idempotent
        assert report.papers_created == 0 and report.papers_matched == N
        assert count(fresh, Paper) == N and count(fresh, LibraryPaper) == N
    assert statements.take() < 2 * N
    other.dispose()
