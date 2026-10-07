"""Smoke test against the real APIs; run with ``pytest --network``."""

import pytest
from sqlmodel import Session

from litresearch.cli import make_http
from litresearch.db import init_db
from litresearch.ingest.pipeline import ingest_library


@pytest.mark.network
def test_live_ingest_of_sample(cfg):
    http = make_http(cfg)
    engine = init_db(cfg.paths.database)
    try:
        with Session(engine) as session:
            report = ingest_library(cfg, session, http)
    finally:
        http.close()
        engine.dispose()
    assert report.entries == 7
    assert report.doi_from_crossref >= 1
    assert report.openalex_enriched >= 4
    # Semantic Scholar may throttle anonymous clients; that must only warn
    assert report.embeddings >= 3 or any("Semantic Scholar" in e for e in report.errors)


@pytest.mark.network
def test_live_recommendations(cfg):
    from litresearch.config import Config
    from litresearch.recommend.engine import batch_items, build_recommendations

    cfg = Config.model_validate(
        {
            **cfg.model_dump(mode="json"),
            "topics": [{"name": "evo", "queries": ["clonal evolution cancer"]}],
            "selection": {"results_per_query": 10},
        }
    )
    http = make_http(cfg)
    engine = init_db(cfg.paths.database)
    try:
        with Session(engine) as session:
            ingest_library(cfg, session, http)
            batch = build_recommendations(cfg, session, http)
            items = batch_items(session, batch)
            assert batch.n_candidates > 0
            assert 1 <= len(items) <= 5
            assert all(not paper.in_library for _, paper in items)
            assert all(rec.breakdown["kind"] in ("fresh", "landmark") for rec, _ in items)
            assert not any("OpenAlex" in w for w in batch.warnings)
    finally:
        http.close()
        engine.dispose()
