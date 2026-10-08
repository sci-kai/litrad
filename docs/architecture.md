# Architecture

## Overview

```
Paperpile ──auto-export──> Google Drive ──rclone──> library.bib
                                                       │
                                         ingest/bibtex.py (parse)
                                                       │
                                        ingest/pipeline.py ──> SQLite
                                         │       │       │
                                   Crossref  OpenAlex  Semantic Scholar
                                   (DOIs)   (metrics)  (SPECTER2, TLDR)
```

```
topics ──> OpenAlex search (fresh, landmark) ─┐
library ─> S2 recommendations ────────────────┼─> candidates ─> filter ─> score ─> MMR ─> ≤ 5 (detail/skim)
                                              │                                       │
                                  S2 SPECTER2 / TF-IDF                        you pick ─> queue ─> read ─> XP
```

Planned stages (see the roadmap in the README): web app, LLM summaries,
Obsidian export.

## Code layout

| Module | Responsibility |
|---|---|
| `cli.py` | Typer commands; builds the HTTP client and DB engine |
| `config.py` | Pydantic models for `config.yaml` |
| `db.py` | SQLModel tables and helpers (`set_status`, `store_embedding`) |
| `ingest/bibtex.py` | Parses Paperpile BibTeX into `BibEntry` records |
| `ingest/pipeline.py` | Library sync, DOI resolution, enrichment; returns an `IngestReport` |
| `sources/http.py` | Shared JSON client: SQLite response cache, per-host throttling, retries |
| `sources/crossref.py` | Title/author/year → DOI, with verification |
| `sources/openalex.py` | Batched lookups by DOI/PMID, venue statistics |
| `sources/semanticscholar.py` | Batched lookups: SPECTER2 embeddings, TLDRs, influential citations; recommendations |
| `papers.py` | Store OpenAlex works as papers (candidates, manual adds) |
| `recommend/engine.py` | Candidate gathering, filtering, persistence of weekly batches |
| `recommend/scoring.py` | Pure scoring: relevance, impact, timeliness, MMR, detail/skim split |
| `recommend/text.py` | TF-IDF fallback similarity |
| `reading.py` | Paper references, queue operations, reading log, notes |
| `game.py` | XP rules, streaks, quests, levels, badges, statistics |
| `cli_reading.py` | Queue/reading/score commands |
| `web/app.py` | FastAPI routes, celebration tokens, same-origin check |
| `web/settings.py` | Settings schema (labels, help, ranges) and comment-preserving save |
| `web/templates/`, `web/static/` | Jinja2 pages, CSS and JS (no external assets) |

## Ingest pipeline

1. **Parse** the BibTeX file. Paperpile quirks it handles:
   - `month = "5\xa0" # apr`: day, non-breaking space and month macro → `pub_date`
   - line-wrapped values, LaTeX escapes (`\%`, `{PD1}`)
   - `keywords` mixing folder paths (`03 Biology / Medicine/Cancer/Evolution`) with
     labels and author keywords → `folders` and `tags`

   Duplicate citation keys only produce a warning. Any other parse error aborts
   *before* the database is touched, so a truncated download never makes papers
   look as if they had left the library.
2. **Sync**: papers are upserted by citation key. A paper already in the
   database with the same DOI and no key (e.g. an earlier recommendation) is
   adopted into the library. Papers missing from the export get
   `in_library = False`; they are not deleted, so reading history and notes
   survive.
3. **Resolve DOIs** for entries that have neither DOI nor PMID, via Crossref.
   Crossref's top hit is not trusted blindly (it can be a "Faculty Opinions"
   record about the paper). A candidate must:
   - not be a peer review, dataset or component record;
   - match the year ±1 and the first author's surname, where both are known;
   - have a title similarity ≥ 0.9, or a truncated library title (≥ 4 words)
     that is a prefix of the candidate's title, confirmed by the author.
4. **OpenAlex enrichment**, batched 50 IDs per request (1 credit per request
   instead of 10 for a search): citation count, FWCI, field/year-normalised
   citation percentile, topics, PMID, missing abstracts, and the venue's 2-year
   mean citedness (an open proxy for the impact factor). PMID-only entries are
   looked up by PMID.
5. **Semantic Scholar enrichment**, batched 100 IDs per request: SPECTER2
   embedding, TLDR, influential citation count.

Each source fails on its own: an outage or rate limit is recorded as a warning
in the report and the remaining steps still run.

