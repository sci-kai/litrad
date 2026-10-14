"""Checks for a reference-manager export: is everything there that litresearch uses?

Reference managers let you choose which fields an export contains. The tool
works with less, but every missing field costs something (see ``FIELDS``).
What each manager's export should contain is documented in
docs/documentation.md ("What an export must contain").
"""

from dataclasses import dataclass, field
from pathlib import Path

from litresearch.ingest.bibtex import BibEntry
from litresearch.ingest.formats import FORMATS, detect_format, read_references


@dataclass(frozen=True)
class FieldRule:
    name: str
    label: str
    why: str
    # below this share a warning is shown (0 = informational only)
    warn_below: float
    required: bool = False


FIELDS = (
    FieldRule("title", "Title", "needed for everything", 0.99, required=True),
    FieldRule("authors", "Authors", "shown in lists; used to verify DOI look-ups", 0.9),
    FieldRule("year", "Year", "decides whether a paper is fresh or a landmark", 0.9),
    FieldRule(
        "doi",
        "DOI",
        "the key to metrics, embeddings and recommendations; entries "
        "without DOI need a slower, less reliable title look-up",
        0.8,
    ),
    FieldRule("abstract", "Abstract", "text similarity fallback and the paper page", 0.5),
    FieldRule(
        "folders",
        "Folders / labels (keywords)",
        "show where a paper sits in your reference manager",
        0.3,
    ),
    FieldRule("journal", "Journal", "shown in lists", 0.5),
    FieldRule("pmid", "PMID", "optional second identifier", 0.0),
    FieldRule("url", "URL", "optional link; DOIs inside URLs are picked up too", 0.0),
)

INSTRUCTIONS = [
    "Paperpile: Export → BibTeX (or automatically: profile picture → Workflows and "
    "Integrations → BibTeX export to Google Drive). Include DOI, PMID, abstract, URL and "
    "keywords (your folders and labels).",
    "Zotero, Mendeley, JabRef, Citavi: export as BibTeX (or RIS / CSL-JSON) with abstracts "
    "and keywords; EndNote: export as RIS.",
    "PDFs are never needed or read. Details: docs/documentation.md, 'What an export must contain'.",
]


@dataclass
class Finding:
    level: str  # error | warning | info | ok
    message: str


@dataclass
class BibCheck:
    entries: int = 0
    format: str | None = None
    coverage: dict[str, float] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    duplicate_keys: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(f.level == "error" for f in self.findings)

    def to_dict(self) -> dict:
        return {
            "entries": self.entries,
            "format": self.format,
            "coverage": self.coverage,
            "findings": [{"level": f.level, "message": f.message} for f in self.findings],
        }


def _present(entry: BibEntry, name: str) -> bool:
    if name == "folders":
        return bool(entry.folders or entry.tags)
    value = getattr(entry, name)
    return bool(value)


def check_entries(entries: list[BibEntry], duplicate_keys=()) -> BibCheck:
    """Check parsed entries: required fields, coverage of useful fields, duplicates."""
    result = BibCheck(entries=len(entries), duplicate_keys=list(duplicate_keys))
    if not entries:
        result.findings.append(Finding("error", "The file contains no references."))
        return result
    for rule in FIELDS:
        share = sum(_present(e, rule.name) for e in entries) / len(entries)
        result.coverage[rule.name] = round(share, 3)
        percent = f"{share:.0%}"
        if rule.required and share < rule.warn_below:
            result.findings.append(
                Finding("error", f"{rule.label}: only {percent} of entries have one ({rule.why}).")
            )
        elif share == 0 and rule.warn_below > 0:
            result.findings.append(
                Finding(
                    "warning",
                    f"{rule.label}: missing in all entries. Include it in the export ({rule.why}).",
                )
            )
        elif share < rule.warn_below:
            result.findings.append(
                Finding("warning", f"{rule.label}: only {percent} of entries ({rule.why}).")
            )
    if duplicate_keys:
        result.findings.append(
            Finding(
                "warning",
                f"{len(duplicate_keys)} duplicate citation key(s): "
                f"{', '.join(list(duplicate_keys)[:5])}; only the first entry is used.",
            )
        )
    if result.ok and not any(f.level == "warning" for f in result.findings):
        result.findings.append(Finding("ok", f"All {len(entries)} entries look good."))
    return result


def check_file(path: Path, ignore_labels=(), fmt: str | None = None) -> BibCheck:
    """Parse and check an export; parse problems become an error finding."""
    duplicates: list[str] = []
    try:
        fmt = fmt or detect_format(path)
        fmt, entries = read_references(path, ignore_labels, duplicates, fmt)
    except (OSError, UnicodeDecodeError) as error:
        return BibCheck(findings=[Finding("error", f"Cannot read the file: {error}")])
    except ValueError as error:
        name = FORMATS.get(fmt or "", "a supported format")
        return BibCheck(findings=[Finding("error", f"Not valid {name}: {error}")])
    result = check_entries(entries, duplicates)
    result.format = fmt
    return result


# the original name, used before other formats were supported
check_bibtex = check_file
