# PyInstaller spec of the litresearch desktop app (one folder; a .app bundle on macOS).
#
#   pyinstaller --noconfirm --distpath DIST --workpath WORK packaging/litresearch.spec
#
# Result: DIST/litresearch/litresearch[.exe] (Linux, Windows) or
# DIST/litresearch.app (macOS). Windowed (no console) on Windows and macOS.
# On Windows and macOS the PNG icon is converted by PyInstaller, which needs
# Pillow at build time (pip install pillow; not a runtime dependency).
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

import litresearch

HERE = Path(SPECPATH)  # noqa: F821 (set by PyInstaller)
PACKAGE = Path(litresearch.__file__).parent
ICON = PACKAGE / "web" / "static" / "icon-512.png"
WINDOWED = sys.platform in ("win32", "darwin")
# strip debug symbols on Linux (smaller AppImage); never on macOS (breaks signatures)
STRIP = sys.platform.startswith("linux")

# every non-Python file of the package: templates, static files and icons,
# sw.js, documentation.md (symlink or real file) and config.example.yaml
datas = collect_data_files("litresearch")
datas += copy_metadata("litresearch")

# uvicorn picks its loop, protocol and lifespan modules by name at run time;
# litresearch imports most of its modules inside functions
hiddenimports = collect_submodules("uvicorn") + collect_submodules("litresearch")
hiddenimports += ["sqlalchemy.dialects.sqlite"]

a = Analysis(  # noqa: F821
    [str(HERE / "desktop_entry.py")],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest", "IPython", "matplotlib"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="litresearch",
    console=not WINDOWED,
    strip=STRIP,
    icon=str(ICON) if WINDOWED else None,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="litresearch", strip=STRIP, upx=False)  # noqa: F821

if sys.platform == "darwin":
    version = litresearch.__version__
    app = BUNDLE(  # noqa: F821
        coll,
        name="litresearch.app",
        icon=str(ICON),
        bundle_identifier="io.github.litresearch",
        version=version.split("-")[0],
        info_plist={
            "CFBundleName": "litresearch",
            "CFBundleDisplayName": "litresearch",
            "CFBundleShortVersionString": version.split("-")[0],
            # no window of its own: runs in the background (no Dock icon), the
            # UI is in the browser, quit from its sidebar
            "LSUIElement": True,
            "NSHighResolutionCapable": True,
        },
    )
