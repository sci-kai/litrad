"""litresearch is now litrad: ``import litresearch.x`` gives the module ``litrad.x``.

Only for git installations from before the rename whose virtual environment
still has the old editable install: after ``git pull`` its
``.venv/bin/litresearch`` imports ``litresearch.cli`` from ``src/``, until
``python3 install.py`` installs litrad. Not part of the wheel (see
pyproject.toml); removed together with the ``litresearch`` command alias.
"""

import importlib
import importlib.abc
import importlib.util
import sys

import litrad

OLD, NEW = "litresearch", "litrad"


class _Alias(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """``litresearch.x`` is the module ``litrad.x`` itself (no second copy)."""

    def find_spec(self, fullname, path, target=None):
        if fullname.startswith(OLD + "."):
            return importlib.util.spec_from_loader(fullname, self)
        return None

    def create_module(self, spec):
        return importlib.import_module(NEW + spec.name[len(OLD) :])

    def exec_module(self, module):
        pass


if not any(isinstance(finder, _Alias) for finder in sys.meta_path):
    sys.meta_path.insert(0, _Alias())
if sys.stderr is not None:
    print(
        "Note: litresearch is now called litrad; run 'python3 install.py' to update "
        "this installation.",
        file=sys.stderr,
    )
sys.modules[__name__] = litrad
