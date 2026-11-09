"""The open data format: version, JSON Schemas and consistency checks.

``DATA_FORMAT_VERSION`` versions the files litresearch writes for you to
keep: the "export everything" archive (``archive.py``) and its JSON files.
It is independent of the app version and of the database schema version
(``migrations.SCHEMA_VERSION``). It increases when the content of the
archive changes in any way (a new file or field, or a changed meaning).
Readers accept every version up to their own and refuse newer ones; from
1.0 on a new version only adds (optional fields, new files), so every 1.x
release reads the archives of all earlier releases (forward compatible:
newer reads older, not the other way round).

Before anything is imported, the data are checked:

1. against the JSON Schema of each file (``schemas/``, draft 2020-12);
2. for internal consistency (``check_data``): every reference to a paper
   resolves, no duplicates, dates parse, badges exist, XP totals add up.

Problems name the file and record, e.g. ``readings.json[12]: paper p77 not
in papers.json``. Unknown fields are allowed and ignored (reported as notes).
The same checks apply to progress and profile files (``backup.py``), with
locations inside that file (``readings[12]``).
"""

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from functools import cache
from importlib import resources

DATA_FORMAT_VERSION = "1"
ARCHIVE_FORMAT = "litresearch-export"
PAPER_KEY = "p{}"
STATUSES = ("abstract", "skimmed", "read", "thorough", "dismissed")
DEPTHS = {"abstract": 1, "skimmed": 2, "read": 3, "thorough": 4}
MAX_LISTED = 200  # problems shown in full; the rest are counted


@dataclass
class Problem:
    """One finding. ``record`` identifies what to skip in a partial import."""

    location: str
    message: str
    # error: blocks the import (or skips the record with --partial) · note: information
    level: str = "error"
    # (section, index[, sub-index]) or ("papers", key) or ("file", name); None = whole data
    record: tuple | None = None

    def __str__(self) -> str:
        return f"{self.location}: {self.message}"


@dataclass
class CheckReport:
    errors: list[Problem] = field(default_factory=list)
    notes: list[Problem] = field(default_factory=list)
    skipped: list[Problem] = field(default_factory=list)
    # problems that even --partial cannot get around (e.g. a newer format)
    fatal: bool = False

    @property
    def ok(self) -> bool:
        return not self.errors and not self.fatal

    def add(self, problem: Problem) -> None:
        (self.errors if problem.level == "error" else self.notes).append(problem)

    def extend(self, problems) -> None:
        for problem in problems:
            self.add(problem)

    def lines(self, limit: int = MAX_LISTED) -> list[str]:
        out = []
        for title, problems in (
            ("Problems", self.errors),
            ("Skipped (--partial)", self.skipped),
            ("Notes", self.notes),
        ):
            if not problems:
                continue
            out.append(f"{title} ({len(problems)}):")
            out += [f"  {p}" for p in problems[:limit]]
            if len(problems) > limit:
                out.append(f"  ... and {len(problems) - limit} more")
        return out


# --- JSON Schema ---------------------------------------------------------------------------


@cache
def load_schema(name: str) -> dict:
    """A schema shipped with the package, e.g. ``readings`` → readings.schema.json."""
    text = resources.files("litresearch.schemas").joinpath(f"{name}.schema.json").read_text("utf-8")
    return json.loads(text)


def schema_names() -> list[str]:
    folder = resources.files("litresearch.schemas")
    return sorted(
        p.name.removesuffix(".schema.json")
        for p in folder.iterdir()
        if p.name.endswith(".schema.json")
    )


@cache
def _validator(name: str):
    from jsonschema import Draft202012Validator

    schema = load_schema(name)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _path_text(path) -> str:
    out = ""
    for part in path:
        out += f"[{part}]" if isinstance(part, int) else f".{part}"
    return out


def validate_schema(name: str, data, label: str, list_key: str | None = None) -> list[Problem]:
    """Schema errors with locations like ``readings.json[12].rating``.

    ``list_key``: the file's main list; its items are shown as ``file[i]`` and an
    error inside one item only affects that record (``record = (label, i)``).
    """
    problems = []
    for error in sorted(_validator(name).iter_errors(data), key=lambda e: list(e.absolute_path)):
        path = list(error.absolute_path)
        record = ("file", label)
        if list_key and len(path) >= 2 and path[0] == list_key and isinstance(path[1], int):
            record = (label, path[1])
            path = path[1:]
            location = f"{label or list_key}[{path[0]}]{_path_text(path[1:])}"
        else:
            location = (label + _path_text(path)).lstrip(".") or "(file)"
        problems.append(Problem(location, _schema_message(error), "error", record))
    return problems


def _schema_message(error) -> str:
    message = error.message
    if len(message) > 200:
        message = message[:200] + "..."
    return message


