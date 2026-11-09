from datetime import timedelta

import httpx
import pytest

from litrad.sources.http import HttpClient, HttpError, ResponseCache


def scripted(responses, seen):
    """Transport that returns the given responses in order."""
    responses = list(responses)

    def handler(request):
        seen.append(request)
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return httpx.MockTransport(handler)


OPEN = []


@pytest.fixture(autouse=True)
def close_clients():
    yield
    while OPEN:
        OPEN.pop().close()


def client(responses, seen, sleeps, tmp_path=None, **kwargs):
    cache = ResponseCache(tmp_path / "c.sqlite") if tmp_path else None
    http = HttpClient(
        cache=cache, transport=scripted(responses, seen), sleep=sleeps.append, **kwargs
    )
    OPEN.append(http)
    return http


def test_json_and_user_agent(sleeps):
    seen = []
    http = client([httpx.Response(200, json={"a": 1})], seen, sleeps, contact_email="me@x.org")
    assert http.get("https://h.org/x", params={"q": 1}) == {"a": 1}
    assert "mailto:me@x.org" in seen[0].headers["User-Agent"]
    assert seen[0].url.params["q"] == "1"


def test_cache_hit_avoids_second_request(tmp_path, sleeps):
    seen = []
    http = client([httpx.Response(200, json=[1])], seen, sleeps, tmp_path)
    assert http.get("https://h.org/x") == [1]
    assert http.get("https://h.org/x") == [1]
    assert len(seen) == 1


def test_cache_key_includes_params_and_body(tmp_path, sleeps):
    seen = []
    http = client([httpx.Response(200, json=[i]) for i in range(3)], seen, sleeps, tmp_path)
    assert http.get("https://h.org/x", params={"q": 1}) == [0]
    assert http.get("https://h.org/x", params={"q": 2}) == [1]
    assert http.post("https://h.org/x", {"ids": [1]}) == [2]
    assert http.get("https://h.org/x", params={"q": 1}) == [0]
    assert len(seen) == 3


def test_expired_cache_and_bypass(tmp_path, sleeps):
    seen = []
    http = client([httpx.Response(200, json=[i]) for i in range(3)], seen, sleeps, tmp_path)
    http.get("https://h.org/x")
    assert http.get("https://h.org/x", max_age=timedelta(seconds=-1)) == [1]
    assert http.get("https://h.org/x", use_cache=False) == [2]


def test_retry_after_is_honoured(sleeps):
    seen = []
    http = client(
        [httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200, json={})],
        seen,
        sleeps,
    )
    assert http.get("https://h.org/x") == {}
    assert sleeps == [7.0]


def test_exponential_backoff_on_server_errors(sleeps):
    seen = []
    http = client(
        [httpx.Response(503), httpx.Response(502), httpx.Response(200, json={})], seen, sleeps
    )
    http.get("https://h.org/x")
    assert sleeps == [1, 2]


def test_retries_exhausted_raise(sleeps):
    seen = []
    http = client([httpx.Response(429)] * 3, seen, sleeps, max_retries=2)
    with pytest.raises(HttpError, match="429"):
        http.get("https://h.org/x")
    assert len(seen) == 3


def test_transport_errors_are_retried(sleeps):
    seen = []
    http = client(
        [httpx.ConnectError("down"), httpx.Response(200, json={"ok": True})], seen, sleeps
    )
    assert http.get("https://h.org/x") == {"ok": True}

    http = client([httpx.ConnectError("down")] * 2, [], sleeps, max_retries=1)
    with pytest.raises(HttpError, match="down"):
        http.get("https://h.org/x")


def test_404_handling(sleeps):
    http = client([httpx.Response(404), httpx.Response(404)], [], sleeps)
    assert http.get("https://h.org/x", allow_404=True) is None
    with pytest.raises(HttpError, match="404"):
        http.get("https://h.org/y")


def test_errors_are_not_cached(tmp_path, sleeps):
    seen = []
    http = client([httpx.Response(400), httpx.Response(200, json=[1])], seen, sleeps, tmp_path)
    with pytest.raises(HttpError):
        http.get("https://h.org/x")
    assert http.get("https://h.org/x") == [1]


def test_throttle_per_host(sleeps):
    seen = []
    http = client([httpx.Response(200, json={})] * 3, seen, sleeps, min_interval={"slow.org": 10})
    http.get("https://slow.org/a")
    http.get("https://fast.org/a")
    assert sleeps == []
    http.get("https://slow.org/b")
    assert len(sleeps) == 1 and 9 < sleeps[0] <= 10


def test_cache_persists_across_instances(tmp_path):
    cache = ResponseCache(tmp_path / "c.sqlite")
    cache.put("k", {"v": 1})
    cache.close()
    reopened = ResponseCache(tmp_path / "c.sqlite")
    assert reopened.get("k", None) == {"v": 1}
    reopened.close()
