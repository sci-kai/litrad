from pathlib import Path

import pytest
from pydantic import ValidationError

from litrad.config import Config, ConfigError, load_config

EXAMPLE = Path(__file__).parents[1] / "config" / "config.example.yaml"


def test_example_config_loads():
    cfg = load_config(EXAMPLE)
    assert cfg.topics
    assert cfg.paths.obsidian_vault is None  # "n/a" -> None
    assert 1 <= cfg.selection.n_recommendations <= 5


def test_more_than_five_papers_rejected():
    with pytest.raises(ValidationError):
        Config.model_validate({"paths": {"bibtex": "x.bib"}, "selection": {"n_recommendations": 6}})


def test_local_embeddings_need_url():
    with pytest.raises(ValidationError):
        Config.model_validate({"paths": {"bibtex": "x.bib"}, "embeddings": {"backend": "local"}})


@pytest.mark.parametrize("key", ["database", "bibtex"])
def test_profile_paths_are_no_setting_of_config_yaml(tmp_path, key):
    path = tmp_path / "config.yaml"
    path.write_text(f"paths:\n  {key}: data/x\n")
    with pytest.raises(ConfigError, match=f"paths.{key} is not a setting of config.yaml"):
        load_config(path)


@pytest.mark.parametrize("value", [".inf", "-.inf", ".nan"])
def test_infinite_numbers_rejected(tmp_path, value):
    from litrad.config import ConfigError

    path = tmp_path / "config.yaml"
    path.write_text(f"paths: {{}}\nbackup:\n  interval_hours: {value}\n")
    with pytest.raises(ConfigError, match="finite"):
        load_config(path)
    path.write_text(f"paths: {{}}\nselection:\n  mmr_lambda: {value}\n")
    with pytest.raises(ConfigError):
        load_config(path)
