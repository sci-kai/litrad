#!/usr/bin/env python3
"""Build the Linux AppImage from the PyInstaller folder.

    python tools/build_appimage.py DIST/litrad --output litrad.AppImage \\
        --appimagetool PATH [--runtime-file PATH] [--workdir DIR]

Creates an AppDir (AppRun, litrad.desktop with Categories=Education;Science;,
the icon, the PyInstaller folder under usr/lib/litrad) and calls
appimagetool (path from --appimagetool or the environment variable
APPIMAGETOOL). appimagetool runs with APPIMAGE_EXTRACT_AND_RUN=1, so FUSE is
not needed. Pass --runtime-file to use a downloaded AppImage runtime instead
of letting appimagetool download one.
"""

import argparse
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DESKTOP_ENTRY = """[Desktop Entry]
Type=Application
Name=litrad
GenericName=The Literature Radar Game
Comment=Paper recommendations, a reading queue and a reading game
Exec=litrad
Icon=litrad
Terminal=false
Categories=Education;Science;
"""

APPRUN = """#!/bin/sh
# start the bundled program with the arguments given to the AppImage
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/lib/litrad/litrad" "$@"
"""

ARCHES = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}


def find_icon(bundle: Path) -> Path:
    for candidate in bundle.rglob("icon-512.png"):
        if candidate.parent.name == "static":
            return candidate
    raise SystemExit(f"icon-512.png not found in {bundle}; is it a litrad PyInstaller folder?")


def make_appdir(bundle: Path, appdir: Path) -> Path:
    """Lay out the AppDir; returns it."""
    if not (bundle / "litrad").is_file():
        raise SystemExit(f"{bundle} has no program 'litrad'; build with PyInstaller first")
    icon = find_icon(bundle)
    shutil.copytree(bundle, appdir / "usr" / "lib" / "litrad", symlinks=True)
    (appdir / "AppRun").write_text(APPRUN, encoding="utf-8")
    (appdir / "AppRun").chmod(0o755)
    (appdir / "litrad.desktop").write_text(DESKTOP_ENTRY, encoding="utf-8")
    applications = appdir / "usr" / "share" / "applications"
    applications.mkdir(parents=True)
    shutil.copy(appdir / "litrad.desktop", applications / "litrad.desktop")
    icons = appdir / "usr" / "share" / "icons" / "hicolor" / "512x512" / "apps"
    icons.mkdir(parents=True)
    shutil.copy(icon, icons / "litrad.png")
    shutil.copy(icon, appdir / "litrad.png")
    (appdir / ".DirIcon").symlink_to("litrad.png")
    return appdir


def build(bundle: Path, output: Path, tool: Path, runtime: Path | None, workdir: Path) -> None:
    appdir = make_appdir(bundle, workdir / "litrad.AppDir")
    arch = ARCHES.get(platform.machine().lower(), platform.machine())
    env = dict(os.environ, ARCH=arch, APPIMAGE_EXTRACT_AND_RUN="1")
    command = [str(tool)]
    if runtime is not None:
        command += ["--runtime-file", str(runtime)]
    command += ["--no-appstream", str(appdir), str(output)]
    print("Running", " ".join(command))
    subprocess.run(command, env=env, check=True)
    output.chmod(0o755)
    print(f"{output}: {output.stat().st_size / 2**20:.1f} MB")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("bundle", type=Path, help="the PyInstaller folder (dist/litrad)")
    parser.add_argument("--output", type=Path, required=True, help="the AppImage to write")
    parser.add_argument("--appimagetool", type=Path, default=os.environ.get("APPIMAGETOOL"))
    parser.add_argument("--runtime-file", type=Path, help="AppImage runtime (type2-runtime)")
    parser.add_argument("--workdir", type=Path, help="folder for the AppDir (default: temporary)")
    args = parser.parse_args()
    if args.appimagetool is None or not args.appimagetool.is_file():
        print("appimagetool not found: pass --appimagetool or set APPIMAGETOOL", file=sys.stderr)
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.workdir is not None:
        args.workdir.mkdir(parents=True, exist_ok=True)
        if (args.workdir / "litrad.AppDir").exists():
            shutil.rmtree(args.workdir / "litrad.AppDir")
        build(args.bundle, args.output, args.appimagetool, args.runtime_file, args.workdir)
    else:
        with tempfile.TemporaryDirectory() as folder:
            build(args.bundle, args.output, args.appimagetool, args.runtime_file, Path(folder))
    return 0


if __name__ == "__main__":
    sys.exit(main())
