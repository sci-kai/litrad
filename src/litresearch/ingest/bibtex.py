"""Parse Paperpile BibTeX exports into plain records."""

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import bibtexparser
from bibtexparser import middlewares as mw
from bibtexparser.model import DuplicateBlockKeyBlock

MONTHS = {
    name: number
    for number, name in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"],
        start=1,
    )
}

DOI_PREFIX = re.compile(r"^(https?://(dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE)


@dataclass
class BibEntry:
    key: str
    entry_type: str
    title: str
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    pub_date: date | None = None
    journal: str | None = None
    abstract: str | None = None
    doi: str | None = None
    pmid: str | None = None
    url: str | None = None
    # Paperpile folders (hierarchical, "/"-separated) and flat labels/keywords
    folders: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


def clean_text(value: str | None) -> str | None:
    """Collapse the line wrapping and stray braces of BibTeX values."""
    if value is None:
        return None
    value = re.sub(r"\s+", " ", str(value).replace("\xa0", " ")).strip()
    value = value.replace("{", "").replace("}", "")
    return value or None


def normalize_doi(value: str | None) -> str | None:
    """Return a bare, lower-case DOI (DOIs are case-insensitive)."""
    value = clean_text(value)
    if not value:
        return None
    value = DOI_PREFIX.sub("", value).strip()
    return value.lower() if value.startswith("10.") else None


def parse_year(value: str | None) -> int | None:
    match = re.search(r"\b(1[89]\d\d|20\d\d)\b", str(value or ""))
    return int(match.group(1)) if match else None


def parse_month_day(value: str | None) -> tuple[int | None, int | None]:
    """Parse Paperpile month values like ``apr``, ``5`` or ``"5\\xa0" # apr``.

    Returns ``(month, day)``; either may be None.
    """
    if not value:
        return None, None
    text = str(value).replace("\xa0", " ").lower()
    name = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", text)
    if name:
        day = re.search(r"\b(\d{1,2})\b", text)
        day_value = int(day.group(1)) if day else None
        return MONTHS[name.group(1)], day_value if day_value and 1 <= day_value <= 31 else None
    number = re.fullmatch(r"\s*\"?(\d{1,2})\"?\s*", text)
    if number and 1 <= int(number.group(1)) <= 12:
        return int(number.group(1)), None
    return None, None


def make_date(year: int | None, month: int | None, day: int | None) -> date | None:
    if not (year and month):
        return None
    for candidate_day in (day or 1, 1):
        try:
            return date(year, month, candidate_day)
        except ValueError:
            continue
    return None


def split_keywords(
    value: str | None, ignore: set[str] = frozenset()
) -> tuple[list[str], list[str]]:
    """Split Paperpile ``keywords`` into folder paths and flat tags.

    Items are separated by ``;``. Folder paths contain ``/``; note that folder
    names may themselves contain `` / `` (e.g. ``03 Biology / Medicine``), which
    is fine because we only keep the path as a string.
    """
    folders, tags = [], []
    text = clean_text(value) or ""
    for item in (part.strip() for part in text.split(";")):
        if not item or item.lower() in ignore:
            continue
        target = folders if "/" in item else tags
        if item not in target:
            target.append(item)
    return folders, tags


def _format_author(name) -> str:
    last = " ".join(name.von + name.last)
    first = " ".join(name.first)
    text = f"{last}, {first}" if first else last
    if name.jr:
        text += f", {' '.join(name.jr)}"
    return clean_text(text) or ""


def _field(entry, name: str) -> str | None:
    item = entry.fields_dict.get(name)
    return None if item is None else item.value


def parse_bibtex(
    path: Path, ignore_labels: list[str] = (), duplicate_keys: list[str] | None = None
) -> list[BibEntry]:
    """Parse a BibTeX file.

    Repeated citation keys keep their first entry and are appended to
    ``duplicate_keys``. Any other unparsable block raises ValueError, so that a
    broken export never makes papers look as if they had left the library.
    """
    library = bibtexparser.parse_file(
        str(path),
        append_middleware=[
            mw.LatexDecodingMiddleware(),
            mw.SeparateCoAuthors(),
            mw.SplitNameParts(),
        ],
    )
    failed = []
    for block in library.failed_blocks:
        if isinstance(block, DuplicateBlockKeyBlock) and duplicate_keys is not None:
            duplicate_keys.append(block.key)
        else:
            failed.append(block)
    if failed:
        details = "; ".join(str(block.raw)[:80] for block in failed)
        raise ValueError(f"{len(failed)} BibTeX block(s) failed to parse: {details}")

    ignore = {label.lower() for label in ignore_labels}
    entries = []
    for entry in library.entries:
        year = parse_year(_field(entry, "year"))
        month, day = parse_month_day(_field(entry, "month"))
        folders, tags = split_keywords(_field(entry, "keywords"), ignore)
        url = clean_text(_field(entry, "url"))
        pmid = clean_text(_field(entry, "pmid"))
        entries.append(
            BibEntry(
                key=entry.key,
                entry_type=entry.entry_type.lower(),
                title=clean_text(_field(entry, "title")) or entry.key,
                authors=[_format_author(name) for name in (_field(entry, "author") or [])],
                year=year,
                pub_date=make_date(year, month, day),
                journal=clean_text(_field(entry, "journal") or _field(entry, "booktitle")),
                abstract=clean_text(_field(entry, "abstract")),
                doi=normalize_doi(_field(entry, "doi")) or normalize_doi(url),
                pmid=pmid if pmid and pmid.isdigit() else None,
                url=url,
                folders=folders,
                tags=tags,
            )
        )
    return entries