def unknown_fields(name: str, data, label: str, list_key: str | None = None) -> list[Problem]:
    """Fields the schema does not describe: allowed and ignored, reported once per field."""
    schema = load_schema(name)
    found: Counter = Counter()

    def resolve(node):
        while isinstance(node, dict) and "$ref" in node:
            node = schema["$defs"][node["$ref"].rsplit("/", 1)[-1]]
        return node

    def walk(node, value, path):
        node = resolve(node)
        if not isinstance(node, dict):
            return
        for option in node.get("anyOf", []):
            option = resolve(option)
            if isinstance(value, dict) and option.get("type") in ("object", ["object", "null"]):
                walk(option, value, path)
        if isinstance(value, dict) and "properties" in node:
            known = node["properties"]
            for key, item in value.items():
                if key in known:
                    walk(known[key], item, f"{path}.{key}")
                elif "additionalProperties" not in node:
                    found[f"{path}.{key}"] += 1
        elif isinstance(value, dict) and isinstance(node.get("additionalProperties"), dict):
            for item in value.values():
                walk(node["additionalProperties"], item, f"{path}.*")
        elif isinstance(value, list) and "items" in node:
            for item in value:
                walk(node["items"], item, f"{path}[]")

    walk(schema, data, "")
    if list_key:
        found = Counter(
            {
                (p.replace(f".{list_key}[]", "[]", 1) if p.startswith(f".{list_key}[]") else p): c
                for p, c in found.items()
            }
        )
    return [
        Problem(
            (label + path).lstrip(".") or "(file)",
            f"unknown field ignored ({count} record{'s' if count != 1 else ''})",
            "note",
        )
        for path, count in sorted(found.items())
    ]


# --- consistency ---------------------------------------------------------------------------


@dataclass
class Naming:
    """How locations are written: archive files or sections of a progress file."""

    sections: dict[str, str]
    papers: str = "papers"
    # paper key → its index in the papers list (archives); None: ``papers.<key>``
    paper_index: dict[str, int] | None = None

    def at(self, section: str, index: int | None = None, sub: str = "") -> str:
        name = self.sections.get(section, section)
        return name + (f"[{index}]" if index is not None else "") + sub

    def paper(self, key: str) -> str:
        if self.paper_index is not None and key in self.paper_index:
            return f"{self.papers}[{self.paper_index[key]}]"
        return f"{self.papers}.{key}"


PROGRESS_NAMING = {
    "libraries": "libraries",
    "readings": "readings",
    "status_events": "status_events",
    "queue": "queue",
    "xp_events": "xp_events",
    "achievements": "achievements",
    "recommendations": "recommendations",
    "example_choices": "example_choices",
    "profile": "profile",
    "xp_total": "xp_total",
}


def parse_datetime(value) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _date_problem(value, kind: str = "datetime") -> str | None:
    if value is None:
        return None
    try:
        if kind == "date":
            date.fromisoformat(value)
        else:
            datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return f"'{value}' is not a valid ISO 8601 {'date' if kind == 'date' else 'date and time'}"
    return None


