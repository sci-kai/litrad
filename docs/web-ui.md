# Web UI

```bash
litresearch serve                 # opens http://127.0.0.1:8080/ in your browser
litresearch serve --port 8765 --no-browser
```

The UI runs on your own computer and loads nothing from the internet (no
fonts, CDNs or trackers). It listens on `127.0.0.1` only, because it has
no login yet. `--host 0.0.0.0` makes it reachable from other machines, which
is not recommended until a login exists. POST requests from other websites
are rejected. Stop the server with Ctrl+C.

The phone version (PWA) will build on the same app later.

## Pages

| Page | What you can do |
|---|---|
| 🏠 **Dashboard** | your up-next paper with a one-click reading log, the weekly quest, hints for what to do next, this week's picks, queue, recent XP |
| ✨ **This week** | the weekly recommendations with score bars, an **evidence tree** per paper, the filter **funnel** and the settings used; add a paper to your queue or mark it not relevant; build or rebuild the batch; browse earlier weeks |
| 📚 **Reading queue** | the ordered queue (q1 = up next): log a reading (level, star rating, notes), reorder, remove; add papers by DOI, PubMed ID, citation key, title words or title only |
| 🔍 **Find & add** | search your library and all known papers (title, author, citation key, DOI, folder) and queue them; add any DOI |
| 📄 **Paper** | abstract, TL;DR, metrics, notes (timestamped), status, every recommendation of it with its evidence tree, the XP it earned |
| 🏆 **Progress** | level ring, streak, Reading Impact Factor, discipline and depth, XP per week (12 weeks), reading calendar (26 weeks), personal bests, badge gallery with progress, career ladder |
| 🗓️ **Weekly reports** | one report card per week: grade, XP, papers, points by kind, badges |
| 💰 **XP ledger** | every transaction with running balance, filter by kind, totals, TSV export |
| 🔎 **How it works** | all recommendation criteria with your current values, and the game rules |
| ⚙️ **Settings** | topics, excluded terms and every recommendation, queue and game parameter |

Keyboard shortcuts (outside text fields): `n` go to the queue, `r`
recommendations, `p` progress.

## Rewards and feedback

- An **XP toast** counts up the points of an action and lists every bonus or
  penalty.
- **Confetti** for earned points; more for a completed weekly quest.
- A **pop-up** for level-ups and unlocked badges.
- The up-next paper glows; the streak flame flickers.
- **Penalties** show as a red, shaking toast. A confirmation dialog warns
  before you remove a picked recommendation unread.
- With your system's *reduce motion* setting on, all animations are off.

## Removing papers from the queue

Removing is free for papers you added yourself or from your library. A paper
you picked **from the recommendations** costs `game.drop_penalty` XP
(default 15) if it leaves the queue before you at least skimmed it: removed,
or marked not relevant. The queue shows this as a red chip, and the remove
button names the cost.

## Settings

Saving writes to `config/config.yaml`:

- comments and order are kept;
- the previous version is kept as `config/config.yaml.bak`;
- invalid values (e.g. more than 5 papers, all weights 0) are rejected and
  the file is left unchanged.

Paths, API keys, embeddings, LLM and web settings are only editable in the
file. Fields of a topic that the UI does not show (`mesh_terms`,
`seed_dois`) are kept.
