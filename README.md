# litresearch

A personal literature radar built on a Paperpile library. On each run it
will propose **up to 5 papers**, split into **read in detail** and **skim**,
ranked by:

1. similarity to your Paperpile library (SPECTER2 embeddings),
2. the topics you define,
3. paper impact (field-normalised citation percentile, FWCI, influential
   citations, venue),
4. timeliness: very recent *or* a landmark paper.

Reading status (abstract / skimmed / read / thorough / later / dismissed),
ratings and notes will be tracked in a self-hosted web app usable on a phone,
and exported to Obsidian.

**Status:** `v0.1.0-dev`. Library ingest works (milestone 2 of 7); see the
[roadmap](#roadmap).

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp config/config.example.yaml config/config.yaml   # set paths.bibtex, topics, API keys
.venv/bin/litresearch ingest
.venv/bin/litresearch status
```

Full instructions, including the Paperpile auto-export and Google Drive sync:
[docs/setup.md](docs/setup.md).

## Commands

| Command | Purpose | Status |
|---|---|---|
| `db-init` | create the SQLite database | ✅ |
| `ingest [--no-s2]` | import the Paperpile export; resolve DOIs; fetch metrics and embeddings | ✅ |
| `status` | library summary: coverage, top folders | ✅ |
| `run [--dry-run]` | build a new reading list | milestone 3 |
| `serve` | web app / phone PWA | milestone 4 |
| `export`, `sync-notes` | Obsidian export and note round-trip | milestone 6 |
| `export-paperpile` | BibTeX with reading-status keywords, for import into Paperpile | milestone 6 |

Global option: `-v/--verbose` shows HTTP requests and retries.

## Documentation

- [Setup](docs/setup.md): installation, Paperpile export, sync, first ingest, troubleshooting
- [Configuration](docs/configuration.md): every config key
- [Architecture](docs/architecture.md): pipeline, data model, external services and their limits
- [Development](docs/development.md): git workflow, tests, lint, fixtures

## Data sources

All free:

- [Crossref](https://www.crossref.org/documentation/retrieve-metadata/rest-api/) for DOIs
- [OpenAlex](https://docs.openalex.org/) for metrics and identifiers
- [Semantic Scholar](https://www.semanticscholar.org/product/api) for SPECTER2 and TLDRs

Planned: PubMed, bioRxiv/medRxiv. Paperpile has no public API, so its BibTeX
auto-export is the interface.

## Roadmap

1. ✅ Skeleton: config, database, CLI
2. ✅ Library ingest and enrichment
3. Interest profile, candidate search, scoring, ≤ 5-paper detail/skim list
4. Reading log and web app (PWA)
5. LLM summaries via an OpenAI-compatible endpoint (LM Studio / Ollama)
6. Obsidian export/sync, Paperpile write-back file, systemd deployment
7. Feedback loop (ratings and dismissals refine the profile)

## Branches

- `main`: releases
- `dev`: development; feature branches `feat-*` branch off `dev`
  (see [docs/development.md](docs/development.md))

## License

MIT with the [Commons Clause](https://commonsclause.com): free to use, study,
change and share, but not to sell this software or a paid product or service
(incl. hosting, consulting or support) based mainly on it. See [LICENSE](LICENSE).
