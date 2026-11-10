import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import platformdirs
import pytest

from litrad.config import Config
from litrad.sources.http import HttpClient, ResponseCache

DATA = Path(__file__).parent / "data"
SAMPLE_BIB = DATA / "paperpile_sample.bib"


def pytest_addoption(parser):
    parser.addoption(
        "--network", action="store_true", help="also run tests that call the real web APIs"
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--network"):
        return
    skip = pytest.mark.skip(reason="needs --network")
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip)


# --- time bounds that do not depend on machine load ---------------------------------------
#
# Performance tests bound the work first (number of queries, rows loaded). Where a
# wall-clock bound is still useful, ``within`` scales it by how slow this machine is
# right now: a fixed calibration task is timed before and after the measured block,
# so a busy or slower machine gets a proportionally larger budget.

# seconds for ``_calibration`` on an idle reference machine (a small ARM board)
CALIBRATION_REFERENCE = 0.16


def _calibration() -> float:
    """A small fixed CPU task (SQLite and Python bytecode, like the app); its duration."""
    started = time.perf_counter()
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE t (i INTEGER, s TEXT)")
    connection.executemany(
        "INSERT INTO t VALUES (?, ?)",
        ((i, hashlib.sha1(str(i).encode()).hexdigest()) for i in range(12_000)),
    )
    connection.execute("SELECT count(*), max(s) FROM t WHERE s LIKE '%ab%'").fetchone()
    connection.close()
    total = 0
    for i in range(100_000):
        total += i % 7
    return time.perf_counter() - started


def slowness() -> float:
    """How many times slower than the reference this machine is right now (at least 1)."""
    return max(1.0, _calibration() / CALIBRATION_REFERENCE)


@contextmanager
def within(seconds: float, label: str = "block"):
    """Assert that the block takes less than ``seconds`` times the current slowness."""
    before = slowness()
    started = time.perf_counter()
    yield
    elapsed = time.perf_counter() - started
    factor = max(before, slowness())
    assert elapsed < seconds * factor, (
        f"{label}: {elapsed:.2f} s, budget {seconds} s x slowness {factor:.1f}"
    )


# the real per-user folders, looked up before any test can redirect them
REAL_USER_DIRS = [
    Path(folder("litrad", appauthor=False))
    for folder in (platformdirs.user_config_dir, platformdirs.user_data_dir)
]


def _state(path: Path):
    """Enough to notice that a test created or changed a folder."""
    if not path.exists():
        return None
    return sorted((str(p), p.stat().st_mtime_ns) for p in [path, *path.rglob("*")])


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Keep every test away from the real data folders.

    Relative default paths (data/, config/) resolve inside a temporary working
    directory, the per-user config and data folders are temporary folders,
    and `serve` never starts the real backup thread. A test that still
    touches the real per-user folders fails.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LITRAD_CONFIG", raising=False)
    user_home = tmp_path / "user-home"
    for variable, folder in (("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data")):
        monkeypatch.setenv(variable, str(user_home / folder))
    monkeypatch.setattr("litrad.locations.user_config_dir", lambda: user_home / "config" / "litrad")
    monkeypatch.setattr("litrad.locations.user_data_dir", lambda: user_home / "data" / "litrad")
    before = [_state(path) for path in REAL_USER_DIRS]
    started = []
    monkeypatch.setattr(
        "litrad.backup.start_backup_thread",
        lambda config_path, log=print: started.append(config_path) or (None, None),
    )
    yield started
    after = [_state(path) for path in REAL_USER_DIRS]
    assert after == before, f"a test wrote into the real per-user folders {REAL_USER_DIRS}"


def _bare_doi(url):
    return (url or "").lower().removeprefix("https://doi.org/")


