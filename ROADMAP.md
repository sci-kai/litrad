# Roadmap

Where litresearch is going and in which order. The order is a proposal:
**feedback from testing decides** what comes first, changes or is dropped.
Each milestone is a GitHub milestone with its issues.

| Version | Focus | Status |
|---|---|---|
| 0.1 | Recommendations, reading queue, game, profiles, web UI | released `v0.1.0-alpha.1` (2026-10-09) |
| 0.2 | Find papers in Europe PMC and PubMed; several libraries, import from any reference manager, export | released as dev `v0.2.0-dev` (2026-10-29), in testing |
| 0.3 | Desktop app for Windows, macOS and Linux (default); server mode as today; Docker, PyPI, installable phone web app | next |
| 0.4 | Your reading history | planned |
| 0.5 | Notes as Markdown, compatible with Obsidian | planned |
| 0.6 | **Stable, open data formats** and a complete export of everything | planned |
| 0.7 / 1.0 | Beta (feature freeze), then 1.0 with a compatibility promise | planned |
| 1.1 | PubMed and PubMed Central; summaries by a local LLM; reference managers: write-back and automatic updates | after 1.0 |

## Principles

- **You decide:** litresearch suggests; nothing is added to your queue,
  library or notes without your action.
- **Transparent:** every number behind a recommendation or score can be seen
  and explained.
- **Private and free:** free services within their limits, no personal data
  sent, AI only through a model you run yourself.
- **Your data stays yours:** plain, documented formats, backups and exports
  for everything, migrations instead of resets; everything per profile.
- **Done means:** tests (including failure cases), documentation, CHANGELOG,
  and tried by at least one person with a real library.

## Milestones

**0.1 · Core loop.** Recommendations, queue, reading log, game and profiles.
Testing asks: are recommendations relevant (how many of 5 are worth
reading)? Are defaults, queue limit, penalty and level speed right? Does
`install.py` work on other machines? Still open: `litresearch report`, a
diagnostic summary for issues.

**0.2 · Find papers and libraries (`v0.2.0-dev`).** Search Europe PMC and
PubMed (Library → Find & add, `litresearch search`) and queue results; add
papers by `pmid:`/PMC id. Several libraries per profile, import BibTeX, RIS
or CSL-JSON into new or existing libraries, export with reading status;
*Recommended* and *Reading queue* libraries. Performance with large
libraries and long use. Testing asks: do non-Paperpile exports import
cleanly? Are several libraries useful? Do searches find what you look for?

**0.3 · Desktop app and distribution.** Two ways to run litresearch:

- **Desktop mode (default):** double-click; it starts on this computer only
  (a free local port), opens in the browser and quits cleanly. Downloads on
  GitHub Releases, built automatically for every tag: Windows installer,
  Linux AppImage, macOS `.dmg`.
- **Server mode (as today):** `litresearch serve` on a Pi, NAS or lab server,
  reachable from your other devices on chosen networks with a password;
  also as a Docker image (ARM and x86) and `pipx install litresearch`.

Groundwork: data and settings in per-user folders (`platformdirs`) with a
first-run setup; existing git installations keep their folder. For phones:
an installable web app (PWA) for server mode, with a guide to reach your
server (same Wi-Fi or a VPN such as Tailscale, which also gives HTTPS). Open:
unsigned builds trigger SmartScreen/Gatekeeper warnings; code signing costs
money. Not planned: native Android/iOS apps (a phone would need its own
database and Apple's App Store; the web app covers the use).

**0.4 · Reading history.** A **Reading → Read papers** tab: every paper you
marked, with date, depth, rating, notes and XP; sort, filter, search notes,
export (TSV). Open: show dismissed papers here? Should unread drops leave
*Recommended*?

**0.5 · Notes, compatible with Obsidian.** One Markdown file per paper with
front matter and templates, an editor with preview, `[[citation-key]]`
links, two-way sync with an Obsidian vault (`paths.obsidian_vault`) that
shows conflicts instead of overwriting; existing notes are migrated.

**0.6 · Stable, open data formats.** Everything you build up in litresearch
(libraries, reading log, statuses, ratings, notes, XP ledger, badges, queue,
recommendations with their evidence, settings and game rules) is stored and
exported in documented standard formats that later versions keep reading:

- **Formats:** libraries as BibTeX, RIS and CSL-JSON; progress, logs and
  recommendations as JSON with a published JSON Schema and a format version;
  tables (reading log, XP ledger) also as TSV; notes as Markdown with YAML
  front matter; settings as YAML.
- **Versioned storage:** the live SQLite database carries a schema version
  and is upgraded by tested migrations, never reset. The data format has
  its own version, independent of the app version.
- **Export everything:** one button and one command write a single archive
  with all of the above plus a README that describes each file; importing
  it rebuilds the profile without loss (tested round trip).
- **Consistency checks on import:** before anything changes, an import is
  checked against the schema and for internal consistency: every reading,
  log entry, XP event, queue item, note and recommendation must point to a
  paper contained in the import (matched by DOI, PMID or id); references
  between files must match (libraries and papers, ledger and readings,
  badges and rules), and totals such as XP must add up. Problems are listed
  by file and record; nothing is imported until the check passes (all or
  nothing), unless you explicitly import only the consistent part. The same
  checks apply to backups, profile files and progress merges.
- **Compatibility tests:** example files and databases from every released
  version are kept in the test suite and must load in the current version.

**0.7 / 1.0 · Beta, then stable.** Feature freeze in the beta; tested upgrade
path from 0.1; no known data-loss bugs. **From 1.0 on, the compatibility
promise holds:** every 1.x version reads and upgrades all data, settings and
exports of earlier versions without breaking them. New features only add
(optional fields, new tables, new files); renaming or removing anything
would need 2.0. This is what lets the features below arrive as minor
versions.

**1.1 · More sources and helpers** (after 1.0, without breaking data):

- **PubMed and PMC:** Europe PMC/PubMed as recommendation sources (topic
  queries, MeSH terms), merged with OpenAlex candidates and shown in funnel
  and evidence tree; free full texts (JATS XML) from PubMed Central on
  request.
- **Local LLM summaries:** summaries, key findings, methods and limitations
  from abstract or full text by a model you run (LM Studio, Ollama,
  llama.cpp; `llm` settings), marked as AI-generated; optional and cached;
  never influences the ranking by default.
- **Reference managers:** Paperpile write-back (a file of only what
  changed), watching an auto-export file and re-importing, folders and
  labels as topic suggestions; Zotero's local API or Better BibTeX; tested
  profiles for Mendeley, EndNote, JabRef and Citavi.

## Ideas for later

Shared lab or journal-club profiles, citation-network exploration, alerts
for followed authors, reading-time estimates and calendar integration,
statistics across profiles and years. Open an issue if you want one.

## How to influence the roadmap

Comment on or 👍 an issue, or open one; report how you use litresearch
(*Testing feedback* template); discuss larger pull requests in an issue
first ([CONTRIBUTING.md](CONTRIBUTING.md)).