def check_data(
    data: dict,
    naming: Naming,
    papers_file: str = "papers",
    badge_codes: set[str] | None = None,
) -> list[Problem]:
    """Consistency of normalized data (the shape of a progress file).

    ``data["papers"]`` maps keys to paper records; list sections may contain
    ``None`` for records already dropped (indexes keep their meaning).
    """
    from litresearch.rules import BADGE_CATALOG, BADGE_CODES, GameRules

    problems: list[Problem] = []
    papers = data.get("papers") or {}
    badge_codes = BADGE_CODES if badge_codes is None else badge_codes

    def error(location, message, record):
        problems.append(Problem(location, message, "error", record))

    def note(location, message):
        problems.append(Problem(location, message, "note"))

    def ref(section, index, key, sub="", record=None, optional=False):
        record = record or (section, index)
        if key is None and optional:
            return True
        if key not in papers:
            error(naming.at(section, index, sub), f"paper {key} not in {papers_file}", record)
            return False
        return True

    def when(section, index, item, name, kind="datetime", sub="", record=None, required=False):
        value = item.get(name)
        if value is None and required:
            error(naming.at(section, index, sub), f"{name} is missing", record or (section, index))
            return
        message = _date_problem(value, kind)
        if message:
            error(naming.at(section, index, f"{sub}.{name}"), message, record or (section, index))

    # papers: dates, unique DOIs
    dois: dict[str, str] = {}
    for key, info in papers.items():
        if info is None:
            continue
        for name, kind in (
            ("pub_date", "date"),
            ("enriched_at", "datetime"),
            ("created_at", "datetime"),
            ("updated_at", "datetime"),
        ):
            message = _date_problem(info.get(name), kind)
            if message:
                error(f"{naming.paper(key)}.{name}", message, ("papers", key))
        doi = (info.get("doi") or "").lower()
        if doi:
            if doi in dois:
                error(
                    naming.paper(key),
                    f"DOI {info['doi']} is also the DOI of paper {dois[doi]}",
                    ("papers", key),
                )
            else:
                dois[doi] = key

    profile = data.get("profile")
    rules = None
    if profile:
        try:
            rules = GameRules.model_validate(profile.get("rules") or {})
        except ValueError as exc:
            first = str(exc).splitlines()
            error(
                naming.at("profile", None, ".rules"),
                f"not valid game rules ({first[1] if len(first) > 1 else first[0]})",
                ("profile", None),
            )
        else:
            stated = profile.get("fingerprint")
            if stated and stated != rules.fingerprint():
                # a note, not an error: the stored string is informative only, merges
                # and comparisons recompute it from the rules (stable across versions,
                # see rules.FINGERPRINT_FIELDS); a mismatch means it was edited
                note(
                    naming.at("profile", None, ".fingerprint"),
                    f"{stated} differs from the fingerprint of the rules here "
                    f"({rules.fingerprint()})",
                )
        when("profile", None, profile, "created_at", record=("profile", None))

    readings_of: set[str] = set()
    for i, item in enumerate(data.get("readings") or []):
        if item is None:
            continue
        ref("readings", i, item.get("paper"))
        when("readings", i, item, "updated_at")
        if item.get("paper") in readings_of:
            error(
                naming.at("readings", i),
                f"a second reading of paper {item.get('paper')}",
                ("readings", i),
            )
        readings_of.add(item.get("paper"))

    events: Counter = Counter()
    deepest: dict[str, int] = {}
    for i, item in enumerate(data.get("status_events") or []):
        if item is None:
            continue
        if ref("status_events", i, item.get("paper")):
            deepest[item["paper"]] = max(
                deepest.get(item["paper"], 0), DEPTHS.get(item.get("status"), 0)
            )
        when("status_events", i, item, "at", required=True)
        when("status_events", i, item, "read_on", kind="date")
        identity = (item.get("paper"), item.get("at"), item.get("status"))
        events[identity] += 1
        if events[identity] == 2:
            note(naming.at("status_events", i), "same paper, status and time as an earlier entry")

    seen_positions, queued = set(), set()
    for i, item in enumerate(data.get("queue") or []):
        if item is None:
            continue
        ref("queue", i, item.get("paper"))
        when("queue", i, item, "added_at")
        if item.get("paper") in queued:
            error(naming.at("queue", i), f"paper {item.get('paper')} is queued twice", ("queue", i))
        if item.get("position") in seen_positions:
            error(
                naming.at("queue", i, ".position"),
                f"position {item.get('position')} is used twice",
                ("queue", i),
            )
        queued.add(item.get("paper"))
        seen_positions.add(item.get("position"))

    names = {b.name: b.code for b in BADGE_CATALOG}
    unlocked: set[str] = set()
    for i, item in enumerate(data.get("achievements") or []):
        if item is None:
            continue
        code = item.get("code")
        if code not in badge_codes:
            error(
                naming.at("achievements", i, ".code"),
                f"unknown badge '{code}' (not in the profile's rules)",
                ("achievements", i),
            )
        if code in unlocked:
            error(
                naming.at("achievements", i), f"badge '{code}' is listed twice", ("achievements", i)
            )
        unlocked.add(code)
        when("achievements", i, item, "unlocked_at", required=True)

    total, balance_wrong = 0, False
    read_xp: dict[str, int] = {}
    for i, item in enumerate(data.get("xp_events") or []):
        if item is None:
            continue
        paper = item.get("paper")
        ref("xp_events", i, paper, optional=True)
        when("xp_events", i, item, "at", required=True)
        points = item.get("points")
        total += points if isinstance(points, int) else 0
        if "balance" in item and item["balance"] != total and not balance_wrong:
            # reported once: every later balance differs as well
            balance_wrong = True
            error(
                naming.at("xp_events", i, ".balance"),
                f"running balance {item['balance']} does not match the sum of points ({total})",
                ("xp_events", i),
            )
        kind = item.get("kind")
        if kind == "read" and paper in papers:
            read_xp[paper] = read_xp.get(paper, 0) + (points or 0)
            if paper not in deepest and paper not in readings_of:
                error(
                    naming.at("xp_events", i),
                    f"reading XP for paper {paper}, which has no reading",
                    ("xp_events", i),
                )
        if kind == "badge":
            name = str(item.get("reason") or "").removeprefix("Badge: ")
            code = names.get(name)
            if code is None:
                note(naming.at("xp_events", i), f"badge XP for an unknown badge '{name}'")
            elif data.get("achievements") is not None and code not in unlocked:
                error(
                    naming.at("xp_events", i),
                    f"badge XP for '{name}', but badge '{code}' is not unlocked",
                    ("xp_events", i),
                )
    stated = data.get("xp_total")
    if stated is not None and stated != total:
        error(
            naming.at("xp_total"),
            f"stated total {stated} XP, but the ledger adds up to {total} XP",
            ("xp_total", None),
        )
    if rules is not None:
        base = rules.base_xp()
        for paper, points in read_xp.items():
            level = deepest.get(paper, 0)
            expected = base[list(DEPTHS)[level - 1]] if level else 0
            if level and points != expected:
                note(
                    naming.paper(paper),
                    f"reading XP {points} differs from the {expected} XP of its deepest "
                    "level under the profile's rules (rules mixed in a merge?)",
                )

    batches = set()
    for i, batch in enumerate(data.get("recommendations") or []):
        if batch is None:
            continue
        when("recommendations", i, batch, "week_start", "date", required=True)
        when("recommendations", i, batch, "created_at", required=True)
        identity = (batch.get("week_start"), batch.get("created_at"))
        if identity in batches:
            error(
                naming.at("recommendations", i),
                "a second batch with the same week and time",
                ("recommendations", i),
            )
        batches.add(identity)
        for j, item in enumerate(batch.get("items") or []):
            if item is None:
                continue
            ref(
                "recommendations",
                i,
                item.get("paper"),
                f".items[{j}]",
                record=("recommendations", i, j),
            )

    choices = set()
    for i, item in enumerate(data.get("example_choices") or []):
        if item is None:
            continue
        ref("example_choices", i, item.get("paper"))
        when("example_choices", i, item, "at")
        identity = (item.get("paper"), item.get("kind"))
        if identity in choices:
            error(
                naming.at("example_choices", i),
                f"a second {item.get('kind')} choice for paper {item.get('paper')}",
                ("example_choices", i),
            )
        choices.add(identity)

    library_names = set()
    for i, library in enumerate(data.get("libraries") or []):
        if library is None:
            continue
        name = str(library.get("name") or "").lower()
        if name in library_names:
            error(
                naming.at("libraries", i, ".name"),
                f"a second library called '{library.get('name')}'",
                ("libraries", i),
            )
        library_names.add(name)
        when("libraries", i, library, "created_at")
        when("libraries", i, library, "updated_at")
        members = set()
        for j, member in enumerate(library.get("papers") or []):
            if member is None:
                continue
            record = ("libraries", i, j)
            ref("libraries", i, member.get("paper"), f".papers[{j}]", record=record)
            if member.get("paper") in members:
                error(
                    naming.at("libraries", i, f".papers[{j}]"),
                    f"paper {member.get('paper')} is listed twice in this library",
                    record,
                )
            members.add(member.get("paper"))
            when("libraries", i, member, "added_at", sub=f".papers[{j}]", record=record)
    return problems


