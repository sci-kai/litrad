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

A list of your areas of interest. Topics will steer candidate search and
ranking *(planned, milestone 3)*.

```yaml
topics:
  - name: liquid-biopsy        # short id
    weight: 0.8                # relative importance, >= 0
    queries: ["circulating tumor DNA", "cfDNA fragmentomics"]
    mesh_terms: ["Circulating Tumor DNA"]
    seed_dois: ["10.1038/s41586-019-1272-6"]   # key papers that define the topic
```

## `excluded_terms`

Papers whose title or abstract contains one of these terms are never
recommended *(planned)*.

## `sources`

Switch individual data sources on or off (`true`/`false`): `semanticscholar`,
`openalex`, and *(planned)* `pubmed`, `biorxiv`. `litresearch ingest --no-s2`
skips Semantic Scholar for a single run.

## `api`

| Key | Meaning |
|---|---|
| `semanticscholar_key` | Free key; gives the tool its own rate limit (1 request/s) |
| `openalex_key` | Optional. Without a key OpenAlex grants a small daily budget, which a normal ingest stays well inside. |
| `contact_email` | Sent as `mailto` to Crossref and OpenAlex (polite pool) |

## `embeddings`

| Key | Default | Meaning |
|---|---|---|
| `backend` | `hybrid` | `s2`: SPECTER2 vectors from Semantic Scholar; `local`: own SPECTER2 service; `hybrid`: S2 first, local service for papers S2 has not embedded |
| `local_url` | `n/a` | OpenAI-style `/v1/embeddings` URL of the local service; required for `local` *(local service planned)* |

## `selection` *(planned, milestone 3)*

| Key | Default | Meaning |
|---|---|---|
| `n_detail` | 2 | papers to read in detail |
| `n_skim` | 3 | papers to skim; `n_detail + n_skim` must be ≤ 5 |
| `fresh_window_months` | 6 | how recent a paper must be to count as "current" |
| `landmark_min_percentile` | 95 | citation percentile for "important" older papers |
| `mmr_lambda` | 0.7 | relevance vs. diversity trade-off (1 = only relevance) |
| `weights` | 0.5 / 0.3 / 0.2 | weights of relevance / impact / timeliness |

## `llm` *(planned, milestone 5)*

Any OpenAI-compatible endpoint (LM Studio, Ollama, an institutional server):
`enabled`, `base_url`, `model`, `api_key`.

## `schedule` / `web` *(planned)*

`schedule.cron`: when to build a new list (cron syntax).
`web.host`, `web.port`, `web.password_hash`: the web app.
