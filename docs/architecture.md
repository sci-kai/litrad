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

Planned stages (see the roadmap in the README): candidate search, scoring and
the ≤ 5-paper list, the web app, LLM summaries, Obsidian export.

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
| `sources/semanticscholar.py` | Batched lookups: SPECTER2 embeddings, TLDRs, influential citations |

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

## Data model

| Table | Content |
|---|---|
| `paper` | one row per paper, from the library or (later) a candidate; identifiers, metadata, metrics, folders/tags |
| `embedding` | `(paper_id, model)` → float32 vector. Vectors from different models are never compared. |
| `readinglist`, `listitem` | generated lists with category (detail/skim), score and score breakdown |
| `reading` | current status (`new`, `abstract`, `skimmed`, `read`, `thorough`, `later`, `dismissed`), rating, tags, notes |
| `statusevent` | history of every status change |

Timestamps are local ISO 8601 without a UTC offset (CUBI convention). There
are no schema migrations yet: before v1.0 the database can be rebuilt with
`ingest`, apart from reading history.

## External services

| Service | Use | Limits without a key |
|---|---|---|
| Crossref | DOI by title | free; polite pool with `mailto` |
| OpenAlex | metrics, IDs, venues | metered (~1000 credits/day); batch lookups cost 1 credit per 50 papers |
| Semantic Scholar | SPECTER2, TLDR | one rate limit shared by all anonymous users → HTTP 429 is common; free key = 1 request/s |

All responses are cached in `data/cache/http_cache.sqlite`: Crossref for
1 year, OpenAlex for 14 days, Semantic Scholar for 30 days.

## Known data caveats

- OpenAlex withholds abstracts for some publishers (e.g. Nature Genetics);
  BibTeX abstracts are preferred anyway.
- Venue mean citedness can be distorted by supplements. *Cancer Research*
  scores 0.66 because of its many meeting abstracts. Scoring must therefore
  rely mainly on paper-level metrics (percentile, FWCI).
