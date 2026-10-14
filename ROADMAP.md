# Roadmap

Where litresearch is going, in which order, and why. The order is a
proposal: **feedback from testing decides** what comes first, what changes
and what is dropped. Every milestone is tracked as a GitHub milestone with
its issues; discussion happens in those issues.

Status: `v0.1.0-alpha.1` is out for testing (2026-10-09).

```
0.1.x  Alpha testing and fixes        ◄── now
0.2    Find papers (Europe PMC, PubMed) and libraries   ◄── next (built on dev, in testing)
0.3    Your reading history
0.4    PubMed and PubMed Central for recommendations and full texts
0.5    Notes, compatible with Obsidian
0.6    Local LLM summaries
0.7    Reference managers: write-back and automatic updates
0.8    More systems and devices
0.9    Beta: feature freeze, stable data format
1.0    Stable release
```

## Principles for every milestone

These hold for every feature below; a design that breaks one needs a good
reason, written down in its issue.

- **You decide**: litresearch suggests, you pick. Nothing is added to your
  queue, library or notes without an action from you.
- **Transparent**: every number that influences a recommendation or a score
  can be seen and explained.
- **Private and free**: only free services within their free limits; no
  personal data leaves your machine; AI only through a model you run
  yourself.
- **Your data stays yours**: plain, documented formats; backups and exports
  for everything new; old data keeps working (migrations, not resets).
- **Per profile**: new data and settings belong to a profile.
- **Done means**: tests (including failure cases), documentation, CHANGELOG,
  and tested by at least one person with a real library.

---

## 0.1.x · Alpha testing and fixes (now)

**Goal:** find out whether the core loop works for real researchers and their
real libraries: get recommendations, pick papers, read, log, keep going.

**How:** testers follow [TESTING.md](TESTING.md) and report through the issue
templates (*Bug report*, *Feature request*, *Testing feedback*). Fixes are
released as `0.1.0-alpha.2`, `alpha.3`, … whenever a few have collected.

**Small additions in this phase**

- `litresearch report`: a diagnostic summary to paste into issues (versions,
  system, counts, last ingest and recommendation funnel; no titles, notes or
  identifiers).
- Clearer error messages wherever testers get stuck.

**Questions testing should answer**

1. Installation: does `install.py` work on other Linux machines and
   Raspberry Pi models? Where does it fail?
2. Are the recommendations relevant? How many of 5 are worth reading, and
   what are the misses (off-topic, too old, too obscure, already known)?
3. Are the default weights, cooldown and reminder interval right?
4. Does the game motivate or annoy? Are the points, the penalty and the
   level speed fair? Which badges are never reached, which too easy?
5. Is the queue limit of 5 right? Is the penalty for unread drops accepted?
6. Which pages are confusing, which are missing? What is used on the phone?
7. How large are real libraries, and how long do ingest and recommendations
   take on them?

**Done when:** at least three testers have used it for four weeks with their
own libraries; no open bugs that lose data or block the core loop; the
answers above are collected in a summary issue that adjusts this roadmap.

---

## 0.2 · Find papers: Europe PMC and PubMed search

**Status:** built on `dev` (2026-10-13), to be tested; released as
`0.2.0-alpha.1` once testers have tried it.

Adding a paper should not need its DOI. Search the literature from within
litresearch and put results straight on the queue:

- One search form (**Library → Find & add**) and command (`litresearch
  search`) with **keywords** (title and abstract), **title**, **author**,
  **journal**, **publication years** and **open access only**; several authors
  or journals separated by `;`; sorted by relevance, date or citations.
- Searches **Europe PMC** (PubMed, PubMed Central, preprints) and **PubMed**
  (NCBI E-utilities), together or separately, or offline in your own library.
  Results of both are merged by DOI/PMID and show where they were found.
- Each result: authors, journal, date, citations, free full text (PMC),
  abstract, and whether it is already in your library, on your queue or read.
  **Add to queue** with one click (CLI: `--add N`).
- Adding works by DOI, `pmid:N` or `PMCn` everywhere (also `queue add`):
  OpenAlex supplies metrics, Europe PMC the PMID, PMC id and abstract, PubMed
  is the fallback.
