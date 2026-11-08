"""Installable phone app: manifest, service worker, offline page, icons."""

import json
import struct
import tomllib
from fnmatch import fnmatch
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import litresearch
from conftest import enter_profile
from litresearch.web.app import create_app
from litresearch.web.auth import hash_password
from test_auth import write_config

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def locked(tmp_path):
    """Password-protected app, no profile chosen, strict host check."""
    path = write_config(tmp_path, password_hash=hash_password("lab-meeting-42"))
    with TestClient(create_app(path, trusted_names=frozenset({"localhost"}))) as client:
        yield client


def png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert data[12:16] == b"IHDR"
    return struct.unpack(">II", data[16:24])


def test_manifest(locked):
    response = locked.get("/manifest.webmanifest", follow_redirects=False)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/manifest+json")
    manifest = json.loads(response.text)
    for key in ("name", "short_name", "description", "start_url", "scope", "display", "icons"):
        assert manifest[key]
    assert manifest["display"] == "standalone"
    assert manifest["start_url"] == manifest["scope"] == "/"
    assert manifest["theme_color"].startswith("#") and manifest["background_color"].startswith("#")
    sizes = {(i["sizes"], i.get("purpose", "any")) for i in manifest["icons"]}
    assert {("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")} <= sizes
    for icon in manifest["icons"]:
        got = locked.get(icon["src"], follow_redirects=False)
        assert got.status_code == 200 and got.headers["content-type"] == "image/png"
        side = int(icon["sizes"].split("x")[0])
        assert png_size(got.content) == (side, side)


def test_apple_touch_icon(locked):
    got = locked.get("/static/apple-touch-icon.png")
    assert got.status_code == 200 and png_size(got.content) == (180, 180)


def test_service_worker(locked):
    response = locked.get("/sw.js", follow_redirects=False)
    assert response.status_code == 200
    assert response.headers["service-worker-allowed"] == "/"
    assert response.headers["cache-control"] == "no-cache"
    assert "javascript" in response.headers["content-type"]
    script = response.text
    assert f'"litresearch-v{litresearch.__version__}"' in script
    assert "__VERSION__" not in script
    assert 'request.method !== "GET"' in script  # POST is never handled or cached
    assert "caches.delete" in script  # old versions are cleaned up
    assert '"/offline"' in script and '"/static/app.css"' in script
    # pages with personal data are never stored: only /static is put into the cache
    assert script.count("cache.put") == 1


def test_open_without_login_and_profile(locked):
    for path in ("/manifest.webmanifest", "/sw.js", "/offline", "/static/icon-192.png"):
        response = locked.get(path, headers={"host": "pi.example.ts.net"}, follow_redirects=False)
        assert response.status_code == 200, path
    assert locked.get("/queue", follow_redirects=False).status_code == 303


def test_open_without_profile_or_password(tmp_path):
    names = frozenset({"localhost"})
    with TestClient(create_app(write_config(tmp_path), trusted_names=names)) as client:
        for path in ("/manifest.webmanifest", "/sw.js", "/offline"):
            assert client.get(path, headers={"host": "localhost"}).status_code == 200, path
        assert client.get("/sw.js", headers={"host": "evil.example"}).status_code == 421


def test_offline_page(locked):
    html = locked.get("/offline").text
    assert "You are offline" in html
    assert "not stored on this device" in html


def assert_head_tags(html: str):
    assert '<link rel="manifest" href="/manifest.webmanifest">' in html
    assert html.count('name="theme-color"') == 2
    assert "prefers-color-scheme: dark" in html
    assert '<link rel="apple-touch-icon" href="/static/apple-touch-icon.png">' in html
    assert 'name="apple-mobile-web-app-capable"' in html


def test_head_tags_on_login_bare_and_base_pages(locked, tmp_path):
    assert_head_tags(locked.get("/login").text)  # login page
    assert_head_tags(locked.get("/offline").text)  # bare layout
    with TestClient(create_app(write_config(tmp_path))) as client:
        assert_head_tags(client.get("/welcome").text)  # bare, no profile yet
        enter_profile(client)
        assert_head_tags(client.get("/").text)  # base layout


def test_service_worker_is_registered_by_app_js(locked):
    script = locked.get("/static/app.js").text
    assert '"serviceWorker" in navigator' in script and "isSecureContext" in script
    assert 'register("/sw.js"' in script


def test_package_data_covers_the_phone_app_files():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = pyproject["tool"]["setuptools"]["package-data"]["litresearch.web"]
    web = ROOT / "src" / "litresearch" / "web"
    files = [
        "sw.js",
        "templates/offline.html",
        "templates/_pwa_head.html",
        "static/icon-192.png",
        "static/icon-512.png",
        "static/icon-maskable-512.png",
        "static/apple-touch-icon.png",
    ]
    for name in files:
        assert (web / name).is_file(), name
        assert any(fnmatch(name, pattern) for pattern in patterns), name
