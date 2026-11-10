import pytest
from sqlmodel import Session, select

from litrad.db import Paper, init_db
from litrad.ingest.bibtex import web_url
from litrad.papers import paper_link, upsert_work
from litrad.sources.openalex import Work


def work(**fields):
    defaults = dict(
        openalex_id="W1", doi="10.1000/x", pmid="123", title="T", year=2024, pub_date=None
    )
    return Work(**(defaults | fields))


def test_upsert_work_matches_a_pubmed_paper_without_doi_by_pmid(tmp_path):
    engine = init_db(tmp_path / "db.sqlite")
    with Session(engine) as session:
        session.add(Paper(title="From PubMed", pmid="123"))
        session.commit()
        paper = upsert_work(session, work(cited_by_count=7))
        session.commit()
        assert len(session.exec(select(Paper)).all()) == 1
        assert paper.doi == "10.1000/x" and paper.citation_count == 7
    engine.dispose()


def test_upsert_work_does_not_merge_a_pmid_match_with_another_doi(tmp_path):
    engine = init_db(tmp_path / "db.sqlite")
    with Session(engine) as session:
        session.add(Paper(title="Other", pmid="123", doi="10.1000/other"))
        session.commit()
        upsert_work(session, work())
        session.commit()
        assert len(session.exec(select(Paper)).all()) == 2
    engine.dispose()


UNSAFE_URLS = [
    "javascript:alert(1)",
    "JavaScript:alert(1)",
    " javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "vbscript:msgbox(1)",
    "/relative/path",
    "//evil.example/x",
    "ftp://example.org/x",
    "http://",
    "https:\\\\evil.example",
    "java\nscript:alert(1)",
]


@pytest.mark.parametrize("url", UNSAFE_URLS)
def test_paper_link_only_for_web_urls(url):
    assert paper_link(Paper(title="t", url=url)) == ""
    assert web_url(url) is None


@pytest.mark.parametrize("url", ["https://example.org/a?b=1#c", "HTTP://Example.org/x"])
def test_paper_link_keeps_web_urls(url):
    assert paper_link(Paper(title="t", url=url)) == url


def test_unsafe_urls_are_not_stored_from_files(tmp_path):
    from litrad.ingest.bibtex import parse_bibtex
    from litrad.ingest.formats import parse_csl_json, parse_ris

    bib = tmp_path / "x.bib"
    bib.write_text(
        "@article{a, title={A}, url={javascript:alert(1)}}\n"
        "@article{b, title={B}, url={https://example.org/b}}\n"
    )
    assert [e.url for e in parse_bibtex(bib)] == [None, "https://example.org/b"]
    ris = "TY  - JOUR\nTI  - R\nUR  - data:text/html,x\nER  - \n"
    csl = '[{"id": "c", "type": "article", "title": "C", "URL": "vbscript:x"}]'
    assert [e.url for e in parse_ris(ris)] == [None]
    assert [e.url for e in parse_csl_json(csl)] == [None]


def test_unsafe_url_from_an_archive_is_dropped():
    from litrad.backup import _value

    assert _value("url", "javascript:alert(1)") is None
    assert _value("url", "https://example.org") == "https://example.org"
