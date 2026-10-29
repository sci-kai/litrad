# Roadmap

Where litresearch is going and in which order. The order is a proposal:
**feedback from testing decides** what comes first, changes or is dropped.
Each milestone is a GitHub milestone with its issues.

| Version | Focus | Status |
|---|---|---|
| 0.1 | Recommendations, reading queue, game, profiles, web UI | released `v0.1.0-alpha.1` (2026-10-09) |
| 0.2 | Find papers in Europe PMC and PubMed; several libraries, import from any reference manager, export | released as dev `v0.2.0-dev` (2026-10-29), in testing |
| 0.3 | Your reading history | next |
| 0.4 | PubMed and PubMed Central for recommendations and full texts | planned |
| 0.5 | Notes as Markdown, compatible with Obsidian | planned |
| 0.6 | Summaries by a local LLM | planned |
| 0.7 | Reference managers: write-back and automatic updates | planned |
| 0.8 | macOS, Windows, Docker, installable phone app | planned |
| 0.9 / 1.0 | Beta with stable data format, then 1.0 | planned |

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

**0.3 · Reading history.** A **Reading → Read papers** tab: every paper you
marked, with date, depth, rating, notes and XP; sort, filter, search notes,
export (TSV). Open: show dismissed papers here? Should unread drops leave
*Recommended*?

**0.4 · PubMed and PMC.** Europe PMC/PubMed as recommendation sources (topic
queries, MeSH terms), merged with OpenAlex candidates and shown in funnel and
evidence tree; free full texts (JATS XML) from PubMed Central on request.

**0.5 · Notes, compatible with Obsidian.** One Markdown file per paper with
front matter and templates, an editor with preview, `[[citation-key]]`
links, two-way sync with an Obsidian vault (`paths.obsidian_vault`) that
shows conflicts instead of overwriting; existing notes are migrated.

**0.6 · Local LLM summaries.** Summaries, key findings, methods and
limitations from abstract or full text by a model you run (LM Studio,
Ollama, llama.cpp; `llm` settings), marked as AI-generated; optional and
cached; never influences the ranking by default.

**0.7 · Reference managers.** Paperpile write-back (a file of only what
changed), watching an auto-export file and re-importing, folders and labels
as topic suggestions; Zotero's local API or Better BibTeX; tested profiles
for Mendeley, EndNote, JabRef and Citavi.

**0.8 · More systems and devices.** macOS and Windows (portable network
detection, installers, CI on all three), a Docker image, an installable
phone app (PWA) with offline queue, an HTTPS option, accessibility check.

**0.9 / 1.0 · Beta and stable.** Feature freeze; stable data format,
configuration and profile files with migrations from every earlier version;
tested upgrade path from 0.1; no known data-loss bugs.

## Ideas for later

Shared lab or journal-club profiles, citation-network exploration, alerts
for followed authors, reading-time estimates and calendar integration,
statistics across profiles and years. Open an issue if you want one.

## How to influence the roadmap

Comment on or 👍 an issue, or open one; report how you use litresearch
(*Testing feedback* template); discuss larger pull requests in an issue
first ([CONTRIBUTING.md](CONTRIBUTING.md)).
