# Changelog

All notable changes are listed here. Versions follow
[semantic versioning](https://semver.org); dates are ISO 8601.

## 1.0.0-rc.1 (2026-11-10)

The first release candidate of litrad 1.0, the Literature Radar Game. From
1.0 on, every 1.x version reads and upgrades the data, settings and archives
of every earlier 1.x version.

Licensed under MIT with the Commons Clause: free to use, change and share,
but not to sell litrad or a paid product or service based mainly on it.

### Added

**Recommendations**

- Up to 5 papers whenever you ask, with a weekly reminder: candidates from
  OpenAlex topic queries and Semantic Scholar's library-based
  recommendations, ranked by SPECTER2 similarity to your library, your
  topics, impact and timeliness; each paper is either fresh or a landmark.
- An evidence tree and funnel for every list; *not relevant* teaches the
  recommender; example lists for Semantic Scholar with rules you choose;
  libraries can be switched on or off for recommendations.

**Finding and adding papers**

- One add box for a DOI, PubMed ID or title on the queue, Find & add and the
  example lists.
- Find & add: search Europe PMC, PubMed or your own library by keywords,
  title, author, journal, years and open access (`litrad search`).

**Libraries**

- Several libraries per profile, imported from any reference manager:
  BibTeX, RIS and CSL-JSON, checked before import, into a new or an existing
  library (adding or replacing); update from file.
- Export as BibTeX or RIS, optionally with your reading status as keywords;
  *Recommended* and *Reading queue* libraries.

**Reading**

- A short, ordered reading queue; four reading depths (abstract, skimmed,
  read, thorough), ratings and notes.
- *Read earlier*: papers you read before using litrad, with an optional date.
- *Read papers*: every paper you marked, with depth, dates, rating, notes and
  XP; sort, filter, search, export (TSV).

**Notes**

- One Markdown file per paper (format `litrad-note/1`, YAML front matter),
  an editor with preview, templates and `[[citation-key]]` links.
- Two-way sync with an Obsidian vault that shows conflicts instead of
  overwriting; every profile has its own folder in the vault
  (`<vault>/litrad/<profile id>/`), and a folder of another profile is
  refused.

**Game**

- XP per reading depth (paid up to the deepest level), bonuses for streaks,
  landmark and fresh papers, recommendations, ratings, reflections and inbox
  zero, and the 🧀 *cheese* for reading the top paper of your queue first
  (only when it got there by the queue's order, not when you moved it or
  added it as read next); a penalty for queued papers dropped unread.
- Weekly quests with a report card, 60 levels from *Curious Undergrad* on,
  more than 120 badges (e.g. *First Bite*, *Big Cheese*, *Chair of Cheese
  Studies*), personal bests and hints. Most XP comes from reading itself
  (about three quarters for a regular reader); the first papers bring a
  level at a time and badges one or two at a time. The career ladder is a
  rank table (level, title, XP needed) on the progress page, the profile
  page and in `litrad score`.
- Rules are locked per profile when it is created and carry a fingerprint, so
  profiles are compared fairly; rules can be set before creating a profile.

**Profiles**

- Separate profiles, each in its own folder with its database, library
  export, settings and notes; a welcome screen to choose, create or load one.
- Save, load, compare and merge profiles as archives (`litrad profile`,
  `litrad import-progress`).

**Your data**

- A versioned SQLite schema (version 1) with numbered, tested upgrade steps;
  a copy is kept before every upgrade, newer databases are refused.
- *Export everything*: one documented zip archive (format `litrad-export`,
  data format 1) with JSON files and their JSON Schemas, TSV tables, BibTeX
  and CSL-JSON per library, notes and settings (`litrad export-all`).
- Imports are checked first (checksums, schema, consistency between files)
  and written all or nothing, or only the consistent part on request
  (`litrad import-all`).
- Automatic backups while the web server runs: a database snapshot and an
  archive when the newest backup is `backup.interval_hours` old (restarts
  add none), rotated to the newest 14 plus one per week for 8 weeks; only
  litrad's own backup files are ever deleted; `litrad backup`,
  `litrad restore`, `litrad check`.

**Running litrad**

- Desktop app for Windows, macOS and Linux (installer, `.dmg`, AppImage):
  starts on this computer only and opens in your browser.
- Server mode with a password, chosen networks and protection against DNS
  rebinding: Docker image (ARM and x86), `pipx install litrad`, or git and
  `install.py` (optionally as a systemd user service).
- An installable web app for phones in server mode.
- Configuration in per-user folders with a first-run setup, or next to a git
  installation; `litrad where` shows what is used and why.
- The full documentation inside the web UI (📖) and a command line for
  everything.
