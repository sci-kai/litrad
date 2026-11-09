"""Exporting a library (or the reading queue) as BibTeX or RIS.

Both formats can be imported by every common reference manager: BibTeX by
Paperpile, Zotero, Mendeley, JabRef and Citavi; RIS also by EndNote.
CSL-JSON (Zotero, Pandoc, citeproc) is written for the "export everything"
archive. With
``with_status`` each entry carries your reading status and rating as extra
keywords (``litresearch-read``, ``litresearch-rating-4``), which become
labels/tags in the reference manager.
"""

import json
import re
from dataclasses import dataclass, field

from sqlmodel import Session, select

from litresearch.db import Library, LibraryPaper, Paper, Reading
from litresearch.libraries import members

FORMATS = {"bibtex": ("BibTeX", "bib"), "ris": ("RIS", "ris")}
# BibTeX entry type → CSL item type
CSL_TYPE = {
    "article": "article-journal",
    "book": "book",
    "incollection": "chapter",
    "inbook": "chapter",
    "inproceedings": "paper-conference",
    "phdthesis": "thesis",
    "techreport": "report",
}
# BibTeX entry type → RIS reference type
RIS_TYPE = {
    "article": "JOUR",
    "book": "BOOK",
    "incollection": "CHAP",
    "inbook": "CHAP",
    "inproceedings": "CPAPER",
    "phdthesis": "THES",
    "techreport": "RPRT",
}


@dataclass
class Item:
    paper: Paper
    key: str | None = None
    folders: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    reading: Reading | None = None


def library_items(session: Session, library: Library) -> list[Item]:
    readings = {r.paper_id: r for r in session.exec(select(Reading)).all()}
    return [
        Item(paper, link.citation_key, list(link.folders), list(link.tags), readings.get(paper.id))
        for paper, link in members(session, library)
    ]


def paper_items(session: Session, papers: list[Paper]) -> list[Item]:
    """Papers outside one library (e.g. the queue): keys and folders from any library."""
    readings = {r.paper_id: r for r in session.exec(select(Reading)).all()}
    items = []
    for paper in papers:
        link = session.exec(select(LibraryPaper).where(LibraryPaper.paper_id == paper.id)).first()
        items.append(
            Item(
                paper,
                (link.citation_key if link else None) or paper.bibtex_key,
                list(paper.folders),
                list(paper.tags),
                readings.get(paper.id),
            )
        )
    return items


def _keywords(item: Item, with_status: bool) -> list[str]:
    words = list(item.folders) + list(item.tags)
    if with_status and item.reading is not None:
        words.append(f"litresearch-{item.reading.status.value}")
        if item.reading.rating:
            words.append(f"litresearch-rating-{item.reading.rating}")
    return list(dict.fromkeys(words))


def _key(item: Item, used: set[str]) -> str:
    """The citation key: the library's, else surname + year (unique in the file)."""
    key = item.key
    if not key:
        surname = item.paper.authors[0].split(",")[0] if item.paper.authors else "paper"
        surname = re.sub(r"[^A-Za-z]", "", surname) or "paper"
        key = f"{surname}{item.paper.year or ''}"
    key = re.sub(r"[\s,{}\"#%'()=~\\\\]", "", key) or f"paper{item.paper.id}"
    candidate, number = key, 2
    while candidate in used:
        candidate, number = f"{key}-{number}", number + 1
    used.add(candidate)
    return candidate


def _braced(value: str) -> str:
    """A BibTeX field value; braces inside would break the file and are dropped."""
    return "{" + str(value).replace("{", "").replace("}", "").replace("\n", " ") + "}"


