import numpy as np
from sqlmodel import Session, select

from litrad.db import (
    Embedding,
    Paper,
    ReadStatus,
    RecommendationBatch,
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


def additive_upgrade(path, monkeypatch):
    """A version-1 database upgraded by an additive step (``sync_schema``), as a 1.x
    release that adds tables, columns or indexes does it."""
    import sqlite3

    from litrad import migrations

    connection = sqlite3.connect(path)
    connection.execute("PRAGMA user_version = 1")
    connection.commit()
    connection.close()
    monkeypatch.setattr(migrations, "SCHEMA_VERSION", 2)
    step = migrations.Step(2, "additive", migrations.sync_schema)
    assert migrations.migrate(path, (step,)).to_version == 2


def test_missing_columns_are_added(tmp_path, monkeypatch):
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
    additive_upgrade(path, monkeypatch)  # creates missing tables, adds missing columns
    engine = init_db(path)
    with Session(engine) as session:
        batch = session.get(RecommendationBatch, 1)
        assert batch.n_candidates == 3 and batch.funnel is None and batch.params is None
    from litrad.migrations import sync_schema

    with engine.begin() as connection:
        assert sync_schema(connection) == []
    engine.dispose()


def test_missing_indexes_are_added_by_an_additive_step(tmp_path, monkeypatch):
    import sqlite3

    path = tmp_path / "old.sqlite"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE xpevent (id INTEGER PRIMARY KEY, paper_id INTEGER, points INTEGER, "
        "kind VARCHAR, reason VARCHAR, at DATETIME)"
    )
    connection.commit()
    connection.close()
    additive_upgrade(path, monkeypatch)
    connection = sqlite3.connect(path)
    indexed = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'xpevent'"
        )
    }
    connection.close()
    assert {"ix_xpevent_at", "ix_xpevent_kind", "ix_xpevent_paper_id"} <= indexed
    init_db(path).dispose()  # a second start finds them and does nothing


def test_json_columns_added_by_an_additive_step_default_to_empty(tmp_path, monkeypatch):
    import sqlite3

    path = tmp_path / "old.sqlite"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE recommendationbatch (id INTEGER PRIMARY KEY, created_at DATETIME, "
        "week_start DATE, n_candidates INTEGER)"
    )
    connection.execute("INSERT INTO recommendationbatch VALUES (1, '2026-10-05', '2026-10-05', 3)")
    connection.commit()
    connection.close()
    additive_upgrade(path, monkeypatch)
    engine = init_db(path)
    with Session(engine) as session:
        batch = session.get(RecommendationBatch, 1)
        assert batch.warnings == []  # not NULL
        assert batch.funnel is None  # no default in the model: stays NULL
    engine.dispose()


def test_models_do_not_use_builtin_default_factories():
    # pydantic inspects builtin factories at every construction (slow for many papers)
    from sqlmodel import SQLModel

    for model in SQLModel.__subclasses__():
        for name, field in model.model_fields.items():
            assert field.default_factory not in (list, dict), f"{model.__name__}.{name}"
    assert Paper().authors == [] and Paper().authors is not Paper().authors
