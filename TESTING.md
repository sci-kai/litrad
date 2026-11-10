# Testing litrad 1.0.0-rc.1

Thank you for testing the release candidate! Use litrad with your **own
library for real reading** for a week or two, not only a quick click through.
Setting it up takes about 30 minutes.

## Before you start

- Install the desktop app or a server ([README](README.md)); note your system,
  the install way and your browser.
- Point `backup.dir` to a synced folder (documentation, "Your data and
  backups").
- Never paste private data (exports, notes, API keys) into an issue; titles of
  public papers are fine.

## Checklist

**Install and set up**

- [ ] Install; start it (desktop app, or `litrad serve` and open it from
      another device after `litrad set-password`)
- [ ] Create a profile; change a game rule before creating it
- [ ] Export your library (BibTeX, RIS or CSL-JSON) and import it under
      *Library → Import*: were the checks and instructions clear?

**The weekly loop**

- [ ] Name your topics and get the first recommendations (how long? any
      warnings?); read one evidence tree
- [ ] Add papers to the queue: from recommendations, your library, *Find &
      add*, and by DOI, PubMed ID or title in the add box
- [ ] Log reads with depth, rating and note; read your top paper first once
      (🧀) and remove one paper unread (the penalty)
- [ ] Add papers you read before litrad (*Read earlier*)
- [ ] Notes: write one in the editor; with an Obsidian vault, edit it there
      and sync
- [ ] Look at *Progress & XP*, the XP ledger, the weekly report and *Read
      papers*
- [ ] Use it from a phone browser (server mode)

**Libraries and data**

- [ ] A second library; import into an existing library, once *adding* and
      once *replacing*; re-import an updated export (nothing doubled?)
- [ ] Export a library *with reading status* and import it into your
      reference manager
- [ ] `litrad backup` (or *Back up now*), *Export everything*, then load the
      archive as a second profile and *Compare* the two
- [ ] Large libraries: how long do import, recommendations and the library
      pages take, for how many papers?

## Reporting

- **Testing feedback** template: how many recommendations were worth reading,
  what motivated or annoyed you, what you missed or never used.
- **Bug report**: steps, expected vs. actual, terminal output (and the browser
  console, F12, for UI problems), `litrad version` and your system.
- **Feature request**: check the [roadmap](ROADMAP.md) and existing issues
  first.

Developers: `python3 install.py --dev && .venv/bin/pytest`; see
[CONTRIBUTING.md](CONTRIBUTING.md).