class FakeApis:
    """Serves Crossref, OpenAlex and Semantic Scholar from recorded responses."""

    def __init__(self, s2_status: int = 200, down: tuple[str, ...] = ()):
        recorded = json.loads((DATA / "api_responses.json").read_text(encoding="utf-8"))
        self.works = recorded["works"]
        self.sources = recorded["sources"]
        self.s2 = recorded["s2"]
        self.crossref_johnson = json.loads((DATA / "crossref_johnson.json").read_text("utf-8"))
        literature = json.loads((DATA / "literature_search.json").read_text("utf-8"))
        self.epmc_search = literature["europepmc_search"]
        self.pubmed_esearch = literature["pubmed_esearch"]
        self.pubmed_esummary = literature["pubmed_esummary"]
        self.s2_status = s2_status
        self.down = down
        # OpenAlex search results, keyed by "fresh" / "landmark"
        self.search_results: dict[str, list[dict]] = {"fresh": [], "landmark": []}
        # DOIs returned by the S2 recommendations endpoint
        self.s2_recommendations: list[str] = []
        self.requests: list[httpx.Request] = []

    def count(self, host: str) -> int:
        return sum(request.url.host == host for request in self.requests)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host = request.url.host
        if host in self.down:
            return httpx.Response(500, text="down")
        if host == "api.crossref.org":
            query = request.url.params["query.bibliographic"]
            items = self.crossref_johnson if query.startswith("Watching") else []
            return httpx.Response(200, json={"message": {"items": items}})
        if host == "api.openalex.org":
            return httpx.Response(200, json={"results": self._openalex(request)})
        if host == "www.ebi.ac.uk":
            return httpx.Response(200, json=self._europepmc(request.url.params["query"]))
        if host == "eutils.ncbi.nlm.nih.gov":
            if request.url.path.endswith("esearch.fcgi"):
                return httpx.Response(200, json=self.pubmed_esearch)
            wanted = request.url.params["id"].split(",")
            result = self.pubmed_esummary["result"]
            uids = [u for u in result["uids"] if u in wanted]
            return httpx.Response(
                200, json={"result": {"uids": uids, **{u: result[u] for u in uids}}}
            )
        if host == "api.semanticscholar.org":
            if self.s2_status != 200:
                return httpx.Response(self.s2_status, text="rate limited")
            if request.url.path.startswith("/recommendations/"):
                papers = [{"externalIds": {"DOI": doi}} for doi in self.s2_recommendations]
                return httpx.Response(200, json={"recommendedPapers": papers})
            ids = json.loads(request.content)["ids"]
            return httpx.Response(200, json=[self._s2(i) for i in ids])
        return httpx.Response(404)

    def _europepmc(self, query: str) -> dict:
        """Searches return the recorded results; look-ups by id are answered from them."""
        records = self.epmc_search["resultList"]["result"]
        for field, key in (("EXT_ID:", "pmid"), ("PMCID:", "pmcid"), ("DOI:", "doi")):
            if query.startswith(field):
                value = query[len(field) :].split(" ")[0].strip('"').lower()
                found = [r for r in records if (r.get(key) or "").lower() == value]
                return {"hitCount": len(found), "resultList": {"result": found}}
        return self.epmc_search

    def _openalex(self, request):
        filters = request.url.params["filter"]
        if filters.startswith("title_and_abstract.search:"):
            kind = "fresh" if "from_publication_date" in filters else "landmark"
            return self.search_results[kind]
        name, values = filters.split(":", 1)
        values = set(values.split("|"))
        if name == "doi":
            return [w for w in self.works if _bare_doi(w.get("doi")) in values]
        if name == "pmid":
            return [
                w
                for w in self.works
                if (w.get("ids") or {}).get("pmid", "").rsplit("/", 1)[-1] in values
            ]
        if name == "openalex":
            return [s for s in self.sources if s["id"].rsplit("/", 1)[-1] in values]
        raise AssertionError(f"unexpected filter {name}")

    def _s2(self, requested):
        kind, value = requested.split(":", 1)
        key = {"DOI": "DOI", "PMID": "PubMed"}[kind]
        for paper in self.s2:
            if str(paper["externalIds"].get(key, "")).lower() == value.lower():
                return paper
        return None


@pytest.fixture
def fake_apis():
    return FakeApis()


@pytest.fixture
def sleeps():
    return []


@pytest.fixture
def make_http(tmp_path, sleeps):
    clients = []

    def factory(apis: FakeApis, cache: bool = True, max_retries: int = 2) -> HttpClient:
        client = HttpClient(
            cache=ResponseCache(tmp_path / "cache.sqlite") if cache else None,
            transport=httpx.MockTransport(apis.handler),
            sleep=sleeps.append,
            max_retries=max_retries,
        )
        clients.append(client)
        return client

    yield factory
    for client in clients:
        client.close()


