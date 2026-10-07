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
            ids = json.loads(request.content)["ids"]
            return httpx.Response(200, json=[self._s2(i) for i in ids])
        return httpx.Response(404)

    def _openalex(self, request):
        name, values = request.url.params["filter"].split(":", 1)
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
