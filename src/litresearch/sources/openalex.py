"""OpenAlex: citation metrics, identifiers and venue statistics.

Look-ups are batched (50 ids per request), which costs one credit per request
in the metered OpenAlex API, instead of ten for a search.
"""

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from litresearch.sources.http import HttpClient

API = "https://api.openalex.org"
BATCH = 50

WORK_FIELDS = ",".join(
    [
        "id",
        "doi",
        "ids",
        "title",
        "publication_year",
        "publication_date",
        "type",
        "authorships",
        "primary_location",
        "cited_by_count",
        "fwci",
        "citation_normalized_percentile",
        "abstract_inverted_index",
        "topics",
    ]
)


@dataclass
class Work:
    openalex_id: str
    doi: str | None
    pmid: str | None
    title: str | None
    year: int | None
    pub_date: date | None
    authors: list[str] = field(default_factory=list)
    abstract: str | None = None
    venue: str | None = None
    venue_id: str | None = None
    cited_by_count: int | None = None
    fwci: float | None = None
    # 0-100, field- and year-normalised citation percentile
    citation_percentile: float | None = None
    topics: list[str] = field(default_factory=list)
    type: str | None = None


def short_id(url: str | None) -> str | None:
    return url.rsplit("/", 1)[-1] if url else None


def strip_doi(url: str | None) -> str | None:
    if not url:
        return None
    return url.lower().removeprefix("https://doi.org/")


def rebuild_abstract(index: dict | None) -> str | None:
    """OpenAlex ships abstracts as an inverted index {word: [positions]}."""
    if not index:
        return None
    words = sorted((pos, word) for word, positions in index.items() for pos in positions)
    return " ".join(word for _, word in words) or None


def parse_work(raw: dict) -> Work:
    location = raw.get("primary_location") or {}
    source = location.get("source") or {}
    percentile = (raw.get("citation_normalized_percentile") or {}).get("value")
    pub_date = raw.get("publication_date")
    return Work(
        openalex_id=short_id(raw["id"]),
        doi=strip_doi(raw.get("doi")),
        pmid=short_id((raw.get("ids") or {}).get("pmid")),
        title=raw.get("title"),
        year=raw.get("publication_year"),
        pub_date=date.fromisoformat(pub_date) if pub_date else None,
        authors=[
            a["author"]["display_name"]
            for a in raw.get("authorships") or []
            if (a.get("author") or {}).get("display_name")
        ],
        abstract=rebuild_abstract(raw.get("abstract_inverted_index")),
        venue=source.get("display_name"),
        venue_id=short_id(source.get("id")),
        cited_by_count=raw.get("cited_by_count"),
        fwci=raw.get("fwci"),
        citation_percentile=round(percentile * 100, 2) if percentile is not None else None,
        topics=[t["display_name"] for t in raw.get("topics") or [] if t.get("display_name")],
        type=raw.get("type"),
    )


def parse_all(raws):
    """Parse records, skipping malformed ones (no id or broken fields)."""
    for raw in raws:
        if not isinstance(raw, dict) or not raw.get("id"):
            continue
        try:
            yield parse_work(raw)
        except (KeyError, TypeError, ValueError):
            continue


class OpenAlex:
    def __init__(
        self, http: HttpClient, api_key: str | None = None, contact_email: str | None = None
    ):
        self.http = http
        self.params = {}
        if api_key:
            self.params["api_key"] = api_key
        if contact_email:
            self.params["mailto"] = contact_email

    def _filter_batches(self, endpoint: str, filter_name: str, values: list[str], select: str):
        values = sorted(set(values))
        for start in range(0, len(values), BATCH):
            chunk = values[start : start + BATCH]
            params = {
                **self.params,
                "filter": f"{filter_name}:{'|'.join(chunk)}",
                "per-page": BATCH,
                "select": select,
            }
            body = self.http.get(f"{API}/{endpoint}", params=params, max_age=timedelta(days=14))
            yield from body.get("results", [])

    def works_by_doi(self, dois: list[str]) -> dict[str, Work]:
        works = parse_all(self._filter_batches("works", "doi", dois, WORK_FIELDS))
        return {work.doi: work for work in works if work.doi}

    def works_by_pmid(self, pmids: list[str]) -> dict[str, Work]:
        works = parse_all(self._filter_batches("works", "pmid", pmids, WORK_FIELDS))
        return {work.pmid: work for work in works if work.pmid}

    def venue_mean_citedness(self, source_ids: list[str]) -> dict[str, float]:
        """2-year mean citedness per source: an open proxy for the impact factor."""
        result = {}
        for raw in self._filter_batches("sources", "openalex", source_ids, "id,summary_stats"):
            value = (raw.get("summary_stats") or {}).get("2yr_mean_citedness")
            if value is not None:
                result[short_id(raw["id"])] = value
        return result

    def search_works(self, query: str, filters: dict[str, str], per_page: int = 25) -> list[Work]:
        """Title/abstract search, sorted by relevance.

        A search costs ten times a look-up in the metered API, so callers keep
        the number of queries small; results are cached for a day.
        """
        # commas and colons separate filters in the OpenAlex syntax
        cleaned = re.sub(r"[,:|]", " ", query).strip()
        filter_text = ",".join(
            [f"title_and_abstract.search:{cleaned}"] + [f"{k}:{v}" for k, v in filters.items()]
        )
        params = {
            **self.params,
            "filter": filter_text,
            "sort": "relevance_score:desc",
            "per-page": per_page,
            "select": WORK_FIELDS,
        }
        body = self.http.get(f"{API}/works", params=params, max_age=timedelta(days=1))
        return list(parse_all(body.get("results", [])))
