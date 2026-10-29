# litresearch

A personal literature radar built on your reference-manager libraries
(Paperpile, Zotero, Mendeley, EndNote …). Whenever you ask (with a weekly
reminder), it proposes **up to 5 papers**, ranked by similarity to your
library (SPECTER2), your topics, paper impact and timeliness: very recent
*or* a landmark.

- **You decide:** pick papers onto a short, ordered **reading queue**; add
  any paper by DOI, PubMed ID or title, or find it in **Europe PMC and
  PubMed**.
- **Track your reading:** abstract / skimmed / read / thorough, ratings and
  notes; a **game layer** (XP, streaks, weekly quests, 20 levels, 44 badges)
  keeps you going.
- **Several libraries** from any reference manager (BibTeX, RIS, CSL-JSON),
  exportable with your reading status.
- **Profiles** hold everything personal, with game rules locked on creation;
  save, load and compare them.
- **Transparent and private:** an evidence tree for every recommendation,
  every rule documented; free services only, no personal data sent.

**Status:** `v0.2.0-dev` (2026-10-29), a development release for testing;
previous release `v0.1.0-alpha.1`. Runs on Linux (developed on a Raspberry
Pi). Please [test it](TESTING.md) and report what breaks.

## Install and first steps

Needs Linux with Python ≥ 3.11 and git (Debian, Ubuntu, Raspberry Pi OS:
`sudo apt install git python3-venv`).

```bash
git clone <repository-url> litresearch && cd litresearch
python3 install.py                 # checks, installs, offers to start the web UI
.venv/bin/litresearch serve        # later: start the web UI, open the address it prints
```

Options: `--service` (start the web UI automatically), `--repair` (if
litresearch does not start), `--remove-service`. Details:
[Setup](docs/documentation.md#1-setup).

In the browser:

1. create a profile (and its game rules);
2. **Library → Import** your reference-manager export;
3. **Reading → Recommendations**: name your topics and get your first list;
4. put papers on your queue, read, and log what you read.

Everything also works on the command line:

```bash
.venv/bin/litresearch profile create "Kai"
.venv/bin/litresearch library import library.bib --new "Paperpile"
.venv/bin/litresearch recommend --new
.venv/bin/litresearch queue add r1
.venv/bin/litresearch read q1 thorough --rating 5 --note "..."
.venv/bin/litresearch score
```

Update with `git pull && python3 install.py`; `config/` and `data/` stay.

## Documentation

Everything is in **[docs/documentation.md](docs/documentation.md)**, also
built into the web UI (📖):

| Part | Contents |
|---|---|
| I. Using litresearch | setup and exports, the weekly loop, profiles, libraries, the web UI and network access |
| II. How it works | every recommendation rule and formula; points, penalty, levels and badges |
| III. Reference | commands, data sources and privacy, configuration, data and backups |
| IV. How it is built | architecture, development, tests, **credits for all packages, services and literature** |

Plans: [ROADMAP.md](ROADMAP.md) · changes: [CHANGELOG.md](CHANGELOG.md) ·
testing: [TESTING.md](TESTING.md).

## Contributing

Issues and pull requests are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md).
`main` holds releases, `dev` is the development branch; feature branches
(`feat-*`, `issueNN-*`) start from `dev`. Security: [SECURITY.md](SECURITY.md).

## License

MIT with the [Commons Clause](https://commonsclause.com): free to use, study,
change and share, but not to sell this software or a paid product or service
(incl. hosting, consulting or support) based mainly on it. See [LICENSE](LICENSE).
