import os
import stat

import pytest
import yaml
from fastapi.testclient import TestClient
from typer.testing import CliRunner

import litrad.cli as cli
from conftest import enter_profile
from litrad.config import load_config
from litrad.web.app import create_app
from litrad.web.auth import (
    COOKIE,
    check_session,
    hash_password,
    load_secret,
    make_session,
    verify_password,
)

runner = CliRunner()


# --- hashing and sessions ---------------------------------------------------------


def test_password_hash_round_trip():
    stored = hash_password("correct horse")
    assert stored.startswith("scrypt$") and "correct horse" not in stored
    assert verify_password("correct horse", stored)
    assert not verify_password("wrong horse!", stored)
    assert hash_password("correct horse") != stored  # salted


@pytest.mark.parametrize("stored", [None, "", "n/a", "md5$x", "scrypt$1$2$3$zz$yy", "a$b$c$d$e$f"])
def test_malformed_hashes_never_verify(stored):
    assert not verify_password("anything", stored)


def test_short_passwords_are_rejected():
    with pytest.raises(ValueError, match="at least 8"):
        hash_password("short")


def test_sessions(tmp_path):
    secret = load_secret(tmp_path / "web_secret")
    assert stat.S_IMODE((tmp_path / "web_secret").stat().st_mode) == 0o600
    assert load_secret(tmp_path / "web_secret") == secret  # stable across restarts
    token = make_session(secret, "hash-1", now=1000)
    assert check_session(token, secret, "hash-1", now=2000)
    assert not check_session(token, secret, "hash-1", now=1000 + 31 * 86400)  # expired
    assert not check_session(token, secret, "hash-2", now=2000)  # password changed
    assert not check_session(token, b"other-secret", "hash-1", now=2000)
    expires, signature = token.split(".")
    assert not check_session(f"{int(expires) + 999}.{signature}", secret, "hash-1", now=2000)
    for broken in (None, "", "garbage", "x.y"):
        assert not check_session(broken, secret, "hash-1", now=2000)


# --- web login ----------------------------------------------------------------------


