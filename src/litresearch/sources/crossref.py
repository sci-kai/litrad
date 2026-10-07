"""Crossref: resolve a DOI from title, first author and year."""

import re
import unicodedata
from datetime import timedelta
from difflib import SequenceMatcher

from litresearch.sources.http import HttpClient

API = "https://api.crossref.org/works"

# record types that describe something *about* a paper, not the paper itself
SKIP_TYPES = {"peer-review", "dataset", "component", "grant", "other"}
MIN_TITLE_SIMILARITY = 0.9
MIN_PREFIX_WORDS = 4


def normalize_title(title: str) -> str:
    text = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def title_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalize_title(a), normalize_title(b)).ratio()


def _surname(author: str) -> str:
    return normalize_title(author.split(",")[0])


def is_match(item: dict, title: str, first_author: str | None, year: int | None) -> bool:
    if item.get("type") in SKIP_TYPES:
        return False
    item_title = " ".join(item.get("title") or [])
    issued = (item.get("issued") or {}).get("date-parts") or [[None]]
    item_year = issued[0][0] if issued and issued[0] else None
    if year and item_year and abs(item_year - year) > 1:
        return False
    families = [normalize_title(a.get("family", "")) for a in item.get("author") or []]
    author_known = bool(first_author and families)
    if author_known and _surname(first_author) not in families:
        return False
    if title_similarity(item_title, title) >= MIN_TITLE_SIMILARITY:
        return True
    # truncated library titles: accept a prefix match, but only when the
    # first author confirms it and the title is not trivially short
    ours, theirs = normalize_title(title), normalize_title(item_title)
    return author_known and len(ours.split()) >= MIN_PREFIX_WORDS and theirs.startswith(ours + " ")


def resolve_doi(
    http: HttpClient,
    title: str,
    first_author: str | None = None,
    year: int | None = None,
    contact_email: str | None = None,
) -> str | None:
    """Return the DOI of the best verified Crossref match, or None."""
    query = " ".join(filter(None, [title, first_author and _surname(first_author), year and str(year)]))
    params = {
        "query.bibliographic": query,
        "rows": 5,
        "select": "DOI,title,author,issued,type",
    }
    if contact_email:
        params["mailto"] = contact_email
    body = http.get(API, params=params, max_age=timedelta(days=365))
    for item in body.get("message", {}).get("items", []):
        if is_match(item, title, first_author, year):
            return item["DOI"].lower()
    return None
