<p align="center"><img src="src/litrad/web/static/favicon.svg" width="112" height="112" alt="litrad logo: a rat wearing a mortarboard"></p>

# litrad · The Literature Radar Game

A personal literature radar on top of your reference-manager libraries
(Paperpile, Zotero, Mendeley, EndNote …). Whenever you ask, with a weekly
reminder, it proposes **up to 5 papers**, ranked by similarity to your library
(SPECTER2), your topics, impact and timeliness; you pick papers onto a short
reading queue, log how deeply you read them, and a game layer (XP, streaks,
weekly quests, levels and badges; read your top paper first and you get the
🧀) keeps you going. Profiles keep everything personal with locked game rules,
every recommendation comes with its evidence, and only free services are used
without sending personal data.

**Status:** `1.0.0-rc.1` (release candidate). Please [test it](TESTING.md) and
report what breaks.

## Install

**Desktop app (default):** download the file for your system from the latest
GitHub release: Windows installer (`.exe`), macOS (`.dmg`) or Linux AppImage.
Double-click it; litrad starts on this computer only and opens in your
browser. Quit with **⏻ Quit** in the sidebar. The builds are unsigned: Windows
*More info → Run anyway*, macOS right-click → *Open*, Linux `chmod +x`.
Details: [Desktop app](docs/documentation.md#desktop-app).

**Server mode** (a home server, NAS or lab server, reachable from your other
devices with a password), one of:

- **Docker:**

  ```bash
  docker run --rm -it -v litrad:/data ghcr.io/<owner>/litrad litrad set-password   # once
  docker run -d --name litrad --restart unless-stopped -p 8080:8080 \
      -v litrad:/data ghcr.io/<owner>/litrad
  ```

- **pipx** (Python ≥ 3.11): `pipx install litrad`, then `litrad serve`.
- **git and install.py** (Linux with Python ≥ 3.11 and git; Debian/Ubuntu:
  `sudo apt install git python3-venv`):

  ```bash
  git clone <repository-url> litrad && cd litrad
  python3 install.py            # checks, installs, offers to start the web UI
  .venv/bin/litrad serve        # later: start the web UI, open the address it prints
  ```

  Options: `--service` (start the web UI automatically), `--repair`,
  `--remove-service`. Update with `git pull && python3 install.py`.

Details: [Server mode](docs/documentation.md#server-mode).

## First steps

In the browser:

1. create a profile (its game rules are locked);
2. **Library → Import** your reference-manager export;
3. **Reading → Recommendations**: name your topics and get your first list;
4. put papers on your queue, read, and log what you read.

The same on the command line:

```bash
litrad profile create "Kai"
litrad library import library.bib --new "Paperpile"
litrad recommend --new
litrad queue add r1
litrad read q1 thorough --rating 5 --note "..."
litrad score
```

## Documentation

Everything is in **[docs/documentation.md](docs/documentation.md)**, also built
into the web UI (📖): using litrad, how recommendations and the game work,
commands, configuration, data formats and backups, architecture and credits.

Plans: [ROADMAP.md](ROADMAP.md) · changes: [CHANGELOG.md](CHANGELOG.md) ·
testing: [TESTING.md](TESTING.md) · contributing:
[CONTRIBUTING.md](CONTRIBUTING.md) · security: [SECURITY.md](SECURITY.md).

## License

MIT with the [Commons Clause](https://commonsclause.com): free to use, study,
change and share, but not to sell litrad or a paid product or service (incl.
hosting, consulting or support) based mainly on it. See [LICENSE](LICENSE).
