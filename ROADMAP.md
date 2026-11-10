# Roadmap of litrad, the Literature Radar Game

Where litrad goes after 1.0. The order is a proposal: **feedback from use
decides** what comes first, changes or is dropped. Each milestone is a GitHub
milestone with its issues.

## Principles

- **You decide:** litrad suggests; nothing is added to your queue, library or
  notes without your action.
- **Transparent:** every number behind a recommendation or score can be seen
  and explained.
- **Private and free:** free services within their limits, no personal data
  sent, AI only through a model you run yourself.
- **Your data stays yours:** plain, documented formats, backups and exports
  for everything, migrations instead of resets; everything per profile.
- **Compatible within 1.x:** every 1.x version reads and upgrades the data,
  settings and archives of every earlier 1.x version. New features only add
  (optional fields, new tables, new files); renaming or removing anything
  needs 2.0.
- **Done means:** tests (including failure cases), documentation, CHANGELOG,
  and tried by at least one person with a real library.

## 1.0

| Area | What 1.0 contains |
|---|---|
| Recommendations | up to 5 papers on request (weekly reminder): SPECTER2 similarity to your library, topics, impact, timeliness; fresh *or* landmark; an evidence tree for each |
| Finding papers | Europe PMC and PubMed search, your library, DOI / PubMed ID / title in one add box |
| Libraries | several per profile; BibTeX, RIS and CSL-JSON import from any reference manager; export with reading status |
| Reading | short ordered queue, four reading depths, ratings, read papers you read before litrad, a reading history |
| Notes | one Markdown file per paper, editor with preview, two-way sync with an Obsidian vault |
| Game | XP, streaks, weekly quests, 60 levels, 120+ badges, the 🧀 for reading your top paper first; rules locked per profile with a fingerprint |
| Profiles | separate folders, save, load, compare and merge profiles |
| Data | versioned SQLite schema with tested upgrades, documented archive format with JSON Schema and consistency checks, automatic backups |
| Running it | desktop app (Windows, macOS, Linux); server mode with password via Docker, pipx or git + `install.py`; installable phone web app |

## 1.1 · More sources and helpers

- **PubMed and PMC:** Europe PMC/PubMed as recommendation sources (topic
  queries, MeSH terms), merged with OpenAlex candidates and shown in funnel
  and evidence tree; free full texts (JATS XML) from PubMed Central on
  request.
- **Local LLM summaries:** summaries, key findings, methods and limitations
  from abstract or full text by a model you run (LM Studio, Ollama,
  llama.cpp; `llm` settings), marked as AI-generated; optional and cached;
  never influences the ranking by default.
- **Reference managers:** Paperpile write-back (a file of only what changed),
  watching an auto-export file and re-importing, folders and labels as topic
  suggestions; Zotero's local API or Better BibTeX; tested profiles for
  Mendeley, EndNote, JabRef and Citavi.
- **Diagnostics:** `litrad report`, a summary to attach to an issue.

## Ideas for later

Shared lab or journal-club profiles, citation-network exploration, alerts for
followed authors, reading-time estimates and calendar integration, statistics
across profiles and years, signed desktop builds. Not planned: native
Android/iOS apps (the installable web app covers the use). Open an issue if
you want one of these.

## How to influence the roadmap

Comment on or 👍 an issue, or open one; report how you use litrad (*Testing
feedback* template); discuss larger pull requests in an issue first
([CONTRIBUTING.md](CONTRIBUTING.md)).
