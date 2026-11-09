"""Europe PMC: search PubMed, PubMed Central and preprints in one free API.

No key is needed. Queries use Europe PMC's syntax, e.g.
``AUTH:"Swanton C" AND JOURNAL:"Nature" AND PUB_YEAR:[2020 TO 2024]``
(see https://europepmc.org/searchsyntax). Results with ``resultType=core``
include abstracts, citation counts and open-access links.
"""

import html
import re
from datetime import date, timedelta

from litrad.sources.hits import Hit
from litrad.sources.http import HttpClient

API = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
# sort orders of the search form → Europe PMC's sort parameter
SORT = {"relevance": None, "newest": "P_PDATE_D desc", "cited": "CITED desc"}


def _date(text: str | None) -> date | None:
    try:
        return date.fromisoformat(text) if text else None
    except ValueError:
        return None


def clean(text: str | None) -> str | None:
    """Plain text from Europe PMC's markup (``<i>``, ``<h4>``, entities)."""
    if not text:
        return None
    # tags arrive as markup or entity-encoded (&lt;sub&gt;): unescape first
    text = html.unescape(text)
    text = re.sub(r"</?(h\d|p|br)\b[^>]*>", " ", text)
    text = re.sub(r"</?[a-zA-Z][a-zA-Z0-9]*(\s[^<>]*)?/?>", "", text)  # not "p<0.05"
    return re.sub(r"\s+", " ", text).strip() or None


def _year(raw) -> int | None:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def parse_result(raw: dict) -> Hit:
    """A Europe PMC result record as a ``Hit`` (tolerant of missing fields)."""
    journal = ((raw.get("journalInfo") or {}).get("journal") or {}).get("title")
    authors = [
        a.get("fullName")
        for a in (raw.get("authorList") or {}).get("author", [])
        if a.get("fullName")
    ]
    if not authors and raw.get("authorString"):
        authors = [a.strip() for a in raw["authorString"].rstrip(".").split(",") if a.strip()]
    pub_types = (raw.get("pubTypeList") or {}).get("pubType") or []
    if raw.get("pubType"):  # the "lite" result type has a flat string
        pub_types = [raw["pubType"]]
    return Hit(
        source="europepmc",
        title=(clean(raw.get("title")) or "(untitled)").rstrip("."),
        pmid=raw.get("pmid"),
        pmcid=raw.get("pmcid"),
        doi=(raw.get("doi") or "").lower() or None,
        authors=authors,
        journal=journal or raw.get("journalTitle"),
        year=_year(raw.get("pubYear")),
        pub_date=_date(raw.get("firstPublicationDate")),
        abstract=clean(raw.get("abstractText")),
        cited_by=raw.get("citedByCount"),
        open_access=raw.get("isOpenAccess") == "Y",
        pub_types=list(pub_types),
        found_in=["europepmc"],
    )


class EuropePMC:
    def __init__(self, http: HttpClient):
        self.http = http

    def search(self, query: str, limit: int = 25, sort: str = "relevance") -> tuple[int, list[Hit]]:
        """Total number of matches and the first ``limit`` hits."""
        params = {
            "query": query,
            "format": "json",
            "resultType": "core",
            "pageSize": max(1, min(limit, 100)),
        }
        if SORT.get(sort):
            params["sort"] = SORT[sort]
        data = self.http.get(API, params=params, max_age=timedelta(days=1))
        results = (data.get("resultList") or {}).get("result") or []
        return int(data.get("hitCount") or 0), [parse_result(r) for r in results]

    def lookup(self, doi: str | None = None, pmid: str | None = None, pmcid: str | None = None):
        """One paper by DOI, PMID or PMC id (with abstract), or None."""
        if pmid:
            query = f"EXT_ID:{pmid} AND SRC:MED"
        elif pmcid:
            query = f"PMCID:{pmcid.upper()}"
        elif doi:
            query = f'DOI:"{doi}"'
        else:
            return None
        _, hits = self.search(query, limit=1)
        return hits[0] if hits else None
