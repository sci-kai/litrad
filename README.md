# litresearch

A personal literature radar built on a Paperpile library. Whenever you ask
(with a weekly reminder), it proposes **up to 5 papers**, ranked by
similarity to your library (SPECTER2), your topics, paper impact and
timeliness: very recent *or* a landmark paper.

- You pick which papers go on your **reading queue**: short (max 5), ordered,
  one paper "up next". Any paper can be added by DOI or title.
- You log how deeply you read (abstract / skimmed / read / thorough), rate
  and take notes; a **game layer** (XP, streaks, weekly quests, 20 career
  levels, 44 badges, weekly report cards) keeps you going.
- Everything belongs to a **profile** with its own library, topics,
  recommendations, progress and game rules, which are locked when the profile
  is created. Profiles can be saved, loaded and compared.
- Everything is **transparent**: each recommendation has an evidence tree, and
  every rule is documented and adjustable.
- Free data sources only (OpenAlex, Semantic Scholar, Crossref), no personal
  data sent.

**Status:** alpha (`v0.1.0-alpha.1`), ready for testing. Expect rough
edges; please [test it](TESTING.md) and report what breaks.
Runs on Linux (developed on a Raspberry Pi); a phone app (PWA) is planned.

## Install

```bash
git clone <repository-url> litresearch && cd litresearch
python3 install.py                       # add --service to start the web UI automatically
.venv/bin/litresearch serve              # asks where to serve, prints the address to open
```

In the browser: create a profile, upload your Paperpile BibTeX export
(**Library → Paperpile library**), name your topics and get your first
recommendations (**Reading → Recommendations**). Everything also works on the
command line:

```bash
.venv/bin/litresearch profile create "Kai"
cp library.bib data/profiles/kai/library.bib && .venv/bin/litresearch ingest
.venv/bin/litresearch recommend          # latest list; --new for new papers
.venv/bin/litresearch queue add r1
.venv/bin/litresearch read q1 thorough --rating 5 --note "..."
.venv/bin/litresearch score
```

Update with `git pull && python3 install.py`; your `config/` and `data/` stay.

## Documentation

Everything is in **[docs/documentation.md](docs/documentation.md)**:

| Part | Contents |
|---|---|
| I. Using litresearch | setup (Paperpile export, sync), the reading loop, profiles, the web UI and network access |
| II. How it works | how papers are recommended (every rule and formula, the evidence tree), points, penalty, levels and badges |
| III. Reference | all commands, data sources and privacy, configuration, where your data lives, backups, moving machines |
| IV. How it is built | architecture, modules, algorithms, tests, development workflow, **credits for all packages, services and literature** |

## Roadmap

`v0.1.0-alpha.1` is in testing; feedback decides the next steps. Planned, in
this order unless testing says otherwise ([details](ROADMAP.md)):

| Version | Focus |
|---|---|
| 0.1.x | alpha testing and fixes ([how to test](TESTING.md)) |
| 0.2 | **next:** find papers in Europe PMC and PubMed by keywords, title, author, journal and year, and queue them (built, in testing) |
| 0.3 | your reading library: recommended papers join it, a tab with all read papers, notes and ratings |
| 0.4 | PubMed and PubMed Central for recommendations and free full texts |
| 0.5 | notes as Markdown files, compatible with an Obsidian vault |
| 0.6 | summaries by a local LLM (LM Studio, Ollama) |
| 0.7 | deeper Paperpile integration; Zotero, Mendeley, EndNote and others |
| 0.8 | macOS, Windows, Docker, installable phone app |
| 0.9 / 1.0 | beta with stable data format, then 1.0 |

## Contributing

Issues and pull requests are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md).
`main` holds releases, `dev` is the development branch, and feature branches
(`feat-*`, `issueNN-*`) branch off `dev`. Changes: [CHANGELOG.md](CHANGELOG.md).

## License

MIT with the [Commons Clause](https://commonsclause.com): free to use, study,
change and share, but not to sell this software or a paid product or service
(incl. hosting, consulting or support) based mainly on it. See [LICENSE](LICENSE).
