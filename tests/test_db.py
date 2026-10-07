from sqlmodel import Session, select

from litresearch.db import Paper, ReadStatus, StatusEvent, init_db, set_status


def test_status_change_is_logged(tmp_path):
    engine = init_db(tmp_path / "test.sqlite")
    with Session(engine) as session:
        paper = Paper(title="A test paper", doi="10.1000/test", authors=["Doe J"])
        session.add(paper)
        session.commit()
        set_status(session, paper.id, ReadStatus.abstract)
        reading = set_status(session, paper.id, ReadStatus.thorough)
        assert reading.status == ReadStatus.thorough
        events = session.exec(select(StatusEvent)).all()
        assert [e.status for e in events] == [ReadStatus.abstract, ReadStatus.thorough]
