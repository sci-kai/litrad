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

**Status:** `v0.1.0-dev`. Local web UI and command line; a phone app (PWA) is
planned.

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp config/config.example.yaml config/config.yaml
.venv/bin/litresearch serve              # prints the address to open
```

In the browser: create a profile, upload your Paperpile BibTeX export
(**Library → Paperpile library**), name your topics and get your first
recommendations (**Reading → Recommendations**). The same works on the
command line:

```bash
.venv/bin/litresearch profile create "Kai"
cp library.bib data/profiles/kai/library.bib && .venv/bin/litresearch ingest
.venv/bin/litresearch recommend          # latest list; --new for new papers
.venv/bin/litresearch queue add r1
.venv/bin/litresearch read q1 thorough --rating 5 --note "..."
.venv/bin/litresearch score
```

## Documentation

Everything is in **[docs/documentation.md](docs/documentation.md)**:

| Part | Contents |
|---|---|
| I. Using litresearch | setup (Paperpile export, sync), the reading loop, profiles, the web UI and network access |
| II. How it works | how papers are recommended (every rule and formula, the evidence tree), points, penalty, levels and badges |
| III. Reference | all commands, data sources and privacy, configuration, where your data lives, backups, moving machines |
| IV. How it is built | architecture, modules, algorithms, tests, development workflow, **credits for all packages, services and literature** |

## Roadmap

1. ✅ Skeleton: config, database, CLI
2. ✅ Library ingest and enrichment
3. ✅ Candidate search, scoring, ≤ 5-paper lists on request (unread library papers included)
4. ✅ Reading queue, reading log, gamification, local web UI, profiles · ⏳ phone app (PWA)
5. LLM summaries via an OpenAI-compatible endpoint (LM Studio / Ollama)
6. Obsidian export/sync, Paperpile write-back file, systemd deployment
7. Feedback loop: ✅ dismissals and good ratings already steer the S2 recommendations; ⏳ weight tuning

## Branches

- `main`: releases
- `dev`: development; feature branches `feat-*` branch off `dev`
  (see [Development](docs/documentation.md#13-development))

## License

MIT with the [Commons Clause](https://commonsclause.com): free to use, study,
change and share, but not to sell this software or a paid product or service
(incl. hosting, consulting or support) based mainly on it. See [LICENSE](LICENSE).
