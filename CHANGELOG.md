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
- Local web UI (`serve`): dashboard, recommendations with an evidence tree and
  filter funnel, reading queue, library search, paper pages with notes,
  progress, weekly reports, XP ledger (TSV export), settings, "How it works";
  animations for rewards.
- Every recommendation stores its evidence; batches store a filter funnel and
  the settings used. All scoring constants are configurable settings.
- Penalty for picked recommendations that leave the queue unread
  (`game.drop_penalty`).
- Additive schema migration for existing databases.
- Documentation: `docs/recommendation-criteria.md`, `docs/web-ui.md`.
- Offline test suite with recorded API responses; live smoke test behind
  `--network`.
- Documentation in `docs/`.
