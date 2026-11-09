"""Where the configuration and the data live.

Which config file is used (first match wins):

1. ``--config``/``-c PATH`` on the command line;
2. the environment variable ``LITRAD_CONFIG`` (or its name before the
   rename, ``LITRESEARCH_CONFIG``; the new name wins); if that file does not
   exist yet, the first run creates it from the bundled example, with the
   data in the same folder (the Docker image sets ``/data/config.yaml``);
3. ``config/config.yaml`` in the current folder (a git installation run
   from its folder, as before 0.3);
4. the per-user config file, e.g. ``~/.config/litrad/config.yaml`` on
   Linux (``platformdirs.user_config_dir``). If it does not exist yet, the
   first run creates it from the bundled example, with the data in the
   per-user data folder (``platformdirs.user_data_dir``).

Relative ``paths.*`` and ``backup.dir`` are resolved against the *base
folder*, never against the current folder:

1. ``paths.base_dir`` if the config sets it (relative: from the config's folder);
2. the folder that contains ``config/`` when the config file is in a folder
   named ``config`` (a git installation: ``data/`` next to ``config/``);
3. otherwise the config file's folder.

Renamed from litresearch: the per-user folders of litresearch
(``~/.config/litresearch``, ``~/.local/share/litresearch``, ...) are moved to
the litrad folders on the first start (``migrate_user_dirs``), unless the new
folders exist already.

The functions here are pure path logic plus ``first_run_setup`` and
``migrate_user_dirs``; they work on Linux, Windows and macOS.
"""

import logging
import os
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from importlib import resources
from pathlib import Path, PurePath

import platformdirs

APP = "litrad"
ENV_CONFIG = "LITRAD_CONFIG"
# the names before the rename to litrad (0.3); still understood
LEGACY_APP = "litresearch"
LEGACY_ENV_CONFIG = "LITRESEARCH_CONFIG"
MOVED_NOTE = f"MOVED-TO-{APP}.txt"
LEGACY_CONFIG = Path("config") / "config.yaml"
CONFIG_NAME = "config.yaml"
EXAMPLE_NAME = "config.example.yaml"

# how each rule is described by `litrad where`
CONFIG_RULES = {
    "option": "given with --config",
    "env": f"from the environment variable {ENV_CONFIG} (or the old {LEGACY_ENV_CONFIG})",
    "legacy": "config/config.yaml in the current folder (git installation)",
    "user": "the per-user config file",
    "first-run": "the per-user config file, created now from the example",
    "missing": "none yet; the first command that needs it creates it",
}
BASE_RULES = {
    "explicit": "paths.base_dir in the config file",
    "legacy": "the folder that contains config/",
    "config": "the config file's folder",
}

# the data paths of a new per-user config, relative to the per-user data folder
USER_PATHS = {
    "paths": {
        "profiles_dir": "profiles",
        "bibtex": "paperpile/library.bib",
        "database": f"{APP}.sqlite",
        "cache_dir": "cache",
    },
    "backup": {"dir": "backups"},
}


def user_config_dir() -> Path:
    """Per-user config folder (``~/.config/litrad`` on Linux, ...)."""
    return Path(platformdirs.user_config_dir(APP, appauthor=False))


def user_data_dir() -> Path:
    """Per-user data folder (``~/.local/share/litrad`` on Linux, ...)."""
    return Path(platformdirs.user_data_dir(APP, appauthor=False))


def legacy_user_config_dir() -> Path:
    """The per-user config folder of litresearch (before the rename)."""
    return Path(platformdirs.user_config_dir(LEGACY_APP, appauthor=False))


def legacy_user_data_dir() -> Path:
    """The per-user data folder of litresearch (before the rename)."""
    return Path(platformdirs.user_data_dir(LEGACY_APP, appauthor=False))


def user_config_file() -> Path:
    return user_config_dir() / CONFIG_NAME


@dataclass(frozen=True)
class ConfigChoice:
    path: Path
    # option | env | legacy | user | first-run | missing (see CONFIG_RULES)
    rule: str

    @property
    def description(self) -> str:
        return CONFIG_RULES[self.rule]


