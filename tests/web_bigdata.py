"""Fill a profile database with years of data (for the use-case and load tests)."""

import random
from datetime import datetime, timedelta

from sqlalchemy import insert
from sqlmodel import Session, select

from litresearch.db import (
    Category,
    IngestRun,
    Library,
    LibraryPaper,
    Paper,
    QueueItem,
    Reading,
    ReadStatus,
    Recommendation,
    RecommendationBatch,
    StatusEvent,
    XpEvent,
    init_db,
)

KINDS = ["read", "streak", "frog", "classic", "hot", "rating", "reflection", "penalty"]
LEVELS = [ReadStatus.abstract, ReadStatus.skimmed, ReadStatus.read, ReadStatus.thorough]


def populate(
    database,
    n_papers: int = 10_000,
    n_libraries: int = 12,
    n_batches: int = 300,
    n_readings: int = 3000,
    n_xp: int = 6000,
    n_queue: int = 20,
    seed: int = 1,
) -> dict:
    """Bulk-insert a large history into ``database``; returns some ids for the tests."""
    rng = random.Random(seed)
    now = datetime.now().replace(microsecond=0)
    start = now - timedelta(days=3 * 365)
    engine = init_db(database)
    try:
        with Session(engine) as session:
            session.execute(
                insert(Paper),
                [
                    {
                        "title": f"Paper {i} on clonal evolution <b>{rng.random():.4f}</b>",
                        "doi": f"10.5555/big.{i}",
                        "bibtex_key": f"Big{i}",
                        "authors": [f"Author{i % 97}, A.", "Second, B."],
                        "year": 1990 + i % 36,
                        "venue": f"Journal {i % 50}",
                        "abstract": "tumour lineage tracing " * 5,
                        "in_library": i < n_papers * 0.8,
                        "folders": [f"folder{i % 30}"],
                        "tags": [f"tag{i % 7}"],
                        "topics": [],
                        "created_at": start,
                        "updated_at": start,
                    }
                    for i in range(n_papers)
                ],
            )
            ids = list(session.exec(select(Paper.id).order_by(Paper.id)).all())
            session.execute(
                insert(Library),
                [
                    {
                        "name": f"Library {n}",
                        "description": "",
                        "kind": "imported",
                        "use_for_recommendations": True,
                        "created_at": start,
                        "updated_at": start,
                    }
                    for n in range(n_libraries)
                ],
            )
            libs = list(session.exec(select(Library.id).order_by(Library.id)).all())
            links = []
            for index, pid in enumerate(ids[: int(n_papers * 0.8)]):
                links.append(
                    {
                        "library_id": libs[0],
                        "paper_id": pid,
                        "citation_key": f"Big{index}",
                        "folders": [f"folder{index % 30}"],
                        "tags": [f"tag{index % 7}"],
                        "added_at": start,
                    }
                )
                if index % 4 == 0:
                    links.append(
                        {
                            "library_id": libs[1 + index % (n_libraries - 1)],
                            "paper_id": pid,
                            "citation_key": f"Big{index}",
                            "folders": [],
                            "tags": [],
                            "added_at": start,
                        }
                    )
            session.execute(insert(LibraryPaper), links)
            session.execute(
                insert(IngestRun),
                [
                    {
                        "at": start + timedelta(days=d),
                        "library_id": libs[d % n_libraries],
                        "mode": "replace",
                        "format": "bibtex",
                        "source": "upload",
                        "file_name": "x.bib",
                        "entries": 100,
                        "warnings": [],
                    }
                    for d in range(200)
                ],
            )
            batches = []
            for b in range(n_batches):
                created = start + timedelta(days=b * 3)
                batches.append(
                    {
                        "created_at": created,
                        "week_start": (created - timedelta(days=created.weekday())).date(),
                        "n_candidates": 400,
                        "warnings": [],
                    }
                )
            session.execute(insert(RecommendationBatch), batches)
            batch_ids = list(session.exec(select(RecommendationBatch.id)).all())
            session.execute(
                insert(Recommendation),
                [
                    {
                        "batch_id": bid,
                        "paper_id": ids[(bid * 5 + r) % n_papers],
                        "rank": r + 1,
                        "category": Category.open,
                        "score": 0.5,
                        "breakdown": {
                            "kind": "fresh",
                            "relevance": 0.5,
                            "impact": 0.4,
                            "timeliness": 0.3,
                            "score": 0.5,
                            "topics": [],
                            "sources": ["openalex"],
                            "evidence": {},
                        },
                        "reason": "",
                    }
                    for bid in batch_ids
                    for r in range(5)
                ],
            )
            read_ids = rng.sample(ids, n_readings)
            readings, events = [], []
            for pid in read_ids:
                at = start + timedelta(seconds=rng.randrange(int((now - start).total_seconds())))
                status = rng.choice(LEVELS + [ReadStatus.dismissed])
                readings.append(
                    {
                        "paper_id": pid,
                        "status": status,
                        "rating": rng.choice([None, 3, 4, 5]),
                        "notes": "",
                        "updated_at": at,
                    }
                )
                events.append({"paper_id": pid, "status": status, "was_queue_top": False, "at": at})
            session.execute(insert(Reading), readings)
            session.execute(insert(StatusEvent), events)
            session.execute(
                insert(XpEvent),
                [
                    {
                        "paper_id": rng.choice(read_ids),
                        "points": rng.choice([10, 20, 40, -15]),
                        "kind": rng.choice(KINDS),
                        "reason": f"reason {n}",
                        "at": start
                        + timedelta(seconds=rng.randrange(int((now - start).total_seconds()))),
                    }
                    for n in range(n_xp)
                ],
            )
            unread = [pid for pid in ids if pid not in set(read_ids)]
            queue = unread[:n_queue]
            session.execute(
                insert(QueueItem),
                [
                    {"paper_id": pid, "position": n + 1, "source": "manual", "added_at": now}
                    for n, pid in enumerate(queue)
                ],
            )
            session.commit()
    finally:
        engine.dispose()
    return {"papers": ids, "libraries": libs, "batches": batch_ids, "queue": queue}
