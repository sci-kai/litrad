# Testing litresearch

Thank you for testing! This alpha needs people who use it with their **own
library for real reading**, not only a quick click through. This guide says
what to try and how to report it. Everything takes about 30 minutes to set
up; then use it for a few weeks as part of your reading.

## Before you start

- Install with `python3 install.py` (see the [README](README.md)); note your
  system (e.g. Raspberry Pi OS 64 bit, Ubuntu 24.04), Python version and
  browser.
- It is an alpha: run `litresearch backup` now and then, and point
  `backup.dir` to a synced folder (documentation, chapter "Your data and
  backups").
- Your library and notes never leave your machine. When you report, do not
  paste private data (BibTeX export, notes, API keys); titles of public
  papers are fine if they help.

## Part 1: first steps (first day)

Tick what worked; report what did not.

- [ ] Installation with `install.py` (how long did it take?)
- [ ] `litresearch serve`: choosing the network, opening the address on
      another device (with `litresearch set-password`)
- [ ] Creating a profile; changing a game rule before creating it
- [ ] Exporting the library from Paperpile (or another reference manager as
      BibTeX) and uploading it: were the checks and instructions clear?
- [ ] Setting up topics on the first visit to *Recommendations*
- [ ] Getting the first recommendations (how long did it take? warnings?)
- [ ] Reading the evidence tree of one recommendation: is it understandable?
- [ ] Adding papers to the queue: from the recommendations, from your
      library, by DOI, by title
- [ ] *Library → Find & add*: search Europe PMC and PubMed by keywords,
      author, journal and years; did you find what you looked for? Add a
      result to the queue
- [ ] Logging a read with depth, rating and a note; the XP pop-up
- [ ] The documentation in the web UI (📖): did you find what you looked for?

## Part 2: everyday use (two to four weeks)

- [ ] Get new recommendations when reminded (about once a week)
- [ ] Mark irrelevant papers as *not relevant*; do later sets get better?
- [ ] Read from the queue; try to keep a weekly streak and the weekly quest
- [ ] Remove a paper unread at least once (the penalty)
- [ ] Update the library after adding papers in Paperpile
- [ ] Look at *Progress & XP* and the weekly reports on the dashboard
- [ ] Use it from the phone browser
- [ ] Make a backup; save a profile file; (optional) restore or load it as a
      second profile and compare the two
- [ ] (Optional) the command line: `litresearch queue`, `read`, `score`

## Part 3: tell us how it went

Use the **Testing feedback** issue template after one week and again after
four weeks. It asks, among other things:

- How many of each set of recommendations were worth reading? What were
  the misses?
- Did the game motivate you? Which rule felt unfair?
- What did you miss most? What did you never use?
- How long did ingest and recommendations take, for how many papers?

Attach `litresearch version` and, if a step failed, the terminal output.

## Reporting a problem

Open an issue with the **Bug report** template:

1. What you did (steps), what you expected, what happened.
2. The terminal output of `litresearch serve` or the command, and for UI
   problems the browser console (F12 → Console).
3. `litresearch version` and your system.

Ideas go into **Feature request**; check the [roadmap](ROADMAP.md) first,
and comment on an existing issue rather than opening a duplicate.

## For developers: automated tests

```bash
python3 install.py --dev
.venv/bin/pytest             # offline suite, recorded API responses
.venv/bin/pytest --network   # also against the real APIs
```

See [CONTRIBUTING.md](CONTRIBUTING.md) and the Development chapter of the
documentation.
