import numpy as np
from sqlmodel import Session, select

from litresearch.db import (
    Embedding,
    Paper,
    ReadStatus,
    depth,
    init_db,
    load_embedding,
    store_embedding,
)


def test_depth_order():
    assert depth(None) == 0
    assert depth(ReadStatus.dismissed) == 0
    assert [depth(s) for s in ("abstract", "skimmed", "read", "thorough")] == [1, 2, 3, 4]


def test_embedding_round_trip_and_update(tmp_path):
    engine = init_db(tmp_path / "test.sqlite")
    with Session(engine) as session:
        paper = Paper(title="A test paper", doi="10.1000/test", authors=["Doe, J"])
        session.add(paper)
        session.commit()
        store_embedding(session, paper.id, "m", [0.5, 1.5])
        session.commit()
        store_embedding(session, paper.id, "m", [1.0, 2.0])
        store_embedding(session, paper.id, "other", [3.0])
        session.commit()
        rows = session.exec(select(Embedding).order_by(Embedding.model)).all()
        assert [r.model for r in rows] == ["m", "other"]
        assert np.allclose(load_embedding(rows[0]), [1.0, 2.0])
        assert session.get(Paper, paper.id).authors == ["Doe, J"]
    engine.dispose()