def find_config(
    option: Path | None = None, env: dict | None = None, cwd: Path | None = None
) -> ConfigChoice:
    """The config file to use, by the lookup order above; creates nothing.

    The rule is ``missing`` when it falls through to a per-user file that does
    not exist yet.
    """
    env = os.environ if env is None else env
    cwd = Path.cwd() if cwd is None else cwd
    if option is not None:
        return ConfigChoice(_absolute(Path(option), cwd), "option")
    from_env = env.get(ENV_CONFIG) or env.get(LEGACY_ENV_CONFIG)
    if from_env:
        return ConfigChoice(_absolute(Path(from_env).expanduser(), cwd), "env")
    legacy = cwd / LEGACY_CONFIG
    if legacy.is_file():
        return ConfigChoice(legacy.absolute(), "legacy")
    user = user_config_file()
    return ConfigChoice(user, "user" if user.exists() else "missing")


def locate_config(option: Path | None = None, announce=None) -> ConfigChoice:
    """Like ``find_config``, but creates the per-user config on the first run.

    ``announce`` receives a message naming the created file.
    """
    choice = find_config(option)
    if choice.rule == "env" and not choice.path.exists():
        # a container or service points LITRAD_CONFIG at a fresh folder
        path = first_run_setup(choice.path, choice.path.parent)
        if announce is not None:
            announce(f"Created your configuration {path} (data in {path.parent}).")
        return ConfigChoice(path, "env")
    if choice.rule != "missing":
        return choice
    path = first_run_setup(choice.path, user_data_dir())
    if announce is not None:
        announce(
            f"Created your configuration {path} (data in {user_data_dir()}). "
            "Edit it to add API keys; 'litrad where' shows all locations."
        )
    return ConfigChoice(path, "first-run")


def example_text() -> str:
    """The bundled ``config.example.yaml``."""
    return resources.files("litrad").joinpath(EXAMPLE_NAME).read_text(encoding="utf-8")


def first_run_setup(target: Path, data_dir: Path) -> Path:
    """Write a config file from the example with its data in ``data_dir``."""
    from litrad.backup import atomic_write_text
    from litrad.yamlfile import updated_text

    updates = {"paths": {"base_dir": str(data_dir), **USER_PATHS["paths"]}}
    updates["backup"] = USER_PATHS["backup"]
    target.parent.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(target, updated_text(example_text(), updates))
    return target


def base_dir(config_path: PurePath, explicit: PurePath | None = None) -> tuple[PurePath, str]:
    """The folder relative paths are resolved against, and the rule that chose it.

    ``config_path`` must be absolute; pure-path logic, so it can be tested
    with Windows paths on Linux.
    """
    folder = config_path.parent
    if explicit is not None:
        return (explicit if explicit.is_absolute() else folder / explicit), "explicit"
    if folder.name == "config":
        return folder.parent, "legacy"
    return folder, "config"


def resolve_path(base: PurePath, path: PurePath) -> PurePath:
    """``path`` (``~`` expanded) resolved against ``base`` unless it is absolute."""
    if str(path).startswith("~"):
        path = type(path)(os.path.expanduser(str(path)))
    return path if path.is_absolute() else base / path


def _absolute(path: Path, cwd: Path) -> Path:
    return path if path.is_absolute() else cwd / path


# --- renamed from litresearch: moving the per-user folders -------------------------------

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FolderMove:
    old: Path
    new: Path
    # moved | copied (another drive: the old folder still holds the files) | kept (the
    # new folder existed already; both stay, the new one is used)
    action: str

    def message(self) -> str:
        if self.action == "moved":
            return f"Renamed to {APP}: moved {self.old} to {self.new}"
        if self.action == "copied":
            return (
                f"Renamed to {APP}: copied {self.old} to {self.new}; the old folder can be "
                "deleted once everything works"
            )
        return f"Old {LEGACY_APP} folder {self.old} is not used ({self.new} exists)"


def _folder_pairs() -> list[tuple[Path, Path]]:
    """(old, new) per-user folders; one pair where config and data share a folder."""
    pairs = []
    for old, new in (
        (legacy_user_config_dir(), user_config_dir()),
        (legacy_user_data_dir(), user_data_dir()),
    ):
        if (old, new) not in pairs:
            pairs.append((old, new))
    return pairs


def _has_files(folder: Path) -> bool:
    """A folder with anything but the note that it moved."""
    try:
        return folder.is_dir() and any(p.name != MOVED_NOTE for p in folder.iterdir())
    except OSError:
        return False


def leftover_legacy_dirs() -> list[Path]:
    """Old litresearch folders that still hold files (shown by ``litrad where``)."""
    return [old for old, _ in _folder_pairs() if _has_files(old)]


def _move_folder(old: Path, new: Path) -> str:
    """Move ``old`` to ``new``; across drives copy it (the original stays)."""
    new.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(old, new)
        return "moved"
    except OSError:
        pass  # e.g. another drive (EXDEV): copy instead
    partial = new.with_name(f".{new.name}.partial")
    shutil.rmtree(partial, ignore_errors=True)
    try:
        shutil.copytree(old, partial, symlinks=True)
        os.rename(partial, new)
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    return "copied"