def to_bibtex(items: list[Item], with_status: bool = False) -> str:
    used: set[str] = set()
    blocks = []
    for item in items:
        paper = item.paper
        fields = [
            ("title", paper.title),
            ("author", " and ".join(paper.authors)),
            ("journal", paper.venue),
            ("year", paper.year or (paper.pub_date.year if paper.pub_date else None)),
            ("month", paper.pub_date.month if paper.pub_date else None),
            ("doi", paper.doi),
            ("pmid", paper.pmid),
            ("pmcid", paper.pmcid),
            ("url", paper.url),
            ("abstract", paper.abstract),
            ("keywords", ";".join(_keywords(item, with_status))),
        ]
        lines = [f"@{paper.entry_type or 'article'}{{{_key(item, used)},"]
        lines += [f"  {name} = {_braced(value)}," for name, value in fields if value]
        blocks.append("\n".join(lines) + "\n}")
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def to_ris(items: list[Item], with_status: bool = False) -> str:
    used: set[str] = set()
    records = []
    for item in items:
        paper = item.paper
        year = paper.year or (paper.pub_date.year if paper.pub_date else None)

        def line(tag: str, value) -> str:
            return f"{tag}  - {' '.join(str(value).split())}"

        lines = [line("TY", RIS_TYPE.get(paper.entry_type or "article", "JOUR"))]
        lines.append(line("ID", _key(item, used)))
        lines.append(line("TI", paper.title))
        lines += [line("AU", author) for author in paper.authors]
        if paper.pub_date:
            lines.append(line("PY", paper.pub_date.strftime("%Y/%m/%d")))
        elif year:
            lines.append(line("PY", year))
        for tag, value in (
            ("JO", paper.venue),
            ("DO", paper.doi),
            ("AN", paper.pmid),
            ("C2", paper.pmcid),
            ("UR", paper.url),
            ("AB", paper.abstract),
        ):
            if value:
                lines.append(line(tag, value))
        lines += [line("KW", word) for word in _keywords(item, with_status)]
        lines.append("ER  - ")
        records.append("\n".join(lines))
    return "\n\n".join(records) + ("\n" if records else "")


def _csl_name(author: str) -> dict:
    family, comma, given = author.partition(",")
    if comma and family.strip():
        return (
            {"family": family.strip(), "given": given.strip()}
            if given.strip()
            else {"family": family.strip()}
        )
    return {"literal": author.strip()}


def to_csl_json(items: list[Item], with_status: bool = False) -> str:
    """CSL-JSON (a list of items), the format of Zotero, Pandoc and citeproc."""
    used: set[str] = set()
    records = []
    for item in items:
        paper = item.paper
        record = {
            "id": _key(item, used),
            "type": CSL_TYPE.get(paper.entry_type or "article", "article-journal"),
            "title": paper.title,
        }
        if paper.authors:
            record["author"] = [_csl_name(a) for a in paper.authors if a.strip()]
        year = paper.year or (paper.pub_date.year if paper.pub_date else None)
        if paper.pub_date:
            record["issued"] = {
                "date-parts": [[paper.pub_date.year, paper.pub_date.month, paper.pub_date.day]]
            }
        elif year:
            record["issued"] = {"date-parts": [[year]]}
        for name, value in (
            ("container-title", paper.venue),
            ("DOI", paper.doi),
            ("PMID", paper.pmid),
            ("PMCID", paper.pmcid),
            ("URL", paper.url),
            ("abstract", paper.abstract),
        ):
            if value:
                record[name] = value
        keywords = _keywords(item, with_status)
        if keywords:
            record["keyword"] = ";".join(keywords)
        records.append(record)
    return json.dumps(records, indent=1, ensure_ascii=False) + "\n"


def render(items: list[Item], fmt: str, with_status: bool = False) -> str:
    if fmt == "bibtex":
        return to_bibtex(items, with_status)
    if fmt == "ris":
        return to_ris(items, with_status)
    raise ValueError(f"Unknown export format {fmt}; use {', '.join(FORMATS)}")


def file_name(name: str, fmt: str, day) -> str:
    """A file name without spaces: ``my-library_20261014.bib``."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "library"
    return f"{slug}_{day.strftime('%Y%m%d')}.{FORMATS[fmt][1]}"
