# Changelog

## 0.1.0-dev (unreleased)

### Added

- Project skeleton: config models, SQLite schema, CLI entry point.
- `ingest` command:
  - parses Paperpile BibTeX exports, including their month/day format,
    folder/label keywords and LaTeX;
  - resolves missing DOIs via Crossref with title/author/year verification;
  - enriches papers with OpenAlex metrics (FWCI, citation percentile, venue
    mean citedness) and Semantic Scholar data (SPECTER2 embeddings, TLDRs);
  - caches HTTP responses, rate-limits requests and retries on 429/5xx;
  - continues when a single source fails.
- `status` command: library summary.
- `recommend`: weekly top papers from OpenAlex topic searches and Semantic
  Scholar recommendations; relevance (SPECTER2 / TF-IDF), impact and
  timeliness scoring; diversity via MMR; detail/skim split.
- Reading queue (`queue add|drop|move`, `next`), limited and ordered.
- Reading log (`read`, `note`, `dismiss`, `show`, `history`).
- Gamification (`score`): XP, bonuses, weekly streaks, weekly quest, levels,
  badges, weekly grade, reading impact factor.
- Offline test suite with recorded API responses; live smoke test behind
  `--network`.
- Documentation in `docs/`.
