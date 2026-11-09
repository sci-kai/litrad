"""Desktop mode (`litrad app`): port, single instance, quit, browser, log, packaging."""

import json
import os
import socket
import subprocess
import sys
import threading
import time
import tomllib
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient
from typer.testing import CliRunner

import litrad.cli as cli
from conftest import enter_profile
from litrad import desktop, locations
from litrad.desktop import Instance
from litrad.web.app import create_app
from litrad.web.auth import hash_password
from litrad.web.network import local_names
from test_auth import write_config

ROOT = Path(__file__).resolve().parent.parent
runner = CliRunner()


def dead_pid() -> int:
    """The id of a process that has ended."""
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


def lock_with(path: Path, pid: int, port: int = 1, age: float = 0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": pid, "port": port, "started": "2026-11-08T10:00:00"}))
    if age:
        past = time.time() - age
        os.utime(path, (past, past))


# --- port ----------------------------------------------------------------------------


def test_bind_prefers_the_stable_port_else_any_free_one(monkeypatch):
    with socket.socket() as busy:
        busy.bind((desktop.HOST, 0))
        busy.listen()
        monkeypatch.setattr(desktop, "PREFERRED_PORT", busy.getsockname()[1])
        sock = desktop.bind(None)
        try:
            host, port = sock.getsockname()
            assert host == "127.0.0.1" and port not in (0, desktop.PREFERRED_PORT)
        finally:
            sock.close()


def test_bind_uses_a_free_preferred_port(monkeypatch):
    with socket.socket() as probe:
        probe.bind((desktop.HOST, 0))
        free = probe.getsockname()[1]
    monkeypatch.setattr(desktop, "PREFERRED_PORT", free)
    with desktop.bind(None) as sock:
        assert sock.getsockname() == ("127.0.0.1", free)


def test_bind_an_explicit_busy_port_fails_with_a_message():
    with socket.socket() as busy:
        busy.bind((desktop.HOST, 0))
        busy.listen()
        port = busy.getsockname()[1]
        with pytest.raises(desktop.DesktopError, match=f"Port {port} .* --port"):
            desktop.bind(port)


# --- single instance -------------------------------------------------------------------


def test_lock_written_read_and_removed_only_by_its_owner(tmp_path):
    path = tmp_path / "data" / desktop.LOCK_NAME
    mine = Instance(4242, 5000, "2026-11-08T10:00:00")
    assert desktop.write_lock(path, mine)
    assert not desktop.write_lock(path, mine)  # exists already
    assert desktop.read_lock(path) == mine
    desktop.remove_lock(path, 1)  # not ours
    assert path.exists()
    desktop.remove_lock(path, 4242)
    assert not path.exists()


def test_claim_without_lock(tmp_path):
    path = tmp_path / desktop.LOCK_NAME
    mine = Instance(os.getpid(), 5000, "now")
    assert desktop.claim(path, mine) is None
    assert desktop.read_lock(path) == mine


def test_claim_finds_a_live_instance(tmp_path, monkeypatch):
    path = tmp_path / desktop.LOCK_NAME
    lock_with(path, pid=999_999, port=5001)
    monkeypatch.setattr(desktop, "answers", lambda port, pid=None, timeout=1.0: port == 5001)
    other = desktop.claim(path, Instance(os.getpid(), 5000, "now"))
    assert other.port == 5001 and other.url == "http://127.0.0.1:5001/"
    assert desktop.read_lock(path).pid == 999_999  # left alone


@pytest.mark.parametrize(
    "case", ["process gone", "process alive but too old", "unreadable", "own pid"]
)
def test_claim_replaces_a_stale_lock(tmp_path, monkeypatch, case):
    path = tmp_path / desktop.LOCK_NAME
    if case == "process gone":
        lock_with(path, dead_pid())
    elif case == "process alive but too old":
        lock_with(path, os.getppid(), age=desktop.STARTUP_GRACE + 10)
    elif case == "unreadable":
        path.write_text("{not json")
        past = time.time() - 60
        os.utime(path, (past, past))
    else:  # a lock this process left behind
        lock_with(path, os.getpid())
    monkeypatch.setattr(desktop, "answers", lambda *a, **k: False)
    mine = Instance(os.getpid(), 5000, "now")
    assert desktop.claim(path, mine) is None
    assert desktop.read_lock(path) == mine


def test_a_starting_instance_is_waited_for(tmp_path, monkeypatch):
    path = tmp_path / desktop.LOCK_NAME
    lock_with(path, os.getppid(), port=5002)  # alive and young: still starting
    calls = []
    monkeypatch.setattr(desktop, "answers", lambda *a, **k: calls.append(1) or len(calls) >= 3)
    monkeypatch.setattr(desktop.time, "sleep", lambda s: None)
    assert desktop.running_instance(path).port == 5002
    assert len(calls) == 3


def test_pid_alive():
    assert desktop.pid_alive(os.getpid())
    assert not desktop.pid_alive(dead_pid())
    assert not desktop.pid_alive(0)


# --- quit route, password, host names ----------------------------------------------


@pytest.fixture
def quits():
    return []


@pytest.fixture
def desktop_client(tmp_path, quits):
    app = create_app(write_config(tmp_path), on_quit=lambda: quits.append(1))
    with TestClient(app, base_url="http://127.0.0.1:47290") as client:
        yield client


def test_quit_needs_a_same_origin_request(desktop_client, quits):
    assert desktop_client.post("/app/quit").status_code == 403  # no Origin, no Referer
    evil = desktop_client.post("/app/quit", headers={"Origin": "http://evil.example"})
    assert evil.status_code == 403
    assert quits == []
    response = desktop_client.post("/app/quit", headers={"Origin": "http://127.0.0.1:47290"})
    assert response.status_code == 200 and "litrad has stopped" in response.text
    assert "/static/" not in response.text  # self-contained: the server is gone
    assert quits == [1]
    referer = desktop_client.post("/app/quit", headers={"Referer": "http://127.0.0.1:47290/"})
    assert referer.status_code == 200 and quits == [1, 1]


def test_ping_and_quit_work_without_profile(desktop_client):
    ping = desktop_client.get("/app/ping", follow_redirects=False)
    assert ping.status_code == 200
    assert ping.json() == {"app": "litrad", "pid": os.getpid(), "version": cli.__version__}
    assert "/app/quit" in desktop_client.get("/welcome").text


def test_quit_button_only_in_desktop_mode(desktop_client, tmp_path):
    enter_profile(desktop_client, adopt=False)
    page = desktop_client.get("/").text
    assert 'action="/app/quit"' in page and "Dashboard" in page
    server = tmp_path / "server"
    server.mkdir()
    with TestClient(create_app(write_config(server))) as client:
        assert "/app/quit" not in client.get("/welcome").text
        enter_profile(client, adopt=False)
        assert "/app/quit" not in client.get("/").text
        ping = client.get("/app/ping", follow_redirects=False)
        assert ping.status_code == 404, (ping.headers, ping.text[:300])
        quit = client.post("/app/quit", headers={"Origin": "http://testserver"})
        assert quit.status_code in (404, 405)


def test_password_still_required_in_desktop_mode(tmp_path, quits):
    path = write_config(tmp_path, password_hash=hash_password("lab-meeting-42"))
    app = create_app(path, trusted_names=local_names(), on_quit=lambda: quits.append(1))
    with TestClient(app, base_url="http://127.0.0.1:47290") as client:
        page = client.get("/", follow_redirects=False)
        assert page.status_code == 303 and page.headers["location"].startswith("/login")
        quit = client.post("/app/quit", headers={"Origin": "http://127.0.0.1:47290"})
        assert quit.status_code == 401 and quits == []
        assert client.get("/app/ping").status_code == 200  # open: no personal data
        client.post("/login", data={"password": "lab-meeting-42", "next": "/"})
        quit = client.post("/app/quit", headers={"Origin": "http://127.0.0.1:47290"})
        assert quit.status_code == 200 and quits == [1]


# --- run(): server, browser, log, errors ---------------------------------------------


class FakeServer:
    """Stands in for uvicorn.Server; ``during`` runs while the 'server' runs."""

    instances: list = []
    during = None

    def __init__(self, config):
        self.config = config
        self.should_exit = False
        self.sockets = None
        FakeServer.instances.append(self)

    def run(self, sockets=None):
        self.sockets = sockets
        self.address = sockets[0].getsockname()
        if FakeServer.during:
            FakeServer.during(self)


@pytest.fixture
def fake_uvicorn(monkeypatch):
    FakeServer.instances = []
    FakeServer.during = None
    monkeypatch.setattr(uvicorn, "Server", FakeServer)
    monkeypatch.setattr("webbrowser.open", lambda *a, **k: pytest.fail("no browser"))
    monkeypatch.setattr(desktop, "PREFERRED_PORT", 0)  # any free port
    monkeypatch.setenv(desktop.ENV_NO_BROWSER, "1")
    return FakeServer


def test_run_serves_locally_with_lock_backup_and_log(fake_uvicorn, isolated):
    seen = {}

    def during(server):
        seen["lock"] = desktop.read_lock(desktop.lock_file())
        app = server.config.app
        with TestClient(app, base_url=f"http://127.0.0.1:{seen['lock'].port}") as client:
            seen["welcome"] = client.get("/welcome").status_code
            seen["quit"] = client.post(
                "/app/quit", headers={"Origin": f"http://127.0.0.1:{seen['lock'].port}"}
            ).status_code
        with TestClient(app, base_url="http://evil.example") as client:
            seen["rebound"] = client.get("/welcome").status_code
        seen["should_exit"] = server.should_exit

    fake_uvicorn.during = during
    assert desktop.run() == 0
    server = fake_uvicorn.instances[0]
    host, port = server.address
    assert host == "127.0.0.1" and port > 0
    assert server.sockets[0].fileno() == -1  # closed afterwards
    assert seen["lock"].pid == os.getpid() and seen["lock"].port == port
    assert seen["welcome"] == 200 and seen["quit"] == 200 and seen["should_exit"] is True
    assert seen["rebound"] == 421  # trusted host names: this computer's names only
    assert not desktop.lock_file().exists()
    config = locations.user_config_file()
    assert config.is_file()  # first run: created
    assert isolated == [config.resolve()]  # backup thread started like serve does
    log = desktop.log_file()
    assert log == locations.user_data_dir() / "logs" / "app.log"
    text = log.read_text(encoding="utf-8")
    assert f"Config file {config}" in text and "Created your configuration" in text
    assert "Quit from the web UI" in text and "litrad has stopped" in text


def test_run_with_explicit_port_and_config(fake_uvicorn, tmp_path):
    with socket.socket() as probe:
        probe.bind((desktop.HOST, 0))
        port = probe.getsockname()[1]
    assert desktop.run(Path(write_config(tmp_path)), port=port) == 0
    assert fake_uvicorn.instances[0].address[1] == port
    assert not locations.user_config_file().exists()  # the given config was used


def test_browser_opens_when_the_server_answers(fake_uvicorn, monkeypatch):
    monkeypatch.delenv(desktop.ENV_NO_BROWSER)
    opened = threading.Event()
    urls = []
    monkeypatch.setattr(desktop, "wait_until_up", lambda port, pid, timeout=60: True)
    monkeypatch.setattr(
        "webbrowser.open", lambda url, *a, **k: urls.append(url) or opened.set() or True
    )
    fake_uvicorn.during = lambda server: opened.wait(5)
    assert desktop.run() == 0
    port = fake_uvicorn.instances[0].address[1]
    assert urls == [f"http://127.0.0.1:{port}/"]


@pytest.mark.parametrize("value", ["1", "true", "YES"])
def test_browser_wanted(monkeypatch, value):
    monkeypatch.delenv(desktop.ENV_NO_BROWSER, raising=False)
    assert desktop.browser_wanted(True) and not desktop.browser_wanted(False)
    monkeypatch.setenv(desktop.ENV_NO_BROWSER, value)
    assert not desktop.browser_wanted(True)


def test_second_start_opens_the_running_instance(fake_uvicorn, monkeypatch):
    lock_with(desktop.lock_file(), pid=999_999, port=5003)
    monkeypatch.setattr(desktop, "answers", lambda port, pid=None, timeout=1.0: port == 5003)
    monkeypatch.delenv(desktop.ENV_NO_BROWSER)
    urls = []
    monkeypatch.setattr("webbrowser.open", lambda url, *a, **k: urls.append(url) or True)
    assert desktop.run() == 0
    assert urls == ["http://127.0.0.1:5003/"] and fake_uvicorn.instances == []
    assert desktop.read_lock(desktop.lock_file()).pid == 999_999
    assert "already running" in desktop.log_file().read_text(encoding="utf-8")


def test_stale_lock_after_a_crash_is_replaced(fake_uvicorn):
    lock_with(desktop.lock_file(), dead_pid(), port=1)
    assert desktop.run() == 0
    assert len(fake_uvicorn.instances) == 1
    assert "Replacing the stale lock" in desktop.log_file().read_text(encoding="utf-8")
    assert not desktop.lock_file().exists()


def test_ctrl_c_stops_cleanly(fake_uvicorn):
    def interrupted(server):
        raise KeyboardInterrupt

    fake_uvicorn.during = interrupted
    assert desktop.run() == 0
    assert not desktop.lock_file().exists()
    assert "Stopped with Ctrl+C" in desktop.log_file().read_text(encoding="utf-8")


def test_startup_error_is_logged_and_shown(fake_uvicorn, monkeypatch):
    monkeypatch.delenv(desktop.ENV_NO_BROWSER)
    urls = []
    monkeypatch.setattr("webbrowser.open", lambda url, *a, **k: urls.append(url) or True)

    def broken(*args, **kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(locations, "locate_config", broken)
    assert desktop.run() == 1
    log = desktop.log_file().read_text(encoding="utf-8")
    assert "litrad could not start" in log and "Traceback" in log
    page = desktop.log_file().parent / desktop.ERROR_PAGE
    assert urls == [page.as_uri()]  # no terminal (stderr is captured): shown in the browser
    assert "disk on fire" in page.read_text(encoding="utf-8")


def test_config_error_is_a_message(fake_uvicorn, tmp_path):
    bad = tmp_path / "config.yaml"
    bad.write_text("queue: {max_size: -3}\n")
    assert desktop.run(bad) == 1
    log = desktop.log_file().read_text(encoding="utf-8")
    assert "ERROR" in log and "Traceback" not in log


def test_main_parses_arguments(monkeypatch):
    calls = []
    monkeypatch.setattr(desktop, "run", lambda *args: calls.append(args) or 3)
    with pytest.raises(SystemExit) as stop:
        desktop.main(["--port", "5555", "--no-browser", "-c", "my.yaml", "-psn_0_12345"])
    assert stop.value.code == 3
    assert calls == [(Path("my.yaml"), 5555, False)]


# --- CLI -------------------------------------------------------------------------------


def test_cli_app_command(fake_uvicorn, tmp_path):
    result = runner.invoke(cli.app, ["app", "--no-browser", "-c", write_config(tmp_path)])
    assert result.exit_code == 0, result.output
    assert fake_uvicorn.instances[0].address[0] == "127.0.0.1"


def test_cli_app_command_failure_exit_code(fake_uvicorn, monkeypatch):
    with socket.socket() as busy:
        busy.bind((desktop.HOST, 0))
        busy.listen()
        port = busy.getsockname()[1]
        result = runner.invoke(cli.app, ["app", "--port", str(port)])
    assert result.exit_code == 1


def test_where_lists_the_desktop_log():
    result = runner.invoke(cli.app, ["where"])
    assert result.exit_code == 0
    assert f"Desktop app log:      {desktop.log_file()}" in result.output


# --- packaging ---------------------------------------------------------------------------


def pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_gui_script_entry():
    assert pyproject()["project"]["gui-scripts"] == {"litrad-app": "litrad.desktop:main"}
    entry = (ROOT / "packaging" / "desktop_entry.py").read_text(encoding="utf-8")
    assert "from litrad.desktop import main" in entry


def package_data_files() -> set[str]:
    """Every file the wheel ships as package data (from the pyproject globs)."""
    files = set()
    for package, patterns in pyproject()["tool"]["setuptools"]["package-data"].items():
        folder = ROOT / "src" / Path(*package.split("."))
        for pattern in patterns:
            for path in folder.glob(pattern):
                if path.is_file():
                    files.add(path.relative_to(ROOT / "src").as_posix())
    return files


def test_package_data_globs_match_files():
    files = package_data_files()
    for needed in [
        "litrad/config.example.yaml",
        "litrad/web/documentation.md",
        "litrad/web/sw.js",
        "litrad/web/templates/stopped.html",
        "litrad/web/static/icon-512.png",
    ]:
        assert needed in files
    assert not any(name.endswith(".py") for name in files)


def test_spec_collects_all_package_data():
    spec = (ROOT / "packaging" / "litrad.spec").read_text(encoding="utf-8")
    assert 'collect_data_files("litrad")' in spec
    assert "desktop_entry.py" in spec and "BUNDLE(" in spec
    hooks = pytest.importorskip("PyInstaller.utils.hooks")
    collected = {
        (Path(dest) / Path(source).name).as_posix()
        for source, dest in hooks.collect_data_files("litrad")
    }
    missing = package_data_files() - collected
    assert not missing, f"not bundled by the PyInstaller spec: {sorted(missing)}"


def test_installer_and_appimage_files():
    iss = (ROOT / "packaging" / "windows" / "litrad.iss").read_text(encoding="utf-8")
    assert "PrivilegesRequired=lowest" in iss and "desktopicon" in iss
    assert "[UninstallDelete]" not in iss  # user data is kept
    assert "are kept in" in iss and "UninstallSilent" in iss
    for tool in ("smoke_desktop.py", "build_appimage.py"):
        path = ROOT / "tools" / tool
        assert path.read_text(encoding="utf-8").startswith("#!/usr/bin/env python3")
        assert os.access(path, os.X_OK)
