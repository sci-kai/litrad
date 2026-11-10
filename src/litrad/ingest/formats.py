"""Reference-manager exports: BibTeX, RIS and CSL-JSON, read into ``BibEntry`` records.

- **BibTeX** (``.bib``): Paperpile, Zotero (also Better BibTeX), Mendeley,
  JabRef, Citavi. Tested with Paperpile's export.
- **RIS** (``.ris``): EndNote, Zotero, Mendeley, Citavi, RefWorks, most
  journal websites. Supported, not yet tested with real exports.
- **CSL-JSON** (``.json``): Zotero, Paperpile, Pandoc. Supported, not yet
  tested with real exports.

All formats yield the same fields; folders and labels come from BibTeX
``keywords`` (Paperpile writes folders there), RIS ``KW`` lines and CSL
``keyword``. Every entry needs a citation key; formats without one (RIS
without ``ID``) get a generated key such as ``ris-12``.
"""

import json
import re
from pathlib import Path

from litrad.ingest.bibtex import (
    BibEntry,
    clean_text,
    make_date,
    normalize_doi,
    parse_bibtex,
    parse_year,
    split_keywords,
    web_url,
)

FORMATS = {"bibtex": "BibTeX", "ris": "RIS", "csl-json": "CSL-JSON"}
EXTENSIONS = {".bib": "bibtex", ".bibtex": "bibtex", ".ris": "ris", ".json": "csl-json"}
RIS_TAG = re.compile(r"^([A-Z][A-Z0-9])  - ?(.*)$")
# RIS reference types and CSL types → BibTeX-like entry types (only used for display)
RIS_TYPES = {
    "JOUR": "article",
    "BOOK": "book",
    "CHAP": "incollection",
    "CONF": "inproceedings",
    "CPAPER": "inproceedings",
    "THES": "phdthesis",
    "RPRT": "techreport",
}
CSL_TYPES = {
    "article-journal": "article",
    "book": "book",
    "chapter": "incollection",
    "paper-conference": "inproceedings",
    "thesis": "phdthesis",
    "report": "techreport",
    "article": "article",
    "posted-content": "article",
}


def detect_format(path: Path, text: str | None = None) -> str:
    """The format of a file, from its extension or else its content."""
    by_extension = EXTENSIONS.get(path.suffix.lower())
    if by_extension:
        return by_extension
    head = text if text is not None else path.read_text(encoding="utf-8", errors="replace")
    head = head.lstrip("﻿ \n\r\t")[:2000]
    if head.startswith("[") or head.startswith("{"):
        return "csl-json"
    if re.search(r"^TY  - ", head, re.MULTILINE):
        return "ris"
    return "bibtex"


def read_references(
    path: Path, ignore_labels=(), duplicate_keys: list[str] | None = None, fmt: str | None = None
) -> tuple[str, list[BibEntry]]:
    """Parse an export in any supported format; returns the format and the entries.

    Raises ValueError for content that cannot be parsed (nothing is imported
    then), OSError/UnicodeDecodeError for unreadable files.
    """
    fmt = fmt or detect_format(path)
    if fmt == "bibtex":
        return fmt, parse_bibtex(path, ignore_labels, duplicate_keys)
    text = path.read_text(encoding="utf-8-sig")
    ignore = {label.lower() for label in ignore_labels}
    if fmt == "ris":
        return fmt, parse_ris(text, ignore, duplicate_keys)
    if fmt == "csl-json":
        return fmt, parse_csl_json(text, ignore, duplicate_keys)
    raise ValueError(f"Unknown format {fmt}")


def _keywords(items: list[str], ignore: set[str]) -> tuple[list[str], list[str]]:
    return split_keywords(";".join(items), ignore)


def _first(record: dict, *tags: str) -> str | None:
    """The first value of the first tag a RIS record has."""
    for tag in tags:
        if record.get(tag):
            return clean_text(record[tag][0])
    return None


def _unique_key(key: str, seen: set[str], duplicate_keys: list[str] | None) -> str | None:
    if key in seen:
        if duplicate_keys is not None:
            duplicate_keys.append(key)
        return None
    seen.add(key)
    return key


def _ris_author(name: str) -> str:
    return clean_text(name) or ""