- Transparent and private: the queries sent to each database are shown;
  only what you type is sent (no e-mail, even to NCBI); free use within the
  limits (3 requests/s for PubMed, 10 with an optional free key).

**Questions for testing:** Do Europe PMC and PubMed find what you look for?
Is the keyword search in title and abstract the right default (Europe PMC
can also search full texts)? Which other fields are missed (MeSH terms,
affiliation, article type, language)? Should results be paged beyond the
first 25 per database?

### Libraries (built on `dev`, 2026-10-14)

- **Several libraries** per profile, each with a name and a description,
  e.g. one per reference manager, project or lab; an overview with size,
  reading progress and last update. A paper exists once, however many
  libraries contain it.
- **Import** from any reference manager: BibTeX (tested with Paperpile), RIS
  (EndNote, Citavi, Zotero, Mendeley) and CSL-JSON (Zotero), into a **new**
  library or an **existing** one (add, or replace with an updated export).
  The file is always checked first; what an export must contain is
  documented and linked from the import page.
- **Export** any library as BibTeX or RIS, optionally with your reading
  status and rating as keywords (labels in the reference manager).
- **Recommended**: recommendations join their own library when you put them
  on the queue, and only then.
- The **reading queue** as a library that can be exported and always holds
  exactly the current queue (max. 5).
- Per library: use it for the recommendation similarity or not.

**Questions for testing:** Do RIS and CSL-JSON exports of Zotero, Mendeley
and EndNote import cleanly? Is the split into several libraries useful, or
would folders within one library be enough? Does an export with reading
status import well into your reference manager (are the labels kept)?

