import json
from pathlib import Path

import httpx
import pytest

from litresearch.config import Config
from litresearch.sources.http import HttpClient, ResponseCache

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


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Keep every test away from the real data folders.

    Relative default paths (data/, config/) resolve inside a temporary working
    directory, and `serve` never starts the real backup thread.
    """
    monkeypatch.chdir(tmp_path)
    started = []
    monkeypatch.setattr(
        "litresearch.backup.start_backup_thread",
        lambda config_path, log=print: started.append(config_path) or (None, None),
    )
    return started


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
        if host == "api.semanticscholar.org":
            if self.s2_status != 200:
                return httpx.Response(self.s2_status, text="rate limited")
            if request.url.path.startswith("/recommendations/"):
                papers = [{"externalIds": {"DOI": doi}} for doi in self.s2_recommendations]
                return httpx.Response(200, json={"recommendedPapers": papers})
            ids = json.loads(request.content)["ids"]
            return httpx.Response(200, json=[self._s2(i) for i in ids])
        return httpx.Response(404)

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
