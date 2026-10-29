import shutil
import stat

import pytest
import yaml
from fastapi.testclient import TestClient
from typer.testing import CliRunner

import litresearch.cli as cli
from conftest import SAMPLE_BIB, enter_profile
from litresearch.config import load_config
from litresearch.web.app import create_app
from litresearch.web.auth import (
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
    bib = tmp_path / "library.bib"
    shutil.copy(SAMPLE_BIB, bib)
    path = tmp_path / "config.yaml"
    path.write_text(
        "# my settings\n"
        + yaml.safe_dump(
            {
                "paths": {
                    "bibtex": str(bib),
                    "database": str(tmp_path / "db.sqlite"),
                    "cache_dir": str(tmp_path / "cache"),
                },
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
    enter_profile(protected, "Ada", adopt=False)
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


def test_no_password_means_no_login(tmp_path):
    client = TestClient(create_app(write_config(tmp_path)))
    assert client.get("/").status_code == 200
    assert "Log out" not in client.get("/").text
    client.close()


def test_without_password_only_trusted_host_names_are_answered(tmp_path):
    """DNS rebinding: evil.example resolving to 127.0.0.1 must not reach the data."""
    names = frozenset({"localhost", "pi", "pi.local"})
    client = TestClient(create_app(write_config(tmp_path), trusted_names=names))
    for host in ("127.0.0.1:8080", "192.168.1.5", "[::1]:8080", "localhost:8080", "PI.local"):
        assert client.get("/welcome", headers={"host": host}).status_code == 200, host
    for host in ("evil.example", "evil.example:8080", "localhost.evil.example"):
        assert client.get("/welcome", headers={"host": host}).status_code == 421, host
    client.close()


def test_with_password_any_host_name_reaches_the_login(tmp_path):
    path = write_config(tmp_path, password_hash=hash_password("lab-meeting-42"))
    client = TestClient(create_app(path, trusted_names=frozenset({"localhost"})))
    response = client.get("/", headers={"host": "pi.fritz.box"}, follow_redirects=False)
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

    result = runner.invoke(cli.app, ["set-password", "-c", str(path)], input="short\nshort\n")
    assert result.exit_code == 1 and "at least 8" in result.stdout
    assert load_config(path).web.password_hash == stored

    result = runner.invoke(cli.app, ["set-password", "--remove", "-c", str(path)])
    assert result.exit_code == 0 and load_config(path).web.password_hash is None