def _leave_note(move: FolderMove) -> None:
    try:
        move.old.mkdir(parents=True, exist_ok=True)
        what = "moved" if move.action == "moved" else "copied (the files here are the originals)"
        (move.old / MOVED_NOTE).write_text(
            f"{LEGACY_APP} is now called {APP}.\n"
            f"On {datetime.now().isoformat('T', 'seconds')} this folder was {what} to\n"
            f"{move.new}\n"
            "You can delete this folder.\n",
            encoding="utf-8",
        )
    except OSError as error:  # a note is a courtesy, never a reason to fail
        log.warning("Could not write %s: %s", move.old / MOVED_NOTE, error)


def moved_path(value: str, moved: dict[Path, Path]) -> str | None:
    """``value`` pointing into a moved folder, pointed into its new place (None: unchanged)."""
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(os.path.expanduser(value.strip()))
    if not path.is_absolute():
        return None
    for old, new in moved.items():
        if path == old or path.is_relative_to(old):
            return str(new / path.relative_to(old))
    return None


def _rewrite_yaml(path: Path, moved: dict[Path, Path], sections: dict[str, tuple]) -> bool:
    """Point absolute paths in a YAML file into the moved folders; comments stay."""
    import io

    from ruamel.yaml import YAML

    from litrad.backup import atomic_write_text

    if not path.is_file():
        return False
    yaml = YAML()
    yaml.preserve_quotes = True
    try:
        document = yaml.load(path.read_text(encoding="utf-8"))
    except Exception as error:  # an invalid file is reported when it is used
        log.warning("Could not update the paths in %s: %s", path, error)
        return False
    changed = False
    for section, keys in sections.items():
        node = document.get(section) if isinstance(document, dict) else None
        if not isinstance(node, dict):
            continue
        for key in keys or list(node):
            new = moved_path(node.get(key), moved)
            if new is not None:
                node[key] = new
                changed = True
    if changed:
        buffer = io.StringIO()
        yaml.dump(document, buffer)
        shutil.copy2(path, path.with_name(path.name + ".bak"))
        atomic_write_text(path, buffer.getvalue())
    return changed


def _rewrite_library_files(database: Path, moved: dict[Path, Path]) -> None:
    """Libraries remember the file they were imported from (``Library.source_path``)."""
    try:
        connection = sqlite3.connect(database, timeout=10)
    except sqlite3.Error:
        return
    try:
        columns = {r[1] for r in connection.execute("PRAGMA table_info(library)")}
        if "source_path" not in columns:
            return
        rows = connection.execute("SELECT id, source_path FROM library").fetchall()
        with connection:
            for row_id, source in rows:
                new = moved_path(source, moved)
                if new is not None:
                    connection.execute(
                        "UPDATE library SET source_path = ? WHERE id = ?", (new, row_id)
                    )
    except sqlite3.Error as error:
        log.warning("Could not update the library files in %s: %s", database, error)
    finally:
        connection.close()


def _rewrite_paths(moved: dict[Path, Path]) -> None:
    """First-run configs store absolute paths (``paths.base_dir``) into the data folder."""
    config = user_config_file()
    _rewrite_yaml(config, moved, {"paths": (), "backup": ("dir",)})
    for settings in sorted(user_data_dir().glob("profiles/*/settings.yaml")):
        _rewrite_yaml(settings, moved, {"paths": ()})
    for database in sorted(user_data_dir().glob("profiles/*/*.sqlite")):
        _rewrite_library_files(database, moved)


def migrate_user_dirs() -> list[FolderMove]:
    """Move the per-user folders of litresearch to the litrad ones (once).

    A new folder that exists already is never overwritten: it is used, and the
    old one is reported (``kept``). Absolute paths into the old data folder in
    ``config.yaml``, the profiles' ``settings.yaml`` and their libraries are
    pointed to the new one. Raises ``OSError`` if a folder cannot be moved.
    """
    results, moved = [], {}
    for old, new in _folder_pairs():
        if old == new or not _has_files(old):
            continue
        if new.exists():
            results.append(FolderMove(old, new, "kept"))
            continue
        move = FolderMove(old, new, _move_folder(old, new))
        _leave_note(move)
        moved[old] = new
        results.append(move)
        log.info("%s", move.message())
    if moved:
        _rewrite_paths(moved)
    return results
