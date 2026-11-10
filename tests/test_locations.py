"""Where the config file and the data are found (litrad.locations)."""

import os
import stat
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest
from typer.testing import CliRunner

from litrad import locations
from litrad.cli import app
from litrad.config import load_config

REPO = Path(__file__).parents[1]
MINIMAL = "paths:\n  profiles_dir: data/profiles\n"


def write(path: Path, text: str = MINIMAL) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --- lookup order --------------------------------------------------------------------


def test_option_wins_over_everything(tmp_path):
    write(tmp_path / "config" / "config.yaml")
    write(locations.user_config_file())
    choice = locations.find_config(Path("other.yaml"), env={"LITRAD_CONFIG": "env.yaml"})
    assert choice == locations.ConfigChoice(tmp_path / "other.yaml", "option")


def test_environment_variable_wins_over_the_current_folder(tmp_path):
    write(tmp_path / "config" / "config.yaml")
    choice = locations.find_config(env={"LITRAD_CONFIG": str(tmp_path / "env.yaml")})
    assert choice == locations.ConfigChoice(tmp_path / "env.yaml", "env")


def test_environment_variable_from_the_real_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("LITRAD_CONFIG", "my.yaml")
    assert locations.find_config() == locations.ConfigChoice(tmp_path / "my.yaml", "env")


def test_git_folder_wins_over_the_user_config(tmp_path):
    write(tmp_path / "config" / "config.yaml")
    write(locations.user_config_file())
    choice = locations.find_config(env={})
    assert choice == locations.ConfigChoice(tmp_path / "config" / "config.yaml", "git")


def test_user_config_last(tmp_path):
    assert locations.find_config(env={}).rule == "missing"
    write(locations.user_config_file())
    choice = locations.find_config(env={})
    assert choice == locations.ConfigChoice(locations.user_config_file(), "user")


def test_user_folders_are_redirected_in_tests(tmp_path):
    assert locations.user_config_dir().is_relative_to(tmp_path)
    assert locations.user_data_dir().is_relative_to(tmp_path)


# --- first run -----------------------------------------------------------------------


def test_first_run_creates_the_user_config(tmp_path):
    messages = []
    choice = locations.locate_config(None, announce=messages.append)
    assert choice == locations.ConfigChoice(locations.user_config_file(), "first-run")
    assert str(choice.path) in messages[0]
    cfg = load_config(choice.path)
    data = locations.user_data_dir()
    assert cfg.paths.base_dir == data
    assert cfg.paths.profiles_dir == data / "profiles"
    assert cfg.paths.profiles_dir == data / "profiles"
    assert cfg.paths.cache_dir == data / "cache"
    assert cfg.backup.dir == data / "backups"
    # comments of the example are kept
    assert "# one folder per profile" in choice.path.read_text(encoding="utf-8")
    if os.name == "posix":  # API keys and the password hash go here
        assert stat.S_IMODE(choice.path.stat().st_mode) == 0o600
    # the second run uses it without creating anything
    assert locations.locate_config(None, announce=messages.append).rule == "user"
    assert len(messages) == 1


def test_first_run_from_the_command_line(tmp_path):
    result = CliRunner().invoke(app, ["profile", "create", "Ada", "--yes"])
    assert result.exit_code == 0, result.output
    assert locations.user_config_file().exists()
    assert "Created your configuration" in result.output
    assert (locations.user_data_dir() / "profiles" / "ada" / "litrad.sqlite").exists()
    # nothing in the current folder
    assert not (tmp_path / "config").exists() and not (tmp_path / "data").exists()


def test_bundled_example_matches_the_repository_example():
    bundled = (REPO / "src" / "litrad" / "config.example.yaml").read_bytes()
    assert (REPO / "config" / "config.example.yaml").read_bytes() == bundled
    assert locations.example_text().encode("utf-8") == bundled


# --- base folder ---------------------------------------------------------------------


def test_git_layout_resolves_like_before(tmp_path):
    """config/config.yaml run from its folder: the same paths as before 0.3."""
    write(
        tmp_path / "config" / "config.yaml", REPO.joinpath("config/config.example.yaml").read_text()
    )
    cfg = load_config(Path("config/config.yaml"))
    assert cfg.paths.profiles_dir == tmp_path / "data" / "profiles"
    assert cfg.paths.profiles_dir == tmp_path / "data" / "profiles"
    assert cfg.paths.cache_dir == tmp_path / "data" / "cache"
    assert cfg.backup.dir == tmp_path / "data" / "backups"
    assert cfg.paths.obsidian_vault is None


