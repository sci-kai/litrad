# litresearch

A personal literature radar built on a Paperpile library. Every week it
proposes **up to 5 papers**, split into **read in detail** and **skim**,
ranked by:

1. similarity to your Paperpile library (SPECTER2 embeddings),
2. the topics you define,
3. paper impact (field-normalised citation percentile, FWCI, influential
   citations, venue),
4. timeliness: very recent *or* a landmark paper.

You choose which of them go on your **reading queue**: short (max 5),
ordered, with exactly one paper "up next". You can also add any paper by
DOI or title. Reading levels (abstract / skimmed / read / thorough), ratings
and notes are logged, and a **game layer** keeps you motivated: XP, streaks,
weekly quests, career levels from *Curious Undergrad* to *Hallmarks Author*,
badges and a weekly report card.

Everything is **transparent**: each recommendation shows an evidence tree,
every criterion is documented, and every parameter can be changed in the UI.

**Status:** `v0.1.0-dev`. Local web UI and command line; a phone app (PWA) is
planned. See the [roadmap](#roadmap).

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp config/config.example.yaml config/config.yaml   # set paths.bibtex, topics, API keys
.venv/bin/litresearch ingest
.venv/bin/litresearch recommend          # this week's top 5
.venv/bin/litresearch queue add r1       # pick one
.venv/bin/litresearch next               # read it ...
.venv/bin/litresearch read q1 thorough --rating 5 --note "..."
.venv/bin/litresearch score
```

Or use the web UI: `litresearch serve` opens it in your browser
(see [docs/web-ui.md](docs/web-ui.md)). The weekly workflow is described in
[docs/usage.md](docs/usage.md).

Full instructions, including the Paperpile auto-export and Google Drive sync:
[docs/setup.md](docs/setup.md).

## Commands

| Command | Purpose | Status |
|---|---|---|
| `db-init` | create the SQLite database | ✅ |
| `ingest [--no-s2]` | import the Paperpile export; resolve DOIs; fetch metrics and embeddings | ✅ |
| `status` | library summary: coverage, top folders | ✅ |
| `recommend [--refresh] [--no-s2]` | this week's ≤ 5 papers (detail / skim) | ✅ |
| `queue [add\|drop\|move]` | the short, ordered reading queue | ✅ |
| `next`, `show REF` | top paper / any paper with abstract, status, notes | ✅ |
| `read REF LEVEL [--rating] [--note]` | log reading and earn XP | ✅ |
| `note REF TEXT`, `dismiss REF` | add a note / mark as not relevant | ✅ |
| `score`, `history` | level, streak, weekly grade, badges / reading log | ✅ |
| `serve [--host] [--port] [--no-browser]` | web UI, on this computer or for your local network | ✅ |
| `set-password [--remove]` | password for network access to the UI | ✅ |
| `backup`, `restore FILE` | database snapshot + progress export / restore a snapshot | ✅ |
| `export-progress FILE`, `import-progress FILE` | move or merge your progress between machines | ✅ |
| `check` | database integrity, contents, last backup | ✅ |
| `export`, `sync-notes` | Obsidian export and note round-trip | milestone 6 |
| `export-paperpile` | BibTeX with reading-status keywords, for import into Paperpile | milestone 6 |

Global option: `-v/--verbose` shows HTTP requests and retries.

## Documentation

| Document | For |
|---|---|
| [Setup](docs/setup.md) | installation, Paperpile export, sync, first ingest, troubleshooting |
| [Usage](docs/usage.md) | the weekly loop of recommend, queue, read and score |
| [Web UI](docs/web-ui.md) | pages, rewards, network access with login |
| [Recommendation criteria](docs/recommendation-criteria.md) | every rule and formula, the evidence tree |
| [Gamification](docs/gamification.md) | points, bonuses, penalty, levels, badges, metrics |
| [Data & backups](docs/data-and-backups.md) | where your progress lives, backups, moving machines |
| [Configuration](docs/configuration.md) | every config key |
| [How it is built](docs/implementation.md) | development, modules, algorithms, tests, **credits for all packages, services and literature** |
| [Architecture](docs/architecture.md) | pipeline, data model, external services |
| [Development](docs/development.md) | git workflow, tests, lint, fixtures |


## Data sources

All free, used within their free limits, and no personal data is sent:

- [Crossref](https://www.crossref.org/documentation/retrieve-metadata/rest-api/) for DOIs
- [OpenAlex](https://docs.openalex.org/) for metrics and identifiers
- [Semantic Scholar](https://www.semanticscholar.org/product/api) for SPECTER2 and TLDRs

Planned: PubMed, bioRxiv/medRxiv. Paperpile has no public API, so its BibTeX
auto-export is the interface.

## Roadmap

1. ✅ Skeleton: config, database, CLI
2. ✅ Library ingest and enrichment
3. ✅ Candidate search, scoring, weekly ≤ 5-paper detail/skim list
4. ✅ Reading queue, reading log, gamification, local web UI · ⏳ phone app (PWA)
5. LLM summaries via an OpenAI-compatible endpoint (LM Studio / Ollama)
6. Obsidian export/sync, Paperpile write-back file, systemd deployment
7. Feedback loop: ✅ dismissals and good ratings already steer the S2 recommendations; ⏳ weight tuning

## Branches

- `main`: releases
- `dev`: development; feature branches `feat-*` branch off `dev`
  (see [docs/development.md](docs/development.md))

## License

MIT with the [Commons Clause](https://commonsclause.com): free to use, study,
change and share, but not to sell this software or a paid product or service
(incl. hosting, consulting or support) based mainly on it. See [LICENSE](LICENSE).