def write_config(tmp_path, **web):
    path = tmp_path / "config.yaml"
    path.write_text(
        "# my settings\n"
        + yaml.safe_dump(
            {
                "paths": {"cache_dir": str(tmp_path / "cache")},
                "web": {"host": "0.0.0.0", "port": 8080, **web},
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def protected(tmp_path):
    path = write_config(tmp_path, password_hash=hash_password("lab-meeting-42"))
    client = TestClient(create_app(path))
    yield client
    client.close()


def test_pages_require_login(protected):
    response = protected.get("/queue?x=1", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=%2Fqueue%3Fx%3D1"
    assert protected.get("/static/app.css").status_code == 200
    assert protected.get("/login").status_code == 200
    assert protected.post("/queue/add", data={"title": "x"}).status_code == 401
    assert protected.get("/ledger.tsv", follow_redirects=False).status_code == 303


def test_wrong_password(protected):
    response = protected.post("/login", data={"password": "nope", "next": "/queue"})
    assert "Wrong password" in response.text
    assert COOKIE not in protected.cookies


def test_login_and_logout(protected):
    response = protected.post(
        "/login", data={"password": "lab-meeting-42", "next": "/queue"}, follow_redirects=False
    )
    assert response.status_code == 303 and response.headers["location"] == "/queue"
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "samesite=lax" in cookie.lower()
    # logged in, but no profile chosen yet: the welcome screen comes first
    assert protected.get("/queue", follow_redirects=False).headers["location"] == "/welcome"
    enter_profile(protected, "Ada")
    page = protected.get("/queue")
    assert page.status_code == 200 and "Reading queue" in page.text and "Log out" in page.text
    protected.post("/logout")
    assert protected.get("/", follow_redirects=False).status_code == 303


def test_login_does_not_redirect_elsewhere(protected):
    response = protected.post(
        "/login",
        data={"password": "lab-meeting-42", "next": "https://evil.example"},
        follow_redirects=False,
    )
    assert response.headers["location"] == "/"
    for target in ("/\\evil.example", "//evil.example", "/\t/evil.example"):
        response = protected.post(
            "/login", data={"password": "lab-meeting-42", "next": target}, follow_redirects=False
        )
        assert response.headers["location"] == "/", target


def test_no_password_means_no_login(tmp_path):
    client = TestClient(create_app(write_config(tmp_path)))
    assert client.get("/").status_code == 200
    assert "Log out" not in client.get("/").text
    client.close()


def test_without_password_only_trusted_host_names_are_answered(tmp_path):
    """DNS rebinding: evil.example resolving to 127.0.0.1 must not reach the data."""
    names = frozenset({"localhost", "nas", "nas.local"})
    client = TestClient(create_app(write_config(tmp_path), trusted_names=names))
    for host in ("127.0.0.1:8080", "192.168.1.5", "[::1]:8080", "localhost:8080", "NAS.local"):
        assert client.get("/welcome", headers={"host": host}).status_code == 200, host
    for host in ("evil.example", "evil.example:8080", "localhost.evil.example"):
        assert client.get("/welcome", headers={"host": host}).status_code == 421, host
    client.close()


def test_with_password_any_host_name_reaches_the_login(tmp_path):
    path = write_config(tmp_path, password_hash=hash_password("lab-meeting-42"))
    client = TestClient(create_app(path, trusted_names=frozenset({"localhost"})))
    response = client.get("/", headers={"host": "nas.fritz.box"}, follow_redirects=False)
    assert response.status_code == 303 and "/login" in response.headers["location"]
    client.close()


# --- serve and set-password ---------------------------------------------------------------


def test_set_password_keeps_comments(tmp_path):
    path = write_config(tmp_path)
    result = runner.invoke(
        cli.app, ["set-password", "-c", str(path)], input="lab-meeting-42\nlab-meeting-42\n"
    )
    assert result.exit_code == 0, result.stdout
    assert "# my settings" in path.read_text()
    stored = load_config(path).web.password_hash
    assert verify_password("lab-meeting-42", stored)
    assert path.with_name("config.yaml.bak").exists()
    if os.name == "posix":  # the password hash: readable only by you
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.with_name("config.yaml.bak").stat().st_mode) == 0o600

    result = runner.invoke(cli.app, ["set-password", "-c", str(path)], input="short\nshort\n")
    assert result.exit_code == 1 and "at least 8" in result.stdout
    assert load_config(path).web.password_hash == stored

    result = runner.invoke(cli.app, ["set-password", "--remove", "-c", str(path)])
    assert result.exit_code == 0 and load_config(path).web.password_hash is None


@pytest.mark.parametrize(
    "target",
    [
        "//evil.example",
        "/\\evil.example",
        "/\\\\evil.example",
        "/x\\..\\y",
        "/\t/evil.example",
        "/\n/evil.example",
        "https://evil.example",
        "javascript:alert(1)",
        "evil.example",
        "",
    ],
)
def test_redirect_targets_stay_on_this_site(target):
    from litrad.web.app import local_path

    assert not local_path(target)


def test_local_redirect_targets_are_kept():
    from litrad.web.app import local_path

    for target in ("/", "/queue", "/search?q=a+b&page=2#top", "/papers/3"):
        assert local_path(target)


def test_preview_links_refuse_backslashes():
    from litrad.notes import safe_href

    for href in ("/\\evil.example", "/\t/evil.example", "https:\\\\evil.example", "//evil.example"):
        assert not safe_href(href), href
    for href in ("https://doi.org/10.1/x", "/papers/3", "#notes", "mailto:a@b.c"):
        assert safe_href(href), href


def test_login_guard_backs_off_and_recovers():
    from litrad.web.auth import LoginGuard

    now = [0.0]
    guard = LoginGuard(limit=3, window=60, clock=lambda: now[0])
    for _ in range(2):
        guard.failed()
    assert not guard.throttled()
    guard.failed()
    assert guard.throttled()
    now[0] = 61  # the failures have aged out: back to normal
    assert not guard.throttled()


def test_login_checks_off_the_event_loop_and_backs_off(protected, monkeypatch):
    import asyncio

    import litrad.web.app as web

    monkeypatch.setattr(web, "FAILED_LOGIN_DELAY", 0)
    threads = []

    def checked(password, stored):
        try:
            asyncio.get_running_loop()
            threads.append("event loop")
        except RuntimeError:
            threads.append("worker")
        return password == "lab-meeting-42"

    monkeypatch.setattr(web, "verify_password", checked)
    guard = protected.app.state.login_guard
    guard.delay = 0.01
    for _ in range(guard.limit):
        response = protected.post("/login", data={"password": "nope"}, follow_redirects=False)
        assert "failed=true" in response.headers["location"]
    assert guard.throttled() and set(threads) == {"worker"}
    # throttled, but the owner is not locked out
    response = protected.post(
        "/login", data={"password": "lab-meeting-42", "next": "/queue"}, follow_redirects=False
    )
    assert response.headers["location"] == "/queue" and COOKIE in protected.cookies


@pytest.mark.parametrize("content", ["", "\n", "abcd", "not hex at all" * 10, "00" * 16])
def test_unusable_secret_files_get_a_new_secret(tmp_path, content):
    path = tmp_path / "web_secret"
    path.write_text(content)
    secret = load_secret(path)
    assert len(secret) == 32 and secret != b"\0" * 16
    assert path.read_text().strip() == secret.hex()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert load_secret(path) == secret
    assert sorted(p.name for p in tmp_path.iterdir()) == ["web_secret"]  # no temporary file
