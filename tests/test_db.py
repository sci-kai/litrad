import numpy as np
from sqlmodel import Session, select

from litresearch.db import (
    Embedding,
    Paper,
    ReadStatus,
    RecommendationBatch,
    add_missing_columns,
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


def test_missing_columns_are_added(tmp_path):
    import sqlite3

    path = tmp_path / "old.sqlite"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE recommendationbatch (id INTEGER PRIMARY KEY, created_at DATETIME, "
        "week_start DATE, n_candidates INTEGER, warnings JSON)"
    )
    connection.execute(
        "INSERT INTO recommendationbatch VALUES (1, '2026-10-05', '2026-10-05', 3, '[]')"
    )
    connection.commit()
    connection.close()
    engine = init_db(path)  # creates missing tables, adds missing columns
    with Session(engine) as session:
        batch = session.get(RecommendationBatch, 1)
        assert batch.n_candidates == 3 and batch.funnel is None and batch.params is None
    assert add_missing_columns(engine) == []
    engine.dispose()
