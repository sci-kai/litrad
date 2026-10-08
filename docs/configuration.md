# Configuration

All settings live in one YAML file, by default `config/config.yaml`
(override with `--config/-c`). Start from
[`config/config.example.yaml`](../config/config.example.yaml).

- Use `n/a` for "not set".
- Unknown keys are rejected, so typos fail early.
- Relative paths are resolved from the directory you run the command in.

Sections marked *(planned)* are already validated but not used yet.

## `paths`

| Key | Default | Meaning |
|---|---|---|
| `bibtex` | required | Paperpile BibTeX export |
| `database` | `data/litresearch.sqlite` | SQLite database (papers, reading status, notes) |
| `cache_dir` | `data/cache` | HTTP response cache |
| `obsidian_vault` | `n/a` | Obsidian export target *(planned)* |

## `library`

| Key | Default | Meaning |
|---|---|---|
| `ignore_labels` | `["Import Mendeley"]` | Paperpile labels without topical meaning; matched case-insensitively |

## `topics`

A list of your areas of interest. Each query runs two OpenAlex title/abstract
searches per recommendation batch (fresh and landmark papers). Papers found by
a topic get a relevance bonus proportional to the topic's `weight`.

```yaml
topics:
  - name: liquid-biopsy        # short id
    weight: 0.8                # relative importance, >= 0
    queries: ["circulating tumor DNA", "cfDNA fragmentomics"]
    mesh_terms: ["Circulating Tumor DNA"]
    seed_dois: ["10.1038/s41586-019-1272-6"]   # key papers that define the topic
```

## `excluded_terms`

Papers whose title or abstract contains one of these terms
(case-insensitive) are never recommended.

## `sources`

Switch individual data sources on or off (`true`/`false`): `semanticscholar`,
`openalex`, and *(planned)* `pubmed`, `biorxiv` (OpenAlex already indexes
PubMed and bioRxiv). `--no-s2` skips Semantic Scholar for a single `ingest`
or `recommend` run.

## `api`

| Key | Meaning |
|---|---|
| `semanticscholar_key` | Optional free key, which gives the tool its own rate limit (1 request/s). Without one, requests may hit HTTP 429; affected steps are skipped with a warning. |
| `openalex_key` | Optional. Without a key OpenAlex grants a small daily budget (≈ 1000 credits). An ingest uses ≈ 6 credits; a recommendation batch ≈ 20 per topic query (two searches) plus a few look-ups, i.e. ≈ 110 with the example config. |
| `contact_email` | Optional; sent as `mailto` to Crossref and OpenAlex (polite pool). Leave it `n/a` to send no personal data. |

**Privacy.** The tool sends no personal data unless you set `contact_email`.
What leaves your machine: titles/authors of library papers without a DOI
(to Crossref), DOIs/PMIDs (to OpenAlex and Semantic Scholar), your topic
queries (to OpenAlex), and the DOIs of library, well-rated and dismissed
papers (to the Semantic Scholar recommendations service). Disable
`sources.semanticscholar` to keep the latter local.

## `embeddings`

| Key | Default | Meaning |
|---|---|---|
| `backend` | `hybrid` | `s2`: SPECTER2 vectors from Semantic Scholar; `local`: own SPECTER2 service; `hybrid`: S2 first, local service for papers S2 has not embedded |
| `local_url` | `n/a` | OpenAI-style `/v1/embeddings` URL of the local service; required for `local` *(local service planned)* |

## `selection`

| Key | Default | Meaning |
|---|---|---|
| `n_recommendations` | 5 | papers per weekly list (1–5); older configs with `n_detail`/`n_skim` are converted automatically |
| `fresh_window_months` | 6 | how recent a paper must be to count as "current" |
| `landmark_min_percentile` | 95 | field/year-normalised citation percentile for "important" papers |
| `landmark_max_age_years` | 15 | landmark papers must be younger than this |
| `mmr_lambda` | 0.7 | relevance vs. diversity trade-off (1 = only relevance) |
| `weights` | 0.5 / 0.3 / 0.2 | weights of relevance / impact / timeliness |
| `results_per_query` | 25 | OpenAlex results per topic query and kind |
| `cooldown_weeks` | 8 | an unpicked recommendation may come back after this many weeks |
| `similarity_share` | 0.7 | relevance = share × library-similarity rank + (1 − share) × topic bonus |
| `knn` | 5 | library papers averaged for the similarity |
| `s2_only_topic_bonus` | 0.5 | topic bonus of papers found only by S2 recommendations |
| `top_venue_citedness` | 30 | journal 2-year mean citedness that counts as impact 1.0 (fresh papers) |
| `unknown_venue_impact` | 0.05 | impact of fresh papers without a known journal |
| `early_citation_bonus` / `early_citation_saturation` | 0.2 / 50 | extra impact for already-cited fresh papers |
| `citations_per_year_saturation` | 200 | citations/year for impact 1.0 when no percentile is known |
| `landmark_timeliness` | 0.4 | timeliness of landmark papers |
| `min_relative_score` | 0.6 | picks need this share of the best score |

All of these are explained with formulas in
[recommendation-criteria.md](recommendation-criteria.md) and editable in the
web UI (Settings).

## `queue`

| Key | Default | Meaning |
|---|---|---|
| `max_size` | 5 | maximum queue length (1–20). Keep it small. |

## `game`

The **defaults for a new game profile**: every reward, bonus, the penalty,
the weekly quest, the level ladder (`levels`) and badge targets
(`badge_targets`). When a profile starts, these rules are copied into the
database and locked; changing this section afterwards only affects the
*next* profile. All keys and defaults are listed in
[gamification.md](gamification.md#rules-you-can-set-defaults). It is
easier to set them on the Profile page when you start the game.

## `backup`

| Key | Default | Meaning |
|---|---|---|
| `dir` | `data/backups` | where snapshots and progress exports go; a cloud-synced folder is ideal |
| `keep` | 14 | snapshots (and exports) to keep |
| `interval_hours` | 24 | `litresearch serve` backs up at start and then every N hours; 0 = off |

See [data-and-backups.md](data-and-backups.md).

## `llm` *(planned, milestone 5)*

Any OpenAI-compatible endpoint (LM Studio, Ollama, an institutional server):
`enabled`, `base_url`, `model`, `api_key`.

## `schedule` *(planned)* / `web`

`schedule.cron`: when to build a new list (cron syntax).
`web.host` is where `litresearch serve` listens: `127.0.0.1` (this computer
only, no login needed) or `0.0.0.0` (whole local network; requires a
password). `web.port` is the port. `web.password_hash` is set by
`litresearch set-password`; never edit it by hand. See
[web-ui.md](web-ui.md#using-it-from-your-laptop-local-network).