def drop(data: dict, problems: list[Problem]) -> None:
    """Remove the records named by errors (``None`` keeps the other indexes valid)."""
    for problem in problems:
        record = problem.record
        if problem.level != "error" or not record:
            continue
        section = record[0]
        if section == "papers":
            data.get("papers", {}).pop(record[1], None)
        elif section == "profile":
            data["profile"] = None
        elif section == "xp_total":
            data.pop("xp_total", None)
        elif section in ("libraries", "recommendations") and len(record) == 3:
            container = data[section][record[1]]
            if container is not None:
                key = "papers" if section == "libraries" else "items"
                container[key][record[2]] = None
        elif section in data and isinstance(data[section], list):
            data[section][record[1]] = None


def compact(data: dict) -> dict:
    """The data without dropped records (for importing)."""
    out = dict(data)
    nested = {"libraries": "papers", "recommendations": "items"}
    for section in (
        "libraries",
        "readings",
        "status_events",
        "queue",
        "xp_events",
        "achievements",
        "recommendations",
        "example_choices",
    ):
        if isinstance(out.get(section), list):
            items = [dict(item) for item in out[section] if item is not None]
            if section in nested:
                for item in items:
                    item[nested[section]] = [
                        x for x in item.get(nested[section]) or [] if x is not None
                    ]
            out[section] = items
    return out


def check_progress(data: dict) -> CheckReport:
    """Schema and consistency of a progress or profile file (all its versions)."""
    report = CheckReport()
    report.extend(validate_schema("progress", data, ""))
    if report.errors:
        return report
    report.extend(unknown_fields("progress", data, ""))
    report.extend(check_data(data, Naming(PROGRESS_NAMING)))
    return report