def parse_ris(text: str, ignore: set[str] = frozenset(), duplicate_keys=None) -> list[BibEntry]:
    """RIS: one ``TAG  - value`` per line, records from ``TY`` to ``ER``."""
    records, current, last_tag = [], None, None
    for raw in text.splitlines():
        line = raw.rstrip()
        match = RIS_TAG.match(line)
        if not match:
            if current is not None and last_tag and line.strip():  # continuation line
                current[last_tag][-1] += " " + line.strip()
            continue
        tag, value = match.group(1), match.group(2).strip()
        if tag == "TY":
            current, last_tag = {"TY": [value]}, "TY"
        elif tag == "ER":
            if current is not None:
                records.append(current)
            current, last_tag = None, None
        elif current is not None:
            current.setdefault(tag, []).append(value)
            last_tag = tag
    if current is not None:
        raise ValueError("The last RIS record has no 'ER  -' end line (truncated file?)")
    if not records and text.strip():
        raise ValueError("No RIS records found (each record starts with 'TY  - ')")

    entries, seen = [], set()
    for number, record in enumerate(records, start=1):
        given = _first(record, "ID")
        key = _unique_key(given or f"ris-{number}", seen, duplicate_keys)
        if key is None:
            continue
        date_text = _first(record, "PY", "Y1", "DA") or ""
        parts = [int(p) for p in re.findall(r"\d+", date_text)[:3]]
        year = parse_year(str(parts[0])) if parts else None
        doi = normalize_doi(_first(record, "DO"))
        url = _first(record, "UR", "L2")
        pmid = None
        for value in record.get("AN", []) + record.get("C2", []):
            if value.strip().isdigit():
                pmid = value.strip()
                break
        folders, tags = _keywords(record.get("KW", []), ignore)
        entries.append(
            BibEntry(
                key=key,
                entry_type=RIS_TYPES.get(_first(record, "TY") or "", "misc"),
                title=_first(record, "TI", "T1", "CT", "BT") or key,
                authors=[_ris_author(a) for a in record.get("AU", []) + record.get("A1", [])],
                year=year,
                pub_date=make_date(year, *(parts[1:3] + [None, None])[:2]) if year else None,
                journal=_first(record, "JO", "T2", "JF", "JA"),
                abstract=_first(record, "AB", "N2"),
                doi=doi or normalize_doi(url),
                pmid=pmid,
                url=web_url(url),
                folders=folders,
                tags=tags,
                generated_key=not given,
            )
        )
    return entries


def _csl_name(name: dict) -> str:
    if name.get("literal"):
        return clean_text(name["literal"]) or ""
    family, given = name.get("family", ""), name.get("given", "")
    return clean_text(f"{family}, {given}" if given else family) or ""


def parse_csl_json(
    text: str, ignore: set[str] = frozenset(), duplicate_keys=None
) -> list[BibEntry]:
    """CSL-JSON: a list of items (or ``{"items": [...]}``)."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"Not valid JSON: {error}") from error
    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list) or any(not isinstance(i, dict) for i in items):
        raise ValueError("Expected a CSL-JSON list of items")
    entries, seen = [], set()
    for number, item in enumerate(items, start=1):
        given = str(item.get("id") or "").strip()
        key = _unique_key(given or f"csl-{number}", seen, duplicate_keys)
        if key is None:
            continue
        parts = ((item.get("issued") or {}).get("date-parts") or [[]])[0] or []
        parts = [int(p) for p in parts if str(p).isdigit()][:3]
        year = parts[0] if parts else None
        keywords = item.get("keyword") or ""
        folders, tags = _keywords(
            keywords if isinstance(keywords, list) else re.split(r"[;,]", keywords), ignore
        )
        pmid = str(item.get("PMID") or "").strip()
        entries.append(
            BibEntry(
                key=key,
                entry_type=CSL_TYPES.get(item.get("type", ""), "misc"),
                title=clean_text(item.get("title")) or key,
                authors=[_csl_name(n) for n in item.get("author") or [] if isinstance(n, dict)],
                year=year,
                pub_date=make_date(year, *(parts[1:3] + [None, None])[:2]) if year else None,
                journal=clean_text(item.get("container-title")),
                abstract=clean_text(item.get("abstract")),
                doi=normalize_doi(item.get("DOI")) or normalize_doi(item.get("URL")),
                pmid=pmid if pmid.isdigit() else None,
                url=web_url(clean_text(item.get("URL"))),
                folders=folders,
                tags=tags,
                generated_key=not given,
            )
        )
    return entries