## Recommendation pipeline

1. **Candidates**:
   - per topic query, two OpenAlex title/abstract searches sorted by relevance:
     *fresh* (published within the fresh window; articles, reviews, preprints)
     and *landmark* (citation percentile above the threshold, published within
     `landmark_max_age_years`). A full-text search sorted by citations returned
     off-topic hits (e.g. `fastp` for "ctDNA"), so the tool does not use it;
   - Semantic Scholar recommendations for the library: positives are well-rated
     papers plus the most recent library papers, negatives are dismissed papers.
2. **Filter**: drop papers in the library, read, dismissed, queued, recommended
   in earlier weeks within the cooldown, matching `excluded_terms`, editorials
   and other non-research types, and conference abstracts from journal
   supplements (titles like `0865 …`, `P-123: …`).
3. **Score** (`recommend/scoring.py`):
   - *relevance* = 0.7 × percentile rank of the mean cosine similarity to the 5
     nearest library papers (SPECTER2; TF-IDF for papers without an embedding)
     + 0.3 × topic bonus (topic weight / maximum weight; 0.5 for papers found
     only by S2 recommendations);
   - *impact*: landmark papers use the citation percentile. Fresh papers cannot
     have citations yet, so they use the journal's log-scaled 2-year mean
     citedness, plus a small bonus for early citations. A missing venue scores
     0.05: preprint servers count as venues, so a missing one is a warning sign;
   - *timeliness*: fresh = 0.5–1.0, decaying over the fresh window;
     landmark = 0.4;
   - score = weighted sum (`selection.weights`).
4. **Select**: only candidates with at least 60 % of the best score; then MMR
   (λ = `mmr_lambda`) picks `n_detail + n_skim` diverse papers. The
   `n_detail` papers with the highest relevance + impact become "read in
   detail", the rest "skim".
5. **Persist**: one `RecommendationBatch` per week (keyed by its Monday), with
   the filter funnel and a snapshot of the settings, and its `Recommendation`
   rows with the score breakdown, the full evidence (shown as an evidence tree
   in the UI) and a one-line reason.

All constants are settings; the full criteria are in
[recommendation-criteria.md](recommendation-criteria.md).

## Data model

| Table | Content |
|---|---|
| `paper` | one row per paper, from the library or (later) a candidate; identifiers, metadata, metrics, folders/tags |
| `embedding` | `(paper_id, model)` → float32 vector. Vectors from different models are never compared. |
| `recommendationbatch`, `recommendation` | weekly batches; rank, category (detail/skim), score, breakdown, reason |
| `queueitem` | the reading queue: paper, position (1 = up next), source, added time |
| `reading` | current status (`abstract`, `skimmed`, `read`, `thorough`, `dismissed`), rating, notes |
| `statusevent` | every status change, including whether the paper was at the top of the queue |
| `xpevent` | every point earned, with kind and reason |
| `achievement` | unlocked badges |

Timestamps are local ISO 8601 without a UTC offset (CUBI convention).
`init_db` adds new columns to existing tables (`add_missing_columns`), which
covers additive schema changes; anything else needs a manual migration. **Reading history, notes and points live only in
the database**, so back up `data/litresearch.sqlite`. Library data can always
be rebuilt with `ingest`.

## External services

| Service | Use | Limits without a key |
|---|---|---|
| Crossref | DOI by title | free; polite pool with `mailto` |
| OpenAlex | metrics, IDs, venues, topic search | metered (~1000 credits/day); batch lookups cost 1 credit per 50 papers, a search 10 credits |
| Semantic Scholar | SPECTER2, TLDR | one rate limit shared by all anonymous users → HTTP 429 is common; free key = 1 request/s |

All responses are cached in `data/cache/http_cache.sqlite`: Crossref for
1 year, OpenAlex look-ups for 14 days, searches and S2 recommendations for
1 day, Semantic Scholar papers for 30 days. The tool accepts the free limits:
when a service refuses, the affected step is skipped with a warning.

## Known data caveats

- OpenAlex withholds abstracts for some publishers (e.g. Nature Genetics);
  BibTeX abstracts are preferred anyway.
- Venue mean citedness can be distorted by supplements. *Cancer Research*
  scores 0.66 because of its many meeting abstracts. Scoring must therefore
  rely mainly on paper-level metrics (percentile, FWCI).
