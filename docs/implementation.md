# How litresearch is built

This document explains how the tool was developed and how it works inside.
It also credits every library, data service and method it relies on. For
using the tool see [usage.md](usage.md) and [web-ui.md](web-ui.md); for the
exact recommendation rules see
[recommendation-criteria.md](recommendation-criteria.md).

Contents:

1. [Purpose in one paragraph](#1-purpose-in-one-paragraph)
2. [How it was developed](#2-how-it-was-developed)
3. [Architecture](#3-architecture)
4. [Module guide](#4-module-guide)
5. [Data model](#5-data-model)
6. [Key algorithms and design decisions](#6-key-algorithms-and-design-decisions)
7. [Web UI technology](#7-web-ui-technology)
8. [Quality assurance](#8-quality-assurance)
9. [Software dependencies](#9-software-dependencies)
10. [Data services](#10-data-services)
11. [Literature](#11-literature)
12. [Related tools and inspiration](#12-related-tools-and-inspiration)
13. [License and citation](#13-license-and-citation)

---

## 1. Purpose in one paragraph

litresearch reads a Paperpile library (BibTeX export) and enriches it with
open bibliographic data. Every week it recommends up to five papers, ranked
by similarity to the library and to self-defined topics, by paper impact,
and by being either recent or a landmark. The user picks papers into a
short, ordered reading queue, decides how deeply to read each one, logs
ratings and notes, and is motivated by a game layer (XP, streaks, quests,
20 levels, 44 badges) whose rules are set once per profile and then locked. Everything runs locally, from the command
line or a web UI, using only free services and without sending personal data.

## 2. How it was developed

- **AI-assisted.** The code, tests and documentation were written with
  Claude Code (Anthropic; model Claude Opus 5.5) in a dialogue with the user,
  who set the requirements and priorities and reviewed the results. Commits
  carry a `Co-Authored-By` line.
- **Written for this project.** No third-party source code was copied. All
  libraries are used through their public APIs (section 9).
  *zotero-arxiv-daily* served as a design reference only (section 12).
- **Research first.** Before any code, existing tools were compared:
  Semantic Scholar feeds, Scholar Inbox, ResearchRabbit, Litmaps,
  zotero-arxiv-daily and others. None combined Paperpile, own topics, impact
  weighting, a short reading list, reading-depth tracking and notes, so the
  decision was to build. The plan and milestones are in the README roadmap.
- **Driven by real data.** Each stage was run against the user's real
  Paperpile export (145 entries) and the live APIs before being finalised.
  Several design decisions came directly from what that revealed
  (section 6), for example:
  - the export had no DOIs at first, so a DOI resolution step was added;
  - Paperpile's `month = "5\xa0" # apr` format needed its own date parser;
  - OpenAlex meters its API, so look-ups are batched by DOI;
  - a full-text search returned off-topic hits;
  - conference abstracts and unknown venues were slipping into the picks.
- **Small, verified steps.** Every feature was developed on a `feat-*`
  branch from `dev`, tested, linted and fast-forwarded into `dev` (CUBI
  conventions, [development.md](development.md)). The web UI was checked
  visually with headless-browser screenshots.

## 3. Architecture

```
              ┌────────────────────────── litresearch ──────────────────────────┐
 Paperpile ─► │ ingest/      parse BibTeX ─► resolve DOIs ─► enrich (metrics,    │
 BibTeX       │              embeddings)                                         │
              │ recommend/   candidates ─► filters ─► score ─► MMR ─► batch      │
 Crossref ◄──►│ sources/     HTTP client (cache, rate limits, retries) + 3 APIs  │
 OpenAlex ◄──►│                                                                  │
 Sem.Scholar◄►│ reading.py   queue, reading log, notes, drop penalty             │
              │ rules.py     locked game rules · profile.py  the game profile    │
              │ game.py      XP, streaks, quests, levels, badges, summaries      │
              │ backup.py    snapshots, progress export/import, restore          │
              │ db.py        SQLite via SQLModel (one file)                      │
              │ cli*.py      Typer commands  ·  web/  FastAPI + Jinja2 UI        │
              └──────────────────────────────────────────────────────────────────┘
```

Layers, from the bottom up:

1. **Storage** (`db.py`): one SQLite file with all papers, embeddings,
   recommendations and personal progress.
2. **Sources** (`sources/`): thin clients for Crossref, OpenAlex and Semantic
   Scholar on top of one shared HTTP client.
3. **Domain logic**: `ingest/`, `recommend/`, `reading.py`, `game.py`,
   `backup.py`. These are plain functions on a database session; the scoring
   functions are pure (no I/O) and therefore easy to test.
4. **Interfaces**: the CLI (`cli.py`, `cli_reading.py`, `cli_data.py`) and the
   web UI (`web/`). Both call the same domain functions, so their behaviour
   is identical.

## 4. Module guide

| Module | Lines | Responsibility |
|---|---:|---|
| `config.py` | ~190 | Pydantic models of `config.yaml`; `n/a` → None; validation (e.g. ≤ 5 papers); `ConfigError` with readable messages |
| `db.py` | ~220 | SQLModel tables; reading levels; embedding storage as float32 bytes; additive migration; durable SQLite connection settings |
| `ingest/bibtex.py` | ~180 | Paperpile BibTeX → `BibEntry`: LaTeX decoding, month/day parsing, DOI normalisation, folder/label split, duplicate keys |
| `ingest/pipeline.py` | ~260 | Library sync, DOI resolution, OpenAlex and Semantic Scholar enrichment; per-source error isolation; `IngestReport`; import history |
| `ingest/check.py` | ~140 | Checks of a Paperpile export (required fields, coverage, duplicates) and export instructions |
| `sources/http.py` | ~170 | JSON client: SQLite response cache with max age, per-host throttling, retries with `Retry-After`/exponential backoff, typed `HttpError` |
| `sources/crossref.py` | ~70 | Title/author/year → DOI with verification (type, title similarity, year, author, prefix rule) |
| `sources/openalex.py` | ~170 | Batched works/sources look-ups, title/abstract search, abstract reconstruction, tolerant parsing |
| `sources/semanticscholar.py` | ~90 | Batched paper look-up (SPECTER2, TLDR, influential citations), recommendations |
| `papers.py` | ~30 | Store OpenAlex works as papers without overwriting Paperpile metadata |
| `recommend/engine.py` | ~330 | Candidate gathering with provenance, filters with funnel counts, embeddings, batch persistence with parameter snapshot |
| `recommend/scoring.py` | ~450 | Eligibility, relevance (kNN without self-matches + topic bonus), impact, timeliness, MMR, evidence tree |
| `recommend/text.py` | ~40 | Dependency-free TF-IDF fallback |
| `reading.py` | ~330 | Paper references (`r1`, `q2`, DOI …), queue operations, reading log, notes, drop penalty |
| `rules.py` | ~500 | `GameRules` (every reward, penalty, quest, level ladder, badge targets), badge catalog, rule fingerprint |
| `profile.py` | ~80 | Start the game profile, lock its rules, auto-start, active rules |
| `game.py` | ~620 | Points (always with the profile's rules), anti-farming, streaks, quest, levels, badges and their statistics, weekly summaries, ledger, calendar, bests, hints |
| `backup.py` | ~800 | Atomic writes, SQLite snapshots, rotation, restore, profile files (rules, progress, recommendations, checksum), load, merge, reset, comparison in an in-memory database, automatic backup thread |
| `cli.py`, `cli_reading.py`, `cli_data.py` | ~880 | Typer commands; `run()` turns expected errors into messages |
| `web/app.py` | ~860 | FastAPI routes, login and same-origin middleware, celebration tokens, downloads |
| `web/auth.py` | ~80 | scrypt password hashes, HMAC-signed session cookies |
| `web/settings.py` | ~540 | Settings schema (labels, help, ranges), game-rule form, form parsing, comment-preserving and atomic `config.yaml` writes |
| `web/templates/`, `web/static/` | ~1 800 | Jinja2 pages, CSS (themes, animations), JavaScript (toasts, confetti, rings) |

## 5. Data model

| Table | Content |
|---|---|
| `paper` | library papers, candidates and manually added papers: identifiers (DOI, PMID, citation key, OpenAlex, S2), metadata, metrics, folders/tags |
| `embedding` | `(paper, model)` → float32 vector (SPECTER2, 768 dimensions) |
| `recommendationbatch` / `recommendation` | weekly batches with funnel and settings snapshot; per paper: rank, category, score, evidence, reason |
| `queueitem` | the reading queue (position 1 = up next) and where each item came from |
| `reading` | current status, rating and notes per paper |
| `statusevent` | every status change, including whether the paper was at the top of the queue |
| `xpevent` | every point earned or lost, with kind and reason; the score is their sum |
| `achievement` | unlocked badges |
| `profile` | the game profile: name, start, a locked copy of the rules, fingerprint |
| `ingestrun` | every import of the Paperpile export: time, source, file hash, counts, check results |

Design choices:

- **Event sourcing for progress.** `statusevent` and `xpevent` are
  append-only logs. Statistics, streaks, quests and the ledger are computed
  from them, so the score is auditable.
- **Locked rules.** The profile stores its own copy of the rules; the config
  only supplies defaults for the next profile.
- **Timestamps** are naive local ISO 8601 (CUBI convention).
- **Embeddings are tagged by model**, so vectors from different models are
  never compared.

## 6. Key algorithms and design decisions

### Ingest

- **Paperpile quirks.**
  - Months are written as `"5\xa0" # apr` (day, non-breaking space,
    month macro) and get their own parser.
  - `keywords` mixes folder paths (containing `/`) with labels.
  - Titles may contain LaTeX braces; decoding uses bibtexparser's
    LaTeX middleware, which relies on pylatexenc.
  - A file that fails to parse aborts the import *before* the database is
    touched, so papers are never wrongly marked as removed.
- **DOI resolution.** Crossref's best hit is not trusted blindly: a
  "Faculty Opinions" record ranked first for one real paper. Candidates must
  - be a real paper type;
  - have a title similarity ≥ 0.9 (`difflib`), or be a ≥ 4-word title prefix
    confirmed by the first author's surname (for titles Paperpile truncated);
  - match the year ±1.
- **Metered OpenAlex.** OpenAlex charges credits: a search costs 10, a
  filter look-up of up to 50 DOIs costs 1. Enrichment is therefore batched,
  and one ingest of 145 papers uses about 6 credits.
- **Failure isolation.** Each source may fail (rate limit, outage, non-JSON
  answer); the failure becomes a warning and the other sources still run.

### Recommendations

- **Candidates.** OpenAlex title/abstract search sorted by relevance
  (full-text search sorted by citations returned off-topic papers), plus
  Semantic Scholar recommendations. Positives are liked papers and recent
  library papers; negatives are dismissed papers, which closes the feedback
  loop.
- **Relevance.**
  - Cosine similarity of SPECTER2 embeddings, averaged over the k nearest
    library papers.
  - Converted to a percentile rank among candidates. This makes SPECTER2
    and the TF-IDF fallback comparable for papers without an embedding.
  - Blended with a topic-weight bonus.
- **Impact.**
  - Landmark papers use OpenAlex's field- and year-normalised citation
    percentile.
  - Fresh papers cannot have citations yet, so they use the journal's 2-year
    mean citedness (an open impact-factor proxy), log-scaled. This is
    distorted by supplements, e.g. *Cancer Research*, which is why it is
    not used for landmarks.
  - An unknown venue scores low, because preprint servers count as venues
    in OpenAlex.
- **Diversity.** Maximal marginal relevance (Carbonell & Goldstein 1998)
  over the candidates above a relative score floor. The floor was added
  after MMR pulled a weak meeting abstract into a real batch for the sake of
  diversity.
- **Transparency.** Every number is stored as an evidence tree per
  recommendation, plus a funnel and a settings snapshot per batch.

### Reading and game

- **Short queue.** The queue is short and strictly ordered. Reading the top
  paper first earns the "frog" bonus, a nod to Brian Tracy's "eat that
  frog" productivity advice.
- **Anti-farming.**
  - Base points are paid only up to the deepest level ever reached.
  - Paper bonuses are paid once.
  - The frog bonus is proportional to newly earned base points.
  - A randomised test checks these invariants over long random sessions.
- **Drop penalty.** Picking a recommendation and abandoning it unread costs
  XP; manual and library papers are free to drop. A library paper picked
  from the recommendations counts as a recommendation.
- **No prescribed depth.** Recommendations are a ranked list; the reader's
  marking decides the points.
- **Locked, configurable rules.** All rewards, the penalty, the quest, the
  level ladder (20 levels) and badge targets (44 badges) form one
  `GameRules` object. Starting a profile freezes a copy in the database with
  a SHA-256-based fingerprint, so changing the config cannot inflate a
  running game and profiles can be compared fairly.
- **Profile files** contain the rules, all progress and all recommendation
  batches, plus a SHA-256 checksum over the content. Loading or merging an
  edited file is refused; merging progress earned under other rules too.
  Comparisons load each file into a throw-away in-memory SQLite database and
  compute the same statistics as the dashboard.
- **Streaks** count weeks rather than days and survive until the end of the
  following week, so they motivate without guilt.

### Data safety

- Each action is one transaction, and commits use `PRAGMA synchronous=FULL`.
- A 30 s busy timeout lets the CLI and the server write concurrently.
- Snapshots use SQLite's online backup API and are integrity-checked.
- Config and export writes are atomic (temporary file, `fsync`, `os.replace`).
- The portable JSON export merges idempotently by DOI, PMID, citation key or
  title. Details: [data-and-backups.md](data-and-backups.md).
- Uploaded BibTeX files are checked in a temporary file first; an invalid
  export never replaces the library, and the previous file is kept as `.bak`.

### Security of the web UI

- **Network access** requires a password, stored as a salted scrypt hash
  (RFC 7914), with an HMAC-SHA256-signed (RFC 2104), HttpOnly, SameSite=Lax
  session cookie whose signature covers the password hash. Changing the
  password therefore logs out every device.
- **Request checks.** POST requests must come from the same origin; redirect
  targets must be local; templates auto-escape everything (tested with
  hostile titles and notes).

## 7. Web UI technology

- Server-rendered HTML with **FastAPI** routes and **Jinja2** templates; forms
  post and redirect, so every action works without JavaScript.
- **No build step and no external assets**: no CDN, web fonts or trackers.
  Icons are emoji and inline SVG; the CSS (light/dark themes, animations) and
  about 200 lines of JavaScript (XP toasts with count-up, a canvas confetti
  burst, level-up and badge modals, progress rings, shortcuts) are
  hand-written.
- Reward feedback travels as a short-lived token in the redirect URL. The
  token is removed from the address bar after display, so a reload does not
  replay it.
- `prefers-reduced-motion` turns all animation off.

## 8. Quality assurance

| Measure | Details |
|---|---|
| Test suite | 18 test modules plus shared fixtures (`conftest.py`), about 330 tests, run with `pytest` |
| Coverage | about 97 % of statements; core logic (scoring, game, reading, config, auth) 100 % |
| Offline by default | `FakeApis` serves recorded real API responses through `httpx.MockTransport`; a synthetic BibTeX fixture reproduces all Paperpile quirks |
| Live tests | `pytest --network` runs ingest and recommendations against the real APIs |
| Robustness | `test_robustness.py`: crashes mid-action, concurrent writers, restarts, broken inputs, hostile content, calendar edges, scale, randomised sessions with invariants |
| Strictness | warnings are errors (catches e.g. unclosed SQLite connections); `ruff` lint and format |
| Manual checks | real-data runs after each milestone; headless-browser screenshots of the UI |

| Test module | Focus |
|---|---|
| `test_config.py`, `test_db.py` | validation, schema, embeddings, migration |
| `test_bibtex.py`, `test_pipeline.py` | parsing quirks, ingest end to end |
| `test_http.py`, `test_sources.py` | cache, retries, throttling; API clients |
| `test_scoring.py`, `test_recommend.py` | scoring maths, evidence, filters, cooldown, funnel |
| `test_reading.py`, `test_game.py` | queue, notes, penalty; XP, streaks, quests, badges, summaries |
| `test_backup.py` | snapshots during writes, rotation, export/import merge, restore |
| `test_profile.py` | rule validation, locking, custom rules, save/load/merge/reset, checksums, comparison, new badges, profile UI and CLI |
| `test_paperpile.py` | export checks, import history, upload (check only, import, invalid), library browsing and filters, notices |
| `test_cli.py`, `test_web.py`, `test_auth.py` | interfaces, settings round trip, login |
| `test_robustness.py` | failure scenarios (see above) |
| `test_live.py` | real APIs (opt-in) |

## 9. Software dependencies

Versions are those used during development (Python 3.13.9). All are open
source; their licenses permit use in this project.

### Runtime (declared in `pyproject.toml`)

| Package | Version | License | Used for |
|---|---|---|---|
| [bibtexparser](https://github.com/sciunto-org/python-bibtexparser) | 2.1.0 | MIT | parsing the Paperpile BibTeX export (v2 API, middlewares for LaTeX decoding and name splitting) |
| [httpx](https://www.python-httpx.org) | 0.28.1 | BSD-3-Clause | HTTP client for all APIs; `MockTransport` in tests |
| [numpy](https://numpy.org) | 2.5.3 | BSD-3-Clause | embedding storage and vector maths (cosine similarity, kNN, MMR, TF-IDF) |
| [pydantic](https://github.com/pydantic/pydantic) | 2.13.5 | MIT | configuration models and validation |
| [PyYAML](https://pyyaml.org/) | 6.0.3 | MIT | reading `config.yaml` |
| [ruamel.yaml](https://sourceforge.net/projects/ruamel-yaml/) | 0.19.1 | MIT | writing `config.yaml` while keeping comments and order |
| [SQLModel](https://github.com/fastapi/sqlmodel) | 0.0.48 | MIT | table models on top of SQLAlchemy and Pydantic |
| [Typer](https://github.com/fastapi/typer) | 0.27.3 | MIT | command line interface |
| [Rich](https://github.com/Textualize/rich) | 15.0.0 | MIT | coloured terminal output |
| [FastAPI](https://github.com/fastapi/fastapi) | 0.142.4 | MIT | web routes, forms, test client |
| [Jinja2](https://jinja.palletsprojects.com/) | 3.1.6 | BSD-3-Clause | HTML templates (auto-escaping) |
| [python-multipart](https://github.com/Kludex/python-multipart) | 0.0.32 | Apache-2.0 | form parsing for FastAPI |
| [Uvicorn](https://uvicorn.dev/) | 0.54.0 | BSD-3-Clause | ASGI web server |

### Important indirect dependencies

| Package | Version | License | Role |
|---|---|---|---|
| [SQLAlchemy](https://www.sqlalchemy.org) | 2.1.3 | MIT | database engine under SQLModel |
| [Starlette](https://github.com/Kludex/starlette) | 1.7.0 | BSD-3-Clause | web toolkit under FastAPI (middleware, static files) |
| pydantic-core | 2.46.5 | MIT | validation engine of Pydantic |
| [Click](https://click.palletsprojects.com/) | 8.5.0 | BSD-3-Clause | CLI toolkit under Typer |
| [pylatexenc](https://github.com/phfaist/pylatexenc) | 2.11 | MIT | LaTeX → Unicode for bibtexparser |
| anyio, h11 | 4.15.1, 0.16.0 | MIT | async I/O and HTTP/1.1 for Starlette/Uvicorn/httpx |

### Development

| Package | Version | License | Used for |
|---|---|---|---|
| [pytest](https://docs.pytest.org/) | 9.1.1 | MIT | tests |
| [pytest-cov](https://github.com/pytest-dev/pytest-cov) | 7.1.0 | MIT | coverage |
| [Ruff](https://docs.astral.sh/ruff) | 0.16.10 | MIT | lint and format |

### Python standard library (notable uses)

`sqlite3` (database, online backup API, integrity check) · `hashlib.scrypt`,
`hmac`, `secrets` (login) · `difflib` (title similarity) · `tempfile`,
`os.replace`, `os.fsync` (atomic writes) · `threading` (automatic backups,
concurrency tests) · `json`, `csv` (exports) · `socket` (finding the LAN
address without sending packets).

## 10. Data services

All are free; no API key is required and no personal data is sent (section 6).

| Service | Used for | Terms |
|---|---|---|
| [OpenAlex](https://openalex.org) (OurResearch) | metadata, citation counts, FWCI, citation percentiles, topics, venue statistics, topic searches | data CC0; metered free API |
| [Semantic Scholar Academic Graph API](https://www.semanticscholar.org/product/api) (Allen Institute for AI) | SPECTER2 embeddings, TLDRs, influential citations, recommendations | API license agreement (linked from the API page); shared rate limit without a key |
| [Crossref REST API](https://www.crossref.org/documentation/retrieve-metadata/rest-api/) | DOI look-up by title, author and year (during ingest) | open metadata; polite use |
| [Paperpile](https://paperpile.com) | source of the library (BibTeX auto-export) | no API used |

## 11. Literature

Data sources and models:

- Priem, J., Piwowar, H., & Orr, R. (2022). *OpenAlex: A fully-open index of
  scholarly works, authors, venues, institutions, and concepts.*
  arXiv:2205.01833.
- Kinney, R., et al. (2023). *The Semantic Scholar Open Data Platform.*
  arXiv:2301.10140.
- Cohan, A., Feldman, S., Beltagy, I., Downey, D., & Weld, D. S. (2020).
  *SPECTER: Document-level representation learning using citation-informed
  transformers.* Proceedings of ACL 2020.
- Singh, A., D'Arcy, M., Cohan, A., Downey, D., & Feldman, S. (2023).
  *SciRepEval: A multi-format benchmark for scientific document
  representations.* Proceedings of EMNLP 2023 (introduces SPECTER2).
  arXiv:2211.13308.
- Cachola, I., Lo, K., Cohan, A., & Weld, D. S. (2020). *TLDR: Extreme
  summarization of scientific documents.* Findings of EMNLP 2020.

Methods:

- Carbonell, J., & Goldstein, J. (1998). *The use of MMR, diversity-based
  reranking for reordering documents and producing summaries.* Proceedings of
  SIGIR '98.
- Spärck Jones, K. (1972). *A statistical interpretation of term specificity
  and its application in retrieval.* Journal of Documentation, 28(1).
- Percival, C. (2009). *Stronger key derivation via sequential memory-hard
  functions* (scrypt); Percival, C., & Josefsson, S. (2016). RFC 7914.
- Krawczyk, H., Bellare, M., & Canetti, R. (1997). *HMAC: Keyed-hashing for
  message authentication.* RFC 2104.
- Field-weighted citation impact (Snowball Metrics) and OpenAlex's
  citation-normalised percentile, as documented by OpenAlex.

## 12. Related tools and inspiration

- [zotero-arxiv-daily](https://github.com/TideDra/zotero-arxiv-daily) (MIT):
  embedding similarity between new preprints and a reference-manager library.
  It inspired the general idea; no code was reused, and litresearch differs in
  input (Paperpile), sources, impact scoring, the reading workflow and the
  game.
- Scholar Inbox (Flicke et al., 2025, arXiv:2504.08385), Semantic Scholar
  Research Feeds, ResearchRabbit and Litmaps were reviewed as existing
  alternatives (section 2).
- "Eat that frog" (Brian Tracy) gave the bonus for reading your planned top
  paper first its name.

## 13. License and citation

litresearch is released under the MIT license with the Commons Clause
([LICENSE](../LICENSE)): no selling it or paid services based mainly on it. To cite
it, use [CITATION.cff](../CITATION.cff); please also cite OpenAlex and
Semantic Scholar/SPECTER2 (section 11) when you report results based on it.
