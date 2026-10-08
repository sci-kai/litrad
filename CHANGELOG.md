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
- Local network access: `serve` uses `web.host`; password login (scrypt hash,
  signed 30-day session cookie) via `set-password`; the server refuses to
  listen on the network without a password and prints the LAN address.
- Backups: consistent SQLite snapshots plus portable progress exports in
  `backup.dir` (rotated; automatic while `serve` runs); `restore`,
  `export-progress`, `import-progress` (idempotent merge by DOI/PMID/key/title,
  notes kept), `check`; data & backups card in the settings page.
- Robustness: durable commits, busy timeout for concurrent writers, atomic
  config writes, readable errors for broken config/BibTeX/database, tolerant
  API parsing; failure-scenario and randomised tests.
- Documentation: `docs/implementation.md` (how it is built, credits),
  `docs/data-and-backups.md`.
- Recommendations: unread papers from the Paperpile library are no longer
  excluded (only papers marked skimmed/read/thorough or dismissed are); a
  library paper never counts as its own neighbour; one ranked list
  (`selection.n_recommendations`) instead of detail/skim, the reader
  chooses the depth.
- Game profiles: all rewards, the penalty, the quest, the level ladder and
  badge targets are configurable and locked when the profile starts;
  20 levels and 44 badges; profile files with recommendations and checksum;
  load, merge (same rules only), reset and compare (web and CLI).
- Paperpile page: library browser, last import and notices, upload with
  checks (check only / check & import), re-import, import history, export
  instructions; `check-bib` command.
- Offline test suite with recorded API responses; live smoke test behind
  `--network`.
- Documentation in `docs/`.
