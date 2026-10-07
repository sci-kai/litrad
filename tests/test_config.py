from pathlib import Path

import pytest
from pydantic import ValidationError

from litresearch.config import Config, load_config

EXAMPLE = Path(__file__).parents[1] / "config" / "config.example.yaml"


def test_example_config_loads():
    cfg = load_config(EXAMPLE)
    assert cfg.topics
    assert cfg.paths.obsidian_vault is None  # "n/a" -> None
    assert cfg.selection.n_detail + cfg.selection.n_skim <= 5


def test_more_than_five_papers_rejected():
    with pytest.raises(ValidationError):
        Config.model_validate(
            {"paths": {"bibtex": "x.bib"}, "selection": {"n_detail": 3, "n_skim": 3}}
        )


def test_local_embeddings_need_url():
    with pytest.raises(ValidationError):
        Config.model_validate({"paths": {"bibtex": "x.bib"}, "embeddings": {"backend": "local"}})
