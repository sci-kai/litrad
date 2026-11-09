#!/usr/bin/env python3
"""Install litrad in this folder with one command.

    python3 install.py                   # virtual environment, dependencies, config
    python3 install.py --service         # ... and start the web UI automatically (systemd)
    python3 install.py --dev             # ... with the test and lint tools
    python3 install.py --repair          # reinstall everything, e.g. after a broken download
    python3 install.py --remove-service  # stop starting the web UI automatically

Safe to run again, e.g. after ``git pull``: it updates the installation and
never overwrites an existing ``config/config.yaml``. It checks the system
first and the result at the end, so a broken installation never says "Done".
Only the standard library is used, so it runs on any Python >= 3.11 (Linux;
tested on a Raspberry Pi).
"""

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

HERE = Path(__file__).resolve().parent
VENV = HERE / ".venv"
CONFIG = HERE / "config" / "config.yaml"
EXAMPLE = HERE / "config" / "config.example.yaml"
SERVICE = Path.home() / ".config" / "systemd" / "user" / "litrad.service"
MINIMUM = (3, 11)

UNIT = """\
[Unit]
Description=litrad web UI
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory={home}
# --no-choose: no question at start; the network is web.host in config/config.yaml
ExecStart={venv}/bin/litrad serve --no-choose -c {home}/config/config.yaml
Restart=on-failure

[Install]
WantedBy=default.target
"""


def step(message: str) -> None:
    print(f"\n==> {message}")


def run(*command: str) -> None:
    subprocess.run(command, check=True)


class InstallError(Exception):
    """A problem the user can fix; the message says how."""


def check_system() -> None:
    """Fail early, with the fix, if this Python cannot build a virtual environment."""
    if sys.version_info < MINIMUM:
        version = ".".join(map(str, MINIMUM))
        raise InstallError(
            f"litrad needs Python {version} or newer; this is {sys.version.split()[0]}.\n"
            f"Install a newer Python and run this script with it, e.g. python{version} install.py"
        )
    if importlib.util.find_spec("ensurepip") is None and not (VENV / "bin" / "python").exists():
        minor = f"{sys.version_info.major}.{sys.version_info.minor}"
        raise InstallError(
            "This Python cannot create virtual environments (the venv/ensurepip part is "
            "missing).\nOn Debian, Ubuntu or Raspberry Pi OS install it with:\n"
            f"    sudo apt install python3-venv   (or python{minor}-venv)\n"
            "then run python3 install.py again."
        )


def create_venv() -> Path:
    if not (VENV / "bin" / "python").exists():
        step(f"Creating a virtual environment in {VENV}")
        venv.EnvBuilder(with_pip=True).create(VENV)
    return VENV / "bin" / "python"


def install_package(python: Path, dev: bool, repair: bool = False) -> None:
    step("Installing litrad and its dependencies (a few minutes on a Raspberry Pi)")
    fresh = ["--force-reinstall", "--no-cache-dir"] if repair else []
    run(str(python), "-m", "pip", "install", "--quiet", "--upgrade", "pip")
    # editable: `git pull` updates the code without reinstalling
    target = f"{HERE}[dev]" if dev else str(HERE)
    run(str(python), "-m", "pip", "install", "--quiet", *fresh, "--editable", target)


def expected_version() -> str:
    for line in (HERE / "pyproject.toml").read_text(encoding="utf-8").splitlines():
        if line.startswith("version"):
            return line.split("=", 1)[1].strip().strip('"')
    return ""


def verify() -> str:
    """Run the installed command once; a corrupt download shows up here, not later."""
    step("Checking the installation")
    result = subprocess.run(
        [str(VENV / "bin" / "litrad"), "version"], capture_output=True, text=True
    )
    version = result.stdout.strip()
    if result.returncode != 0 or version != expected_version():
        detail = (result.stderr.strip().splitlines() or [version or "no output"])[-1]
        raise InstallError(
            f"litrad does not start: {detail}\n"
            "Reinstall all packages without pip's cache with:\n"
            "    python3 install.py --repair"
        )
    print(f"litrad {version} works.")
    return version


def create_config() -> None:
    if CONFIG.exists():
        step(f"Keeping your configuration {CONFIG}")
        return
    step(f"Creating {CONFIG} from the example")
    shutil.copyfile(EXAMPLE, CONFIG)


def install_service() -> None:
    if shutil.which("systemctl") is None:
        print("systemd is not available here; start the web UI with `litrad serve`.")
        return
    step(f"Installing the systemd user service {SERVICE}")
    SERVICE.parent.mkdir(parents=True, exist_ok=True)
    SERVICE.write_text(UNIT.format(home=HERE, venv=VENV), encoding="utf-8")
    run("systemctl", "--user", "daemon-reload")
    run("systemctl", "--user", "enable", "--now", "litrad.service")
    print("The web UI now starts with your user session.")
    print("To keep it running after you log out (e.g. on a Pi): sudo loginctl enable-linger $USER")
    print("Status and logs: systemctl --user status litrad; journalctl --user -u litrad")


def remove_service() -> None:
    if not SERVICE.exists():
        print("No litrad service is installed.")
        return
    step(f"Removing the systemd user service {SERVICE}")
    subprocess.run(["systemctl", "--user", "disable", "--now", "litrad.service"], check=False)
    SERVICE.unlink()
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    print("The web UI no longer starts automatically; your data is untouched.")


def next_steps(service: bool) -> None:
    step("Done")
    command = ".venv/bin/litrad"
    print(f"litrad is installed in {HERE}. Run its commands from this folder:")
    print(f"    cd {HERE}")
    if service:
        print("The web UI is running; see the address with: systemctl --user status litrad")
    else:
        print(f"    {command} serve      # start the web UI, then open the address it prints")
    print("In the browser: create a profile, import your library (Library -> Import),")
    print("name your topics and get your first recommendations.")
    print(f"Command line:   {command} --help")
    print("Documentation:  docs/documentation.md (also in the web UI under 📖 Documentation)")
    print("Update later:   git pull && python3 install.py")


def offer_start() -> None:
    """Interactive installs may start the web UI right away."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return
    try:
        answer = input("\nStart the web UI now? [Y/n] ").strip().lower()
    except EOFError:
        return
    if answer in ("", "y", "yes"):
        os.chdir(HERE)
        command = str(VENV / "bin" / "litrad")
        os.execv(command, [command, "serve"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--service", action="store_true", help="start the web UI with systemd")
    parser.add_argument("--dev", action="store_true", help="also install test and lint tools")
    parser.add_argument(
        "--repair", action="store_true", help="reinstall all packages without pip's cache"
    )
    parser.add_argument(
        "--remove-service", action="store_true", help="remove the systemd service again"
    )
    args = parser.parse_args()
    # keep our lines, pip's output and errors in order, also in a log file
    sys.stdout.reconfigure(line_buffering=True)

    try:
        if args.remove_service:
            remove_service()
            return 0
        check_system()
        python = create_venv()
        install_package(python, args.dev, args.repair)
        create_config()
        verify()
        if args.service:
            install_service()
    except InstallError as error:
        print(f"\nInstallation stopped: {error}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as error:
        print(
            f"\nInstallation failed: {error}\n"
            "Check the messages above (often: no internet connection), then run "
            "python3 install.py again.",
            file=sys.stderr,
        )
        return 1

    next_steps(args.service)
    if not args.service:
        offer_start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
