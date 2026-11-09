"""PubMed through NCBI's E-utilities (esearch + esummary, JSON).

Free; without a key NCBI allows 3 requests per second, with a free key
(``api.ncbi_key``) 10. Requests carry ``tool=litrad`` and, unlike NCBI's
recommendation, no e-mail address: no personal data is sent. Queries use
PubMed's syntax, e.g. ``Swanton C[au] AND Nature[journal] AND 2020:2024[dp]``.
Abstracts are not part of esummary; they are taken from Europe PMC when a
paper is added.
"""

import re
from datetime import date, timedelta

from litrad.sources.europepmc import clean
from litrad.sources.hits import Hit
from litrad.sources.http import HttpClient

API = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
SORT = {"relevance": "relevance", "newest": "pub_date", "cited": "relevance"}
MONTHS = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}


def _pub_date(raw: dict) -> date | None:
    """From ``sortpubdate`` ("2017/04/26 00:00") or ``pubdate`` ("2017 Apr 26")."""
    if match := re.match(r"(\d{4})/(\d{2})/(\d{2})", raw.get("sortpubdate") or ""):
        try:
            return date(*map(int, match.groups()))
        except ValueError:
            return None
    parts = (raw.get("pubdate") or "").split()
    try:
        year = int(parts[0])
        month = MONTHS.get(parts[1][:3].lower(), 1) if len(parts) > 1 else 1
        day = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
        return date(year, month, day)
    except (IndexError, ValueError):
        return None


def parse_summary(raw: dict) -> Hit:
    """An esummary record as a ``Hit``."""
    ids = {a.get("idtype"): a.get("value") for a in raw.get("articleids") or []}
    pub_date = _pub_date(raw)
    pmcid = ids.get("pmc")
    return Hit(
        source="pubmed",
        title=(clean(raw.get("title")) or "(untitled)").rstrip("."),
        pmid=str(raw.get("uid") or ids.get("pubmed") or "") or None,
        pmcid=pmcid if pmcid and pmcid.startswith("PMC") else None,
        doi=(ids.get("doi") or "").lower() or None,
        authors=[a["name"] for a in raw.get("authors") or [] if a.get("authtype") == "Author"],
        journal=raw.get("fulljournalname") or raw.get("source"),
        year=pub_date.year if pub_date else None,
        pub_date=pub_date,
        # a PMC copy is free to read; PubMed does not say more about licences
        open_access=True if pmcid else None,
        pub_types=list(raw.get("pubtype") or []),
        found_in=["pubmed"],
    )


class PubMed:
    def __init__(self, http: HttpClient, api_key: str | None = None):
        self.http = http
        self.base = {"tool": "litrad", "retmode": "json"}
        if api_key:
            self.base["api_key"] = api_key

    def search(self, term: str, limit: int = 25, sort: str = "relevance") -> tuple[int, list[Hit]]:
        """Total number of matches and the first ``limit`` hits."""
        found = self.http.get(
            f"{API}/esearch.fcgi",
            params={
                **self.base,
                "db": "pubmed",
                "term": term,
                "retmax": max(1, min(limit, 100)),
                "sort": SORT.get(sort, "relevance"),
            },
            max_age=timedelta(days=1),
        ).get("esearchresult", {})
        ids = found.get("idlist") or []
        return int(found.get("count") or 0), self.summaries(ids)

    def summaries(self, pmids: list[str]) -> list[Hit]:
        """Records of the given PMIDs, in the given order."""
        if not pmids:
            return []
        result = self.http.get(
            f"{API}/esummary.fcgi",
            params={**self.base, "db": "pubmed", "id": ",".join(pmids)},
            max_age=timedelta(days=14),
        ).get("result", {})
        return [parse_summary(result[uid]) for uid in result.get("uids", []) if uid in result]
