#!/usr/bin/env python3
"""Install litresearch in this folder with one command.

    python3 install.py              # virtual environment, dependencies, config
    python3 install.py --service    # ... and start the web UI automatically (systemd)
    python3 install.py --dev        # ... with the test and lint tools

Safe to run again, e.g. after ``git pull``: it updates the installation and
never overwrites an existing ``config/config.yaml``. Only the standard library
is used, so it runs on any Python >= 3.11 (Linux; tested on a Raspberry Pi).
"""

import argparse
import shutil
import subprocess
import sys
import venv
from pathlib import Path

HERE = Path(__file__).resolve().parent
VENV = HERE / ".venv"
CONFIG = HERE / "config" / "config.yaml"
EXAMPLE = HERE / "config" / "config.example.yaml"
SERVICE = Path.home() / ".config" / "systemd" / "user" / "litresearch.service"
MINIMUM = (3, 11)

UNIT = """\
[Unit]
Description=litresearch web UI
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory={home}
# --no-choose: no question at start; the network is web.host in config/config.yaml
ExecStart={venv}/bin/litresearch serve --no-choose -c {home}/config/config.yaml
Restart=on-failure

[Install]
WantedBy=default.target
"""


def step(message: str) -> None:
    print(f"\n==> {message}")


def run(*command: str) -> None:
    subprocess.run(command, check=True)


def create_venv() -> Path:
    if not (VENV / "bin" / "python").exists():
        step(f"Creating a virtual environment in {VENV}")
        venv.EnvBuilder(with_pip=True).create(VENV)
    return VENV / "bin" / "python"


def install_package(python: Path, dev: bool) -> None:
    step("Installing litresearch and its dependencies (a few minutes on a Raspberry Pi)")
    run(str(python), "-m", "pip", "install", "--quiet", "--upgrade", "pip")
    # editable: `git pull` updates the code without reinstalling
    target = f"{HERE}[dev]" if dev else str(HERE)
    run(str(python), "-m", "pip", "install", "--quiet", "--editable", target)


def create_config() -> None:
    if CONFIG.exists():
        step(f"Keeping your configuration {CONFIG}")
        return
    step(f"Creating {CONFIG} from the example")
    shutil.copyfile(EXAMPLE, CONFIG)


def install_service() -> None:
    if shutil.which("systemctl") is None:
        print("systemd is not available here; start the web UI with `litresearch serve`.")
        return
    step(f"Installing the systemd user service {SERVICE}")
    SERVICE.parent.mkdir(parents=True, exist_ok=True)
    SERVICE.write_text(UNIT.format(home=HERE, venv=VENV), encoding="utf-8")
    run("systemctl", "--user", "daemon-reload")
    run("systemctl", "--user", "enable", "--now", "litresearch.service")
    print("The web UI now starts with your user session.")
    print("To keep it running after you log out (e.g. on a Pi): sudo loginctl enable-linger $USER")
    print("Status and logs: systemctl --user status litresearch; journalctl --user -u litresearch")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--service", action="store_true", help="start the web UI with systemd")
    parser.add_argument("--dev", action="store_true", help="also install test and lint tools")
    args = parser.parse_args()

    if sys.version_info < MINIMUM:
        print(f"litresearch needs Python {'.'.join(map(str, MINIMUM))} or newer.")
        return 1
    try:
        python = create_venv()
        install_package(python, args.dev)
        create_config()
        if args.service:
            install_service()
    except subprocess.CalledProcessError as error:
        print(f"\nInstallation failed: {error}", file=sys.stderr)
        return 1

    command = VENV / "bin" / "litresearch"
    step("Done")
    print(f"Start the web UI:   {command} serve")
    print("Then open the printed address, create your profile and upload your Paperpile export.")
    print("Documentation:      docs/documentation.md (also in the web UI under 📖 Documentation)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
