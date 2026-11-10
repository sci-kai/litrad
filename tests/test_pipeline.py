import numpy as np
import pytest
from sqlmodel import Session, select

from conftest import SAMPLE_BIB, FakeApis
from litrad.db import Embedding, Paper, init_db, load_embedding
from litrad.ingest.pipeline import ingest_library


@pytest.fixture
def engine(cfg):
    engine = init_db(cfg.paths.database)
    yield engine
    engine.dispose()


def run(cfg, engine, http, **kwargs):
    with Session(engine) as session:
        return ingest_library(cfg, session, http, **kwargs)


def papers(engine) -> dict[str, Paper]:
    with Session(engine) as session:
        return {p.bibtex_key or p.doi: p for p in session.exec(select(Paper)).all()}


def test_full_ingest(cfg, engine, fake_apis, make_http):
    report = run(cfg, engine, make_http(fake_apis))
    assert report.entries == report.added == 7
    assert report.doi_from_bibtex == 3
    assert report.doi_from_crossref == 1  # Johnson, via the prefix rule
    assert report.openalex_enriched == 4  # 3 by DOI + Hofmann by PMID
    assert report.s2_enriched == report.embeddings == 3
    assert sorted(report.unresolved) == ["Olivero2018-ol", "Turajlic2017-zn"]
    assert report.errors == []

    stored = papers(engine)
    barkley = stored["Barkley2022-ri"]
    assert barkley.in_library and barkley.resolved_via == "bibtex"
    assert barkley.openalex_id and barkley.fwci > 0 and barkley.citation_percentile > 90
    assert barkley.venue_mean_citedness > 1
    assert barkley.s2_id and barkley.influential_citation_count is not None
    assert barkley.folders[0] == "Projects/Example A/Interactions"
    # BibTeX abstract wins over OpenAlex
    assert barkley.abstract.startswith("Transcriptional heterogeneity")

    johnson = stored["Johnson_undated-wl"]
    assert johnson.doi == "10.1038/d41586-019-01709-2" and johnson.resolved_via == "crossref"

    hofmann = stored["Hofmann2003-af"]
    assert hofmann.doi is None and hofmann.openalex_id and hofmann.citation_count
    # OpenAlex fills the missing abstract
    assert hofmann.abstract

    with Session(engine) as session:
        vectors = session.exec(select(Embedding)).all()
    assert {v.model for v in vectors} == {"specter_v2"}
    assert load_embedding(vectors[0]).dtype == np.float32 and len(load_embedding(vectors[0])) == 4


def test_rerun_is_idempotent_and_cached(cfg, engine, fake_apis, make_http):
    http = make_http(fake_apis)
    run(cfg, engine, http)
    calls = len(fake_apis.requests)
    report = run(cfg, engine, http)
    assert report.added == 0 and report.updated == 7 and report.removed == 0
    assert report.doi_from_crossref == 0  # already resolved
    assert len(fake_apis.requests) == calls  # everything came from the cache
    with Session(engine) as session:
        assert len(session.exec(select(Paper)).all()) == 7
        assert len(session.exec(select(Embedding)).all()) == 3


def test_removed_entries_leave_library(cfg, engine, fake_apis, make_http, tmp_path):
    run(cfg, engine, make_http(fake_apis))
    text = SAMPLE_BIB.read_text(encoding="utf-8")
    start = text.index("@INCOLLECTION")
    smaller = tmp_path / "smaller.bib"
    smaller.write_text(text[:start], encoding="utf-8")
    report = run(
        cfg.model_copy(update={"paths": cfg.paths.model_copy(update={"bibtex": smaller})}),
        engine,
        make_http(fake_apis),
    )
    assert report.removed == 1
    olivero = papers(engine)["Olivero2018-ol"]
    assert not olivero.in_library  # kept in the DB (reading history), but flagged


def test_recommended_paper_is_adopted_when_added_to_library(cfg, engine, fake_apis, make_http):
    with Session(engine) as session:
        session.add(Paper(title="Recommended earlier", doi="10.1038/s41588-022-01141-9"))
        session.commit()
    report = run(cfg, engine, make_http(fake_apis))
    # added = new to this library; the stored paper is reused, not duplicated
    assert report.added == 7 and report.updated == 0
    with Session(engine) as session:
        rows = session.exec(select(Paper).where(Paper.doi == "10.1038/s41588-022-01141-9")).all()
    assert len(rows) == 1 and rows[0].bibtex_key == "Barkley2022-ri" and rows[0].in_library


