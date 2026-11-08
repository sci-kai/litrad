#!/usr/bin/env python3
"""Keep the copies of repository files that ship inside the package identical.

``docs/documentation.md`` is the source of the documentation and
``config/config.example.yaml`` of the example configuration; the package
carries real copies (not symlinks, which break in wheels and on Windows) so
they are installed with it. Edit the sources, then run::

    python tools/sync_docs.py          # copy the sources over the package files
    python tools/sync_docs.py --check  # only report differences (exit code 1)
"""

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# (source, copy inside the package)
PAIRS = [
    (ROOT / "docs" / "documentation.md", ROOT / "src" / "litresearch" / "web" / "documentation.md"),
    (
        ROOT / "config" / "config.example.yaml",
        ROOT / "src" / "litresearch" / "config.example.yaml",
    ),
]
FIX_HINT = "run: python tools/sync_docs.py"


def _name(path: Path) -> str:
    return str(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path)


def differing() -> list[tuple[Path, Path]]:
    return [
        (src, copy)
        for src, copy in PAIRS
        if not copy.is_file() or src.read_bytes() != copy.read_bytes()
    ]


def main(argv: list[str]) -> int:
    stale = differing()
    if "--check" in argv:
        for src, copy in stale:
            print(f"{_name(copy)} differs from {_name(src)}")
        return 1 if stale else 0
    for src, copy in stale:
        shutil.copyfile(src, copy)
        print(f"updated {_name(copy)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
