#!/usr/bin/env python3
"""Entry point of the PyInstaller desktop builds (see litrad.spec)."""

import os
import sys

# windowed builds (Windows, macOS) have no console: sys.stdout and sys.stderr
# are None, and libraries that print would fail
for name in ("stdout", "stderr"):
    if getattr(sys, name) is None:
        setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))  # noqa: SIM115

from litrad.desktop import main  # noqa: E402

if __name__ == "__main__":
    main()
