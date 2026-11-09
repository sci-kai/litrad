"""Desktop mode: litresearch as an app on this computer (the default way to run it).

``litresearch app``, the ``litresearch-app`` program and the double-click
builds of the GitHub releases all run ``run()``:

1. log to a rotating file in the per-user data folder (``logs/app.log``);
   windowed builds have no console, and ``litresearch where`` names the file;
2. find the config file (``locations.locate_config``: the first start creates
   it from the example);
3. one instance per user: ``app-instance.json`` in the per-user data folder
   holds the pid and port. A second start opens the browser on the running
   instance and exits; a lock left behind by a crash (no answer on its port,
   process gone or older than ``STARTUP_GRACE``) is replaced;
4. listen on 127.0.0.1 only: ``--port``, else ``PREFERRED_PORT`` if it is
   free (a stable address for bookmarks), else any free port. Host names are
   limited to this computer's names as in ``litresearch serve``; a configured
   password is still required (never weaker than server mode);
5. serve the web UI with uvicorn in this process, start the backup thread
   like ``serve``, and open the default browser once the server answers
   (not with ``--no-browser`` or ``LITRESEARCH_NO_BROWSER=1``);
6. **Quit** in the sidebar (``POST /app/quit``, only in desktop mode, same
   origin only) or Ctrl+C stops the server gracefully and removes the lock.

Unexpected start-up errors are logged and, when there is no terminal to show
them, written to ``logs/startup-error.html`` and opened in the browser.
"""

import argparse
import html
import json
import logging
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from dataclasses import asdict, dataclass
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from litresearch import __version__, locations

log = logging.getLogger("litresearch.desktop")

HOST = "127.0.0.1"
PREFERRED_PORT = 47290
ENV_NO_BROWSER = "LITRESEARCH_NO_BROWSER"
LOCK_NAME = "app-instance.json"
LOG_FOLDER = "logs"
LOG_NAME = "app.log"
ERROR_PAGE = "startup-error.html"
# seconds a starting instance may take to answer before its lock counts as stale
STARTUP_GRACE = 60
READY_TIMEOUT = 60


class DesktopError(RuntimeError):
    """An expected problem at start-up, shown to the user as a message."""


def lock_file() -> Path:
    return locations.user_data_dir() / LOCK_NAME


def log_file() -> Path:
    return locations.user_data_dir() / LOG_FOLDER / LOG_NAME


# --- logging -----------------------------------------------------------------


def setup_logging(path: Path) -> list[logging.Handler]:
    """Log INFO and above to ``path`` (rotating) and to stderr if there is one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    handlers: list[logging.Handler] = [
        RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    ]
    has_stream = any(type(h) is logging.StreamHandler for h in root.handlers)
    if sys.stderr is not None and not has_stream:
        handlers.append(logging.StreamHandler(sys.stderr))
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%dT%H:%M:%S"
    )
    for handler in handlers:
        handler.setFormatter(formatter)
        root.addHandler(handler)
    log.setLevel(logging.INFO)
    return handlers


def teardown_logging(handlers: list[logging.Handler]) -> None:
    root = logging.getLogger()
    for handler in handlers:
        root.removeHandler(handler)
        handler.close()


# --- single instance ---------------------------------------------------------


@dataclass(frozen=True)
class Instance:
    pid: int
    port: int
    started: str
    version: str = __version__

    @property
    def url(self) -> str:
        return f"http://{HOST}:{self.port}/"


def read_lock(path: Path) -> Instance | None:
    """The instance named in the lock file, or None (missing or unreadable).

    A file that is still being written (empty, very young) is read again.
    """
    for _ in range(10):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return Instance(int(data["pid"]), int(data["port"]), str(data.get("started", "")))
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError):
            try:
                young = time.time() - path.stat().st_mtime < 5
            except OSError:
                return None
            if not young:
                return None
            time.sleep(0.2)
    return None


def write_lock(path: Path, instance: Instance) -> bool:
    """Create the lock file; False if it exists already."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(asdict(instance), handle)
    return True