def test_relative_paths_do_not_depend_on_the_current_folder(tmp_path, monkeypatch):
    home = tmp_path / "install"
    config = write(home / "config" / "config.yaml")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert load_config(config).paths.profiles_dir == home / "data" / "profiles"
    flat = write(tmp_path / "flat" / "settings.yaml")
    assert load_config(flat).paths.profiles_dir == tmp_path / "flat" / "data" / "profiles"


def test_explicit_base_dir(tmp_path):
    text = "paths:\n  base_dir: store\n  profiles_dir: profiles\n  cache_dir: /abs/cache\n"
    cfg = load_config(write(tmp_path / "config" / "config.yaml", text))
    # relative base_dir: from the config file's folder
    assert cfg.paths.profiles_dir == tmp_path / "config" / "store" / "profiles"
    assert cfg.paths.cache_dir == Path("/abs/cache")
    text = f"paths:\n  base_dir: {tmp_path / 'abs'}\n"
    cfg = load_config(write(tmp_path / "c.yaml", text))
    assert cfg.backup.dir == tmp_path / "abs" / "data" / "backups"


def test_home_folder_in_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    cfg = load_config(write(tmp_path / "c.yaml", "paths: {}\nbackup:\n  dir: ~/sync\n"))
    assert cfg.backup.dir == tmp_path / "home" / "sync"


def test_profile_settings_paths_use_the_same_base(tmp_path, monkeypatch):
    from litrad import profiles

    base = load_config(write(tmp_path / "config" / "config.yaml"))
    slug = profiles.create_profile(base, "Ada", settings={"paths": {"bibtex": "exports/a.bib"}})
    monkeypatch.chdir(tmp_path / "config")
    cfg = profiles.profile_config(load_config(tmp_path / "config" / "config.yaml"), slug)
    assert cfg.paths.bibtex == tmp_path / "exports" / "a.bib"
    assert cfg.paths.database == tmp_path / "data" / "profiles" / slug / "litrad.sqlite"
    assert cfg.backup.dir == tmp_path / "data" / "backups" / slug


@pytest.mark.parametrize(
    "config, explicit, expected, rule",
    [
        ("C:/Users/ada/litrad/config/config.yaml", None, "C:/Users/ada/litrad", "git"),
        ("C:/Users/ada/AppData/Local/litrad/config.yaml", None,
         "C:/Users/ada/AppData/Local/litrad", "config"),
        ("C:/x/config.yaml", "D:/data", "D:/data", "explicit"),
        ("C:/x/config.yaml", "data", "C:/x/data", "explicit"),
    ],
)  # fmt: skip
def test_base_dir_with_windows_paths(config, explicit, expected, rule):
    found = locations.base_dir(
        PureWindowsPath(config), PureWindowsPath(explicit) if explicit else None
    )
    assert found == (PureWindowsPath(expected), rule)
    db = locations.resolve_path(found[0], PureWindowsPath("data/litrad.sqlite"))
    assert db == PureWindowsPath(expected) / "data" / "litrad.sqlite"
    assert locations.resolve_path(found[0], PureWindowsPath("E:/abs.sqlite")) == PureWindowsPath(
        "E:/abs.sqlite"
    )


def test_base_dir_with_posix_paths():
    assert locations.base_dir(PurePosixPath("/srv/lit/config/config.yaml")) == (
        PurePosixPath("/srv/lit"),
        "git",
    )


# --- litrad where ---------------------------------------------------------------


def test_where_in_a_git_folder(tmp_path):
    write(tmp_path / "config" / "config.yaml")
    result = CliRunner().invoke(app, ["where"])
    assert result.exit_code == 0, result.output
    assert str(tmp_path / "config" / "config.yaml") in result.output
    assert "config/config.yaml in the current folder" in result.output
    assert "the folder that contains config/" in result.output
    assert str(tmp_path / "data" / "profiles") in result.output
    assert str(tmp_path / "data" / "backups") in result.output
    assert str(tmp_path / "data" / "cache") in result.output


def test_where_before_the_first_run_creates_nothing(tmp_path):
    result = CliRunner().invoke(app, ["where"])
    assert result.exit_code == 0, result.output
    assert "none yet" in result.output
    assert str(locations.user_data_dir()) in result.output
    assert not locations.user_config_file().exists()


def test_where_with_option_and_environment(tmp_path, monkeypatch):
    config = write(tmp_path / "lab" / "lit.yaml")
    result = CliRunner().invoke(app, ["where", "-c", str(config)])
    assert "given with --config" in result.output
    assert "the config file's folder" in result.output
    assert str(tmp_path / "lab" / "data" / "profiles") in result.output
    monkeypatch.setenv("LITRAD_CONFIG", str(config))
    result = CliRunner().invoke(app, ["where"])
    assert "LITRAD_CONFIG" in result.output
