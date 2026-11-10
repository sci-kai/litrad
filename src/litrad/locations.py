"""Where the configuration and the data live.

Which config file is used (first match wins):

1. ``--config``/``-c PATH`` on the command line;
2. the environment variable ``LITRAD_CONFIG``; if that file does not
   exist yet, the first run creates it from the bundled example, with the
   data in the same folder (the Docker image sets ``/data/config.yaml``);
3. ``config/config.yaml`` in the current folder (a git installation run
   from its folder);
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

The functions here are pure path logic plus ``first_run_setup``; they work
on Linux, Windows and macOS.
"""

import os
from dataclasses import dataclass
from importlib import resources
from pathlib import Path, PurePath

import platformdirs

APP = "litrad"
ENV_CONFIG = "LITRAD_CONFIG"
GIT_CONFIG = Path("config") / "config.yaml"
CONFIG_NAME = "config.yaml"
EXAMPLE_NAME = "config.example.yaml"

# how each rule is described by `litrad where`
CONFIG_RULES = {
    "option": "given with --config",
    "env": f"from the environment variable {ENV_CONFIG}",
    "git": "config/config.yaml in the current folder (git installation)",
    "user": "the per-user config file",
    "first-run": "the per-user config file, created now from the example",
    "missing": "none yet; the first command that needs it creates it",
}
BASE_RULES = {
    "explicit": "paths.base_dir in the config file",
    "git": "the folder that contains config/",
    "config": "the config file's folder",
}

# the data paths of a new per-user config, relative to the per-user data folder
USER_PATHS = {
    "paths": {
        "profiles_dir": "profiles",
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


def user_config_file() -> Path:
    return user_config_dir() / CONFIG_NAME


@dataclass(frozen=True)
class ConfigChoice:
    path: Path
    # option | env | git | user | first-run | missing (see CONFIG_RULES)
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
    from_env = env.get(ENV_CONFIG)
    if from_env:
        return ConfigChoice(_absolute(Path(from_env).expanduser(), cwd), "env")
    git = cwd / GIT_CONFIG
    if git.is_file():
        return ConfigChoice(git.absolute(), "git")
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
        return folder.parent, "git"
    return folder, "config"


def resolve_path(base: PurePath, path: PurePath) -> PurePath:
    """``path`` (``~`` expanded) resolved against ``base`` unless it is absolute."""
    if str(path).startswith("~"):
        path = type(path)(os.path.expanduser(str(path)))
    return path if path.is_absolute() else base / path


def _absolute(path: Path, cwd: Path) -> Path:
    return path if path.is_absolute() else cwd / path
