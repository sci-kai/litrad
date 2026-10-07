"""Semantic Scholar: SPECTER2 embeddings, TLDRs and influential citations."""

from dataclasses import dataclass
from datetime import timedelta

from litresearch.sources.http import HttpClient

API = "https://api.semanticscholar.org/graph/v1"
# the batch endpoint accepts 500 ids, but responses with embeddings get large
BATCH = 100
FIELDS = "paperId,externalIds,tldr,citationCount,influentialCitationCount,embedding.specter_v2"
EMBEDDING_MODEL = "specter_v2"


@dataclass
class S2Paper:
    s2_id: str
    tldr: str | None
    citation_count: int | None
    influential_citation_count: int | None
    embedding: list[float] | None


def parse_paper(raw: dict) -> S2Paper:
    embedding = raw.get("embedding") or {}
    return S2Paper(
        s2_id=raw["paperId"],
        tldr=(raw.get("tldr") or {}).get("text"),
        citation_count=raw.get("citationCount"),
        influential_citation_count=raw.get("influentialCitationCount"),
        embedding=embedding.get("vector") if embedding.get("model", "").startswith("specter") else None,
    )


class SemanticScholar:
    def __init__(self, http: HttpClient, api_key: str | None = None):
        self.http = http
        self.headers = {"x-api-key": api_key} if api_key else None

    def papers(self, ids: list[str]) -> dict[str, S2Paper]:
        """Batch look-up; ``ids`` use S2 syntax, e.g. ``DOI:10.1/x`` or ``PMID:123``.

        Returns a mapping from the requested id to the paper; unknown ids are
        left out.
        """
        result = {}
        ids = list(dict.fromkeys(ids))
        for start in range(0, len(ids), BATCH):
            chunk = ids[start : start + BATCH]
            body = self.http.post(
                f"{API}/paper/batch",
                {"ids": chunk},
                params={"fields": FIELDS},
                headers=self.headers,
                max_age=timedelta(days=30),
            )
            for requested, raw in zip(chunk, body):
                if raw and raw.get("paperId"):
                    result[requested] = parse_paper(raw)
        return result
