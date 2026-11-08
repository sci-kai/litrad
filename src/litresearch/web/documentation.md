# litresearch documentation

litresearch is a personal literature radar built on your reference-manager
libraries (Paperpile, Zotero, Mendeley, EndNote …). On request it proposes
**up to 5 papers**, ranked by similarity to your library (SPECTER2
embeddings), your topics, paper impact and timeliness (very recent *or* a
landmark). You choose which go on your short, ordered **reading queue**, log
how deeply you read them, and a **game layer** (XP, streaks, quests, levels,
badges) with rules locked per **profile** keeps you going. Every criterion is
transparent: each recommendation has an evidence tree, and every rule is
documented here.

This is the complete documentation. In the web UI it is under
**📖 Documentation**, one page per chapter.

## Contents

- **Part I: Using litresearch:** [1. Setup](#1-setup) · [2. The weekly loop](#2-the-weekly-loop) · [3. Profiles](#3-profiles) · [4. Web UI](#4-web-ui)
- **Part II: How it works:** [5. How papers are recommended](#5-how-papers-are-recommended) · [6. Gamification](#6-gamification)
- **Part III: Reference:** [7. Commands](#7-commands) · [8. Data sources and privacy](#8-data-sources-and-privacy) · [9. Configuration](#9-configuration) · [10. Your data and backups](#10-your-data-and-backups)
- **Part IV: How it is built:** [11. Architecture](#11-architecture) · [12. Implementation and credits](#12-implementation-and-credits) · [13. Development](#13-development)

---

# Part I: Using litresearch


## 1. Setup

### 1. Install

Requires Linux, Python ≥ 3.11, `git` and an internet connection for the
installation (developed on a Raspberry Pi 5, Python 3.13). On Debian, Ubuntu
or Raspberry Pi OS: `sudo apt install git python3-venv`.

```bash
git clone <repository-url> litresearch && cd litresearch
python3 install.py                # about 1–3 minutes; offers to start the web UI at the end
.venv/bin/litresearch serve       # later: start the web UI, open the address it prints
```

`install.py` checks the system, creates `.venv`, installs litresearch,
creates `config/config.yaml` from the example (an existing one is never
overwritten) and checks that litresearch starts. If something is missing it
stops with the fix (e.g. the `apt` command); it never says "Done" over a
broken installation. Run litresearch commands from the litresearch folder:
there `config/config.yaml` is found, and `data/` lives next to `config/`.

| Option | Does |
|---|---|
| `--service` | start the web UI automatically (systemd user service), on the network set as `web.host`, without asking |
| `--remove-service` | stop starting it automatically (data stays) |
| `--repair` | reinstall all packages without pip's cache, e.g. after an interrupted download ("litresearch does not start") |
| `--dev` | add test and lint tools |

- **Update:** `litresearch backup`, then `git pull && python3 install.py`.
  `config/` and `data/` are kept; the CHANGELOG says when a version changes
  the data format.
- **Without the installer:** `python3 -m venv .venv && .venv/bin/pip install -e .`
  and `cp config/config.example.yaml config/config.yaml`.
- **As a package** (`pipx install litresearch` once it is on PyPI, or the
  wheel of a GitHub release: `pip install litresearch-*.whl`): no git folder
  needed, run it from any folder. The first command creates your configuration and data in your
  per-user folders:

  | System | Configuration | Data (profiles, backups, cache) |
  |---|---|---|
  | Linux | `~/.config/litresearch/config.yaml` | `~/.local/share/litresearch/` |
  | macOS | `~/Library/Application Support/litresearch/config.yaml` | `~/Library/Application Support/litresearch/` |
  | Windows | `%LOCALAPPDATA%\litresearch\config.yaml` | `%LOCALAPPDATA%\litresearch\` |

`litresearch where` shows which configuration and data folders are in use
and why ([lookup order](#9-configuration)).

**With Docker** (server mode for a Pi, NAS or lab server; the image runs on
ARM and x86). The image is named after the GitHub repository,
`ghcr.io/<owner>/litresearch` below:

```bash
docker run --rm -it -v litresearch:/data ghcr.io/<owner>/litresearch litresearch set-password   # once
docker run -d --name litresearch --restart unless-stopped -p 8080:8080 \
    -v litresearch:/data ghcr.io/<owner>/litresearch
```

Open `http://<host>:8080` and log in. The server listens on all networks of
the container and refuses to start without a password, so set it first; for a
script or a Docker secret use `set-password --password-file FILE` (first line
of the file) instead of the prompt. Everything lives in the volume `/data`
(configuration `/data/config.yaml`, created on first start, profiles,
database, backups in `/data/backups`); to edit the file, find the volume
with `docker volume inspect litresearch`.

**Update:** run `docker exec litresearch litresearch backup`, then
`docker pull` the image, `docker rm -f litresearch` and start it again with
the same volume. The port is plain HTTP: keep
it in your home network or behind a VPN, as described in
[Web UI](#using-it-from-your-laptop-local-network).

### 2. Export your library from your reference manager

litresearch reads your library from an export file, so it works with any
reference manager and never needs its password or PDFs.

| Reference manager | How to export | Format |
|---|---|---|
| **Paperpile** (tested) | *Export → BibTeX*; or automatically: profile picture → *Workflows and Integrations* → *BibTeX export* to Google Drive (step 3) | BibTeX |
| **Zotero** | *File → Export Library…* (or Better BibTeX with *Keep updated*) | BibTeX, RIS, CSL-JSON |
| **Mendeley** | *File → Export…* | BibTeX, RIS |
| **EndNote** | *File → Export…*, output style *RefMan (RIS) Export* | RIS |
| **JabRef**, **Citavi** | export as BibTeX (Citavi also RIS) | BibTeX, RIS |

Only Paperpile's BibTeX has been tested with real libraries; the other
formats are covered by tests, and reports from real exports are welcome.

### What an export must contain

The import checks every file and says what is missing; nothing is imported
when it finds an error. Files up to 50 MB can be uploaded.

| Field | BibTeX | RIS | CSL-JSON | Needed? | Why it matters |
|---|---|---|---|---|---|
| Title | `title` | `TI` | `title` | **required** | needed for everything |
| DOI | `doi` (or inside `url`) | `DO` | `DOI` | strongly recommended | the key to metrics, embeddings and recommendations; without it a slower, less reliable title look-up is needed |
| Authors | `author` | `AU` | `author` | recommended | shown in lists; verifies DOI look-ups |
| Year (and month) | `year`, `month` | `PY`, `DA` | `issued` | recommended | decides whether a paper is fresh or a landmark |
| Abstract | `abstract` | `AB` | `abstract` | recommended | text-similarity fallback and the paper page |
| Journal | `journal` | `JO`, `T2` | `container-title` | recommended | shown in lists |
| Folders and labels | `keywords` | `KW` | `keyword` | optional | filters on the library page |
| PMID | `pmid` | `AN` | `PMID` | optional | second identifier |
| URL | `url` | `UR` | `URL` | optional | link; a DOI inside it (also `www.doi.org`) is picked up |
| Citation key | entry key | `ID` | `id` | optional | recognises a paper on the next import (otherwise DOI, PMID or title + year). Keys that RIS/CSL generate for entries without `ID`/`id` do not identify papers. If the entry behind a key now has a different DOI/PMID, it counts as a new paper (so correcting a DOI in your reference manager yields a new paper) |

- **Paperpile:** in the export options include DOI, PMID, abstract, URL and
  keywords (keywords carry folders and labels; folder paths contain `/`).
- **Zotero:** abstracts are exported by default; include notes only if you
  want them as keywords.
- Prefer file names without spaces, e.g. `library.bib`.
- Check a file without importing it: *Check only* on the import page, or
  `litresearch check-bib FILE`.

### 3. Keep the export up to date (optional)

Update a library from its file at any time (*Update from file* on its page,
or `litresearch ingest` for all libraries). For a file your reference manager
keeps up to date, e.g. Paperpile's auto-export to Google Drive, fetch it
with [rclone](https://rclone.org/drive/):

```bash
rclone config                     # once: create a remote called "gdrive"
rclone copyto "gdrive:<path in Drive>/library.bib" ~/litresearch-exports/paperpile.bib
litresearch library import ~/litresearch-exports/paperpile.bib --new "Paperpile"   # once
litresearch ingest                # then e.g. daily from cron
```

Watching the file and re-importing automatically is planned (roadmap 1.1).

### 4. Configure

`config/config.yaml` (or your per-user config, see `litresearch where`)
holds the machine settings (APIs, web server, backups) and the defaults for
new profiles ([Configuration](#9-configuration)). In a git folder it and
`data/` are git-ignored, so API keys stay local. Recommended, both free:

- a **Semantic Scholar API key** (<https://www.semanticscholar.org/product/api>):
  without it all anonymous users share one rate limit and requests often
  fail with HTTP 429;
- a **contact e-mail** (`api.contact_email`) for the Crossref and OpenAlex
  "polite pools".

### 5. Create your profile and import your library

In the web UI: `litresearch serve`, open the printed address, create a
profile, then **Library → Import**: choose the file, name the new library and
press *Check & import*. On the command line:

```bash
litresearch profile create "Kai"      # game rules are locked from now on
litresearch library import library.bib --new "Paperpile" --description "everything"
litresearch status
```

Data from before profiles existed is taken over with `litresearch profile
adopt NAME` (or on the welcome screen); its library becomes "My library".

The import report lists entries added, updated and removed, DOIs taken from
the file or found via Crossref/PMID, entries **not found anywhere** (with
their citation keys) and how many papers got OpenAlex metrics and SPECTER2
embeddings. Imports can be repeated safely; API answers are cached, so
repeated runs make almost no network requests.

### Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `WARNING: Semantic Scholar: ... HTTP 429` | Shared anonymous rate limit. Add `api.semanticscholar_key`, or rerun later; everything else is still imported. |
| "Not found anywhere" | No DOI/PMID and the title could not be matched (e.g. a talk without DOI, or the abstract pasted into the title field). Fix the entry in your reference manager; the next import picks it up. |
| `Duplicates (skipped): key (doi)` | Two entries are the same paper (same DOI, PMID or title + year); the first is used. Merge them in your reference manager. |
| `duplicate citation key ...` | Two entries share a citation key; only the first is used. |
| `Not valid BibTeX/RIS/CSL-JSON: ...` | Broken (e.g. truncated) or other format. Nothing is changed; export the file again. |
| "The file is larger than 50 MB" | Split the export, or leave out attachments/notes. |


## 2. The weekly loop

```
recommend  ──pick──>  queue (max 5, ordered)  ──next──>  read ... ──>  score
   ^                       ^                                  │
   │                  manual adds                             │
   └──── dismissals and good ratings refine the next batch ───┘
```

The tool proposes, **you** decide what goes on the queue. The queue is short
and ordered, so there is always one paper up next and never a backlog. Each
step works in the web UI ([Web UI](#4-web-ui)) and on the command line, shown
below. Commands work on the **current profile** (the one chosen last in the
web UI or with `profile use`, or the only one); `--profile NAME` picks another
for one command.

### 1. Get recommendations

Web: **Reading → Recommendations → Get recommendations**. CLI:

```bash
litresearch recommend          # latest list; no API requests
litresearch recommend --new    # a new list (about a minute), e.g. after changing topics
```

- Every paper is **fresh** (published within `fresh_window_months`) or a
  **landmark** (top `100 − landmark_min_percentile` % cited in its field and
  year), ranked by score.
- Ask for a new list any time; you are reminded when it is older than
  `selection.reminder_days` (7).
- Papers you ever marked skimmed or deeper, dismissed or queued never show
  up; unread papers from your libraries can. A paper you did not pick can
  come back after `cooldown_weeks` (8).
- A new list every Monday by cron (`crontab -e`):
  `0 7 * * 1  cd /path/to/litresearch-tool && .venv/bin/litresearch ingest && .venv/bin/litresearch recommend --new > data/this-week.txt`

### 2. Fill your reading queue

```bash
litresearch queue add r1              # a recommendation
litresearch queue add r3 --top        # ... straight to position 1
litresearch queue add Barkley2022-ri  # a paper from your libraries (citation key)
litresearch queue add 10.1038/s41586-024-07894-z   # any DOI
litresearch queue add pmid:35931863   # any PubMed ID, or a PMC id: PMC5812436
litresearch queue add --title "Preprint from the lab meeting" --url https://...
litresearch queue                     # show it
litresearch queue move q4 1           # reorder
litresearch queue drop q3             # remove (costs XP if not at least skimmed)
```

- When the queue is full (`queue.max_size`, default 5) nothing can be added
  until you read or drop something.
- A paper that leaves the queue before you at least skimmed it (removed or
  marked not relevant) costs the penalty of your profile (default 15 XP),
  wherever it came from. Dismissing a recommendation you never queued is
  free. The queue shows the cost as a red chip and asks before removing.

#### Find papers in Europe PMC and PubMed

Web: **Library → Find & add**. CLI:

```bash
litresearch search "circulating tumor DNA" --journal "Nature Medicine" --from 2023
litresearch search --author "Swanton C; Turajlic S" --title evolution --sort cited
litresearch search "ctDNA" --oa --source pubmed --limit 20     # free full texts, PubMed only
litresearch search "ctDNA" --author "Swanton C" --add 2        # put result 2 on the queue
litresearch search clonal -s library                          # only your own libraries
```

Keywords are searched in title and abstract; `--title`, `--author`,
`--journal`, `--from`/`--to` and `--oa` narrow it; separate several authors or
journals with `;`. Both databases are searched (those enabled in `sources`)
and merged; each result shows its DOI or `pmid:`, where it was found, whether
it is free to read and whether it is already in your library, queue or read.
The queries sent are shown. Results from your own library (*My library*) are
paginated, 50 per page.

#### Referring to papers

| Form | Meaning |
|---|---|
| `r2` | rank 2 in the latest recommendation list |
| `q1` | position 1 in the queue (renumbers as papers leave) |
| `#123` | internal paper id (shown by `show`) |
| `10.1038/...`, `https://doi.org/...` | DOI |
| `pmid:35931863`, `PMC5812436` | PubMed / PubMed Central id (fetched if not stored yet) |
| `Barkley2022-ri` | citation key from one of your libraries |
| `clonal melanoma` | words from the title (must match exactly one paper) |

### 3. Read

```bash
litresearch next                      # top paper: abstract, TL;DR, link
litresearch read q1 thorough --rating 5 --note "Key figure 3; method usable for our SCC data"
litresearch read q2 abstract          # an abstract read keeps it on the queue
litresearch note q1 "Follow-up: check their supplementary table S4"
litresearch dismiss r4                # not relevant; teaches the recommender
litresearch show Barkley2022-ri       # details, status, all notes
litresearch history
```

Reading levels: `abstract` → `skimmed` → `read` → `thorough`. Reaching
`skimmed` or deeper, or dismissing, removes the paper from the queue. Notes
are stored with an ISO 8601 timestamp. In the web UI, log a reading on the
queue or the dashboard (level, star rating, note).

### 4. Check your score

Web: **Progress & XP** and the dashboard. CLI: `litresearch score` shows
level and XP to the next level, streak and the current XP multiplier ("next
reads earn ×1.05"), this week's grade and quest progress, Reading Impact
Factor, discipline, depth and badges with the next one in reach.

How points, levels and badges work: [Gamification](#6-gamification).


## 3. Profiles

Everything personal belongs to a **profile**: libraries, topics and
recommendation settings, recommendations, queue, readings, notes, XP, badges
and the locked game rules. Profiles (e.g. one per person or project) never
mix.

**Web UI:**

1. The **welcome screen** lists your profiles (level, XP, papers read, last
   activity) and offers *Create a new profile*, *Load a saved profile* and,
   if data from before profiles exists, *Use your existing library*.
2. Creating a profile means choosing its game rules: the form is pre-filled
   from `config.yaml`; every reward, the penalty, the level ladder and the
   badge targets can be changed. **The rules are locked on creation.**
3. The chip at the top right shows profile, level and XP and leads to the
   profile page; **Switch profile** is at the bottom of the sidebar. The
   browser remembers your choice for a year.
4. The first visit to **Recommendations** asks for the profile's topics (or
   *Skip: use my library only*); change them there or under Settings.

**Command line:**

```bash
litresearch profile list                     # * marks the current profile
litresearch profile create "Grace" --yes     # rules from config.yaml, locked
litresearch profile use grace
litresearch --profile Kai queue              # one command for another profile
litresearch profile adopt "Kai"              # data from before profiles -> profile
litresearch profile load saved.json --name "Kai (laptop)"
```

Saving, loading and comparing profiles: [Gamification](#saving-loading-and-comparing-profiles);
where the files live: [Your data and backups](#10-your-data-and-backups).


## 4. Web UI

```bash
litresearch serve                    # asks where to serve; Enter keeps web.host
litresearch serve --network wlan0    # no question: only the WLAN (or eth0, all, local, an IP)
litresearch serve --no-choose        # no question: web.host / web.port from the config
litresearch serve --list-networks    # the networks of this computer
```

`serve` asks where the UI should be reachable (choosing another network
offers to make it the default). It never asks when not run in a terminal
(systemd, cron), never opens a browser itself, and prints the address to
open. The UI loads nothing from the internet and rejects POST requests from
other websites. Stop it with Ctrl+C.

### Where the web server is reachable

| `web.host` / `--network` | Reachable from |
|---|---|
| `local` (default) | only this computer (`127.0.0.1`); no password needed |
| `all` | every network of this computer (cable, WLAN, VPN, …) |
| `eth0`, `wlan0`, … | only that network; other networks and `127.0.0.1` are refused |
| an IPv4 address of this computer | only that address |

Prefer interface **names**: the address is looked up at every start, so a
new DHCP address does not break anything. If the configured network does not
exist, the server does not start and lists the choices.

### Using it from your laptop (local network)

1. Set a password once: `litresearch set-password` (stored only as a salted
   scrypt hash in `web.password_hash`).
2. Choose the network when `serve` asks, or set `web.host: wlan0`.
3. Open the printed address (e.g. `http://10.100.7.134:8080/`) on the laptop
   or phone and log in. The login lasts 30 days; **Log out** is at the bottom
   of the sidebar.

- Without a password the server refuses to listen on a network (`--no-login`
  overrides this; not recommended).
- Without a password the server only answers requests addressed to an IP
  address, `localhost` or the computer's own name (`<hostname>`,
  `<hostname>.local`); other names get "Unknown host name" (HTTP 421). This
  protects against DNS rebinding (a website pointing its own domain at your
  computer). With a password set, any name works (e.g. a router DNS name),
  since the login protects the data.
- `set-password` again signs out all devices; `set-password --remove`
  removes it (the UI then only starts locally).
- A wrong password costs a one-second delay.
- Plain HTTP: fine in your home network, but never expose the port to the
  internet; use a VPN (Tailscale, WireGuard) from outside.
- Run it in the background: `python3 install.py --service`.
- A desktop app (Windows, macOS, Linux) and an installable phone web app for server mode are planned (roadmap 0.3).

### Pages

| Menu | Page | What you can do |
|---|---|---|
| | 👋 **Welcome** | choose, create or load a profile; take over existing data |
| 🏠 | **Dashboard** | the up-next paper with one-click logging, weekly quest, hints, a reminder when recommendations are due, latest recommendations, queue, recent XP, **weekly report cards** |
| Reading | 📚 **Reading queue** | log a reading (level, rating, note), reorder, remove; add papers by DOI, PubMed ID, citation key or title |
| Reading | ✨ **Recommendations** | set up topics (first visit); **Get recommendations**; the ranked list with score bars, an **evidence tree** per paper, the filter **funnel**, the settings used and earlier lists; queue or mark not relevant; **How recommendations are made** with your current values |
| Progress | 🏆 **Progress & XP** | level, streak, Reading Impact Factor, discipline and depth, XP per week, reading calendar, personal bests, badges, career ladder, the **XP ledger** (paginated; filter, totals, TSV export) and the locked rules |
| Library | 🗂️ **Libraries** | all libraries (papers, read, last update), plus *Reading queue* and *Recommended*; per library: papers (search, folder/label and status filters, 50 per page), **Export**, **Update from file**, edit, delete, import history |
| Library | ⬆️ **Import** | import a BibTeX, RIS or CSL-JSON file into a new or an existing library; *Check only* first if you like |
| Library | 🔍 **Find & add** | search Europe PMC, PubMed or your own library and queue a result ([details](#find-papers-in-europe-pmc-and-pubmed)); add any paper by DOI, `pmid:` or PMC id |
| | ⚙️ **Settings** | topics, excluded terms, every recommendation and queue setting; data & backups |
| | 📖 **Documentation** | this documentation |
| (chip) | 🎮 **Profile** | locked rules and fingerprint; save, download, compare profiles; switch or create one |
| | 📄 **Paper** | abstract, TL;DR, metrics, notes, status, every recommendation of it with its evidence tree, XP earned |

Old addresses (`/ledger`, `/how`, `/paperpile`) redirect.

- A new profile's dashboard shows a five-step **Getting started** checklist:
  import a library, set topics, get recommendations, queue a paper, log a
  reading.
- Weekly reports on the dashboard are paged, 4 weeks per page (newer/older
  links), back to the first week with activity; the old `/weekly` address
  leads there.
- Earlier recommendation sets are grouped by month.
- Library search results say which ones are shown ("Showing 51–100 · page 2
  of 12"). Deleting a library asks once (type its name); its papers stay in
  your other libraries, queue and reading history.
- Compare lists your saved profile files with dates (newest first); you can
  still upload a file.
- Unknown pages and invalid values show an error page with links back; form
  errors name the field and its limit.
- On phones the menu is a sideways-scrolling bar at the top; switching
  profile and logging out are on the Profile page.
- Keyboard shortcuts outside text fields: `?` opens the shortcut help (also
  the "⌨️ Shortcuts" button in the sidebar), `n` next paper in the queue,
  `r` recommendations, `p` progress; `Esc` closes dialogs.

Rewards show as an XP toast listing every bonus or penalty,
confetti, and a pop-up for level-ups and badges; penalties as a red toast.
Your system's *reduce motion* setting turns all animation off.

### Libraries: import and export

A profile can have several **libraries** (e.g. one per reference manager,
project or lab). A paper exists once, however many libraries contain it; its
status, notes and points are shared.

| Action | How | What happens |
|---|---|---|
| **Import** into a new library | Library → Import, `library import FILE --new NAME` | the file becomes the library; *Update from file* / `ingest` read it again later |
| Import into an existing library | `--to NAME` (add) or `--to NAME --replace` | **add** its papers, or **replace** the library (papers missing in the file leave this library, nothing else changes) |
| **Export** | library page, `library export NAME [FILE] --format bibtex\|ris [--status]` | BibTeX (Paperpile, Zotero, Mendeley, JabRef) or RIS (EndNote, Citavi); *with reading status* adds keywords such as `litresearch-read`, `litresearch-rating-4` (labels in your reference manager) |
| 🎯 **Used for recommendations** | Libraries page (checkboxes, *Save*), `library use NAME… [--off]` | which libraries form "your library": the similarity score and the pool of the positive-example rules `recent`, `impact`, `fwci`, `cited` (not `liked`, `read` or papers you add). With none checked, the similarity is 0 for every candidate; the Libraries page and the recommendation warnings say so |

Every file is checked first; an invalid file changes nothing
([what an export must contain](#what-an-export-must-contain)). Two libraries
are kept by litresearch: **Recommended** (a recommendation joins it when you
queue it, and stays after reading; remove with ✖) and **Reading queue**
(always exactly the current queue; exportable). Databases from before
libraries existed are migrated to a library called **My library**.

**Positive examples** and **Negative examples** are the papers sent to
Semantic Scholar's recommendations ([how they are chosen](#1-candidates)).
litresearch regenerates them before every list and whenever you open them;
each paper shows its origin (*added by you* or *rule: …*, also exported as a
label). On their pages you can **remove** a paper (the rules then skip it;
*Removed by you* → **Restore**), **add a paper** (DOI, `pmid:N`, `PMCn`,
citation key or title words), choose the **rules**, their order and the
**number of examples** (*Save rules*, saved in the profile's
settings), and **Undo my changes**. Every paper page has *Use as positive /
negative example* and *Not an example*. These two lists never count as your
library, cannot be deleted or imported into, and are not saved in profile
files (your choices are, and the lists are rebuilt from them).

### Settings

Settings belong to the profile and are saved to its `settings.yaml`
(`config.yaml` is not changed). Comments and order are kept, the previous
version is kept as `settings.yaml.bak`, and invalid values (e.g. more than 5
papers, all weights 0, infinite or NaN numbers) are rejected with a message,
leaving the file unchanged. Paths, API keys, embeddings, LLM and web settings
are only editable in the file.


---

# Part II: How it works


## 5. How papers are recommended

Every rule that decides which papers you see. Each number is a setting
(`selection.…`) of your profile, editable under **Settings**. The
Recommendations page shows the same rules with your current values (**How
recommendations are made**), and each recommendation has an **evidence
tree** with the actual numbers. Code: `src/litresearch/recommend/`
(`engine.py`: candidates and filters; `scoring.py`: scores and selection);
keep this chapter and `web/templates/_how_recommend.html` in sync with it.

```
topics ─► OpenAlex searches (fresh, landmark) ─┐
examples ─► Semantic Scholar recommendations ──┴─► candidates
   ─► filters ─► fresh-or-landmark ─► score ─► score floor ─► diverse picks ─► ranked list
```

Searches are cached for a day, so a second list on the same day costs no
extra API budget. Every list stores a **funnel** (how many candidates each
step removed) and a **snapshot of the settings** used.

### 1. Candidates

**OpenAlex topic searches** (`sources.openalex`): for every query of every
topic, two searches in titles and abstracts, sorted by relevance,
`selection.results_per_query` (25) results each:

| Search | Filter |
|---|---|
| fresh | published within `fresh_window_months` (6); article, review, preprint |
| landmark | published in the last `landmark_max_age_years` (15), citation percentile above `landmark_min_percentile` (95) |

(A full-text search sorted by citations was tested and rejected: it returned
off-topic hits such as a FASTQ tool for "circulating tumor DNA".)

**Semantic Scholar recommendations** (`sources.semanticscholar`): recent
papers similar to *positive* and unlike *negative* example papers. Both
lists are libraries (Library → *Positive examples*, *Negative examples*;
`litresearch examples`) and are built in this order, without duplicates,
papers you removed and papers without DOI, until `s2_positive_count` (100)
or `s2_negative_count` (50) papers are reached:

1. papers you added yourself, newest first (more than the count: the newest
   are used, with a warning);
2. the rules `s2_positive_rules` (`liked`, `recent`) or `s2_negative_rules`
   (`dismissed`), one after the other:

| Rule | List | Papers, best first |
|---|---|---|
| `liked` | positive | rated 4–5 stars or read thoroughly; most recently read first |
| `read` | positive | skimmed or deeper; most recently read first |
| `recent` | positive | libraries used for recommendations, by publication date |
| `impact` | positive | same libraries, by OpenAlex citation percentile (field and year normalised), then FWCI |
| `fwci` | positive | same libraries, by field-weighted citation impact |
| `cited` | positive | same libraries, by number of citations |
| `dismissed` | negative | marked **not relevant**; most recent first |
| `low_rated` | negative | rated 1–2 stars; most recent first |

A paper is never in both lists: adding it to one removes it from the other,
and the positive rules skip papers chosen as negative examples (a library
paper you marked not relevant is a negative example only). Without positive
examples, or when the free service is busy, this step is skipped with a
warning. The batch records the rules, counts and the number of examples
used; the evidence tree says e.g. "Semantic Scholar recommendation #7 (from
100 positive / 3 negative examples)".

### 2. Filters

| Reason (funnel name) | A candidate is removed if it … |
|---|---|
| `wrong_type` | is not an article, review or preprint (editorial, erratum, letter, …) |
| `meeting_abstract` | is a conference abstract from a journal supplement: titles like `0865 …`, `P-123: …`, `LB01. …` (not `CD8 T cells…`, `53BP1…`, `1000 Genomes…`) |
| `read_or_dismissed` | was ever marked skimmed, read or thorough (even if later re-marked "abstract"), or not relevant; an abstract glance alone does **not** count |
| `queued` | is on your reading queue |
| `recently_recommended` | was recommended within `cooldown_weeks` (8), including earlier lists of today |
| `excluded_term` | contains one of `excluded_terms` in title or abstract (case-insensitive) |
| `without_doi` | has no DOI (the pipeline is keyed on DOIs) |

Papers in your **libraries are not excluded**: having a reference does not
mean you have read it. Such a recommendation is marked "in your library, not
read yet".

### 3. Eligibility: current *or* important

Only two kinds of papers are scored; the rest is dropped
(`not_fresh_or_landmark`):

- **fresh**: published at most `fresh_window_months` × 30.5 days ago;
- **landmark**: older, with an OpenAlex citation percentile (normalised by
  field and year) ≥ `landmark_min_percentile` and an age ≤
  `landmark_max_age_years`. Papers without a date are dropped.

### 4. Score

```
score = w_r × relevance + w_i × impact + w_t × timeliness       weights = selection.weights (0.5 / 0.3 / 0.2)
```

All components are in [0, 1]; only the ratio of the weights matters.

**Relevance**

```
relevance = s × similarity_rank + (1 − s) × topic_bonus        s = selection.similarity_share (0.7)
```

- **similarity_rank**: cosine similarity of the candidate's SPECTER2
  embedding (from Semantic Scholar) to your library papers, averaged over the
  `selection.knn` (5) most similar ones, then ranked among all candidates
  (0 = least, 1 = most similar). Candidates without an embedding use TF-IDF
  similarity of title + abstract; the ranking makes both comparable. A
  library paper never counts as its own neighbour.
- **topic_bonus**: the weight of the topic that found the paper ÷ the highest
  topic weight (the best topic if several); `s2_only_topic_bonus` (0.5) for
  papers found only by Semantic Scholar; otherwise 0.

**Impact**

- *Landmark*: citation percentile ÷ 100; without a percentile, citations per
  year on a log scale, `citations_per_year_saturation` (200) per year = 1.0.
- *Fresh* (no meaningful citations yet), the journal counts:

  ```
  impact = min(1, venue + early)
  venue  = log(1 + c) / log(1 + C)    c = journal 2-year mean citedness (OpenAlex), C = top_venue_citedness (30)
         = unknown_venue_impact (0.05) if the journal is unknown
  early  = early_citation_bonus (0.2) × log(1 + citations) / log(1 + early_citation_saturation (50))
  ```

  The 2-year mean citedness is an open counterpart of the impact factor. It
  can be distorted (*Cancer Research* scores 0.66 because of its many
  meeting-abstract supplements), which is why landmarks use paper-level
  percentiles. A missing journal is a warning sign, since preprint servers
  count as journals in OpenAlex.

**Timeliness**: fresh `0.5 + 0.5 × (1 − age / fresh window)` (1.0 on
publication day, 0.5 at the end of the window); landmark
`landmark_timeliness` (0.4).

### 5. Selection

1. **Score floor**: only candidates with at least `min_relative_score` (60 %)
   of the best score stay in the running.
2. **Diversity (maximal marginal relevance)**: papers are picked one at a
   time, each maximising

   ```
   λ × score − (1 − λ) × similarity to the most similar paper already picked      λ = selection.mmr_lambda (0.7)
   ```

   λ = 1 picks strictly the top scores; lower values spread the list over
   subjects. Similarity uses SPECTER2 where both papers have it, else TF-IDF.
3. Up to `n_recommendations` (5) papers are shown, ranked by score.

Recommendations never prescribe a reading depth: you mark the paper after
reading and earn the points your profile defines ([Gamification](#6-gamification)).
(Old batches still carry detail/skim labels in the data; the UI ignores them.)

### The evidence tree

Shown under **🌳 Evidence tree**:

| Node | Content |
|---|---|
| Score | the formula with actual values and contributions |
| Found by | each search (topic, query, position) or the S2 recommendation rank |
| Eligible as | fresh (age vs. window) or landmark (percentile and age vs. thresholds) |
| Relevance | method (SPECTER2/TF-IDF), mean similarity, rank among N candidates, nearest library papers, topic bonus and reason |
| Impact | basis (journal / percentile / citations per year) with inputs |
| Timeliness | age and window |
| Selection | score rank, best score and floor, MMR pick order, most similar earlier pick |
| Category | relevance + impact and its position among the picks |

### Feedback loop

- **Not relevant**: never shown again; a negative example for Semantic Scholar
  (rule `dismissed`).
- **Rating 4–5 or thorough read**: a positive example for Semantic Scholar
  (rule `liked`).
- **Your own examples**: add or remove papers in the example lists.
- Your queue, read papers and the cooldown keep the list fresh.


## 6. Gamification

The point system rewards **depth** and **discipline** (reading the paper you
planned to read first), not volume.

### Your profile and its locked rules

1. **Create** a profile (welcome screen, `litresearch profile create`) and
   choose every reward, the penalty, the level ladder and badge targets; the
   form is pre-filled from the `game` section of `config.yaml`.
2. The rules are copied into the profile's database and **locked**. Their
   **fingerprint** (a short hash of the rule set) is on the profile page. All
   points use these stored rules; editing the config cannot inflate them.
3. **Different rules** need another profile; the old one stays and you can
   switch back at any time.

### Rules you can set (defaults)

| Rule | Default | Meaning |
|---|---|---|
| `xp_abstract` / `xp_skimmed` / `xp_read` / `xp_thorough` | 5 / 15 / 40 / 80 | base points for the depth **you** choose; deeper may never pay less |
| `frog_bonus` | 0.5 | 🐸 share of the base points when the paper was **q1** (up next) and read ≥ skimmed |
| `streak_step` / `streak_max_steps` | 0.05 / 10 | 🔥 +5 % per consecutive reading week, at most +50 % ("next reads earn ×N") |
| `classic_percentile` / `classic_xp` | 99 / 20 | 🏛️ landmark (top 1 % cited) read at least at "read" depth |
| `hot_days` / `hot_xp` | 30 / 10 | 📰 read within 30 days of publication |
| `recommendation_xp` | 15 | 🎯 read (≥ skimmed) a paper from your recommendations |
| `rating_xp` | 2 | ⭐ first rating of a paper |
| `reflection_chars` / `reflection_xp` | 140 / 10 | ✍️ notes on a paper reach 140 characters |
| `inbox_zero_xp` | 10 | 📭 the queue becomes empty because you *read* its last paper (once per day) |
| `weekly_detail_goal` / `weekly_skim_goal` / `quest_xp` | 1 / 2 / 50 | 🗓️ weekly quest: 1 paper at least "read" + 2 more at least skimmed |
| `badge_xp` | 25 | 🏅 per unlocked badge |
| `drop_penalty` | 15 | ⚠️ a queued paper (from anywhere) leaves the queue, removed or marked not relevant, before you at least skimmed it |
| `levels` | 20 levels | the ladder below |
| `badge_targets` | see below | how much each badge needs |

Fairness rules that always apply:

- **Only a deeper level counts.** A reading counts only when the paper
  reaches a level it never had before. Upgrading pays the difference;
  logging a paper again at the same or a shallower level earns nothing and
  counts for no quest, streak, report card, badge or calendar, even if the
  paper was recommended again.
- Paper bonuses (landmark, fresh, recommendation, rating, notes) are paid
  once per paper; the reading bonuses only with a step to a deeper level. The
  frog bonus is a share of *newly* earned base points.
- Dismissing a recommendation you never queued is free.
- Penalties can lower the total and the level, but never below level 1.

### Streaks, weekly quest and metrics

- A **reading week** (Monday to Sunday) has at least one paper that reached
  skimmed or a deeper level for the first time. The **weekly quest** and
  the report card count each paper's new level in that week. The **streak** counts consecutive reading weeks and stays alive
  through the following week, so it does not reset on Monday morning.
- **Weekly grade:** S = quest complete including a thorough read; A = quest
  complete; B = at least half the quest; C = something read; - = rest week.
- **Reading Impact Factor**: average XP per week over the last four weeks.
- **Discipline**: share of papers read while at the top of the queue.
- **Depth**: share of papers read at least at "read" depth.
- **Queue nudge**: a warning when the top paper has waited 14 days or more.

### Levels (default ladder)

| Level | Title | XP | Level | Title | XP |
|---|---|---|---|---|---|
| 1 | 🎓 Curious Undergrad | 0 | 11 | 🔬 Postdoc | 2 400 |
| 2 | 📘 Bachelor Student | 60 | 12 | 🧬 Senior Postdoc | 3 000 |
| 3 | 🧪 Lab Rotation Student | 140 | 13 | 🧑‍🔬 Research Fellow | 3 700 |
| 4 | 📗 Master's Student | 250 | 14 | 🌱 Junior Group Leader | 4 500 |
| 5 | 🧫 Research Assistant | 400 | 15 | 👩‍🔬 Group Leader | 5 400 |
| 6 | 📚 PhD Candidate | 600 | 16 | 🏫 Associate Professor | 6 500 |
| 7 | ☕ Journal Club Regular | 850 | 17 | 🎩 Professor | 7 800 |
| 8 | 🪧 Poster Presenter | 1 150 | 18 | 🏛️ Department Head | 9 300 |
| 9 | 🎤 Conference Speaker | 1 500 | 19 | 🖋️ Review Editor | 11 000 |
| 10 | 📜 Doctor of Philosophy | 1 900 | 20 | 🏆 Hallmarks Author | 13 000 |

A typical week (quest done, one thorough read from the top of the queue, a
few bonuses) is worth about 200–300 XP.

### Badges (default targets)

"Read" means skimmed or deeper, reached for the first time (re-logging a
paper counts once).

| Group | Badge | Requirement | Code |
|---|---|---|---|
| Reading | 🌱 First Steps | Read your first paper (skimmed or deeper) | `first_read` |
| Reading | 📄 Page Turner | Read 10 papers | `page_turner` |
| Reading | 🐛 Bookworm | Read 25 papers | `bookworm` |
| Reading | 📚 Bibliophile | Read 50 papers | `bibliophile` |
| Reading | 💯 Centurion | Read 100 papers | `centurion` |
| Reading | 🏛️ Living Library | Read 250 papers | `living_library` |
| Depth | 🤿 Deep Diver | Read 5 papers thoroughly | `deep_diver` |
| Depth | 🐙 Abyss Explorer | Read 25 papers thoroughly | `abyss_explorer` |
| Depth | ✍️ Scribe | Write substantial notes on 10 papers | `scribe` |
| Depth | 📝 Novelist | Write 20000 characters of notes in total | `novelist` |
| Depth | ⭐ Critic | Rate 20 papers | `critic` |
| Depth | 🌟 Fan Club | Give 10 five-star ratings | `fan_club` |
| Discipline | 🐸 Frog Eater | Read your top paper first, 10 times | `frog_eater` |
| Discipline | 🤴 Frog Prince | Read your top paper first, 50 times | `frog_prince` |
| Discipline | 📭 Inbox Zero | Empty your reading queue by reading it | `inbox_zero` |
| Discipline | 🧘 Minimalist | Empty your reading queue 5 times | `minimalist` |
| Discipline | 🗂️ Curator | Mark 10 papers as not relevant (teaches the recommender) | `curator` |
| Consistency | 🔥 Month of Momentum | Read something 4 weeks in a row | `streak_4` |
| Consistency | 🏃 Quarter Marathon | Read something 12 weeks in a row | `streak_12` |
| Consistency | 🦸 Half-Year Hero | Read something 26 weeks in a row | `streak_26` |
| Consistency | 🗓️ Year of Reading | Read something 52 weeks in a row | `streak_52` |
| Consistency | 🧭 Questing | Complete your first weekly quest | `questing` |
| Consistency | 🗺️ Quest Master | Complete 10 weekly quests | `quest_master` |
| Consistency | 🐉 Quest Legend | Complete 25 weekly quests | `quest_legend` |
| Consistency | 🅰️ Straight A | Get grade A or S in 5 weeks | `straight_a` |
| Consistency | 💎 Perfectionist | Get grade S in 3 weeks | `perfectionist` |
| Consistency | 🔄 Comeback Kid | Read again after a break of 4 weeks or more | `comeback` |
| Pace | 🏅 Marathon Day | Read 4 papers in one day | `marathon` |
| Pace | 🍿 Binge Week | Read 8 papers in one week | `binge_week` |
| Pace | 🦉 Night Owl | Read 5 different papers between 22:00 and 04:00 | `night_owl` |
| Pace | 🌅 Early Riser | Read 5 different papers between 05:00 and 08:00 | `early_riser` |
| Pace | 🏖️ Weekend Warrior | Read 5 different papers on weekends | `weekend_warrior` |
| Breadth | 🏺 Classicist | Read 5 landmark papers (top 1 % cited) | `classicist` |
| Breadth | 📜 Canon Keeper | Read 20 landmark papers | `canon_keeper` |
| Breadth | 🐦 Early Bird | Read 5 papers within 30 days of publication | `early_bird` |
| Breadth | 📰 Scoop | Read 3 papers within 7 days of publication | `scoop` |
| Breadth | 🦴 Archaeologist | Read a paper that is 25+ years old | `archaeologist` |
| Breadth | 🧩 Polymath | Read papers from 8 different research topics | `polymath` |
| Breadth | 🎨 Renaissance Mind | Read papers from 20 different topics | `renaissance` |
| Breadth | 🧗 Trailblazer | Read 10 papers from your recommendations | `trailblazer` |
| Breadth | 📁 Library Diver | Read 10 papers from your libraries | `library_diver` |
| Breadth | 🪁 Free Spirit | Read 5 papers you added yourself | `free_spirit` |
| Experience | 💰 Thousand Club | Earn 1000 XP | `thousand_club` |
| Experience | 🖐️ High Five | Earn 5000 XP | `high_five` |

### Saving, loading and comparing profiles

- **Save** (Profile page, `litresearch profile save`): a file with the
  profile and its locked rules, every status, rating and note, history,
  queue, XP, badges, recommendation batches with evidence, and the libraries
  with their papers. Automatic backups contain the same.
- **Load** (welcome screen, `profile load FILE`): becomes a *new* profile;
  nothing is replaced.
- **Compare** (Profile → Compare, `profile compare FILE ...`): level, XP,
  papers read, thorough reads, longest streak, quests, badges, discipline,
  depth, Reading Impact Factor, notes and active weeks side by side, the best
  value highlighted; says whether profiles played by the **same rules**
  (fingerprint) and whether a file was **edited** outside litresearch.
- Every file has a **checksum**; a file whose content no longer matches is
  refused. This catches hand edits; it is not a cryptographic proof.
- Merging progress from another machine (`import-progress`) only works
  between profiles with the same rules.


---

# Part III: Reference


## 7. Commands

| Command | Purpose |
|---|---|
| `profile list\|create\|adopt\|use\|show\|save\|load\|compare` | profiles: list, create (rules locked), take over old data, choose the current one, show rules, save, load as a new profile, compare |
| `library list\|import\|export\|edit\|use\|delete` | `import FILE --new NAME` or `--to NAME [--replace]` (`--check` only checks); `export NAME\|queue [FILE] [--format bibtex\|ris] [--status]`; `edit NAME [--name] [--description] [--recommendations/--no-recommendations]`; `use NAME… [--off]` (libraries used for recommendations) |
| `examples [positive\|negative]` | example papers for Semantic Scholar with their origin; `add\|remove\|restore positive\|negative REF`; `reset positive\|negative`; `rules positive\|negative [RULE…] [--none] [--count N]` (saved in the profile's settings) |
| `ingest [--no-s2]` | update every library from its file; resolve DOIs; fetch metrics and embeddings |
| `check-bib [FILE]` | check an export (BibTeX, RIS, CSL-JSON) without importing |
| `status` | libraries: coverage, top folders, papers per library |
| `recommend [--new] [--no-s2]` | latest ≤ 5 papers; `--new` gets new ones |
| `search [KEYWORDS] [--title] [--author] [--journal] [--from] [--to] [--oa] [--source all\|europepmc\|pubmed\|library] [--sort relevance\|newest\|cited] [--limit N] [--add N]` | find papers in Europe PMC and PubMed and queue them |
| `queue [add\|drop\|move]` | the reading queue |
| `next`, `show REF` | top paper / any paper with abstract, status, notes |
| `read REF LEVEL [--rating] [--note]` | log a reading and earn XP |
| `note REF TEXT`, `dismiss REF` | add a note / mark as not relevant |
| `score`, `history` | level, streak, grade, badges / reading log |
| `serve [--network local\|all\|eth0\|wlan0\|IP] [--no-choose] [--port] [--list-networks]` | the web UI |
| `set-password [--remove] [--password-file FILE]` | password for network access |
| `backup`, `restore FILE` | snapshot + progress export / restore a snapshot |
| `export-progress FILE`, `import-progress FILE [--dry-run]` | move or merge progress between machines |
| `check` | database integrity, contents, last backup |
| `where` | the config file, data folder, profiles, backups and cache in use, and why |
| `db-init`, `version` | create the database / show the version |

Global options: `-p/--profile NAME`, `-v/--verbose` (HTTP requests and
retries), `-c/--config PATH`.


## 8. Data sources and privacy

All services are free, used within their free limits, and receive no
personal data:

| Service | Used for | Limits without a key |
|---|---|---|
| [Crossref](https://www.crossref.org/documentation/retrieve-metadata/rest-api/) | DOI by title (during import) | free; polite pool with `mailto` |
| [OpenAlex](https://docs.openalex.org/) | metrics, identifiers, venues, topic searches | ≈ 1000 credits/day; a batch look-up of 50 papers costs 1, a search 10 |
| [Semantic Scholar](https://www.semanticscholar.org/product/api) | SPECTER2 embeddings, TL;DRs, recommendations | one rate limit shared by all anonymous users (HTTP 429 is common); a free key gives 1 request/s |
| [Europe PMC](https://europepmc.org/RestfulWebService) | paper search, look-up by DOI/PMID/PMC id | free; litresearch waits 0.1 s between requests |
| [PubMed](https://www.ncbi.nlm.nih.gov/books/NBK25501/) (NCBI E-utilities) | paper search, look-up by PMID | 3 requests/s (10 with `api.ncbi_key`) |

**What leaves your machine:** titles/authors of library papers without DOI
(Crossref); DOIs/PMIDs (OpenAlex, Semantic Scholar, Europe PMC, PubMed); your
topic queries (OpenAlex); what you type into the paper search (Europe PMC,
PubMed); the DOIs of the positive and negative example papers (Semantic
Scholar recommendations; switch off with `sources.semanticscholar`). PubMed
requests carry `tool=litresearch` but no e-mail; `api.contact_email` stays
`n/a` unless you want the polite pools.

Answers are cached in `data/cache/http_cache.sqlite` (Crossref 1 year,
OpenAlex look-ups 14 days, searches and S2 recommendations 1 day, Semantic
Scholar papers 30 days); answers older than a year are deleted
automatically. When a service refuses, the affected step is skipped with a
warning.

Paperpile has no public API, so its BibTeX auto-export is the interface.
Planned: PubMed as a recommendation source and PubMed Central full texts
(roadmap 1.1).


## 9. Configuration

- **`config.yaml`**: machine settings (`api`,
  `embeddings`, `backup`, `web`, `llm`, `schedule`), the defaults for new
  profiles (`game`) and the defaults every profile starts from. Start from
  [`config/config.example.yaml`](../config/config.example.yaml).
- **`data/profiles/<profile>/settings.yaml`**: what a profile changes:
  `topics`, `excluded_terms`, `selection`, `sources`, `queue`, `library`,
  `paths.bibtex`. The web UI writes it; you can edit it by hand. A new profile
  starts without topics; a profile taken over from old data starts with the
  topics of `config.yaml`.

**Which `config.yaml`** (the first that applies; `litresearch where` shows
the choice):

1. `-c/--config PATH`;
2. the environment variable `LITRESEARCH_CONFIG` (a missing file is created on the first run, with the data in the same folder; the Docker image sets `/data/config.yaml`);
3. `config/config.yaml` in the current folder (a git installation);
4. your per-user config file ([Setup](#1-install)). If it does not exist
   yet, the first command creates it from the example, with the data in your
   per-user data folder, and prints where.

**Relative paths** (`paths.*`, `backup.dir`, a profile's `paths.bibtex`) are
resolved against the *data folder*, not the folder you run the command in:
`paths.base_dir` if set (relative to the config file's folder); otherwise
the folder that contains `config/` when the file is `.../config/config.yaml`
(a git installation keeps `data/` next to `config/`, as before); otherwise
the config file's folder. `~` stands for your home folder.

Use `n/a` for "not set". Unknown keys, and infinite or NaN numbers, are
rejected. Sections marked *(planned)* are validated but not used yet.

### `paths`

| Key | Default | Meaning |
|---|---|---|
| `base_dir` | `n/a` | folder relative paths start from (see above); a new per-user config sets it to your per-user data folder |
| `profiles_dir` | `data/profiles` | one folder per profile |
| `bibtex` | `data/paperpile/library.bib` | an export file that `ingest` imports into **My library** when no library has a file yet (the set-up before 0.2); a profile uses `library.bib` in its folder unless its `settings.yaml` sets it |
| `database` | `data/litresearch.sqlite` | database *before profiles*; a profile uses `litresearch.sqlite` in its folder |
| `cache_dir` | `data/cache` | HTTP response cache |
| `obsidian_vault` | `n/a` | Obsidian export target *(planned)* |

### `topics`, `excluded_terms`, `library`

```yaml
topics:
  - name: liquid-biopsy        # short id
    weight: 0.8                # relative importance, >= 0
    queries: ["circulating tumor DNA", "cfDNA fragmentomics"]
    mesh_terms: ["Circulating Tumor DNA"]       # (planned, kept by the UI)
    seed_dois: ["10.1038/s41586-019-1272-6"]   # key papers (planned, kept by the UI)
excluded_terms: ["mouse model"]  # never recommend papers with these in title/abstract
library:
  ignore_labels: ["Import Mendeley"]   # labels without topical meaning, ignored on import
```

Each query runs two OpenAlex searches per list (fresh and landmark); papers
found by a topic get a bonus proportional to its `weight`.

### `sources`

`true`/`false` per source: `semanticscholar`, `openalex`; `europepmc` and
`pubmed` for the paper search (Europe PMC is used if both are off);
`biorxiv` *(planned; OpenAlex and Europe PMC already index bioRxiv)*.
`--no-s2` skips Semantic Scholar for one `ingest` or `recommend` run.

### `api`

| Key | Meaning |
|---|---|
| `semanticscholar_key` | optional free key: own rate limit (1 request/s) instead of the shared one |
| `openalex_key` | optional; without one ≈ 1000 credits/day. An ingest uses ≈ 6, a recommendation list ≈ 20 per topic query (≈ 110 with the example config) |
| `contact_email` | optional; `mailto` for the Crossref/OpenAlex polite pools. `n/a` sends no personal data |
| `ncbi_key` | optional free NCBI key ([how to get one](https://support.nlm.nih.gov/kbArticle/?pn=KA-05317)): 10 instead of 3 PubMed requests/s |

### `embeddings`

| Key | Default | Meaning |
|---|---|---|
| `backend` | `hybrid` | `s2`: SPECTER2 from Semantic Scholar; `local`: own SPECTER2 service; `hybrid`: S2 first, local for the rest |
| `local_url` | `n/a` | OpenAI-style `/v1/embeddings` URL, required for `local` *(local service planned)* |

### `selection`

Formulas: [How papers are recommended](#5-how-papers-are-recommended).

| Key | Default | Meaning |
|---|---|---|
| `n_recommendations` | 5 | papers per list (1–5); old `n_detail`/`n_skim` are converted |
| `fresh_window_months` | 6 | how recent a paper must be to count as "current" |
| `landmark_min_percentile` | 95 | field/year-normalised citation percentile for "important" papers |
| `landmark_max_age_years` | 15 | landmark papers must be younger than this |
| `mmr_lambda` | 0.7 | relevance vs. diversity (1 = only relevance) |
| `weights` | 0.5 / 0.3 / 0.2 | relevance / impact / timeliness |
| `results_per_query` | 25 | OpenAlex results per topic query and kind (1–100) |
| `cooldown_weeks` | 8 | an unpicked recommendation may come back after this |
| `reminder_days` | 7 | remind me to get new recommendations (0 = never) |
| `similarity_share` | 0.7 | share of library similarity vs. topic bonus in relevance |
| `knn` | 5 | library papers averaged for the similarity |
| `s2_only_topic_bonus` | 0.5 | topic bonus of papers found only by S2 |
| `top_venue_citedness` | 30 | journal citedness that counts as impact 1.0 (fresh papers) |
| `unknown_venue_impact` | 0.05 | impact of fresh papers without a known journal |
| `early_citation_bonus` / `early_citation_saturation` | 0.2 / 50 | extra impact for already-cited fresh papers |
| `citations_per_year_saturation` | 200 | citations/year for impact 1.0 without a percentile |
| `landmark_timeliness` | 0.4 | timeliness of landmark papers |
| `min_relative_score` | 0.6 | picks need this share of the best score |
| `s2_positive_rules` | `[liked, recent]` | rules for the positive examples of Semantic Scholar, in order: `liked`, `read`, `recent`, `impact`, `fwci`, `cited` ([rules](#1-candidates)) |
| `s2_positive_count` | 100 | number of positive examples (1–500) |
| `s2_negative_rules` | `[dismissed]` | rules for the negative examples: `dismissed`, `low_rated` |
| `s2_negative_count` | 50 | number of negative examples (0–500) |

### `queue`, `game`, `backup`

| Key | Default | Meaning |
|---|---|---|
| `queue.max_size` | 5 | maximum queue length (1–20); keep it small |
| `game.*` | see [Gamification](#rules-you-can-set-defaults) | defaults for the **next** profile; copied and locked when a profile is created (easier to set on the welcome screen) |
| `backup.dir` | `data/backups` | snapshots and progress exports, one subfolder per profile; a cloud-synced folder is ideal |
| `backup.keep` | 14 | snapshots (and exports) to keep |
| `backup.interval_hours` | 24 | `serve` backs up at start and then every N hours; 0 = off |

### `web`, `llm`, `schedule`

- `web.host`: where `serve` listens: `local` (no login), `all`, an interface
  name (`eth0`, `wlan0`) or an IPv4 address of this computer; all but
  `local` need a password. `127.0.0.1` and `0.0.0.0` still work. `web.port`
  (8080). `web.password_hash` is set by `set-password`; never edit it.
  See [Web UI](#using-it-from-your-laptop-local-network).
- `llm` *(planned, roadmap 1.1)*: any OpenAI-compatible endpoint (LM Studio,
  Ollama, an institutional server): `enabled`, `base_url`, `model`, `api_key`.
- `schedule.cron` *(planned)*: when to build a new list (cron syntax).


## 10. Your data and backups

### Where everything is stored

`data/` below is the data folder: in a git installation `data/` next to
`config/`; installed as a package (pip, pipx), your per-user data folder
(without the `data/` part, e.g. `~/.local/share/litresearch/profiles/`); in
the Docker image the volume `/data` (also holding `config.yaml`; find it on
the host with `docker volume inspect litresearch`).
`litresearch where` prints the actual folders. Each profile has its own
folder `data/profiles/<profile>/`; `data/profiles/.current` names the
profile the command line uses.

| What | Where (default) | Rebuildable? |
|---|---|---|
| **Your progress**: statuses, ratings, notes, history, queue, XP, badges, recommendations, example choices, locked rules | `data/profiles/<profile>/litresearch.sqlite` | **no**, back it up |
| Libraries, paper metadata, metrics, embeddings | same database | yes, from the export files |
| Uploaded export files | `data/profiles/<profile>/libraries/` | yes, export again |
| Topics and settings | `data/profiles/<profile>/settings.yaml` | no; in every backup |
| Machine settings | `config/config.yaml` or the per-user config file | no; keep a copy |
| Cached API answers (all profiles) | `data/cache/http_cache.sqlite` | yes, delete any time |
| Login cookie key | `data/web_secret` | yes; deleting it logs everyone out |
| Backups | `data/backups/<profile>/` (`backup.dir`) | they *are* the copy |
| Data from before profiles | `data/litresearch.sqlite` | kept as it was |

All paths are settings, so everything can live in one folder of your choice.

### Recommended setup

1. **One live database on one machine** (e.g. an always-on Raspberry Pi);
   laptop and phone use the web UI over the network, so there is never a
   second copy to get out of sync.
2. **Point `backup.dir` to a cloud-synced folder**, not the database:

   ```yaml
   backup:
     dir: /home/kai/Nextcloud/litresearch-backups
     keep: 14
     interval_hours: 24
   ```

   While `serve` runs it backs up every profile at start and every 24 h.
   Backups keep running even if a profile's `settings.yaml` or
   `config.yaml` is invalid (the problem is logged). On demand:
   `litresearch backup` or **Back up now** under Settings → Data & backups.
3. Now and then run `litresearch check`: location, integrity check,
   contents and age of the last backup.

**Do not sync the live database file**: a sync client may copy it mid-write
(a corrupt copy), and two machines using it create conflict copies that lose
progress. Snapshots are complete and never change. Use the JSON
export/import below to move progress.

### What a backup contains

Two files per backup, named with `YYYYMMDD_HHMMSS`:

| File | Content | Use |
|---|---|---|
| `litresearch_20261008_070000.sqlite` | complete database snapshot (SQLite online backup API, consistent while the server writes, integrity-checked) | `litresearch restore FILE` |
| `litresearch_progress_20261008_070000.json` | the whole **profile**: rules, settings, statuses, ratings, notes, history, queue, XP, badges, recommendations, libraries, example choices (merged: the newer choice wins), enough metadata to find each paper again, and a checksum | `profile load` (new profile) or `import-progress` (merge) |

The oldest files beyond `backup.keep` (default 14 of each) are deleted.
Profiles you save yourself (`litresearch_profile_<name>_<date>_<time>.json`)
are never deleted. Files of format version 1 (before game profiles) can still
be imported.

### Restoring

```bash
# stop `litresearch serve` first
litresearch restore /path/to/backups/litresearch_20261008_070000.sqlite
```

The current database is first saved as `…_before_restore.sqlite`, so a
restore can be undone; these copies are rotated by time like other snapshots
and count toward `backup.keep`. Files that are not litresearch databases, and
paths that do not exist, are refused without changing anything.

### Moving to another machine, or merging two

```bash
litresearch export-progress progress.json             # old machine (or take the latest backup JSON)
litresearch import-progress progress.json --dry-run   # new machine: preview
litresearch import-progress progress.json
```

How the merge works:

- papers are matched by DOI, then PMID, then citation key, then title; the
  title match ignores case and spacing, needs the same year when both have
  one, and never matches a paper with a different DOI/PMID. Unknown papers
  are created from the stored metadata;
- reading status and rating: the newer change wins; notes from both sides are
  kept;
- history, XP, badges and recommendation batches are added if not present;
  queue entries are appended while there is room;
- the profile is taken over if the target has none; otherwise both must have
  the same rules (fingerprint), or the merge is refused;
- importing the same file twice changes nothing; the target is backed up
  first; an invalid file changes nothing.

To start a profile from a saved file instead, use *Load a saved profile* on
the welcome screen or `profile load FILE [--name NAME]`.

### How progress is protected against crashes

Every action is one database transaction (all saved or nothing), written
durably even if the power drops right after (`PRAGMA synchronous=FULL`). The
CLI and the web UI can write at the same time. Settings and exports are
written to a temporary file and renamed, so a crash never leaves a
half-written file. A failed automatic backup is logged and never stops the
server.


---

# Part IV: How it is built


## 11. Architecture

### Overview

Layers from the bottom: **storage** (`db.py`, one SQLite file per profile);
**sources** (`sources/`, thin API clients on one shared HTTP client);
**domain logic** (`ingest/`, `recommend/`, `reading.py`, `game.py`,
`backup.py`, …: plain functions on a database session; scoring functions are
pure and easy to test); **interfaces** (CLI and `web/`, which call the same
domain functions, so their behaviour is identical). Planned stages are in
`ROADMAP.md`.

### Module guide

| Module | Responsibility |
|---|---|
| `config.py` | Pydantic models of `config.yaml`; `n/a` → None; validation; readable `ConfigError` |
| `db.py` | SQLModel tables, reading levels, embeddings as float32 bytes, additive migrations, indexes, durable SQLite settings |
| `ingest/bibtex.py`, `ingest/formats.py` | BibTeX (LaTeX decoding, Paperpile's month/day format, DOI normalisation, folders/labels), RIS and CSL-JSON parsers, format detection |
| `ingest/check.py` | checks of an export (required fields, coverage, duplicates) |
| `ingest/pipeline.py` | import into a library (new, add, replace), matching, DOI resolution, OpenAlex and S2 enrichment, per-source error isolation, import history |
| `libraries.py`, `export.py`, `cli_library.py` | libraries and memberships, Recommended and queue libraries, uploads, migration; BibTeX/RIS export; `library` and `examples` commands |
| `examples.py` | the positive and negative example lists for Semantic Scholar: rules, your choices, regeneration |
| `sources/http.py` | JSON client: SQLite cache with max age and clean-up, per-host throttling, retries (`Retry-After`, backoff) |
| `sources/crossref.py`, `openalex.py`, `semanticscholar.py`, `europepmc.py`, `pubmed.py`, `hits.py` | API clients; a common search `Hit` with merging |
| `papers.py`, `search.py` | storing works and search hits without overwriting library metadata; one search query translated for Europe PMC, PubMed and the library |
| `recommend/engine.py`, `scoring.py`, `text.py` | candidates with provenance, filters and funnel, batches with settings snapshot; eligibility, relevance, impact, timeliness, MMR, evidence tree; TF-IDF fallback |
| `reading.py` | paper references (`r1`, `q2`, DOI …), queue, reading log, notes, drop penalty |
| `rules.py`, `profile.py`, `game.py` | `GameRules`, badge catalog, fingerprint; locking; points, anti-farming, streaks, quests, levels, badges, summaries, ledger, calendar |
| `profiles.py` | profile folders, settings overlay, current profile, taking over old data, loading saved files |
| `backup.py` | atomic writes, snapshots, rotation, restore, profile files with checksum, merge, comparison in an in-memory database, automatic backup thread |
| `cli.py`, `cli_reading.py`, `cli_data.py` | Typer commands; expected errors become messages |
| `web/app.py`, `auth.py`, `settings.py`, `docs.py` | FastAPI routes and middleware; scrypt/HMAC login; settings schema and forms; this documentation as chapter pages |
| `web/templates/`, `web/static/` | Jinja2 pages, CSS (themes, animations), JavaScript (toasts, confetti, rings) |

### Data model

| Table | Content |
|---|---|
| `paper` | library papers, candidates and added papers: identifiers (DOI, PMID, PMC id, citation key, OpenAlex, S2), metadata, metrics, folders/tags |
| `library` / `librarypaper` | libraries (name, description, kind, format, file, used for recommendations) and their papers with each library's citation key, folders, labels |
| `embedding` | `(paper, model)` → float32 vector (SPECTER2, 768 dimensions) |
| `recommendationbatch` / `recommendation` | lists with funnel and settings snapshot; per paper rank, category, score, evidence, reason |
| `queueitem` | the queue (position 1 = up next) and where each item came from |
| `reading` | current status, rating and notes per paper |
| `statusevent` / `xpevent` | append-only logs of status changes (incl. whether the paper was on top) and of every point earned or lost |
| `achievement` / `profile` | unlocked badges / the game profile with a locked copy of the rules and fingerprint |
| `ingestrun` | every import: library, mode, format, time, source, file hash, counts, checks |
| `examplechoice` | papers you added to (`include`) or removed from (`exclude`) the positive or negative examples |

Design choices: progress is **event-sourced** (statistics, streaks, quests
and the ledger are computed from the logs, so the score is auditable); each
profile has **its own database**; **timestamps** are naive local ISO 8601;
**embeddings are tagged by model**, so vectors of different models are never
compared.

### Ingest pipeline

1. **Parse and check** the file. BibTeX quirks handled: Paperpile's
   `month = "5\xa0" # apr` (day, non-breaking space, month macro),
   line-wrapped values, LaTeX escapes and braces (bare `%` and `&` stay text),
   `keywords` mixing folder paths (with `/`) and labels. Duplicate citation
   keys only warn; any other error aborts *before* the database is touched,
   so a truncated download never makes papers look removed.
2. **Sync**: papers are matched by citation key, DOI, PMID, then title +
   year (a key whose entry now has another DOI/PMID is a new paper; keys
   generated for RIS/CSL entries without id are not used); an existing paper
   with the same DOI (e.g. an earlier recommendation) is adopted. Papers missing from a replaced library leave
   it but are not deleted, so history and notes survive.
3. **Resolve DOIs** via Crossref for entries without DOI or PMID. The top hit
   is not trusted blindly (it was once a "Faculty Opinions" record): it must
   be a real paper type, match the year ±1 and the first author's surname,
   and have a title similarity ≥ 0.9 (`difflib`) or be a ≥ 4-word title
   prefix confirmed by the author (titles Paperpile truncated).
4. **OpenAlex enrichment**, 50 IDs per request (1 credit instead of 10 for a
   search; about 6 credits for 145 papers): citations, FWCI, normalised
   citation percentile, topics, PMID, missing abstracts, venue 2-year mean
   citedness. **Semantic Scholar**, 100 IDs per request: SPECTER2, TLDR,
   influential citations.

Each source fails on its own: an outage becomes a warning and the other steps
still run. Known caveats: OpenAlex withholds abstracts for some publishers
(export abstracts are preferred anyway); venue citedness is distorted by
supplements, so scoring relies mainly on paper-level metrics.

### Recommendation pipeline

Described rule by rule in [How papers are recommended](#5-how-papers-are-recommended).
Each list is stored as a `RecommendationBatch` (dated with the Monday of its
week) with the funnel and settings snapshot, and `Recommendation` rows with
score breakdown, evidence tree and a one-line reason. Design notes: kNN
similarity is converted to a rank so SPECTER2 and TF-IDF are comparable; the
score floor was added after MMR pulled a weak meeting abstract into a real
batch for diversity's sake.

### Game design

Rules in [Gamification](#6-gamification). Design notes: the frog bonus is a
nod to Brian Tracy's "eat that frog"; anti-farming invariants (base points
only up to the deepest level, paper bonuses once, frog bonus on new base
points) are checked by a randomised test over long sessions; all rules form
one `GameRules` object frozen per profile with a SHA-256-based fingerprint;
profile files carry a SHA-256 checksum, and comparisons load each file into a
throw-away in-memory SQLite database; streaks count weeks, not days, to
motivate without guilt.

### Profiles

`profiles.py` keeps one folder per profile under `paths.profiles_dir`. A
profile's effective configuration is `config.yaml` merged with its
`settings.yaml` (the keys in `OVERLAY_KEYS`), with database, bibtex path and
backup dir pointing into the profile. The web UI keeps the chosen profile in
a signed cookie; a middleware sends requests without a valid profile to the
welcome screen and passes the profile to the routes in a context variable.
The CLI uses `--profile`, else `data/profiles/.current`, else the only
profile; without profiles it falls back to `paths.database`, which
`adopt_legacy` copies into a new profile.

### Data safety and web security

- One transaction per action, `PRAGMA synchronous=FULL`, a 30 s busy
  timeout; snapshots via SQLite's online backup API, integrity-checked;
  atomic writes (temporary file, `fsync`, `os.replace`).
- Uploads are checked in a temporary file, limited to 50 MB (HTTP 413 for
  larger requests) and stored under safe names; an invalid export never
  replaces a library; the previous file is kept as `.bak`.
- Network access needs a password (salted scrypt, RFC 7914) and an
  HMAC-SHA256-signed (RFC 2104), HttpOnly, SameSite=Lax cookie whose
  signature covers the password hash. POST requests must be same-origin,
  redirects local, and templates auto-escape (tested with hostile input).
  Without a password, requests for other host names are refused (HTTP 421,
  DNS-rebinding protection).
- Server-rendered HTML (FastAPI, Jinja2): every action works without
  JavaScript. No build step and no external assets: emoji and inline SVG
  icons, hand-written CSS (light/dark) and JavaScript (toasts, confetti,
  modals, progress rings, shortcuts). Reward feedback is a short-lived token
  in the redirect URL, removed after display so a reload does not replay it.


## 12. Implementation and credits

### How it was developed

- **AI-assisted.** Code, tests and documentation were written with Claude
  Code (Anthropic; model Claude Opus 5.5) in a dialogue with the user, who
  set requirements and priorities and reviewed the results; commits carry a
  `Co-Authored-By` line.
- **Written for this project.** No third-party code was copied; libraries
  are used through their public APIs; *zotero-arxiv-daily* was a design
  reference only.
- **Research first.** Semantic Scholar feeds, Scholar Inbox, ResearchRabbit,
  Litmaps, zotero-arxiv-daily and others were compared; none combined a
  reference-manager library, own topics, impact weighting, a short reading
  list, reading-depth tracking and notes.
- **Driven by real data.** Each stage ran against a real Paperpile export
  (145 entries) and the live APIs, which led e.g. to DOI resolution, the
  Paperpile date parser, batched OpenAlex look-ups, rejecting full-text
  search and the meeting-abstract and unknown-venue rules.
- **Small, verified steps** on `feat-*` branches (CUBI conventions,
  [Development](#13-development)).

### Quality assurance

About 510 tests in 29 modules (offline by default with recorded real API
responses; live tests with `--network`), covering failure scenarios, hostile
input, data integrity, scale and years of use; about 97 % statement coverage
at v0.1.0-alpha.1, with scoring, game, reading, config and auth at 100 %.
Warnings are errors; ruff lints and formats; CI runs on Python 3.11–3.13.
Each milestone was also run on real data and checked with headless-browser
screenshots. Details: [Development](#13-development).

### Software dependencies

Versions used during development (Python 3.13.9). All are open source with
licenses that permit use in this project.

| Package | Version | License | Used for |
|---|---|---|---|
| [bibtexparser](https://github.com/sciunto-org/python-bibtexparser) | 2.1.0 | MIT | parsing BibTeX (v2 API, LaTeX decoding and name-splitting middlewares) |
| [httpx](https://www.python-httpx.org) | 0.28.1 | BSD-3-Clause | HTTP client for all APIs; `MockTransport` in tests |
| [numpy](https://numpy.org) | 2.5.3 | BSD-3-Clause | embeddings and vector maths (cosine, kNN, MMR, TF-IDF) |
| [platformdirs](https://github.com/tox-dev/platformdirs) | 4.14.5 | MIT | per-user config and data folders on Linux, macOS and Windows |
| [pydantic](https://github.com/pydantic/pydantic) | 2.13.5 | MIT | configuration models and validation |
| [PyYAML](https://pyyaml.org/) | 6.0.3 | MIT | reading `config.yaml` |
| [ruamel.yaml](https://sourceforge.net/projects/ruamel-yaml/) | 0.19.1 | MIT | writing YAML while keeping comments and order |
| [SQLModel](https://github.com/fastapi/sqlmodel) | 0.0.48 | MIT | table models on SQLAlchemy and Pydantic |
| [Typer](https://github.com/fastapi/typer) | 0.27.3 | MIT | command line interface |
| [Rich](https://github.com/Textualize/rich) | 15.0.0 | MIT | coloured terminal output |
| [FastAPI](https://github.com/fastapi/fastapi) | 0.142.4 | MIT | web routes, forms, test client |
| [Jinja2](https://jinja.palletsprojects.com/) | 3.1.6 | BSD-3-Clause | HTML templates (auto-escaping) |
| [Python-Markdown](https://python-markdown.github.io/) | 3.11 | BSD-3-Clause | renders this documentation in the web UI |
| [python-multipart](https://github.com/Kludex/python-multipart) | 0.0.32 | Apache-2.0 | form parsing for FastAPI |
| [Uvicorn](https://uvicorn.dev/) | 0.54.0 | BSD-3-Clause | ASGI web server |
| *indirect:* [SQLAlchemy](https://www.sqlalchemy.org) | 2.1.3 | MIT | database engine under SQLModel |
| *indirect:* [Starlette](https://github.com/Kludex/starlette) | 1.7.0 | BSD-3-Clause | web toolkit under FastAPI |
| *indirect:* pydantic-core | 2.46.5 | MIT | validation engine of Pydantic |
| *indirect:* [Click](https://click.palletsprojects.com/) | 8.5.0 | BSD-3-Clause | CLI toolkit under Typer |
| *indirect:* [pylatexenc](https://github.com/phfaist/pylatexenc) | 2.11 | MIT | LaTeX → Unicode for bibtexparser |
| *indirect:* anyio, h11 | 4.15.1, 0.16.0 | MIT | async I/O and HTTP/1.1 for Starlette, Uvicorn, httpx |
| *dev:* [pytest](https://docs.pytest.org/) | 9.1.1 | MIT | tests |
| *dev:* [pytest-cov](https://github.com/pytest-dev/pytest-cov) | 7.1.0 | MIT | coverage |
| *dev:* [Ruff](https://docs.astral.sh/ruff) | 0.16.10 | MIT | lint and format |

Python standard library, notable uses: `sqlite3` (database, online backup,
integrity check) · `hashlib.scrypt`, `hmac`, `secrets` (login) · `difflib`
(title similarity) · `tempfile`, `os.replace`, `os.fsync` (atomic writes) ·
`threading` (automatic backups, concurrency tests) · `json`, `csv` (exports)
· `socket` (finding the LAN address without sending packets).

### Data services

All free; no key required; no personal data sent ([details](#8-data-sources-and-privacy)).

| Service | Used for | Terms |
|---|---|---|
| [OpenAlex](https://openalex.org) (OurResearch) | metadata, citation counts, FWCI, percentiles, topics, venue statistics, topic searches | data CC0; metered free API |
| [Semantic Scholar Academic Graph API](https://www.semanticscholar.org/product/api) (Allen Institute for AI) | SPECTER2 embeddings, TLDRs, influential citations, recommendations | API license agreement; shared rate limit without a key |
| [Crossref REST API](https://www.crossref.org/documentation/retrieve-metadata/rest-api/) | DOI look-up by title, author and year | open metadata; polite use |
| [Europe PMC REST API](https://europepmc.org/RestfulWebService) (EMBL-EBI) | paper search, abstracts, PMC ids, citation counts | free; article texts under their own licences |
| [NCBI E-utilities](https://www.ncbi.nlm.nih.gov/books/NBK25501/) (PubMed) | paper search, summaries by PMID | free; 3 requests/s without key |
| [Paperpile](https://paperpile.com) and other reference managers | source of the libraries (exports; tested with Paperpile's BibTeX auto-export) | no API used |

### Literature

Data sources and models:

- Priem, J., Piwowar, H., & Orr, R. (2022). *OpenAlex: A fully-open index of
  scholarly works, authors, venues, institutions, and concepts.*
  arXiv:2205.01833.
- Kinney, R., et al. (2023). *The Semantic Scholar Open Data Platform.*
  arXiv:2301.10140.
- Cohan, A., Feldman, S., Beltagy, I., Downey, D., & Weld, D. S. (2020).
  *SPECTER: Document-level representation learning using citation-informed
  transformers.* Proceedings of ACL 2020.
- Singh, A., D'Arcy, M., Cohan, A., Downey, D., & Feldman, S. (2023).
  *SciRepEval: A multi-format benchmark for scientific document
  representations.* Proceedings of EMNLP 2023 (introduces SPECTER2).
  arXiv:2211.13308.
- Cachola, I., Lo, K., Cohan, A., & Weld, D. S. (2020). *TLDR: Extreme
  summarization of scientific documents.* Findings of EMNLP 2020.
- Europe PMC Consortium (2015). *Europe PMC: a full-text literature database
  for the life sciences and platform for innovation.* Nucleic Acids Research,
  43(D1), D1042–D1048.
- Sayers, E. (2010). *A General Introduction to the E-utilities.* In: Entrez
  Programming Utilities Help. National Center for Biotechnology Information.

Methods:

- Carbonell, J., & Goldstein, J. (1998). *The use of MMR, diversity-based
  reranking for reordering documents and producing summaries.* Proceedings of
  SIGIR '98.
- Spärck Jones, K. (1972). *A statistical interpretation of term specificity
  and its application in retrieval.* Journal of Documentation, 28(1).
- Percival, C. (2009). *Stronger key derivation via sequential memory-hard
  functions* (scrypt); Percival, C., & Josefsson, S. (2016). RFC 7914.
- Krawczyk, H., Bellare, M., & Canetti, R. (1997). *HMAC: Keyed-hashing for
  message authentication.* RFC 2104.
- Field-weighted citation impact (Snowball Metrics) and OpenAlex's
  citation-normalised percentile, as documented by OpenAlex.

### Related tools and inspiration

- [zotero-arxiv-daily](https://github.com/TideDra/zotero-arxiv-daily) (MIT):
  embedding similarity between new preprints and a reference-manager library.
  It inspired the general idea; no code was reused, and litresearch differs
  in input, sources, impact scoring, the reading workflow and the game.
- Scholar Inbox (Flicke et al., 2025, arXiv:2504.08385), Semantic Scholar
  Research Feeds, ResearchRabbit and Litmaps were reviewed as alternatives.
- "Eat that frog" (Brian Tracy) named the bonus for reading your planned top
  paper first.

### License and citation

MIT license with the Commons Clause ([LICENSE](../LICENSE)): you may use, change
and share litresearch, but not sell it or a paid product or service (including
hosting, consulting or support) whose value comes mainly from it. To cite litresearch use
[CITATION.cff](../CITATION.cff); please also cite OpenAlex and Semantic
Scholar/SPECTER2 (see Literature) when you report results based on it.


## 13. Development

### Development cycle

The full contributor guide is [CONTRIBUTING.md](../CONTRIBUTING.md). In
short (CUBI conventions):

```
issue ─► feat-*/issueNN-* branch from dev ─► pull request into dev (CI green)
      ─► rebase and merge ─► dev ─► fast-forward main ─► tag vX.Y.Z ─► GitHub release
```

- `main` holds releases only; `dev` is kept linear; no merge or squash
  commits.
- **CI** (`.github/workflows/ci.yml`) runs ruff and the offline suite on
  Python 3.11–3.13 for pushes and pull requests to `main` and `dev`.
- **Releases**: the version bump is its own commit (`pyproject.toml`,
  `__init__.py`, `CITATION.cff`). A tag `vX.Y.Z` runs
  `.github/workflows/release.yml` (tests, wheel and source archive, GitHub
  release with the CHANGELOG section; pre-release for `-alpha.N`/`-dev`).
- Issues use `.github/ISSUE_TEMPLATE/` (bug report, feature request, testing
  feedback); pull requests `.github/pull_request_template.md`. Hosting:
  GitHub; a GitLab@HHU mirror can be added as a second push target.

### Tests

```bash
.venv/bin/pytest                         # offline suite (default)
.venv/bin/pytest --network               # also the real APIs
.venv/bin/pytest --cov=litresearch --cov-report=term-missing
.venv/bin/ruff check src tests && .venv/bin/ruff format src tests
```

- Offline by default: `tests/conftest.py::FakeApis` serves recorded
  responses (`tests/data/api_responses.json`); `@pytest.mark.network` tests
  run only with `--network`.
- `tests/data/paperpile_sample.bib` is a synthetic export with every
  Paperpile quirk seen so far; add new quirks there.
- Warnings are errors. Each test runs in its own temporary directory
  (autouse fixture `isolated`), so it never touches real data or starts the
  real backup thread. Pass ids, not ORM objects, between threads.
- Web tests: `conftest.enter_profile(client)` creates a profile through the
  welcome screen; `conftest.profile_cfg(config_path)` gives its config.

| Test module | Covers |
|---|---|
| `test_bibtex.py`, `test_pipeline.py`, `test_libraries.py` | parsing quirks; ingest end to end; formats, import modes, exports, special libraries, migration, `library` CLI |
| `test_http.py`, `test_sources.py`, `test_search.py` | cache, retries, throttling; API clients and match rules; paper search |
| `test_scoring.py`, `test_recommend.py` | scoring maths, evidence; candidates, filters, cooldown, outages |
| `test_reading.py`, `test_game.py`, `test_papers.py` | queue, notes, ratings; XP, anti-farming, streaks, quests, badges; paper storage |
| `test_backup.py`, `test_profile.py`, `test_profiles.py` | snapshots, rotation, merge, restore; rules, locking, checksums, comparison; profile folders, welcome screen, separation |
| `test_cli.py`, `test_web.py`, `test_web_usecases.py`, `test_web_polish.py`, `test_auth.py` | interfaces, every page, settings round trip, user flows, login |
| `test_config.py`, `test_db.py` | validation, schema, migration |
| `test_robustness.py`, `test_data_integrity.py` | crashes, concurrency, broken and hostile inputs; matching and merge correctness |
| `test_scale.py`, `test_import_scale.py`, `test_longterm.py` | large libraries and years of use (`web_bigdata.py` builds the data) |
| `test_network.py`, `test_live.py` | network interfaces for `serve`; real APIs (`--network`) |

### Recording new API fixtures

Run a real `litresearch ingest`, then take the responses from
`data/cache/http_cache.sqlite` (table `cache`, JSON in column `body`). Trim
them before committing: shorten embeddings and drop affiliations.
