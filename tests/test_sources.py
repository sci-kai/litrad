import json
from datetime import date

import httpx
import pytest

from conftest import DATA, FakeApis
from litresearch.sources import crossref
from litresearch.sources.http import HttpClient, HttpError
from litresearch.sources.openalex import BATCH, OpenAlex, parse_work, rebuild_abstract
from litresearch.sources.semanticscholar import SemanticScholar, parse_paper

RECORDED = json.loads((DATA / "api_responses.json").read_text(encoding="utf-8"))


def work_by_doi(doi):
    return next(w for w in RECORDED["works"] if (w.get("doi") or "").endswith(doi))


# --- Crossref -------------------------------------------------------------


def item(title, family="Smith", year=2020, kind="journal-article", doi="10.1/a"):
    return {
        "DOI": doi,
        "title": [title],
        "type": kind,
        "author": [{"family": family}] if family else [],
        "issued": {"date-parts": [[year]]},
    }


@pytest.mark.parametrize(
    "candidate, expected",
    [
        (item("Tumour evolution in cancer!"), True),  # punctuation, spelling-insensitive
        (item("Tumor evolution in cancer", kind="peer-review"), False),
        (item("Tumor evolution in cancer", year=2023), False),
        (item("Tumor evolution in cancer", year=2021), True),
        (item("Tumor evolution in cancer", family="Jones"), False),
        (item("Tumor evolution in cancer", family=None), True),  # no authors to check
        (item("Something entirely different"), False),
    ],
)
def test_crossref_match_rules(candidate, expected):
    assert crossref.is_match(candidate, "Tumor evolution in cancer", "Smith, Ann", 2020) is expected


def test_crossref_prefix_rule_needs_author_and_length():
    long_title = item("Watching cancer cells evolve through chromosomal instability", "Johnson")
    assert crossref.is_match(long_title, "Watching cancer cells evolve", "Johnson, Sarah C", None)
    # same prefix, but the author does not match
    assert not crossref.is_match(long_title, "Watching cancer cells evolve", "Smith, A", None)
    # without an author we cannot confirm a prefix match
    assert not crossref.is_match(long_title, "Watching cancer cells evolve", None, None)
    # too short to be trusted
    short = item("Cancer evolution in the clinic", "Johnson")
    assert not crossref.is_match(short, "Cancer evolution", "Johnson, S", None)


def test_crossref_resolve_skips_bad_top_hit():
    items = [
        item("Faculty Opinions recommendation of Tumor evolution", kind="dataset", doi="10.9/fo"),
        item("Tumor evolution", doi="10.1/REAL"),
    ]
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"message": {"items": items}})

    http = HttpClient(transport=httpx.MockTransport(handler))
    doi = crossref.resolve_doi(http, "Tumor evolution", "Smith, A", 2020, contact_email="a@b.c")
    assert doi == "10.1/real"
    params = seen[0].url.params
    assert params["query.bibliographic"] == "Tumor evolution smith 2020"
    assert params["mailto"] == "a@b.c"


def test_crossref_no_match_returns_none():
    http = HttpClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"message": {"items": []}})
        )
    )
    assert crossref.resolve_doi(http, "Nothing", None, None) is None


# --- OpenAlex -------------------------------------------------------------


def test_rebuild_abstract():
    assert rebuild_abstract({"world": [1], "hello": [0, 2]}) == "hello world hello"
    assert rebuild_abstract(None) is None
    assert rebuild_abstract({}) is None


def test_parse_recorded_work():
    work = parse_work(work_by_doi("10.1038/s41588-022-01141-9"))
    assert work.openalex_id.startswith("W")
    assert work.doi == "10.1038/s41588-022-01141-9"
    assert work.pmid == "35931863"
    assert work.year == 2022 and isinstance(work.pub_date, date)
    assert work.venue == "Nature Genetics" and work.venue_id.startswith("S")
    assert work.cited_by_count > 0 and work.fwci > 0
    assert 0 <= work.citation_percentile <= 100
    assert work.authors[0] == "Dalia Barkley"
    assert work.topics
    # OpenAlex withholds some publishers' abstracts
    assert work.abstract is None
    abstract = parse_work(work_by_doi("10.1126/science.aaf6546")).abstract
    assert abstract and abstract[0].isupper()


def test_parse_minimal_work():
    work = parse_work({"id": "https://openalex.org/W1"})
    assert work.openalex_id == "W1"
    assert work.doi is None and work.citation_percentile is None and work.authors == []


def test_openalex_batches_and_params():
    apis = FakeApis()
    http = HttpClient(transport=httpx.MockTransport(apis.handler))
    client = OpenAlex(http, api_key="KEY", contact_email="a@b.c")
    dois = [f"10.1/{i}" for i in range(BATCH * 2 + 1)] + ["10.1038/s41588-022-01141-9"]
    works = client.works_by_doi(dois)
    assert list(works) == ["10.1038/s41588-022-01141-9"]
    assert apis.count("api.openalex.org") == 3
    params = apis.requests[0].url.params
    assert params["api_key"] == "KEY" and params["mailto"] == "a@b.c"


def test_openalex_pmid_and_venues():
    apis = FakeApis()
    client = OpenAlex(HttpClient(transport=httpx.MockTransport(apis.handler)))
    works = client.works_by_pmid(["14678978"])
    assert works["14678978"].openalex_id
    venue = parse_work(work_by_doi("10.1038/s41588-022-01141-9")).venue_id
    citedness = client.venue_mean_citedness([venue, "S0"])
    assert citedness[venue] > 1 and "S0" not in citedness


# --- Semantic Scholar ----------------------------------------------------


def test_parse_s2_paper():
    paper = parse_paper(RECORDED["s2"][0])
    assert paper.s2_id and len(paper.embedding) == 4
    assert paper.citation_count is not None
    assert parse_paper({"paperId": "x"}).embedding is None


def test_s2_batch_aligns_ids_and_skips_unknown():
    apis = FakeApis()
    client = SemanticScholar(HttpClient(transport=httpx.MockTransport(apis.handler)), "KEY")
    found = client.papers(["DOI:10.1038/s41588-022-01141-9", "DOI:10.1/unknown", "PMID:35931863"])
    assert set(found) == {"DOI:10.1038/s41588-022-01141-9", "PMID:35931863"}
    assert found["PMID:35931863"].s2_id == found["DOI:10.1038/s41588-022-01141-9"].s2_id
    request = apis.requests[0]
    assert request.headers["x-api-key"] == "KEY"
    assert "embedding.specter_v2" in request.url.params["fields"]


def test_s2_batch_length_mismatch_raises():
    http = HttpClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[None])))
    with pytest.raises(HttpError, match="expected 2 results"):
        SemanticScholar(http).papers(["DOI:10.1/a", "DOI:10.1/b"])