**Done when:** tested by several testers with their own searches and
libraries (at least one non-Paperpile export); both databases' outages are
handled (one failing database never hides the other's results).

---

## 0.3 · Your reading history

Closing the gaps in the daily workflow found so far. (Recommended papers
joining their own library is done since 0.2; open: should a paper you drop
unread leave *Recommended*? Should papers added by hand get a library of
their own?)

### Read papers: your reading history

- A new tab **Reading → Read papers**: every paper you marked abstract,
  skimmed, read or thorough, with date, depth, rating, the start of your notes
  and the XP earned.
- Sort and filter by date, depth, rating, topic, folder and origin; full-text
  search in notes; open a paper to read and edit its notes.
- Export the list (TSV) and the notes (Markdown, see 0.5).
- *Open questions:* show dismissed papers here or separately? A "re-read"
  reminder for highly rated papers?

**Done when:** tested with a real library; the data migration from 0.2
keeps all progress.

---

## 0.4 · PubMed and PubMed Central for recommendations and full texts

- **PubMed and Europe PMC as recommendation sources** (the search clients
  exist since 0.2): topic queries and MeSH terms (already in the topic
  settings) find candidates there too. Results are merged with OpenAlex
  candidates by DOI/PMID; the funnel and evidence tree show which source
  found a paper.
- **PubMed Central** for open-access papers: link to the free full text, and
  download it (JATS XML) on request for the reading view and for summaries
  (0.6).
- *Open questions:* is PubMed better than OpenAlex for your topics (compare
  sets side by side during testing)? Also bioRxiv/medRxiv directly, or is
  OpenAlex's coverage enough?

**Done when:** recommendation sets with and without PubMed are compared by
testers; outages of NCBI are handled like those of the other services.

---

## 0.5 · Notes, compatible with Obsidian

- A **note per paper** in Markdown, stored as a plain `.md` file: front
  matter with title, authors, DOI, citation key, status, rating, tags and
  dates; sections for summary, key findings, methods, own thoughts and
  quotes (templates you can change).
- Editor in the web UI with preview; links between papers and to topics
  (`[[citation-key]]`), so the notes form a graph.
- **Obsidian vault compatible**: point `paths.obsidian_vault` (per profile)
  to a folder in your vault. litresearch writes notes there and reads your
  changes back (two-way sync by file modification time and a content hash;
  conflicts are shown, never silently overwritten). Also works without
  Obsidian (any Markdown editor, or none).
- Reading log notes from 0.1 are migrated into these files.
- Dataview-friendly front matter, so Obsidian users can build their own
  tables.
- *Open questions:* one folder per profile or per topic? Should highlights
  from PDFs be imported (needs PDF access, see 0.7)?

**Done when:** a round trip (edit in litresearch → edit in Obsidian → back)
loses nothing, tested with a real vault; the vault folder can be synced by
the user's own tool (Obsidian Sync, Syncthing).

---

## 0.6 · Local LLM summaries

- Summaries by a **model you run yourself** through an OpenAI-compatible
  endpoint (LM Studio, Ollama, llama.cpp server, an institutional server);
  configured in `llm` (already validated in the config, unused until now).
  No cloud AI by default.
- From the abstract, or the full text where it is available (PMC, 0.4, or a
  PDF you provide): a short summary, key findings, methods, limitations, and
  why it matches your library and topics.
- Shown on the paper page and the queue, and written into the note's summary
  section (0.5), marked as AI-generated with model name and date.
- Optional: a summary of a whole recommendation set ("what is new this
  week"), and questions to the paper.
- Everything is optional and cached; without a model, nothing changes.
- *Open questions:* which models work well on typical hardware (Pi, laptop,
  workstation)? Should summaries influence the ranking (no, by default)?

**Done when:** tested with at least two local model servers; the quality is
judged by testers on their own papers; failures and slow models never block
the UI.

---

## 0.7 · Reference managers: write-back and automatic updates

Since 0.2 every reference manager's export can be imported (BibTeX, RIS,
CSL-JSON) into several libraries, and every library can be exported with
your reading status as labels. Building on that:

### Deeper Paperpile integration

Paperpile has no public API; its BibTeX auto-export and its BibTeX import
are the interface. Within that:

- **Write-back**: a ready-made file of only what changed in litresearch
  (new papers, new reading status) since the last export, so that importing
  it into Paperpile does not create duplicates.
- **Automatic updates**: watch the auto-export file (e.g. synced by rclone)
  and re-import when it changes; show what is new or removed.
- **Folders and labels as topics**: suggest topics from your Paperpile
  folders and use labels for filters.
- Links from each paper to open it in Paperpile.
- *Open questions:* does Paperpile keep custom labels on re-import? Which
  fields survive its import (test with real accounts)?

### Other reference managers

- **Zotero** directly: its local API or Better BibTeX's auto-export, and
  write-back of tags and notes through its API.
- Tested import and export profiles for **Mendeley**, **EndNote**, **JabRef**
  and **Citavi** once testers have tried them with real exports.
- The paper page shows in which libraries a paper is.

**Done when:** each supported manager is tested with a real export; switching
the reference manager keeps all progress.

---

## 0.8 · More systems and devices

- **macOS and Windows**: replace the Linux-only parts (network interface
  detection, systemd) with portable code; installers for each system
  (`install.py` plus a launchd agent / Windows task to start the server).
  CI runs the tests on all three systems.
- **Docker image** for servers and NAS devices.
- **Phone**: an installable web app (PWA) with offline reading of the queue
  and notes; layout tuned for small screens; quick logging of a read.
- **HTTPS** option (self-signed or Let's Encrypt behind a reverse proxy)
  for use outside the home network, documented with a VPN alternative.
- Accessibility check (keyboard, screen readers, contrast).
- *Open questions:* is a native desktop app wanted, or is the browser enough?

**Done when:** testers on each system install and use it without help from
the documentation's troubleshooting section.

---

## 0.9 · Beta and 1.0

- **Beta (0.9):** feature freeze; the data format, configuration and profile
  files are declared stable, with migrations from every earlier version;
  documentation complete for users and developers; performance with large
  libraries (10 000 papers).
- **1.0:** no known data-loss bugs; upgrade path tested from 0.1; release
  packages for all supported systems.

## Ideas for later (not planned yet)

Open an issue if you want one of these, or something else:

- shared profiles for a lab or journal club (who reads what, joint reading
  lists);
- citation-network exploration ("papers citing my favourites");
- alerts for new papers by followed authors;
- a reading-time estimate and calendar integration;
- statistics across profiles and years.

## How to influence the roadmap

- Comment on or 👍 the issue of a feature; create one if it is missing.
- Report how you use litresearch (*Testing feedback* template); real usage
  decides priorities more than wishes.
- Pull requests are welcome; please discuss larger changes in an issue
  first ([CONTRIBUTING.md](CONTRIBUTING.md)).
