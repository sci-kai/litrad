from sqlmodel import Session, select

from litrad.db import Paper, init_db
from litrad.papers import upsert_work
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
