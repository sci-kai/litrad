#!/usr/bin/env python3
"""Smoke-test a built litresearch desktop app (PyInstaller folder, AppImage, .app).

    python tools/smoke_desktop.py PATH-TO-BINARY [--port N] [--timeout S]

Starts the binary with LITRESEARCH_NO_BROWSER=1 on a fixed port in a fresh
temporary home folder, then checks that

1. it answers on /welcome (first run: config created), creates a profile
   with the default rules from the welcome form, and then serves the
   dashboard, the documentation, the icons and the service worker (the
   package data is bundled);
2. /app/quit refuses a request without Origin;
3. a second start finds the running instance and exits with 0;
4. POST /app/quit shows the "stopped" page, the process exits with 0 and
   removes its lock file, and the log file exists.

Linux and macOS use the temporary home folder (HOME, XDG_*). On Windows,
platformdirs asks the system for the folders, so the run uses the CI user's
real %LOCALAPPDATA%\\litresearch: run it on a CI machine, not on your own.

Uses only the standard library (the data folder is looked up with
platformdirs if it is installed, else derived from the platform rules).
Exit code 0 if all checks pass.
"""

import argparse
import http.cookiejar
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

LOCK_NAME = "app-instance.json"
LOG = Path("logs") / "app.log"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


COOKIES = http.cookiejar.CookieJar()


def request(url, method="GET", headers=None, timeout: float = 5, data: bytes | None = None):
    """(status, body) without proxies, with cookies; (0, error) when nothing answers."""
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(COOKIES)
    )
    if method == "POST" and data is None:
        data = b""
    req = urllib.request.Request(url, method=method, headers=headers or {}, data=data)
    try:
        with opener.open(req, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")
    except OSError as error:
        return 0, str(error)


def data_dir(env: dict) -> Path:
    """The per-user data folder the app will use with this environment."""
    code = "import platformdirs; print(platformdirs.user_data_dir('litresearch', appauthor=False))"
    try:
        out = subprocess.run(
            [sys.executable, "-I", "-c", code], env=env, capture_output=True, text=True, check=True
        )
        return Path(out.stdout.strip())
    except subprocess.CalledProcessError:
        pass
    if sys.platform == "darwin":
        return Path(env["HOME"]) / "Library" / "Application Support" / "litresearch"
    if os.name == "nt":
        return Path(os.environ["LOCALAPPDATA"]) / "litresearch"
    return Path(env["XDG_DATA_HOME"]) / "litresearch"


class FormFields(HTMLParser):
    """The fields (with their default values) of the form posting to ``action``."""

    def __init__(self, action: str):
        super().__init__()
        self.action, self.inside, self.textarea = action, False, None
        self.fields: list[tuple[str, str]] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.inside = attrs.get("action") == self.action
        elif self.inside and tag == "input" and attrs.get("name"):
            if attrs.get("type") in ("checkbox", "radio") and "checked" not in attrs:
                return
            self.fields.append((attrs["name"], attrs.get("value") or ""))
        elif self.inside and tag == "textarea" and attrs.get("name"):
            self.textarea = [attrs["name"], ""]

    def handle_data(self, data):
        if self.textarea is not None:
            self.textarea[1] += data

    def handle_endtag(self, tag):
        if tag == "form":
            self.inside = False
        elif tag == "textarea" and self.textarea is not None:
            self.fields.append(tuple(self.textarea))
            self.textarea = None


class Failure(Exception):
    pass


def check(condition: bool, message: str) -> None:
    if not condition:
        raise Failure(message)
    print(f"ok   {message}")


def smoke(binary: Path, port: int, timeout: float) -> None:
    home = Path(tempfile.mkdtemp(prefix="litresearch-smoke-"))
    env = dict(os.environ)
    env.update(
        HOME=str(home),
        XDG_CONFIG_HOME=str(home / "config"),
        XDG_DATA_HOME=str(home / "data"),
        LITRESEARCH_NO_BROWSER="1",
    )
    env.pop("LITRESEARCH_CONFIG", None)
    data = data_dir(env)
    lock = data / LOCK_NAME
    if lock.exists():  # Windows: the CI user's real folder
        lock.unlink()
    command = [str(binary), "--port", str(port)]
    base = f"http://127.0.0.1:{port}"
    print(f"Starting {' '.join(command)} (home {home}, data {data})")
    started = time.monotonic()
    process = subprocess.Popen(command, env=env, cwd=home)
    try:
        status = 0
        while time.monotonic() - started < timeout:
            if process.poll() is not None:
                raise Failure(f"the app exited early with code {process.returncode}")
            status, _ = request(base + "/welcome", timeout=2)
            if status == 200:
                break
            time.sleep(0.25)
        check(status == 200, f"/welcome answers 200 after {time.monotonic() - started:.1f} s")
        name = f"Smoke test {os.getpid()}"  # unique: Windows reuses the real data folder
        parser = FormFields("/welcome/create")
        parser.feed(request(base + "/welcome")[1])
        fields = [(k, v) for k, v in parser.fields if k != "name"] + [("name", name)]
        status, body = request(
            base + "/welcome/create",
            "POST",
            {"Origin": base, "Content-Type": "application/x-www-form-urlencoded"},
            data=urllib.parse.urlencode(fields).encode(),
        )
        check(status == 200 and name in body, "a profile is created with the default rules")
        for path, needle in [
            ("/", "Dashboard"),
            ("/docs", "Setup"),
            ("/sw.js", "litresearch service worker"),
            ("/static/icon-192.png", "PNG"),
            ("/manifest.webmanifest", "litresearch"),
        ]:
            status, body = request(base + path)
            check(status == 200 and needle in body, f"{path} is served")
        status, body = request(base + "/app/ping")
        check(status == 200 and json.loads(body).get("app") == "litresearch", "/app/ping answers")
        check(lock.is_file() and json.loads(lock.read_text())["port"] == port, f"lock {lock}")
        status, _ = request(base + "/app/quit", "POST")
        check(status == 403, "/app/quit without Origin is refused")

        second = subprocess.run(command, env=env, cwd=home, timeout=60)
        check(second.returncode == 0, "a second start finds the running instance and exits")
        check(process.poll() is None, "the first instance keeps running")

        status, body = request(base + "/app/quit", "POST", {"Origin": base})
        check(status == 200 and "has stopped" in body, "/app/quit shows the stopped page")
        try:
            code = process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            raise Failure("the app did not exit within 30 s after Quit") from None
        check(code == 0, "the app exits with code 0")
        check(not lock.exists(), "the lock file is removed")
        check((data / LOG).is_file(), f"log file {data / LOG}")
    except Failure:
        log = data / LOG
        if log.is_file():
            print(f"--- {log}\n{log.read_text(encoding='utf-8', errors='replace')}---")
        raise
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        shutil.rmtree(home, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("binary", type=Path, help="the built program (or an AppImage)")
    parser.add_argument("--port", type=int, default=0, help="port to use (default: a free one)")
    parser.add_argument("--timeout", type=float, default=120, help="seconds to wait for start-up")
    args = parser.parse_args()
    if not args.binary.is_file():
        print(f"FAIL {args.binary} is not a file", file=sys.stderr)
        return 2
    try:
        smoke(args.binary.resolve(), args.port or free_port(), args.timeout)
    except Failure as failure:
        print(f"FAIL {failure}", file=sys.stderr)
        return 1
    print("Smoke test passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
