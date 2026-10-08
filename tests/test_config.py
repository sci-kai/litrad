from pathlib import Path

import pytest
from pydantic import ValidationError

from litresearch.config import Config, load_config

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


def test_old_detail_skim_keys_are_migrated():
    cfg = Config.model_validate(
        {"paths": {"bibtex": "x.bib"}, "selection": {"n_detail": 2, "n_skim": 3}}
    )
    assert cfg.selection.n_recommendations == 5
    capped = Config.model_validate(
        {"paths": {"bibtex": "x.bib"}, "selection": {"n_detail": 4, "n_skim": 4}}
    )
    assert capped.selection.n_recommendations == 5