def remove_lock(path: Path, pid: int) -> None:
    """Remove the lock file if it is ours."""
    instance = read_lock(path)
    if instance is not None and instance.pid == pid:
        path.unlink(missing_ok=True)


def pid_alive(pid: int) -> bool:
    """True if a process with this id exists (without touching it)."""
    if pid <= 0:
        return False
    if os.name == "nt":  # os.kill(pid, 0) would terminate the process on Windows
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def answers(port: int, pid: int | None = None, timeout: float = 1.0) -> bool:
    """True if litresearch (with this pid) answers on 127.0.0.1:``port``."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://{HOST}:{port}/app/ping", timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError):
        return False
    return data.get("app") == "litresearch" and (pid is None or data.get("pid") == pid)


def running_instance(path: Path, grace: float = STARTUP_GRACE) -> Instance | None:
    """The instance that holds the lock and answers, waiting while it starts up."""
    instance = read_lock(path)
    if instance is None:
        return None
    while True:
        if answers(instance.port, instance.pid):
            return instance
        try:
            age = time.time() - path.stat().st_mtime
        except OSError:
            return None
        if instance.pid == os.getpid() or not pid_alive(instance.pid) or age > grace:
            return None
        time.sleep(0.5)


def claim(path: Path, instance: Instance) -> Instance | None:
    """Write our lock; returns the other instance instead if one is running."""
    for _ in range(3):
        if write_lock(path, instance):
            return None
        other = running_instance(path)
        if other is not None:
            return other
        stale = read_lock(path)
        log.warning(
            "Replacing the stale lock %s (pid %s; litresearch was not stopped cleanly)",
            path,
            stale.pid if stale else "unknown",
        )
        path.unlink(missing_ok=True)
    raise DesktopError(f"Could not create the lock file {path}; delete it and start again.")


# --- network and browser ------------------------------------------------------


def bind(port: int | None = None) -> socket.socket:
    """A socket bound to 127.0.0.1: ``port``, else PREFERRED_PORT, else any free port."""
    candidates = [port] if port else [PREFERRED_PORT, 0]
    for candidate in candidates:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if os.name != "nt":  # restart right after a quit (TIME_WAIT); Windows would share ports
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((HOST, candidate))
        except OSError:
            sock.close()
            continue
        return sock
    raise DesktopError(f"Port {port} on {HOST} is in use; choose another one with --port.")


def browser_wanted(flag: bool = True) -> bool:
    """False with --no-browser or LITRESEARCH_NO_BROWSER=1 (tests, CI)."""
    return flag and os.environ.get(ENV_NO_BROWSER, "").lower() not in ("1", "true", "yes")


def open_browser(url: str) -> None:
    """Open ``url`` in the default browser."""
    # a frozen Linux build points LD_LIBRARY_PATH at its own libraries; the
    # browser must start with the user's original environment
    if getattr(sys, "frozen", False) and sys.platform.startswith("linux"):
        original = os.environ.pop("LD_LIBRARY_PATH_ORIG", None)
        if original is None:
            os.environ.pop("LD_LIBRARY_PATH", None)
        else:
            os.environ["LD_LIBRARY_PATH"] = original
    if not webbrowser.open(url):
        log.warning("Could not open a browser; open %s yourself", url)


def wait_until_up(port: int, pid: int, timeout: float = READY_TIMEOUT) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if answers(port, pid):
            return True
        time.sleep(0.2)
    return False


def open_when_up(instance: Instance) -> None:
    if wait_until_up(instance.port, instance.pid):
        log.info("Opening %s in your browser", instance.url)
        open_browser(instance.url)
    else:
        log.error(
            "The server did not answer within %s s; open %s yourself", READY_TIMEOUT, instance.url
        )


def report_error(message: str, browser: bool) -> None:
    """Make a start-up error visible when there is no terminal (windowed builds)."""
    if not browser or (sys.stderr is not None and sys.stderr.isatty()):
        return
    page = log_file().parent / ERROR_PAGE
    try:
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(
            "<!doctype html><meta charset='utf-8'><title>litresearch could not start</title>"
            "<body style='font-family:sans-serif;max-width:40rem;margin:3rem auto'>"
            "<h1>litresearch could not start</h1>"
            f"<p>{html.escape(message)}</p>"
            f"<p>Details are in the log file <code>{html.escape(str(log_file()))}</code>.</p>",
            encoding="utf-8",
        )
        open_browser(page.as_uri())
    except OSError:
        log.exception("Could not show the error page")


# --- start-up -----------------------------------------------------------------


def serve(config_path: Path, sock: socket.socket) -> None:
    """Run the web UI on ``sock`` until Quit or Ctrl+C."""
    import uvicorn

    from litresearch.web.app import create_app
    from litresearch.web.network import local_names

    servers = []

    def quit_app():
        log.info("Quit from the web UI")
        for server in servers:
            server.should_exit = True

    app = create_app(config_path, trusted_names=local_names(), on_quit=quit_app)
    server = uvicorn.Server(
        uvicorn.Config(app, log_level="warning", log_config=None, timeout_graceful_shutdown=5)
    )
    servers.append(server)
    server.run(sockets=[sock])


def _start(config: Path | None, port: int | None, browser: bool) -> int:
    from litresearch.config import load_config

    log.info("litresearch %s, desktop mode; log file %s", __version__, log_file())
    choice = locations.locate_config(config, announce=log.info)
    log.info("Config file %s (%s)", choice.path, choice.description)
    config_path = choice.path.resolve()
    cfg = load_config(config_path)
    log.info("Profiles in %s", cfg.paths.profiles_dir)

    lock = lock_file()
    other = running_instance(lock)
    if other is None:
        sock = bind(port)
        mine = Instance(
            os.getpid(), sock.getsockname()[1], datetime.now().isoformat("T", "seconds")
        )
        other = claim(lock, mine)
        if other is not None:
            sock.close()
    if other is not None:
        log.info("litresearch is already running at %s; opening it", other.url)
        if browser:
            open_browser(other.url)
        return 0

    try:
        if cfg.web.password_hash:
            log.info("A password is set (web.password_hash): the browser asks for it")
        if cfg.backup.interval_hours > 0:
            from litresearch.backup import start_backup_thread

            log.info(
                "Backups every %g h to %s (keeping %s)",
                cfg.backup.interval_hours,
                cfg.backup.dir,
                cfg.backup.keep,
            )
            start_backup_thread(config_path, log=log.info)
        log.info("litresearch runs at %s; quit with Quit in the sidebar or Ctrl+C", mine.url)
        if browser:
            threading.Thread(target=open_when_up, args=(mine,), daemon=True).start()
        serve(config_path, sock)
    finally:
        sock.close()
        remove_lock(lock, mine.pid)
        log.info("litresearch has stopped")
    return 0


def run(config: Path | None = None, port: int | None = None, browser: bool = True) -> int:
    """Start desktop mode; returns the exit code."""
    from litresearch.config import ConfigError

    browser = browser_wanted(browser)
    handlers = setup_logging(log_file())
    try:
        return _start(config, port, browser)
    except KeyboardInterrupt:
        log.info("Stopped with Ctrl+C")
        return 0
    except (DesktopError, ConfigError) as error:
        log.error("%s", error)
        report_error(str(error), browser)
        return 1
    except Exception as error:
        log.exception("litresearch could not start")
        report_error(f"{type(error).__name__}: {error}", browser)
        return 1
    finally:
        teardown_logging(handlers)


def main(argv: list[str] | None = None) -> None:
    """Entry point of ``litresearch-app`` and of the desktop builds."""
    parser = argparse.ArgumentParser(
        prog="litresearch-app",
        description="Start litresearch on this computer and open it in your browser.",
    )
    parser.add_argument("--port", type=int, help=f"port on {HOST} (default: a free one)")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser")
    parser.add_argument("-c", "--config", type=Path, help="path to config.yaml")
    # macOS may add arguments such as -psn_0_12345 when started from the Finder
    args, _unknown = parser.parse_known_args(argv)
    raise SystemExit(run(args.config, args.port, not args.no_browser))


if __name__ == "__main__":
    main()
