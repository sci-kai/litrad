# Development

## Git workflow (CUBI conventions)

- `main`: releases only; `dev` is fast-forwarded into it, with no direct commits.
- `dev`: integration branch, kept linear.
- Feature work goes on `feat-<short-name>` (or `issueNN-<short-name>`), branched
  from `dev`. Clean up with an interactive rebase, then rebase-and-merge into
  `dev` and delete the branch. No merge commits, no squash merges.
- A version bump is its own commit (`pyproject.toml`, `__init__.py`,
  `CITATION.cff`, `CHANGELOG.md`), with no code changes. Dev releases carry the
  `-dev` suffix, e.g. `0.2.0-dev`.
- Push target: GitLab@HHU (internal).

```bash
git switch dev && git switch -c feat-scoring
# ... commits ...
git rebase dev                   # keep history linear
git switch dev && git merge --ff-only feat-scoring
git branch -d feat-scoring
```

## Tests

```bash
.venv/bin/pytest                         # offline suite (default)
.venv/bin/pytest --network               # also hit the real APIs
.venv/bin/pytest --cov=litresearch --cov-report=term-missing
```

- The default suite never touches the network. `tests/conftest.py::FakeApis`
  serves Crossref, OpenAlex and Semantic Scholar from recorded responses in
  `tests/data/api_responses.json`, through `httpx.MockTransport`.
- `tests/data/paperpile_sample.bib` is a synthetic export that reproduces every
  Paperpile quirk seen so far: month/day values with non-breaking spaces,
  folder/label keywords, LaTeX, `@BOOK`/`@INCOLLECTION`, undated, PMID-only and
  DOI-in-URL entries. It uses public paper metadata with invented folder names.
  Add new quirks here when you meet them.
- Warnings are turned into errors (`filterwarnings = ["error"]`), which catches
  e.g. unclosed SQLite connections.
- Tests marked `@pytest.mark.network` only run with `--network`.

| Test module | Covers |
|---|---|
| `test_bibtex.py` | parser helpers and the sample export |
| `test_http.py` | cache, retries, `Retry-After`, throttling, error handling |
| `test_sources.py` | Crossref match rules, OpenAlex/S2 parsing and batching |
| `test_pipeline.py` | end-to-end ingest, idempotency, removal, adoption, duplicates, source outages |
| `test_scoring.py` | classification, impact/timeliness, kNN, MMR, ranking, TF-IDF fallback |
| `test_recommend.py` | candidate gathering, filters, cooldown, weekly batches, S2 negatives, outages |
| `test_reading.py` | references, queue order/limits, reading log, notes, ratings |
| `test_game.py` | XP rules, anti-farming, bonuses, streaks, quests, badges, levels, statistics |
| `test_cli.py` | all CLI commands (plain console, fake APIs) |
| `test_web.py` | every page, actions, celebrations, penalties, settings round trip (comments kept), same-origin and redirect checks, TSV export |
| `test_config.py`, `test_db.py` | config validation, status history |
| `test_live.py` | smoke test against the real APIs (`--network`) |

## Lint and format

```bash
.venv/bin/ruff check src tests
.venv/bin/ruff format src tests
```

## Recording new API fixtures

Run a real `litresearch ingest` once, then take the responses you need from
`data/cache/http_cache.sqlite` (table `cache`, JSON in column `body`). Trim them
before committing: shorten embeddings to a few dimensions and drop
affiliations.
