# Testing litresearch 0.2.0-dev

Thank you for testing! Use litresearch with your **own library for real
reading** for a few weeks, not only a quick click through. Setting it up
takes about 30 minutes.

## Before you start

- Install with `python3 install.py` ([README](README.md)); note your system,
  Python version and browser.
- Point `backup.dir` to a synced folder and run `litresearch backup` now and
  then (documentation, "Your data and backups").
- Never paste private data (exports, notes, API keys) into an issue; titles
  of public papers are fine.

## First day

- [ ] Install; `litresearch serve`; open it from another device (after
      `litresearch set-password`)
- [ ] Create a profile; change a game rule before creating it
- [ ] Export your library (BibTeX, RIS or CSL-JSON) and import it under
      *Library → Import*: were the checks and instructions clear?
- [ ] Set up topics on the first visit to *Recommendations*; get the first
      list (how long did it take? warnings?); read one evidence tree
- [ ] Add papers to the queue: from recommendations, your library, by DOI,
      `pmid:`, title
- [ ] Log a read with depth, rating and note; watch the XP pop-up
- [ ] Find the answer to a question in the documentation (📖)

## New in 0.2: libraries and Find & add

- [ ] A **second library** (another manager or project); import into an
      **existing** library, once *adding* and once *replacing*
- [ ] **Export** a library as BibTeX or RIS *with reading status* and import
      it into your reference manager: are the labels kept?
- [ ] Re-import an updated export: are papers recognised, nothing doubled?
- [ ] *Recommended* and *Reading queue* libraries; switch a library off for
      recommendations
- [ ] **Find & add**: search Europe PMC and PubMed by keywords, author,
      journal, years, open access; did you find what you looked for? Queue a
      result; search your own library
- [ ] Large libraries: how long do import, recommendations and the library
      pages take, for how many papers?

## Two to four weeks

- [ ] New recommendations when reminded; mark misses *not relevant*; do
      later lists get better?
- [ ] Read from the queue; keep a weekly streak and the quest
- [ ] Remove a paper unread at least once (the penalty)
- [ ] Update a library from your reference manager (*Update from file*)
- [ ] *Progress & XP*, the XP ledger and weekly reports
- [ ] Use it from the phone browser
- [ ] Save a profile file; (optional) load it as a second profile and compare

## Reporting

- **Testing feedback** template after one and after four weeks: how many
  recommendations were worth reading, what motivated or annoyed you, what
  you missed or never used.
- **Bug report**: steps, expected vs. actual, terminal output (and browser
  console, F12, for UI problems), `litresearch version` and your system.
- **Feature request**: check the [roadmap](ROADMAP.md) and existing issues
  first.

Developers: `python3 install.py --dev && .venv/bin/pytest`; see
[CONTRIBUTING.md](CONTRIBUTING.md).