def test_duplicate_dois_and_keys_are_reported(cfg, engine, fake_apis, make_http, tmp_path):
    text = SAMPLE_BIB.read_text(encoding="utf-8")
    duplicate = (
        text
        + """
@ARTICLE{Barkley2022-copy,
  title = "Cancer cell states recur (duplicate import)",
  author = "Barkley, Dalia",
  year = 2022,
  doi = "10.1038/S41588-022-01141-9"
}

@ARTICLE{Cheung2016-vl,
  title = "Same key twice",
  author = "Someone, A",
  year = 2020
}
"""
    )
    path = tmp_path / "dup.bib"
    path.write_text(duplicate, encoding="utf-8")
    report = run(
        cfg.model_copy(update={"paths": cfg.paths.model_copy(update={"bibtex": path})}),
        engine,
        make_http(fake_apis),
    )
    # the second entry of the same paper is skipped, not stored as a second paper
    assert report.duplicates == ["Barkley2022-copy (10.1038/s41588-022-01141-9)"]
    assert any("duplicate citation key Cheung2016-vl" in e for e in report.errors)
    assert papers(engine)["Cheung2016-vl"].title.startswith("A collective route")
    assert "Barkley2022-copy" not in papers(engine)
    assert papers(engine)["Barkley2022-ri"].title.startswith("Cancer cell states recur across")


def test_s2_failure_is_not_fatal(cfg, engine, make_http):
    apis = FakeApis(s2_status=429)
    report = run(cfg, engine, make_http(apis, max_retries=1))
    assert report.openalex_enriched == 4
    assert report.embeddings == 0
    assert any("Semantic Scholar" in e and "API key" in e for e in report.errors)


def test_sources_can_be_disabled(cfg, engine, fake_apis, make_http):
    cfg = cfg.model_copy(update={"sources": cfg.sources.model_copy(update={"openalex": False})})
    report = run(cfg, engine, make_http(fake_apis), use_s2=False)
    assert report.openalex_enriched == report.s2_enriched == 0
    assert fake_apis.count("api.openalex.org") == fake_apis.count("api.semanticscholar.org") == 0


def test_report_lines_mention_warnings(cfg, engine, make_http):
    report = run(cfg, engine, make_http(FakeApis(s2_status=503), max_retries=0))
    text = "\n".join(report.lines())
    assert "Library:               My library (BibTeX)" in text
    assert "Entries:               7" in text and "WARNING: Semantic Scholar" in text


@pytest.mark.parametrize(
    "down, message",
    [("api.crossref.org", "Crossref"), ("api.openalex.org", "OpenAlex")],
)
def test_outage_of_one_source_only_warns(cfg, engine, make_http, down, message):
    report = run(cfg, engine, make_http(FakeApis(down=(down,)), max_retries=0))
    assert any(e.startswith(message) for e in report.errors)
    assert report.entries == report.added == 7
    # the other sources still ran
    assert report.embeddings == 3
    assert papers(engine)["Barkley2022-ri"].in_library


def test_title_and_year_do_not_merge_papers_with_different_pmids(engine):
    from litrad.db import Library
    from litrad.ingest.bibtex import BibEntry
    from litrad.ingest.pipeline import IngestReport, sync_library

    with Session(engine) as session:
        library = Library(name="L")
        session.add(library)
        session.commit()
        first = [BibEntry("a", "article", "Editorial", year=2020, pmid="111")]
        sync_library(session, library, first, IngestReport())
        session.commit()
        both = first + [BibEntry("b", "article", "Editorial", year=2020, pmid="222")]
        sync_library(session, library, both, IngestReport())
        session.commit()
        assert sorted(p.pmid for p in session.exec(select(Paper)).all()) == ["111", "222"]
        # without a PMID, title and year still find the paper
        sync_library(
            session,
            library,
            [BibEntry("c", "article", "Editorial", year=2020)],
            IngestReport(),
            replace=False,
        )
        session.commit()
        assert len(session.exec(select(Paper)).all()) == 2
