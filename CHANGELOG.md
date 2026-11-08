# Changelog

All notable changes are listed here. Versions follow
[semantic versioning](https://semver.org); dates are ISO 8601.

## Unreleased

### Added

- **Phone app (server mode)**: litresearch can be installed on a phone's
  home screen (web app manifest, icons, service worker with an offline page;
  no personal data is cached). Needs HTTPS for full installation; guide for
  the home network and Tailscale in the documentation. Icons are drawn by
  `tools/make_icons.py`.
- **Read papers** (*Reading → Read papers*, `/read`): every paper you marked
  with deepest depth, last and first reading, rating, notes, XP and source;
  sort, filter (depth, rating, period, library, notes, not relevant), search
  in title, authors and notes, 50 per page, **Export TSV** of all matching
  rows; same as `litresearch read-papers [--tsv FILE]`.
- **Examples for Semantic Scholar recommendations**: the positive and
  negative example papers are two libraries (*Positive examples*, *Negative
  examples*) that show each paper's origin. Choose the rules that fill them
  (`liked`, `read`, `recent`, `impact`, `fwci`, `cited`; `dismissed`,
  `low_rated`), their order and the number of examples; add, remove and
  restore papers yourself, also from any paper's page; undo your changes.
  New settings `selection.s2_positive_rules`, `s2_positive_count`,
  `s2_negative_rules`, `s2_negative_count` (the defaults keep the previous
  choice, except that a paper you marked not relevant is no longer also a
  positive example); command `litresearch examples`. Your choices are part
  of profile files and backups; the evidence tree names the number of
  examples used.
- **Choose the libraries used for recommendations** with checkboxes on the
  Libraries page (and on the positive examples' page) or with
  `litresearch library use NAME… [--off]`; a warning when none is used.
- **Runs from any folder and as an installed package.** litresearch looks
  for its configuration in `--config`, `LITRESEARCH_CONFIG`,
  `config/config.yaml` in the current folder (git installations: unchanged),
  then in your per-user config folder; if there is none, the first command
  creates one from the example with the data in your per-user data folder
  (Linux, macOS, Windows; new dependency `platformdirs`). New command
  `litresearch where` shows the files and folders in use and why.

### Changed

- Relative paths in the configuration (`paths.*`, `backup.dir`, a profile's
  `paths.bibtex`) no longer depend on the folder you run a command in: they
  start from the folder that contains `config/` (as before when run from the
  litresearch folder), else from the config file's folder, or from the new
  setting `paths.base_dir`. `~` is expanded.

### Changed

- **More concise web UI**: much less explanatory text on every page; the
  numbers, rules with their current values, funnel, evidence trees and
  warnings (penalties, data loss) stay. Explanations of the obvious are gone
  or moved to the documentation; XP per reading depth is shown only where a
  reading is logged. Same action, same label: *Get recommendations*, *Add to
  queue*, *Log reading*, *Abstract*, *Save*. "Why was this recommended?"
  is now *Evidence tree*. The "How recommendations are made" and points
  reference is tightened into lists and tables, still with every value.
- The evidence tree's filter line no longer claims a paper is "not in your
  library" (unread library papers can be recommended).

## 0.2.0-dev (2026-10-29)

A development release for testing the new search and libraries.

### Added

- **Paper search in Europe PMC and PubMed** (Library → Find & add,
  `litresearch search`): keywords, title, author, journal, years, open access;
  sorted by relevance, date or citations; results of both databases merged,
  with source, citations, free full text, abstract and whether you already
  have the paper; one click (or `--add N`) queues a result; the queries sent
  are shown. The same search works offline in your own library.
- Papers can be added by `pmid:N` and `PMCn` everywhere (also `queue add`);
  papers store their PubMed Central id and link to the free full text.
- **Libraries**: several per profile, each with name and description, from
  any reference manager. Import BibTeX (tested with Paperpile), RIS (EndNote,
  Citavi, Zotero, Mendeley) or CSL-JSON (Zotero) into a new or an existing
  library (add, or replace with an updated export); files are checked first.
  A paper exists once, however many libraries contain it.
- **Export** any library as BibTeX or RIS, optionally with reading status
  and rating as keywords. *Recommended* (recommendations you queued) and
  *Reading queue* are libraries too; each library can be used for the
  recommendation similarity or not.
- Settings `sources.europepmc` and `api.ncbi_key`.
- Documentation: "What an export must contain", linked from the import page;
  `ROADMAP.md`, `TESTING.md` and a *Testing feedback* issue template.

### Changed

- "Paperpile library" is now **Library** (Libraries, Import, Find & add);
  old addresses redirect, existing data becomes "My library".
- `litresearch ingest` updates every library from its file; `check-bib`
  checks all three formats; profile files contain the libraries.
- **Much faster with large libraries and long use**: recommendations for a
  5 000-paper library take 8 s instead of 62 s and 0.3 GB instead of 2.3 GB
  of memory; profile comparison 1 s instead of 5 min; faster progress page,
  ledger, library pages, progress import/export and BibTeX parsing of long
  abstracts; database indexes on event times.
- Long lists are paginated: library pages and "Find & add → My library"
  (50 per page) and the XP ledger.
- Uploads are limited to 50 MB, stored under safe names; oversized requests
  get HTTP 413. Invalid form values (including infinite/NaN numbers) get
  friendly messages.
- Cached API answers older than a year are deleted automatically.
- The documentation and READMEs were condensed and brought up to date.
- Web UI polish: getting-started checklist for new profiles; weekly reports
  paged by 4 weeks; earlier recommendation sets grouped by month; compare
  page lists saved files with dates; library deletion asks once; library
  search says which results are shown.
- Phones: compact sticky top menu that scrolls sideways; no sideways page
  scrolling; responsive ledger, recommendation and paper pages.
- Accessibility: every form field has a label; rating stars and level
  pickers usable by keyboard and screen readers; visible focus outlines;
  skip link; dialogs keep focus and close with Esc.
- Keyboard shortcut help (`?`).
- Friendly error pages (404/422) instead of raw JSON; form errors name the
  field and its limit instead of raw validation text.
- Faster pages for big profiles: progress page 4.4 s to about 1 s and
  dashboard 2.3 s to under 1 s at 10k papers (stats computed once per
  request).
- Dark mode: readable grade badges.

### Fixed

- **Installer:** checks Python and `venv` first (with the `apt` command to
  fix it), checks at the end that litresearch actually starts instead of
  saying "Done" over a broken installation (e.g. a corrupt download), and
  offers to start the web UI. New options `--repair` (reinstall without
  pip's cache) and `--remove-service`.
- **Only a deeper level counts:** logging a paper again at the same or a
  shallower level (also after it was recommended again) no longer counts for
  the weekly quest, streak, report card, badges, personal bests or the
  reading calendar, and earns no landmark, fresh or recommendation bonus.
- Without a password, the web server only answers requests for IP addresses,
  `localhost` or the computer's own name, which blocks DNS-rebinding attacks
  from websites.
- **Data integrity:** re-importing RIS/CSL-JSON files without ids no longer
  mixes up papers; a reused citation key whose entry has another DOI/PMID is
  a new paper; duplicates without DOI within one file are detected; the
  progress merge matches non-ASCII titles and no longer joins papers with
  the same title from another year or with a different DOI/PMID; a progress
  file listing one paper twice no longer creates two readings; an imported
  library called "Recommended" stays a separate library; an OpenAlex result
  joins a PubMed paper without DOI by its PMID instead of duplicating it.
- BibTeX import keeps bare `%` and `&` as text; DOIs in `www.doi.org` links
  are recognised.
- **Robustness:** an invalid `settings.yaml` or `config.yaml` no longer stops
  automatic backups or the welcome page; locked or old cache entries and
  leftover temporary files are handled; `restore` of a mistyped path changes
  nothing and a restore discards stale journal files; `…_before_restore.sqlite`
  copies are rotated with the other snapshots; game rules reject infinite
  numbers; columns added to old databases start empty instead of NULL.
- Night Owl, Early Riser and Weekend Warrior count different papers.
- A paper re-marked "abstract" after being skimmed or read is no longer
  recommended again.
- "Next reads earn ×N" counts the current week (it was one step too low
  before the week's first read); `litresearch history` shows penalties as
  `-15` instead of `+-15`.
- Recommendation sets are dated with the day they were built for.

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
