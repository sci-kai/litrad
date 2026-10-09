# Changelog

All notable changes are listed here. Versions follow
[semantic versioning](https://semver.org); dates are ISO 8601.

## 0.2.0-dev (unreleased)

### Added

- `ROADMAP.md`: milestones 0.2 to 1.0 (reading library and search, PubMed
  and PMC, Obsidian-compatible notes, local LLM summaries, reference
  managers, more systems and devices), principles and open questions.
- `TESTING.md` and a *Testing feedback* issue template for the alpha phase.

### Fixed

- The documentation still listed the removed placeholder commands and
  called the systemd service "planned".

## 0.1.0-alpha.1 (2026-10-09)

The first release, for testing. Expect rough edges and report them as issues.
Before 1.0.0, minor versions may change the data format or the configuration;
the changelog says when.

### Highlights

- **Recommendations on request**: up to 5 papers, ranked by similarity to
  your Paperpile library (SPECTER2, TF-IDF fallback), your topics, impact
  (citation percentile or journal) and timeliness (fresh or landmark), with
  diversity (MMR) and a score floor. A weekly reminder; a new set shows papers
  not recommended within the cooldown; a finished set says so.
- **Full transparency**: an evidence tree for every paper, a filter funnel and
  a settings snapshot for every set, and every rule explained with your
  current values.
- **Reading queue** (max 5, ordered, one paper up next) that you fill
  yourself: from the recommendations, your library or any DOI or title.
- **Reading log**: abstract / skimmed / read / thorough, ratings and
  timestamped notes; you choose the depth, the depth sets the points.
- **Reading game**: XP with bonuses, a penalty for queued papers that leave
  the queue unread, weekly streaks and quests, 20 career levels, 44 badges,
  weekly report cards, an XP ledger with TSV export and statistics. All rules
  are configurable and locked when a profile is created.
- **Profiles**: everything (library, topics, settings, recommendations,
  progress, rules) belongs to a profile with its own folder; several profiles
  side by side; save, load and compare profile files (with checksum).
- **Web UI** for laptop and phone browsers: welcome screen, dashboard,
  reading, progress, library and settings, with animations for rewards, and
  the complete documentation built in. Serves on this computer, all networks
  or one chosen network (asked at start); password login for network access.
- **Paperpile integration** through the BibTeX auto-export: upload with
  checks, import history, library browser, `check-bib`.
- **Your data stays yours**: free services only (OpenAlex, Semantic Scholar,
  Crossref) within their free limits, no personal data sent; one SQLite file
  per profile; automatic, consistent backups into a folder of your choice;
  restore, export and idempotent merge of progress.
- **Command line** for everything, including `--profile` and the `profile`
  commands.
- **One-command installation** (`python3 install.py`, optionally as a systemd
  service).
- **Quality**: about 380 tests (offline with recorded API responses, plus
  live tests), failure and randomised scenarios, ruff, CI on GitHub for
  Python 3.11 to 3.13, and a release workflow.

### Known limitations

- Linux only (developed and tested on a Raspberry Pi 5 with Python 3.13).
- Semantic Scholar without an API key is often rate-limited (HTTP 429); those
  steps are skipped with a warning and the rest still works.
- The web UI uses plain HTTP: use it in your own network or through a VPN.
- Planned, not yet included: installable phone app (PWA), LLM summaries,
  Obsidian export, Paperpile write-back, PubMed and bioRxiv as extra sources.
