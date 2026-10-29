from datetime import date

import pytest

from conftest import SAMPLE_BIB
from litresearch.ingest.bibtex import (
    clean_text,
    make_date,
    normalize_doi,
    parse_bibtex,
    parse_month_day,
    parse_year,
    split_keywords,
)


@pytest.fixture(scope="module")
def entries():
    return {e.key: e for e in parse_bibtex(SAMPLE_BIB, ["Import Mendeley"])}


@pytest.mark.parametrize(
    "value, expected",
    [
        ("10.1038/S41588-022-01141-9", "10.1038/s41588-022-01141-9"),
        ("https://doi.org/10.1126/science.aaf6546", "10.1126/science.aaf6546"),
        ("http://dx.doi.org/10.1/x", "10.1/x"),
        ("doi: 10.1/x", "10.1/x"),
        ("https://www.doi.org/10.1/X", "10.1/x"),
        ("HTTPS://DX.DOI.ORG/10.1/X", "10.1/x"),
        ("DOI:10.1/X", "10.1/x"),
        ("http://www.nature.com/articles/abc", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_doi(value, expected):
    assert normalize_doi(value) == expected


@pytest.mark.parametrize(
    "value, expected",
    [
        ("apr", (4, None)),
        ("5", (5, None)),
        ("12", (12, None)),
        ("13", (None, None)),
        ('"5\xa0" # apr', (4, 5)),
        ('"17\xa0" # dec', (12, 17)),
        ("September", (9, None)),
        ("", (None, None)),
        (None, (None, None)),
        ("spring", (None, None)),
    ],
)
def test_parse_month_day(value, expected):
    assert parse_month_day(value) == expected


def test_make_date_handles_invalid_day_and_missing_parts():
    assert make_date(2022, 2, 30) == date(2022, 2, 1)
    assert make_date(2022, 8, 1) == date(2022, 8, 1)
    assert make_date(2022, None, 3) is None
    assert make_date(None, 4, 3) is None


def test_parse_year():
    assert parse_year("2017") == 2017
    assert parse_year("in press 2024") == 2024
    assert parse_year("n.d.") is None


def test_split_keywords_separates_folders_and_tags():
    folders, tags = split_keywords(
        "PhD/Thesis;03 Biology /\n   Medicine/Cancer;metastasis;Import Mendeley;metastasis",
        {"import mendeley"},
    )
    assert folders == ["PhD/Thesis", "03 Biology / Medicine/Cancer"]
    assert tags == ["metastasis"]


def test_clean_text_collapses_wrapping():
    assert clean_text("A  long\n     title {PD1}") == "A long title PD1"
    assert clean_text("   ") is None


def test_sample_parses_completely(entries):
    assert len(entries) == 7


def test_full_article(entries):
    e = entries["Barkley2022-ri"]
    assert e.entry_type == "article"
    assert e.title.startswith(
        "Cancer cell states recur across tumor types and form specific interactions"
    )
    assert "\n" not in e.title
    assert e.authors[0] == "Barkley, Dalia"
    assert e.authors[2] == "França, Gustavo S"
    assert e.year == 2022 and e.pub_date == date(2022, 8, 1)
    assert e.doi == "10.1038/s41588-022-01141-9"
    assert e.pmid == "35931863"
    assert e.journal == "Nature Genetics"
    assert e.folders == [
        "Projects/Example A/Interactions",
        "01 Biology / Medicine/Cancer/Tumor Microenvironment",
    ]
    assert e.tags == []


def test_doi_taken_from_url_and_latex_decoded(entries):
    e = entries["Cheung2016-vl"]
    assert e.doi == "10.1126/science.aaf6546"
    assert e.pub_date == date(2016, 4, 1)
    assert "25%" in e.abstract
    assert e.tags == ["metastasis", "circulating tumor cells"]


def test_braces_removed_from_title(entries):
    assert "anti-PD1" in entries["Bassez2021-fu"].title
    assert entries["Bassez2021-fu"].entry_type == "book"


def test_undated_entry(entries):
    e = entries["Johnson_undated-wl"]
    assert e.year is None and e.pub_date is None and e.doi is None


def test_pmid_only_and_day_month(entries):
    e = entries["Hofmann2003-af"]
    assert e.doi is None and e.pmid == "14678978"
    assert e.pub_date == date(2003, 12, 15)
    assert e.folders == [] and e.tags == []


def test_incollection_with_bare_month(entries):
    e = entries["Olivero2018-ol"]
    assert e.entry_type == "incollection"
    assert e.pub_date == date(2018, 5, 1)
    assert e.tags == ["Stemness"]


def test_broken_file_raises(tmp_path):
    bad = tmp_path / "bad.bib"
    bad.write_text('@ARTICLE{x,\n  title = "unterminated\n', encoding="utf-8")
    with pytest.raises(ValueError, match="failed to parse"):
        parse_bibtex(bad)


def test_duplicate_keys_are_collected(tmp_path):
    path = tmp_path / "dup.bib"
    path.write_text(
        '@ARTICLE{a1, title = "First"}\n@ARTICLE{a1, title = "Second"}\n', encoding="utf-8"
    )
    duplicates = []
    entries = parse_bibtex(path, duplicate_keys=duplicates)
    assert [e.title for e in entries] == ["First"]
    assert duplicates == ["a1"]
    # without a list to collect them, duplicates are treated as errors
    with pytest.raises(ValueError):
        parse_bibtex(path)
