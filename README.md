# litresearch

Personal literature radar on top of a Paperpile library. It proposes up to
5 papers per run, split into **read in detail** and **skim**, ranked by
similarity to your library and your topics, paper impact, and
recency/landmark status. Reading status and notes are tracked in a
self-hosted web app (mobile PWA) and an Obsidian vault.

Status: `v0.1.0-dev`, milestone 1 (skeleton) of 7. See `CHANGELOG.md`.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp config/config.example.yaml config/config.yaml   # then edit
.venv/bin/litresearch db-init
.venv/bin/pytest
```

`config/config.yaml` and `data/` are git-ignored (private settings, local database).

## Data sources

- Paperpile library: BibTeX auto-export to Google Drive, pulled via `rclone`
  (Paperpile has no public API).
- Semantic Scholar API (SPECTER2 embeddings, TLDRs, recommendations), OpenAlex
  (citation percentiles, FWCI, venue metrics), PubMed E-utilities, bioRxiv/medRxiv.
- Optional: a local SPECTER2 embedding service and an OpenAI-compatible LLM endpoint
  (e.g. LM Studio, Ollama) for summaries.

## Commands

| Command | Purpose | Milestone |
|---|---|---|
| `db-init` | create the SQLite database | 1 |
| `ingest` | import the Paperpile BibTeX export + enrich metadata | 2 |
| `run [--dry-run]` | build a new reading list | 3 |
| `serve` | start the web app | 4 |
| `export` / `sync-notes` | Obsidian export and note round-trip | 6 |
| `export-paperpile` | BibTeX with reading-status keywords for Paperpile import | 6 |

## Branches

- `main`: releases
- `dev`: development; feature branches `feat-*` branch off `dev`