@pytest.fixture
def cfg(tmp_path):
    return Config.model_validate(
        {
            "paths": {
                "bibtex": str(SAMPLE_BIB),
                "database": str(tmp_path / "test.sqlite"),
                "cache_dir": str(tmp_path / "cache"),
            }
        }
    )


def make_work(
    doi: str,
    title: str,
    pub_date: str,
    percentile: float | None = None,
    cites: int = 0,
    venue: str | None = "S1",
    abstract: str = "",
    kind: str = "article",
) -> dict:
    """An OpenAlex work record as returned by the API."""
    words = abstract.split()
    return {
        "id": f"https://openalex.org/W{abs(hash(doi)) % 10**8}",
        "doi": f"https://doi.org/{doi}",
        "ids": {},
        "title": title,
        "publication_year": int(pub_date[:4]),
        "publication_date": pub_date,
        "type": kind,
        "authorships": [{"author": {"display_name": "Ada Example"}}],
        "primary_location": {
            "source": {"id": f"https://openalex.org/{venue}", "display_name": f"Journal {venue}"}
        }
        if venue
        else None,
        "cited_by_count": cites,
        "fwci": None,
        "citation_normalized_percentile": {"value": percentile / 100} if percentile else None,
        "abstract_inverted_index": {w: [i] for i, w in enumerate(words)} or None,
        "topics": [],
    }


# --- archives ------------------------------------------------------------------------


def archive_data(session, cfg) -> dict:
    """The profile in ``session`` as ``backup.merge`` takes it, via a checked archive.

    The same data as writing an archive and reading it back, without the file.
    """
    from litrad import archive
    from litrad.db import now

    files = archive.collect(session, cfg)
    info = archive.manifest(files, None, now())
    checked = archive.check(archive.Archive(Path("memory.zip"), info, files), cfg)
    assert checked.report.ok, checked.report.lines()
    return archive._keys(checked)


# --- profiles ------------------------------------------------------------------------


def make_profile(config_path, name: str = "Tester", database=None, bibtex=None) -> str:
    """A profile for the config at ``config_path``; it becomes the current one.

    ``database``: a prepared database that becomes the profile's (a copy);
    ``bibtex``: its Paperpile export. The profile gets the topics of the config.
    """
    import shutil

    from litrad import profiles
    from litrad.backup import snapshot
    from litrad.config import load_config

    base = load_config(config_path)
    settings = {
        "topics": [t.model_dump(mode="json") for t in base.topics],
        "excluded_terms": list(base.excluded_terms),
    }
    slug = profiles.create_profile(base, name, settings=settings)
    home = profiles.folder(base, slug)
    if database is not None:
        snapshot(Path(database), home / profiles.PROFILE_DB)
    if bibtex is not None:
        shutil.copy(bibtex, home / profiles.PROFILE_BIB)
    profiles.set_current(base, slug)
    return slug


def rules_form(rules=None) -> dict:
    """The fields of the rules form, filled with ``rules`` (default rules)."""
    from litrad.rules import GameRules
    from litrad.web.settings import RULE_GROUPS, levels_text

    rules = rules or GameRules()
    data = {name: str(getattr(rules, name)) for _, _, fields in RULE_GROUPS for name, *_ in fields}
    data["levels"] = levels_text(rules)
    return data


def enter_profile(client, name: str = "Tester", **fields):
    """Create a profile in the web UI and select it."""
    response = client.post(
        "/welcome/create",
        data={"name": name, **rules_form(), **fields},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    assert response.headers["location"] != "/welcome", "profile not created"
    return response


def select_profile(client, slug: str):
    """Choose an existing profile in the web UI."""
    response = client.post("/welcome/select", data={"slug": slug}, follow_redirects=False)
    assert response.status_code == 303, response.text
    return response


def profile_cfg(config_path, slug: str | None = None):
    """The effective configuration of the current (or given) profile."""
    from litrad import profiles
    from litrad.config import load_config

    base = load_config(config_path)
    return profiles.profile_config(base, slug or profiles.get_current(base))
