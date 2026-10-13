# litresearch documentation

litresearch is a personal literature radar built on a Paperpile library. On
request it proposes **up to 5 papers**, ranked by similarity to your library
(SPECTER2 embeddings), the topics you define, paper impact and timeliness
(very recent *or* a landmark). You choose which go on your short, ordered
**reading queue**, log how deeply you read them, and a **game layer** (XP,
streaks, quests, levels, badges) with rules locked per **profile** keeps you
going. Every criterion is transparent: each recommendation has an evidence
tree, and every rule is documented here.

This file is the complete documentation: using the tool (parts I and II),
reference (part III) and how it is built (part IV). The README has a short
overview. In the web UI it is under **📖 Documentation**, one page per
chapter (rendered from this file by `web/docs.py`: every `## ` heading is a
chapter, every `# Part` heading groups chapters).

## Contents

**Part I: Using litresearch**

1. [Setup](#1-setup)
2. [The weekly loop on the command line](#2-the-weekly-loop-on-the-command-line)
3. [Profiles](#3-profiles)
4. [Web UI](#4-web-ui)

**Part II: How it works**

5. [How papers are recommended](#5-how-papers-are-recommended)
6. [Gamification](#6-gamification)

**Part III: Reference**

7. [Commands](#7-commands)
8. [Data sources and privacy](#8-data-sources-and-privacy)
9. [Configuration](#9-configuration)
10. [Your data and backups](#10-your-data-and-backups)

**Part IV: How it is built**

11. [Architecture](#11-architecture)
12. [Implementation and credits](#12-implementation-and-credits)
13. [Development](#13-development)


---

# Part I: Using litresearch


## 1. Setup

### 1. Install

Requires Python ≥ 3.11 on Linux (tested on a Raspberry Pi 5, aarch64,
Python 3.13) and `git`.

```bash
git clone <repository-url> litresearch
cd litresearch
python3 install.py                # or: python3 install.py --service
.venv/bin/litresearch serve
```

`install.py` creates a virtual environment in `.venv`, installs litresearch
with its dependencies and creates `config/config.yaml` from the example (an
existing one is never overwritten). With `--service` it also installs a
systemd user service, so the web UI starts automatically; it then serves on
the network set as `web.host` without asking. `--dev` adds the test and lint
tools. Run it again after `git pull` to update.

To update: `git pull && python3 install.py`. Your data in `data/` and your
`config/config.yaml` are kept. Back up first (`litresearch backup`), since an
alpha may change the data format; the CHANGELOG says when.

Without the installer: `python3 -m venv .venv && .venv/bin/pip install -e .`,
then `cp config/config.example.yaml config/config.yaml`. Each GitHub release
also has a wheel (`pip install litresearch-*.whl`); then run litresearch in a
folder of your choice, where it keeps `config/` and `data/`.

### 2. Export the Paperpile library

Paperpile has no public API, so the tool reads a BibTeX file that Paperpile
keeps up to date automatically:

1. In Paperpile, click your profile picture (top right) → *Workflows and
   Integrations* and add a **BibTeX export** of the whole library (or of a
   folder/label) to **Google Drive**. A one-off manual export (Export →
   BibTeX) works as well.
2. In the export options, include **DOI**, **PMID**, **abstract**, **URL** and
   **keywords** (keywords carry your folders and labels). The option names can
   differ between Paperpile versions. Without DOIs every paper has to be
   matched by title, which is slower and less reliable.
3. Prefer a file name without spaces, e.g. `library.bib`.
4. Check what arrived: `litresearch check-bib FILE`, or upload the file on the
   **Paperpile** page of the web UI (with *Check only* first, if you like).
   Errors (e.g. an empty or broken file) block the import; warnings name the
   missing fields.

What the tool uses from the export:

| BibTeX field | Used for |
|---|---|
| `doi`, `pmid`, `url` | identifiers (a DOI inside `url` is picked up as well) |
| `title`, `author`, `year`, `month`, `journal`, `abstract` | metadata; BibTeX wins over API data |
| `keywords` | Paperpile **folders** (items containing `/`) and **labels** / author keywords (other items) |

The export does not contain the date a paper was added to Paperpile.

### 3. Sync the export to the machine running the tool

Use [rclone](https://rclone.org/drive/) to pull the file from Google Drive:

```bash
rclone config                     # once: create a remote called "gdrive"
rclone copyto "gdrive:<path in Drive>/library.bib" data/profiles/kai/library.bib
```

Run this before each ingest, e.g. from cron or a systemd timer. Watching
the export and re-importing automatically is planned (roadmap 0.7).

### 4. Configure

```bash
cp config/config.example.yaml config/config.yaml
```

The file holds the machine settings (APIs, web server, backups) and the
defaults for new profiles; see [Configuration](#9-configuration). Topics are
asked for per profile in the web UI. `config/config.yaml` and `data/` are
git-ignored, so private settings and API keys stay local.

Recommended (both free):

- **Semantic Scholar API key**, from
  <https://www.semanticscholar.org/product/api>. Without one, all anonymous
  users share a single rate limit and requests often fail with HTTP 429.
- **Contact e-mail** (`api.contact_email`), which puts requests into the
  Crossref and OpenAlex "polite pools".

### 5. Create your profile and import the library

Easiest in the web UI: `litresearch serve`, open the printed address and
create a profile; then upload the export on **Library → Paperpile library**.
On the command line:

```bash
.venv/bin/litresearch profile create "Kai"      # rules are locked from now on
cp library.bib data/profiles/kai/library.bib   # the profile's Paperpile export
.venv/bin/litresearch ingest      # add -v to see every HTTP request
.venv/bin/litresearch status
```

If you used litresearch before profiles existed, `litresearch profile adopt
NAME` (or the welcome screen) turns the existing data into a profile.

Example output for a 145-paper library:

```
BibTeX entries:        145
  added / updated:     145 / 0
  removed from library:  0
DOI from BibTeX:       140
New DOI via Crossref:  1
New DOI via PMID:      0
Not found anywhere:    2 Turajlic2017-zn, Rossi2021-qy
Enriched (OpenAlex):   143
Enriched (S2):         142
SPECTER2 embeddings:   142
```

The ingest is idempotent: run it as often as you like. API responses are
cached in `data/cache/`, so repeated runs make almost no network requests.

### Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `WARNING: Semantic Scholar: ... HTTP 429` | Shared anonymous rate limit. Add `api.semanticscholar_key`, or rerun later. Everything else still gets imported. |
| A paper is listed under "Not found anywhere" | It has no DOI/PMID and its title could not be matched. Typical causes: conference talks without a DOI, or a broken title in Paperpile (e.g. the abstract pasted into the title field). Fix the entry in Paperpile; the next ingest picks it up. |
| `Duplicate DOIs: key (doi)` | Two Paperpile entries share a DOI. The second one is kept without DOI; merge the duplicates in Paperpile. |
| `duplicate BibTeX key ...` | Two entries share a citation key; only the first is used. |
| `ValueError: ... BibTeX block(s) failed to parse` | The export is broken (e.g. truncated download). Nothing is changed in the database; re-sync the file. |


## 2. The weekly loop on the command line

The easiest way is the web UI: `litresearch serve` (see [Web UI](#4-web-ui)).
Everything below also works on the command line. Commands work on the
**current profile**: the one you chose last (in the web UI or with
`litresearch profile use NAME`), or the only one there is. `--profile NAME`
picks another for one command, e.g. `litresearch --profile Grace queue`.

```
recommend  ──pick──>  queue (max 5, ordered)  ──next──>  read ... ──>  score
   ^                       ^                                  │
   │                  manual adds                             │
   └──── dismissals and good ratings refine the next batch ───┘
```

The tool proposes papers, but **you** decide what goes on the queue. The
queue is short and ordered, so there is always exactly one paper to read
next and it never grows into a backlog you will not read.

### 1. Get recommendations

```bash
litresearch recommend
```

The first call builds a list, which takes about a minute. Later calls show
the latest list without any API requests and remind you when it is older
than `selection.reminder_days` (7). `--new` gets a new list whenever you
like, e.g. after you change your topics. A new list shows papers that were
not recommended in the last `cooldown_weeks`.

```
Recommendations from 2026-10-05 (313 candidates checked)

r1  A single-cell lens into the co-evolution of genotypes and phenotypes in cancer
     Franco Izzo et al. (2026-09-08) · Nature reviews. Cancer
     fresh · Nature reviews. Cancer · topic: tumor-evolution
     https://doi.org/10.1038/s41568-026-00970-8
r2  Identifying structural variants using linked-read sequencing data
     ...
     landmark · top 2.9% cited · 92 citations · Bioinformatics · topic: long-read-sv
r3  ...
```

Every paper is either **fresh** (published within `fresh_window_months`) or a
**landmark** (top `100 - landmark_min_percentile` % cited in its field and
year). The list is ranked by score; how deeply you read each paper is your
choice. Papers you marked skimmed, read or thorough, dismissed or queued
never show up; unread papers from your Paperpile library can. A paper that
was recommended but not picked can come back after `cooldown_weeks`.

To get a new list automatically every Monday morning, add a cron job
(`crontab -e`):

```
0 7 * * 1  cd /path/to/litresearch-tool && .venv/bin/litresearch ingest && .venv/bin/litresearch recommend --new > data/this-week.txt
```

### 2. Fill your reading queue

```bash
litresearch queue add r1              # a recommendation
litresearch queue add r3 --top        # ... straight to position 1
litresearch queue add Barkley2022-ri  # a paper from your Paperpile library
litresearch queue add 10.1038/s41586-024-07894-z   # any DOI (OpenAlex, Europe PMC)
litresearch queue add pmid:35931863   # any PubMed ID, or a PubMed Central id: PMC5812436
litresearch queue add --title "Preprint from the lab meeting" --url https://...
litresearch queue                     # show it
litresearch queue move q4 1           # reorder
litresearch queue drop q3             # remove (costs XP if you did not at least skim it)
```

When the queue is full (`queue.max_size`, default 5) nothing can be added
until you read or drop something. That limit is intentional. Removing a paper
before you at least skimmed it costs the penalty set in your game profile
(default 15 XP), wherever it came from: the queue is a commitment.

#### Finding papers in Europe PMC and PubMed

```bash
litresearch search "circulating tumor DNA" --journal "Nature Medicine" --from 2023
litresearch search --author "Swanton C; Turajlic S" --title evolution --sort cited
litresearch search "ctDNA" --oa --source pubmed --limit 20     # only free full texts, only PubMed
litresearch search "ctDNA" --author "Swanton C" --add 2        # put result 2 on the queue
litresearch search clonal -s library                          # only papers you already have
```

Keywords are searched in title and abstract; `--title`, `--author`,
`--journal`, `--from`/`--to` and `--oa` narrow the search; several authors or
journals are separated by `;`. By default both databases are searched
(those enabled in `sources`) and the results merged; each result shows its
DOI or `pmid:`, where it was found, whether it is free to read, and whether
it is already in your library, on your queue or read. The same search is on
the web page **Library → Find & add**.

#### Referring to papers

| Form | Meaning |
|---|---|
| `r2` | rank 2 in the latest recommendation list |
| `q1` | position 1 in the queue (positions renumber as papers leave) |
| `#123` | internal paper id (shown by `show`) |
| `10.1038/...`, `https://doi.org/...` | DOI |
| `pmid:35931863` | PubMed ID (fetched from Europe PMC or PubMed if not stored yet) |
| `PMC5812436` | PubMed Central id |
| `Barkley2022-ri` | Paperpile citation key |
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
`skimmed` or deeper, or dismissing a paper, removes it from the queue. Notes
are stored with an ISO 8601 timestamp.

### 4. Check your score

```bash
litresearch score
```

```
Level 2 · Lab Rotation Student   202 XP  [██████████░░░░░░░░░░] 98 XP to next level
Streak: 1 week(s) (best 1) · next reads earn x1.00

This week (from 2026-10-05): grade B, Over halfway there
  Quest [███████░░░]  deep reads 1/1, papers 2/3  ·  202 XP this week
  Last week: grade -, 0 XP

Reading Impact Factor (avg XP/week, last 4 weeks): 50.5
Discipline (top paper read first): 50% · Depth (read or deeper): 50% · papers read: 2

Badges 1/15: 🏅 First Steps
  next: Polymath [██░░░░░░░░] 2/8 · Read papers from 8 different research topics
```

For how points, levels and badges work, see [Gamification](#6-gamification).


## 3. Profiles

Everything personal belongs to a **profile**: the Paperpile library, the
topics and recommendation settings, the recommendations, the reading queue,
readings, notes, XP, badges and the locked game rules. Several profiles can
live side by side, e.g. one per person or project, and never mix.

**In the web UI** you choose a profile before you can see anything:

1. The **welcome screen** offers your profiles (with level, XP, papers read
   and last activity), **Create a new profile**, and **Load a saved
   profile**. If data from before profiles exists, it offers to turn it into
   a profile (*Use your existing library*), without importing anything again.
2. Creating a profile means choosing its game rules: the form is pre-filled
   with the defaults of `config.yaml` and can be opened to change every
   reward, the penalty, the level ladder and the badge targets. **The rules
   are locked when the profile is created.**
3. Then the dashboard opens. The chip at the top right shows the profile,
   its level and XP and leads to the profile page; **Switch profile** at the
   bottom of the sidebar returns to the welcome screen. The browser remembers
   your choice for a year.
4. The first visit to **Recommendations** asks for the profile's topics of
   interest (or *Skip: use my library only*). They can be changed on that page
   and under Settings.

**On the command line** commands work on the current profile (the last one
chosen in the web UI or with `profile use`, or the only one there is);
`--profile NAME` chooses another for one command:

```bash
litresearch profile list                     # * marks the current profile
litresearch profile create "Grace" --yes     # rules from config.yaml, locked
litresearch profile use grace
litresearch --profile Kai queue              # one command for another profile
litresearch profile adopt "Kai"              # data from before profiles -> profile
litresearch profile load saved.json --name "Kai (laptop)"
```

Where a profile's files live: [Your data and backups](#10-your-data-and-backups).


## 4. Web UI

```bash
litresearch serve                    # asks where to serve; Enter keeps web.host from the config
litresearch serve --network wlan0    # no question: only on the WLAN (or eth0, all, local, an IP)
litresearch serve --no-choose        # no question: use web.host / web.port from the config
litresearch serve --list-networks    # which networks this computer has
```

`serve` first asks where the UI should be reachable:

```
Where should the web UI be reachable?
  1. local    only this computer (127.0.0.1) ← default
  2. all      all networks (needs a password)
  3. eth0     cable (Ethernet): 192.168.50.2 (needs a password)
  4. wlan0    WLAN: 10.100.7.134 (needs a password)
Choose a number [1]:
```

Enter keeps the default (`web.host`); choosing another network offers to
make it the new default. `serve` never asks when it is not run in a terminal
(systemd, cron, `nohup … &`): then it uses `web.host`.

`serve` only starts the web server and prints the address(es) to open; it
never opens a browser on the server itself, so a Raspberry Pi without a
screen never shows a text-mode browser. The UI loads nothing from the
internet (no fonts, CDNs or trackers). POST requests from other websites are
rejected. Stop the server with Ctrl+C.

### Where the web server is reachable

| `web.host` / `--network` | Reachable from |
|---|---|
| `local` (default) | only this computer (`127.0.0.1`); no password needed |
| `all` | every network of this computer (cable, WLAN, VPN, …) |
| `eth0`, `wlan0`, … | only that one network; other networks and `127.0.0.1` are refused |
| an IPv4 address of this computer, e.g. `192.168.50.2` | only that address |

Prefer interface **names** over addresses: the address is looked up at every
start, so a new DHCP address does not break your setup.
`litresearch serve --list-networks` shows the names, for example:

```
  local    this computer only (127.0.0.1)
  all      every network below
  eth0     cable (Ethernet): 192.168.50.2
  wlan0    WLAN: 10.100.7.134
```

If the configured network does not exist (e.g. the WLAN is down), the server
does not start and lists the available choices.

### Using it from your laptop (local network)

Run the UI on one machine (e.g. the Raspberry Pi) and open it from any device
in the same network:

1. Set a password once (stored only as a salted scrypt hash in
   `web.password_hash`):

   ```bash
   litresearch set-password
   ```

2. Choose the network when `litresearch serve` asks, or set it with
   `web.host: wlan0` in `config/config.yaml` or `litresearch serve --network wlan0`.
3. Start it; the server prints the address to open:

   ```
   litresearch web server on wlan0, WLAN
     open http://10.100.7.134:8080/
     only reachable via 10.100.7.134, also from this computer itself
     login required (password set with litresearch set-password)
   ```

4. Open that address on the laptop and log in. The login stays valid for 30
   days (HttpOnly cookie); **Log out** and **Switch profile** are at the bottom of the sidebar.

Safety rules:

- Without a password the server refuses to listen on the network. `--no-login`
  overrides this, which is not recommended.
- Changing the password (`set-password` again) signs out all devices;
  `set-password --remove` removes it, after which the UI only starts locally.
- A wrong password costs a one-second delay, which slows down guessing.
- The connection is plain HTTP. That is fine in your own home network, but do
  not expose the port to the internet; use a VPN such as Tailscale or
  WireGuard for access from outside.
- To keep it running in the background, install it as a systemd user
  service: `python3 install.py --service` (see Setup).

The pages also work on a phone browser in the same network; an installable
phone app (PWA) is planned (roadmap 0.8).

### Pages

The UI centres on your **profile**: everything you see belongs to the
profile you chose on the welcome screen ([Profiles](#3-profiles)). The chip at
the top right shows the profile name, level and XP and leads to the profile
page.

| Menu | Page | What you can do |
|---|---|---|
| | 👋 **Welcome** (before anything else) | continue with a profile, create a new one (choose and lock its rules), take over existing data, or load a saved profile |
| 🏠 | **Dashboard** | your up-next paper with a one-click reading log, the weekly quest, hints, a reminder when new recommendations are due, your latest recommendations, queue, recent XP, and the **weekly reports** (one report card per week: grade, XP, papers, points by kind, badges; *show more* for older weeks) |
| Reading | 📚 **Reading queue** | the ordered queue (q1 = up next): log a reading (level, star rating, notes), reorder, remove; add papers by DOI, PubMed ID, citation key, title words or title only |
| Reading | ✨ **Recommendations** | on the first visit: set up the topics of your profile (or skip and use your library only). Then: **Get new recommendations** whenever you like (a reminder appears after `selection.reminder_days`, default 7); the list ranked by score with score bars, an **evidence tree** per paper, the filter **funnel**, the settings used and earlier lists; add to your queue or mark not relevant; once every paper of a set is handled the page says **Finished this recommendation set!** (the set stays available in the drop-down menu); your topics with an inline editor; **How recommendations are made**: every rule with your current values |
| Progress | 🏆 **Progress & XP** | level ring, streak, Reading Impact Factor, discipline and depth, XP per week (12 weeks), reading calendar (26 weeks), personal bests, badge gallery with progress, career ladder, the **XP ledger** (every transaction with running balance, filter by kind, totals, TSV export) and **how points work** (the locked rules of your profile) |
| Library | 📁 **Paperpile library** | your library without PDFs (search, folder and read-status filters, queue buttons), last import and notices when it is outdated, upload of a new BibTeX export with checks, re-import, export instructions, import history |
| Library | 🔍 **Find & add** | search **Europe PMC and PubMed** (or only one of them, or your own library) by keywords, title, author, journal, publication years and open access, sorted by relevance, date or citations; results show where they were found, citations, free full texts, the abstract and whether you already have them; queue a result with one click; the queries sent are shown; add any paper by DOI, `pmid:` or PMC id |
| | ⚙️ **Settings** | the profile's topics, excluded terms and every recommendation and queue parameter; data & backups of the profile |
| | 📖 **Documentation** | this documentation, one page per chapter, with a linked table of contents of all chapters and sections and previous/next links; pages link to the matching chapter (e.g. Recommendations → chapter 5) |
| (chip) | 🎮 **Profile** | the locked rules and fingerprint; save, download and compare profiles; your other profiles; switch or create a profile |
| | 📄 **Paper** | abstract, TL;DR, metrics, notes (timestamped), status, every recommendation of it with its evidence tree, the XP it earned |

Old addresses (`/weekly`, `/ledger`, `/how`) lead to their new places.

Keyboard shortcuts (outside text fields): `n` go to the queue, `r`
recommendations, `p` progress.

### Rewards and feedback

- An **XP toast** counts up the points of an action and lists every bonus or
  penalty.
- **Confetti** for earned points; more for a completed weekly quest.
- A **pop-up** for level-ups and unlocked badges.
- The up-next paper glows; the streak flame flickers.
- **Penalties** show as a red, shaking toast. A confirmation dialog warns
  before you remove a paper unread.
- With your system's *reduce motion* setting on, all animations are off.

### Removing papers from the queue

Every paper on your queue (from the recommendations, your library or added
by hand) costs the penalty of your game profile (default 15) if it leaves the
queue before you at least skimmed it: removed, or marked not relevant.
Dismissing a recommendation you never queued is free. The queue shows this as a red chip, and the remove
button names the cost.

### Settings

Settings belong to the profile. Saving writes to the profile's
`settings.yaml` (`data/profiles/<profile>/settings.yaml`); `config.yaml` is
not changed:

- comments and order are kept;
- the previous version is kept as `settings.yaml.bak`;
- invalid values (e.g. more than 5 papers, all weights 0) are rejected and
  the file is left unchanged.

Paths, API keys, embeddings, LLM and web settings are only editable in the
file. Fields of a topic that the UI does not show (`mesh_terms`,
`seed_dois`) are kept.


---

# Part II: How it works


## 5. How papers are recommended

This section documents every rule that decides which papers you see. Each
number is a setting (shown as `selection.…`) of your profile that you can
change in the UI (**Settings**, or the topic editor on the Recommendations
page). The Recommendations page shows the same rules with your current values
under **How recommendations are made**, and each recommendation has an
**evidence tree** with the actual numbers for that paper.

The code lives in `src/litresearch/recommend/` (`engine.py`: candidates and
filters; `scoring.py`: scores and selection). Keep this section and the
template `web/templates/_how_recommend.html` in sync with it.

### Overview

```
topics ─► OpenAlex searches (fresh, landmark) ─┐
library ─► Semantic Scholar recommendations ───┴─► candidates
   ─► filters ─► fresh-or-landmark ─► score ─► score floor ─► diverse picks ─► ranked list
```

You ask for a new list whenever you like; the UI reminds you after
`selection.reminder_days` (7) days. Searches are cached for a day, so a second
list on the same day costs no extra API budget. Every list (batch) stores:

- a **funnel**: how many candidates each step removed;
- a **snapshot of the settings** used, so old batches stay interpretable
  after you change settings.

### 1. Candidates

#### OpenAlex topic searches (`sources.openalex`)

For every query of every topic (`topics[].queries`), two searches in titles
and abstracts, sorted by OpenAlex relevance, `selection.results_per_query`
results each (default 25):

| Search | Filter |
|---|---|
| fresh | published within `selection.fresh_window_months` (6); types article, review, preprint |
| landmark | published in the last `selection.landmark_max_age_years` (15) years, citation percentile above `selection.landmark_min_percentile` (95) |

A full-text search sorted by citations was tested and rejected: it returned
off-topic hits (e.g. a FASTQ preprocessing tool for "circulating tumor DNA").

#### Semantic Scholar recommendations (`sources.semanticscholar`)

Semantic Scholar's recommendation service returns recently published papers
similar to *positive* and unlike *negative* example papers:

- positives (up to 100): papers you rated 4–5 stars or read thoroughly, then
  your most recent library papers;
- negatives (up to 50): papers you marked **not relevant**.

So your ratings and dismissals change what this source suggests. Works
without an API key; when the free service is busy, this step is skipped
with a warning.

### 2. Filters

A candidate is removed (counted in the funnel) if it

| Reason (funnel name) | Rule |
|---|---|
| `wrong_type` | is not an article, review or preprint (editorial, erratum, letter, …) |
| `meeting_abstract` | is a conference abstract from a journal supplement: titles like `0865 …`, `P-123: …`, `LB01. …` (but not `CD8 T cells…`, `53BP1…`, `1000 Genomes…`) |
| `read_or_dismissed` | was marked skimmed, read or thorough in litresearch, or not relevant (an abstract glance does **not** count as read) |
| `queued` | is on your reading queue |
| `recently_recommended` | was recommended within the last `selection.cooldown_weeks` (8) weeks, including earlier lists of today; so a new list shows new papers, and a paper you did not pick may come back after the cooldown |
| `excluded_term` | contains one of `excluded_terms` in title or abstract (case-insensitive) |

Papers without a DOI are skipped, since the pipeline is keyed on DOIs
(`without_doi` in the funnel).

Papers in your **Paperpile library are not excluded**: adding a paper to
Paperpile does not mean you have read it. Only your markings in litresearch
count. A library paper that is recommended carries the note "in your
Paperpile, not read yet".

### 3. Eligibility: current *or* important

Only two kinds of papers are scored:

- **fresh**: published at most `fresh_window_months` × 30.5 days ago;
- **landmark**: older, but with an OpenAlex citation percentile (normalised by
  field and publication year) ≥ `landmark_min_percentile` and an age of at
  most `landmark_max_age_years`.

Papers without a date, and older papers below the threshold, are dropped
(`not_fresh_or_landmark` in the funnel).

### 4. Score

```
score = w_r × relevance + w_i × impact + w_t × timeliness
```

with `selection.weights` (default 0.5 / 0.3 / 0.2). All components are in
[0, 1]; only the ratio of the weights matters.

#### Relevance

```
relevance = s × similarity_rank + (1 − s) × topic_bonus        s = selection.similarity_share (0.7)
```

- **similarity_rank**: the candidate's SPECTER2 embedding (a vector for
  scientific papers, from Semantic Scholar) is compared with the embeddings of
  your library papers by cosine similarity. The mean similarity to the
  `selection.knn` (5) most similar library papers is then ranked among all
  candidates: 0 = least similar, 1 = most similar. Candidates without an
  embedding use TF-IDF similarity of title + abstract instead. The ranking
  makes both methods comparable. The evidence tree lists the nearest library
  papers. A library paper never counts as its own neighbour; otherwise its
  similarity would trivially be 1.
- **topic_bonus**: for papers found by a topic search, the topic's weight
  divided by the highest topic weight (the best topic counts if several
  found the paper); `selection.s2_only_topic_bonus` (0.5) for papers found
  only by the Semantic Scholar recommendations; otherwise 0.

#### Impact

*Landmark papers*: citation percentile ÷ 100. Without a percentile, citations
per year on a log scale, where `selection.citations_per_year_saturation` (200)
per year = 1.0.

*Fresh papers* cannot have meaningful citations yet, so the journal counts:

```
impact = min(1, venue + early)
venue  = log(1 + c) / log(1 + C)       c = journal 2-year mean citedness (OpenAlex), C = selection.top_venue_citedness (30)
       = selection.unknown_venue_impact (0.05) if the journal is unknown
early  = selection.early_citation_bonus (0.2) × log(1 + citations) / log(1 + selection.early_citation_saturation (50))
```

The 2-year mean citedness is an open counterpart of the journal impact
factor. It can be distorted, e.g. *Cancer Research* scores 0.66 because of its
many meeting-abstract supplements, which is why landmark papers use
paper-level percentiles instead. A missing journal is a warning sign:
preprint servers count as journals in OpenAlex.

#### Timeliness

- fresh: `0.5 + 0.5 × (1 − age / fresh window)`, from 1.0 on publication day
  down to 0.5 at the end of the window;
- landmark: constant `selection.landmark_timeliness` (0.4).

### 5. Selection

1. **Score floor**: only candidates with at least `selection.min_relative_score`
   (60 %) of the best score stay in the running.
2. **Diversity (maximal marginal relevance)**: papers are picked one at a
   time; each pick maximises

   ```
   λ × score − (1 − λ) × similarity to the most similar paper already picked      λ = selection.mmr_lambda (0.7)
   ```

   With λ = 1 the picks are strictly the top scores; lower values spread the
   list over different subjects. Similarity uses SPECTER2 where both papers
   have it, TF-IDF otherwise.
3. Up to `selection.n_recommendations` (default 5) papers are picked and
   shown ranked by score.

### 6. How deeply to read is your choice

Recommendations do not prescribe a reading depth. After reading, you mark
the paper (abstract, skimmed, read, thorough) and earn the points your game
profile defines for that depth ([Gamification](#6-gamification)).
Batches built before this change still show their old detail/skim labels in
the data, but the UI ignores them.

### The evidence tree

Every recommendation stores its evidence, shown in the UI under **Why was
this recommended?**:

| Node | Content |
|---|---|
| Score | the formula with the actual values and contributions |
| Found by | each search (topic, query, result position) or the S2 recommendation rank |
| Eligible as | fresh (age vs. window) or landmark (percentile and age vs. thresholds); filters passed |
| Relevance | method (SPECTER2/TF-IDF), mean similarity, rank among N candidates, the nearest library papers with similarities, topic bonus and its reason |
| Impact | basis (journal / citation percentile / citations per year) with the inputs |
| Timeliness | age and window |
| Selection | score rank among eligible papers, best score and floor, MMR pick order, most similar earlier pick |
| Category | relevance + impact and its position among the picks |

### Feedback loop

- **Not relevant** (dismiss): never shown again; becomes a negative example
  for Semantic Scholar.
- **Rating 4–5 or thorough read**: becomes a positive example for Semantic
  Scholar.
- Your reading queue, read papers and the cooldown keep the list fresh.


## 6. Gamification

The point system rewards **depth** and **discipline** (reading the paper you
planned to read first), not volume. All rules are stored in your **game
profile** and are **locked** once the game starts.

### Your profile and its locked rules

1. **Create**: on the welcome screen (or `litresearch profile create NAME`)
   you name the profile and choose every reward and penalty, the level ladder
   and the badge targets. The form is pre-filled with the defaults from the
   `game` section of `config.yaml`.
2. **Locked**: the rules are copied into the profile's database and locked.
   Their **fingerprint** (a short hash of the complete rule set) is shown on
   the profile page. From then on, all points are computed with these stored
   rules; editing `config.yaml` or the settings can no longer change them,
   which prevents inflating points after the fact.
3. **Different rules** need another profile: create one on the welcome
   screen (*Switch profile* in the sidebar). The old profile stays as it is,
   and you can switch back at any time.

### Rules you can set (defaults)

| Rule | Default | Meaning |
|---|---|---|
| `xp_abstract` / `xp_skimmed` / `xp_read` / `xp_thorough` | 5 / 15 / 40 / 80 | base points for the depth **you** choose after reading; deeper may never pay less |
| `frog_bonus` | 0.5 | 🐸 share of the base points when the paper was **q1** (up next) and read ≥ skimmed |
| `streak_step` / `streak_max_steps` | 0.05 / 10 | 🔥 +5 % per consecutive reading week, at most +50 % |
| `classic_percentile` / `classic_xp` | 99 / 20 | 🏛️ landmark (top 1 % cited) read at least at "read" depth |
| `hot_days` / `hot_xp` | 30 / 10 | 📰 read within 30 days of publication |
| `recommendation_xp` | 15 | 🎯 read (≥ skimmed) a paper from your recommendations |
| `rating_xp` | 2 | ⭐ first rating of a paper |
| `reflection_chars` / `reflection_xp` | 140 / 10 | ✍️ notes on a paper reach 140 characters |
| `inbox_zero_xp` | 10 | 📭 the queue becomes empty because you *read* its last paper (once per day) |
| `weekly_detail_goal` / `weekly_skim_goal` / `quest_xp` | 1 / 2 / 50 | 🗓️ weekly quest: 1 paper read at least at "read" depth + 2 more at least skimmed |
| `badge_xp` | 25 | 🏅 per unlocked badge |
| `drop_penalty` | 15 | ⚠️ a paper on your queue (from the recommendations, your library or added by hand) leaves it, removed or marked not relevant, before you at least skimmed it |
| `levels` | 20 levels | the ladder below |
| `badge_targets` | see below | how much each badge needs |

Fairness rules that always apply:

- Base points per paper are paid only up to the deepest level ever reached.
  Upgrading pays the difference; re-logging or downgrading and upgrading
  again pays nothing.
- Paper bonuses (landmark, fresh, recommendation, rating, notes) are paid
  once per paper. The frog bonus is a share of *newly* earned base points.
- Dismissing a recommendation you never put on the queue is free.
- Penalties can push the total, and therefore the level, down; the level
  never drops below 1.

Recommendations never prescribe how deeply to read: you decide, and your
marking determines the points.

### Streaks

A *reading week* is a week (Monday to Sunday) in which at least one paper
reached "skimmed" or deeper. The streak counts consecutive reading weeks. It
stays alive through the following week, so it does not reset on Monday
morning before you had a chance to read.

### Weekly quest and report card

| Grade | Meaning |
|---|---|
| S | quest complete, including a thorough read |
| A | quest complete |
| B | at least half of the quest |
| C | something read |
| - | nothing read (a rest week) |

### Levels (default ladder)

| Level | Title | XP |
|---|---|---|
| 1 | 🎓 Curious Undergrad | 0 |
| 2 | 📘 Bachelor Student | 60 |
| 3 | 🧪 Lab Rotation Student | 140 |
| 4 | 📗 Master's Student | 250 |
| 5 | 🧫 Research Assistant | 400 |
| 6 | 📚 PhD Candidate | 600 |
| 7 | ☕ Journal Club Regular | 850 |
| 8 | 🪧 Poster Presenter | 1 150 |
| 9 | 🎤 Conference Speaker | 1 500 |
| 10 | 📜 Doctor of Philosophy | 1 900 |
| 11 | 🔬 Postdoc | 2 400 |
| 12 | 🧬 Senior Postdoc | 3 000 |
| 13 | 🧑‍🔬 Research Fellow | 3 700 |
| 14 | 🌱 Junior Group Leader | 4 500 |
| 15 | 👩‍🔬 Group Leader | 5 400 |
| 16 | 🏫 Associate Professor | 6 500 |
| 17 | 🎩 Professor | 7 800 |
| 18 | 🏛️ Department Head | 9 300 |
| 19 | 🖋️ Review Editor | 11 000 |
| 20 | 🏆 Hallmarks Author | 13 000 |

A typical week (quest done, one thorough read from the top of the queue, a
few bonuses) is worth about 200–300 XP.

### Badges (default targets)

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
| Pace | 🦉 Night Owl | Log 5 reads between 22:00 and 04:00 | `night_owl` |
| Pace | 🌅 Early Riser | Log 5 reads between 05:00 and 08:00 | `early_riser` |
| Pace | 🏖️ Weekend Warrior | Log 5 reads on weekends | `weekend_warrior` |
| Breadth | 🏺 Classicist | Read 5 landmark papers (top 1 % cited) | `classicist` |
| Breadth | 📜 Canon Keeper | Read 20 landmark papers | `canon_keeper` |
| Breadth | 🐦 Early Bird | Read 5 papers within 30 days of publication | `early_bird` |
| Breadth | 📰 Scoop | Read 3 papers within 7 days of publication | `scoop` |
| Breadth | 🦴 Archaeologist | Read a paper that is 25+ years old | `archaeologist` |
| Breadth | 🧩 Polymath | Read papers from 8 different research topics | `polymath` |
| Breadth | 🎨 Renaissance Mind | Read papers from 20 different topics | `renaissance` |
| Breadth | 🧗 Trailblazer | Read 10 papers from your recommendations | `trailblazer` |
| Breadth | 📁 Library Diver | Read 10 papers from your Paperpile library | `library_diver` |
| Breadth | 🪁 Free Spirit | Read 5 papers you added yourself | `free_spirit` |
| Experience | 💰 Thousand Club | Earn 1000 XP | `thousand_club` |
| Experience | 🖐️ High Five | Earn 5000 XP | `high_five` |

### Saving, loading and comparing profiles

- **Save** (Profile page, `litresearch profile save`) writes a profile file:
  the profile and its locked rules, every reading status, rating and note,
  the reading history, queue, XP transactions, badges and all recommendation
  batches with their evidence. Automatic backups contain the same data.
- **Load** (welcome screen, `litresearch profile load FILE`) turns a saved
  profile into a new profile next to your existing ones; nothing is
  replaced.
- **Compare** (Profile → Compare, `litresearch profile compare FILE ...`)
  shows profiles side by side: level, XP, papers read, thorough reads,
  longest streak, quests, badges, discipline, depth, Reading Impact Factor,
  notes and active weeks. It highlights the best value per row, says whether
  two profiles played by the **same rules** (fingerprint), and whether a file
  was **edited** outside litresearch.
- Every file carries a **checksum**. Loading or merging a file whose content
  no longer matches it is refused. This catches hand-edited files; it is a
  consistency check, not a cryptographic proof, because all data lives on
  your own machine.
- Merging progress from another machine (`import-progress`) only works
  between profiles with the same rules, so points earned under different
  rules are never mixed.

### Performance metrics

- **Reading Impact Factor**: average XP per week over the last four weeks.
- **Discipline**: share of papers read while they were at the top of the
  queue.
- **Depth**: share of papers read at least at "read" depth.
- **Queue nudge**: a warning when the top paper has been waiting 14 days or
  more.

### Overviews in the web UI

Weekly reports (on the dashboard); the XP ledger with TSV export, XP per
week, the reading calendar, personal bests, next-goal hints and the badge
gallery grouped by theme (on **Progress & XP**) ([Web UI](#4-web-ui)).


---

# Part III: Reference


## 7. Commands

| Command | Purpose | Status |
|---|---|---|
| `db-init` | create the SQLite database | ✅ |
| `ingest [--no-s2]` | import the Paperpile export; resolve DOIs; fetch metrics and embeddings | ✅ |
| `status` | library summary: coverage, top folders | ✅ |
| `recommend [--new] [--no-s2]` | your latest ≤ 5 papers, ranked; `--new` gets new ones | ✅ |
| `profile list\|create\|adopt\|use\|show\|save\|load\|compare` | profiles: list, create (rules locked), take over existing data, choose the current one, show its rules, save, load a saved one as a new profile, compare | ✅ |
| `check-bib [FILE]` | check a Paperpile BibTeX export | ✅ |
| `queue [add\|drop\|move]` | the short, ordered reading queue | ✅ |
| `search [KEYWORDS] [--title] [--author] [--journal] [--from] [--to] [--oa] [--source all\|europepmc\|pubmed\|library] [--sort relevance\|newest\|cited] [--add N]` | find papers in Europe PMC and PubMed and queue them | ✅ |
| `next`, `show REF` | top paper / any paper with abstract, status, notes | ✅ |
| `read REF LEVEL [--rating] [--note]` | log reading and earn XP | ✅ |
| `note REF TEXT`, `dismiss REF` | add a note / mark as not relevant | ✅ |
| `score`, `history` | level, streak, weekly grade, badges / reading log | ✅ |
| `serve [--network local\|all\|eth0\|wlan0\|IP] [--no-choose] [--port] [--list-networks]` | web server for this computer, all networks or one chosen network; asks where to serve unless `--network`/`--no-choose` is given (never opens a browser) | ✅ |
| `set-password [--remove]` | password for network access to the UI | ✅ |
| `backup`, `restore FILE` | database snapshot + progress export / restore a snapshot | ✅ |
| `export-progress FILE`, `import-progress FILE` | move or merge your progress between machines | ✅ |
| `check` | database integrity, contents, last backup | ✅ |

Global options: `-p/--profile NAME` works on another profile than the current
one; `-v/--verbose` shows HTTP requests and retries. Every command takes
`-c/--config PATH`.


## 8. Data sources and privacy

All services are free, used within their free limits, and no personal data
is sent:

- [Crossref](https://www.crossref.org/documentation/retrieve-metadata/rest-api/) for DOIs
- [OpenAlex](https://docs.openalex.org/) for metrics, identifiers and topic searches
- [Semantic Scholar](https://www.semanticscholar.org/product/api) for SPECTER2, TLDRs and recommendations
- [Europe PMC](https://europepmc.org/RestfulWebService) and
  [PubMed](https://www.ncbi.nlm.nih.gov/books/NBK25501/) (NCBI E-utilities) for
  the paper search and for adding papers by PMID or PMC id

What leaves your machine: titles of library papers without DOI (Crossref,
during ingest), DOIs/PMIDs (OpenAlex, Semantic Scholar, Europe PMC, PubMed),
your topic queries (OpenAlex), what you type into the paper search (Europe
PMC, PubMed), and the DOIs of library, well-rated and dismissed papers
(Semantic Scholar recommendations; switch off with `sources.semanticscholar`).
PubMed requests carry `tool=litresearch` but no e-mail address.
`api.contact_email` stays `n/a` unless you want the polite pools. Paperpile
has no public API, so its BibTeX auto-export is the interface. Planned:
PubMed as a source for recommendations and PubMed Central full texts
(roadmap 0.4).


## 9. Configuration

Settings come from two places:

- **`config/config.yaml`** (override with `--config/-c`): machine settings
  (`api`, `embeddings`, `backup`, `web`, `llm`, `schedule`), the defaults for
  new profiles (`game`) and the defaults every profile starts from. Start from
  [`config/config.example.yaml`](../config/config.example.yaml).
- **`data/profiles/<profile>/settings.yaml`**: what a profile changes:
  `topics`, `excluded_terms`, `selection`, `sources`, `queue`, `library` and
  `paths.bibtex`. The web UI writes this file; you can also edit it by hand.
  A new profile starts without topics (they are asked for on the
  Recommendations page); a profile taken over from existing data starts with
  the topics of `config.yaml`.

- Use `n/a` for "not set".
- Unknown keys are rejected, so typos fail early.
- Relative paths are resolved from the directory you run the command in.

Sections marked *(planned)* are already validated but not used yet.

### `paths`

| Key | Default | Meaning |
|---|---|---|
| `profiles_dir` | `data/profiles` | one folder per profile (database, Paperpile export, settings) |
| `bibtex` | `data/paperpile/library.bib` | Paperpile BibTeX export *before profiles*; a profile reads `library.bib` in its folder unless its `settings.yaml` sets `paths.bibtex` |
| `database` | `data/litresearch.sqlite` | database *before profiles*; a profile always uses `litresearch.sqlite` in its folder |
| `cache_dir` | `data/cache` | HTTP response cache |
| `obsidian_vault` | `n/a` | Obsidian export target *(planned)* |

### `library`

| Key | Default | Meaning |
|---|---|---|
| `ignore_labels` | `["Import Mendeley"]` | Paperpile labels without topical meaning; matched case-insensitively |

### `topics`

A list of your areas of interest. Each query runs two OpenAlex title/abstract
searches per recommendation batch (fresh and landmark papers). Papers found by
a topic get a relevance bonus proportional to the topic's `weight`.

```yaml
topics:
  - name: liquid-biopsy        # short id
    weight: 0.8                # relative importance, >= 0
    queries: ["circulating tumor DNA", "cfDNA fragmentomics"]
    mesh_terms: ["Circulating Tumor DNA"]
    seed_dois: ["10.1038/s41586-019-1272-6"]   # key papers that define the topic
```

### `excluded_terms`

Papers whose title or abstract contains one of these terms
(case-insensitive) are never recommended.

### `sources`

Switch individual data sources on or off (`true`/`false`): `semanticscholar`,
`openalex`; `europepmc` and `pubmed` for the paper search (Find & add,
`litresearch search`; at least Europe PMC is used if both are off); and
*(planned)* `biorxiv` (OpenAlex and Europe PMC already index bioRxiv). `--no-s2` skips Semantic Scholar for a single `ingest`
or `recommend` run.

### `api`

| Key | Meaning |
|---|---|
| `semanticscholar_key` | Optional free key, which gives the tool its own rate limit (1 request/s). Without one, requests may hit HTTP 429; affected steps are skipped with a warning. |
| `openalex_key` | Optional. Without a key OpenAlex grants a small daily budget (≈ 1000 credits). An ingest uses ≈ 6 credits; a recommendation batch ≈ 20 per topic query (two searches) plus a few look-ups, i.e. ≈ 110 with the example config. |
| `contact_email` | Optional; sent as `mailto` to Crossref and OpenAlex (polite pool). Leave it `n/a` to send no personal data. |
| `ncbi_key` | Optional free NCBI API key ([how to get one](https://support.nlm.nih.gov/kbArticle/?pn=KA-05317)): PubMed then allows 10 instead of 3 requests per second. Not needed for normal use. |

**Privacy.** The tool sends no personal data unless you set `contact_email`.
What leaves your machine: titles/authors of library papers without a DOI
(to Crossref), DOIs/PMIDs (to OpenAlex and Semantic Scholar), your topic
queries (to OpenAlex), and the DOIs of library, well-rated and dismissed
papers (to the Semantic Scholar recommendations service). Disable
`sources.semanticscholar` to keep the latter local.

### `embeddings`

| Key | Default | Meaning |
|---|---|---|
| `backend` | `hybrid` | `s2`: SPECTER2 vectors from Semantic Scholar; `local`: own SPECTER2 service; `hybrid`: S2 first, local service for papers S2 has not embedded |
| `local_url` | `n/a` | OpenAI-style `/v1/embeddings` URL of the local service; required for `local` *(local service planned)* |

### `selection`

| Key | Default | Meaning |
|---|---|---|
| `n_recommendations` | 5 | papers per list (1–5); older configs with `n_detail`/`n_skim` are converted automatically |
| `fresh_window_months` | 6 | how recent a paper must be to count as "current" |
| `landmark_min_percentile` | 95 | field/year-normalised citation percentile for "important" papers |
| `landmark_max_age_years` | 15 | landmark papers must be younger than this |
| `mmr_lambda` | 0.7 | relevance vs. diversity trade-off (1 = only relevance) |
| `weights` | 0.5 / 0.3 / 0.2 | weights of relevance / impact / timeliness |
| `results_per_query` | 25 | OpenAlex results per topic query and kind |
| `cooldown_weeks` | 8 | an unpicked recommendation may come back after this many weeks |
| `reminder_days` | 7 | remind me to get new recommendations after this many days (0 = never) |
| `similarity_share` | 0.7 | relevance = share × library-similarity rank + (1 − share) × topic bonus |
| `knn` | 5 | library papers averaged for the similarity |
| `s2_only_topic_bonus` | 0.5 | topic bonus of papers found only by S2 recommendations |
| `top_venue_citedness` | 30 | journal 2-year mean citedness that counts as impact 1.0 (fresh papers) |
| `unknown_venue_impact` | 0.05 | impact of fresh papers without a known journal |
| `early_citation_bonus` / `early_citation_saturation` | 0.2 / 50 | extra impact for already-cited fresh papers |
| `citations_per_year_saturation` | 200 | citations/year for impact 1.0 when no percentile is known |
| `landmark_timeliness` | 0.4 | timeliness of landmark papers |
| `min_relative_score` | 0.6 | picks need this share of the best score |

All of these are explained with formulas in
[How papers are recommended](#5-how-papers-are-recommended) and editable in the
web UI (Settings).

### `queue`

| Key | Default | Meaning |
|---|---|---|
| `max_size` | 5 | maximum queue length (1–20). Keep it small. |

### `game`

The **defaults for a new game profile**: every reward, bonus, the penalty,
the weekly quest, the level ladder (`levels`) and badge targets
(`badge_targets`). When a profile is created, these rules are copied into
its database and locked; changing this section afterwards only affects the
*next* profile. All keys and defaults are listed in
[Gamification](#rules-you-can-set-defaults). It is
easier to set them on the welcome screen when you create a profile.

### `backup`

| Key | Default | Meaning |
|---|---|---|
| `dir` | `data/backups` | where snapshots and progress exports go (one subfolder per profile); a cloud-synced folder is ideal |
| `keep` | 14 | snapshots (and exports) to keep |
| `interval_hours` | 24 | `litresearch serve` backs up at start and then every N hours; 0 = off |

See [Your data and backups](#10-your-data-and-backups).

### `llm` *(planned, roadmap 0.6)*

Any OpenAI-compatible endpoint (LM Studio, Ollama, an institutional server):
`enabled`, `base_url`, `model`, `api_key`.

### `schedule` *(planned)* / `web`

`schedule.cron`: when to build a new list (cron syntax).
`web.host` is where `litresearch serve` listens: `local` (this computer only,
no login needed), `all` (every network), an interface name such as `eth0` or
`wlan0` (only that network), or an IPv4 address of this computer. Everything
except `local` requires a password. The older values `127.0.0.1` and `0.0.0.0`
still work. `web.port` is the port. `web.password_hash` is set by
`litresearch set-password`; never edit it by hand. See
[Web UI](#using-it-from-your-laptop-local-network).


## 10. Your data and backups

### Where everything is stored

Each profile has its own folder, so everything personal belongs to exactly one
profile:

```
data/profiles/
├── .current                  the profile the command line uses (set by the web UI and `profile use`)
├── kai/
│   ├── litresearch.sqlite    progress, queue, recommendations, library metadata
│   ├── library.bib           the profile's Paperpile export (unless settings.yaml points elsewhere)
│   └── settings.yaml         topics and recommendation settings of the profile
└── grace/ ...
```

| What | Where (default) | Rebuildable? |
|---|---|---|
| **Your progress**: reading statuses, ratings, notes, history, queue, XP, badges, recommendation lists, locked rules | `data/profiles/<profile>/litresearch.sqlite` | **no**, back it up |
| Library metadata, metrics, embeddings | same database | yes, `litresearch ingest` |
| Topics and recommendation settings | `data/profiles/<profile>/settings.yaml` | no; small, included in every backup |
| Machine settings and defaults | `config/config.yaml` | no; small, keep a copy |
| Cached API answers (shared by all profiles) | `data/cache/http_cache.sqlite` | yes, delete any time |
| Login cookie key | `data/web_secret` | yes; deleting it logs everyone out |
| Backups | `data/backups/<profile>/` (`backup.dir`) | they *are* the copy |
| Data from before profiles | `data/litresearch.sqlite` (`paths.database`) | kept as it was after you take it over |

All paths are settings, so you can move everything into one folder of your
choice (e.g. `~/litresearch-data/`).

### Recommended setup

```
Raspberry Pi (always on)                       laptop / phone
┌──────────────────────────────┐   LAN       ┌───────────────┐
│ litresearch serve            │◄───────────►│ browser       │
│ data/profiles/*/ (live)      │             └───────────────┘
│ backup.dir ──► cloud folder ─┼──► Nextcloud / Dropbox / Google Drive / Syncthing
└──────────────────────────────┘
```

1. **One live database on one machine.** Use it from your other devices
   through the web UI over the network ([Web UI](#4-web-ui)). That way
   there is never a second copy to get out of sync.
2. **Point `backup.dir` to a cloud-synced folder**, not the database itself:

   ```yaml
   backup:
     dir: /home/kai/Nextcloud/litresearch-backups   # or a Syncthing / rclone folder
     keep: 14
     interval_hours: 24
   ```

   While `litresearch serve` runs, it backs up every profile at start and
   then every 24 h, each into its own subfolder. `litresearch backup` does it on demand, as does the **Back up now**
   button under Settings → Data & backups. A weekly cron job works as well:

   ```
   0 7 * * 1  cd /path/to/litresearch-tool && .venv/bin/litresearch backup
   ```

3. Check now and then: `litresearch check` shows the database location, an
   integrity check, its contents and the age of the last backup.

#### Why not sync the live database file?

SQLite writes in place. A sync client (Dropbox, Google Drive, Nextcloud,
OneDrive) can copy the file while a write is half done, which yields a
corrupt copy. If two machines use the "same" synced file, the client
creates conflict copies, and one machine's progress silently goes missing.
The snapshots below avoid both problems: each is complete and consistent,
and never changes again after it is written.

If you do want a database on several machines, use **Syncthing** (it
handles changing files better) **and** only ever run litresearch on one
machine at a time. The JSON export/import below is the safer route.

### What a backup contains

Every backup writes two files (file names follow `YYYYMMDD_HHMMSS`):

| File | Content | Use |
|---|---|---|
| `litresearch_20261008_070000.sqlite` | complete database snapshot, made with SQLite's online backup API (consistent even while the server writes) and integrity-checked | full restore: `litresearch restore FILE` |
| `litresearch_progress_20261008_070000.json` | your whole **profile**: the profile with its locked game rules and settings, statuses, ratings, notes, history, queue, XP, badges and all recommendation batches, plus enough paper metadata (DOI, PMID, citation key, title, …) to find each paper again, and a checksum | readable; load as a new profile (`litresearch profile load`) or merge into another installation (`litresearch import-progress`) |

Profiles you save yourself (Profile page, `litresearch profile save`) have the
same content and are named `litresearch_profile_<name>_<date>_<time>.json`;
they are never deleted by rotation. Files of format version 1 (before game
profiles existed) can still be imported.

The oldest files beyond `backup.keep` (default 14 of each) are deleted.

### Restoring

```bash
# stop `litresearch serve` first
litresearch restore /path/to/backups/litresearch_20261008_070000.sqlite
```

The current database is saved as `…_before_restore.sqlite` first, so a
restore can itself be undone. Files that are not valid litresearch
databases are refused.

### Loading a saved profile

- The welcome screen (*Load a saved profile*) or `litresearch profile load
  FILE [--name NAME]` turns a saved profile, including its locked rules, its
  recommendations and its settings, into a **new profile**. Existing profiles
  are not changed.
- A file that was edited outside litresearch (checksum mismatch) is refused.
- To start over with other rules, create another profile; the old one stays.

### Moving to another machine, or merging two

```bash
# on the old machine
litresearch export-progress progress.json          # or take the latest backup JSON

# on the new machine (after ingest of the same Paperpile library)
litresearch import-progress progress.json --dry-run   # preview
litresearch import-progress progress.json
```

How the merge works:

- papers are matched by DOI, then PMID, then citation key, then exact title,
  because internal ids differ between installations; unknown papers are
  created from the stored metadata;
- reading status and rating: the newer change wins;
- notes: entries from both sides are kept, none are lost;
- history, XP transactions and badges are added if they are not present yet;
- queue entries are appended while there is room (`queue.max_size`);
- recommendation batches are added if they are not present yet;
- the profile is taken over if the target has none; if both have one, their
  rules must be identical (same fingerprint), otherwise the merge is refused
  so that points earned under different rules are never mixed;
- **idempotent**: importing the same file twice changes nothing;
- the target database is backed up before the merge; an invalid file
  changes nothing.

### How progress is protected against crashes

- Every action (logging a reading, a note, a queue change) is **one database
  transaction**: either everything is saved or nothing.
  `tests/test_robustness.py` simulates crashes in the middle of actions.
- Commits are written with `PRAGMA synchronous=FULL`: a confirmed change is
  on the disk, even if the power drops right afterwards (relevant on a
  Raspberry Pi SD card).
- The CLI and the web UI can write at the same time; a writer waits up to 30
  seconds for the other instead of failing.
- Settings and exports are written to a temporary file and then renamed,
  so a crash never leaves a half-written `settings.yaml` or `config.yaml`.
- A failed automatic backup is logged and never stops the server.


---

# Part IV: How it is built


## 11. Architecture

### Overview

```
Paperpile ──auto-export──> Google Drive ──rclone──> library.bib
                                                       │
                                         ingest/bibtex.py (parse)
                                                       │
                                        ingest/pipeline.py ──> SQLite
                                         │       │       │
                                   Crossref  OpenAlex  Semantic Scholar
                                   (DOIs)   (metrics)  (SPECTER2, TLDR)
```

```
topics ──> OpenAlex search (fresh, landmark) ─┐
library ─> S2 recommendations ────────────────┼─> candidates ─> filter ─> score ─> MMR ─> ≤ 5 ranked
                                              │                                       │
                                  S2 SPECTER2 / TF-IDF                        you pick ─> queue ─> read ─> XP
```

Planned stages are described in `ROADMAP.md`: reading library and search,
PubMed/PMC, Obsidian-compatible notes, local LLM summaries, more reference
managers, more systems and devices.

### Ingest pipeline

1. **Parse** the BibTeX file. Paperpile quirks it handles:
   - `month = "5\xa0" # apr`: day, non-breaking space and month macro → `pub_date`
   - line-wrapped values, LaTeX escapes (`\%`, `{PD1}`)
   - `keywords` mixing folder paths (`03 Biology / Medicine/Cancer/Evolution`) with
     labels and author keywords → `folders` and `tags`

   Duplicate citation keys only produce a warning. Any other parse error aborts
   *before* the database is touched, so a truncated download never makes papers
   look as if they had left the library.
2. **Sync**: papers are upserted by citation key. A paper already in the
   database with the same DOI and no key (e.g. an earlier recommendation) is
   adopted into the library. Papers missing from the export get
   `in_library = False`; they are not deleted, so reading history and notes
   survive.
3. **Resolve DOIs** for entries that have neither DOI nor PMID, via Crossref.
   Crossref's top hit is not trusted blindly (it can be a "Faculty Opinions"
   record about the paper). A candidate must:
   - not be a peer review, dataset or component record;
   - match the year ±1 and the first author's surname, where both are known;
   - have a title similarity ≥ 0.9, or a truncated library title (≥ 4 words)
     that is a prefix of the candidate's title, confirmed by the author.
4. **OpenAlex enrichment**, batched 50 IDs per request (1 credit per request
   instead of 10 for a search): citation count, FWCI, field/year-normalised
   citation percentile, topics, PMID, missing abstracts, and the venue's 2-year
   mean citedness (an open proxy for the impact factor). PMID-only entries are
   looked up by PMID.
5. **Semantic Scholar enrichment**, batched 100 IDs per request: SPECTER2
   embedding, TLDR, influential citation count.

Each source fails on its own: an outage or rate limit is recorded as a warning
in the report and the remaining steps still run.

### Recommendation pipeline

1. **Candidates**:
   - per topic query, two OpenAlex title/abstract searches sorted by relevance:
     *fresh* (published within the fresh window; articles, reviews, preprints)
     and *landmark* (citation percentile above the threshold, published within
     `landmark_max_age_years`). A full-text search sorted by citations returned
     off-topic hits (e.g. `fastp` for "ctDNA"), so the tool does not use it;
   - Semantic Scholar recommendations for the library: positives are well-rated
     papers plus the most recent library papers, negatives are dismissed papers.
2. **Filter**: drop papers marked skimmed/read/thorough or dismissed, queued, recommended
   within the cooldown, matching `excluded_terms`, editorials
   and other non-research types, and conference abstracts from journal
   supplements (titles like `0865 …`, `P-123: …`).
3. **Score** (`recommend/scoring.py`):
   - *relevance* = 0.7 × percentile rank of the mean cosine similarity to the 5
     nearest library papers (SPECTER2; TF-IDF for papers without an embedding)
     + 0.3 × topic bonus (topic weight / maximum weight; 0.5 for papers found
     only by S2 recommendations);
   - *impact*: landmark papers use the citation percentile. Fresh papers cannot
     have citations yet, so they use the journal's log-scaled 2-year mean
     citedness, plus a small bonus for early citations. A missing venue scores
     0.05: preprint servers count as venues, so a missing one is a warning sign;
   - *timeliness*: fresh = 0.5–1.0, decaying over the fresh window;
     landmark = 0.4;
   - score = weighted sum (`selection.weights`).
4. **Select**: only candidates with at least 60 % of the best score; then MMR
   (λ = `mmr_lambda`) picks `n_recommendations` diverse papers, shown ranked
   by score. The reader chooses the reading depth.
5. **Persist**: one `RecommendationBatch` per list (with the Monday of its week), with
   the filter funnel and a snapshot of the settings, and its `Recommendation`
   rows with the score breakdown, the full evidence (shown as an evidence tree
   in the UI) and a one-line reason.

All constants are settings; the full criteria are in
[How papers are recommended](#5-how-papers-are-recommended).

### External services

| Service | Use | Limits without a key |
|---|---|---|
| Crossref | DOI by title | free; polite pool with `mailto` |
| OpenAlex | metrics, IDs, venues, topic search | metered (~1000 credits/day); batch lookups cost 1 credit per 50 papers, a search 10 credits |
| Semantic Scholar | SPECTER2, TLDR | one rate limit shared by all anonymous users → HTTP 429 is common; free key = 1 request/s |
| Europe PMC | paper search, look-up by DOI/PMID/PMC id (abstract, PMC id) | free, no key; litresearch waits 0.1 s between requests |
| PubMed (E-utilities) | paper search, look-up by PMID | 3 requests/s without key (10 with `api.ncbi_key`); litresearch keeps to it |

All responses are cached in `data/cache/http_cache.sqlite`: Crossref for
1 year, OpenAlex look-ups for 14 days, searches and S2 recommendations for
1 day, Semantic Scholar papers for 30 days. The tool accepts the free limits:
when a service refuses, the affected step is skipped with a warning.

### Known data caveats

- OpenAlex withholds abstracts for some publishers (e.g. Nature Genetics);
  BibTeX abstracts are preferred anyway.
- Venue mean citedness can be distorted by supplements. *Cancer Research*
  scores 0.66 because of its many meeting abstracts. Scoring must therefore
  rely mainly on paper-level metrics (percentile, FWCI).

### Profiles

`profiles.py` keeps one folder per profile under `paths.profiles_dir`. The
effective configuration of a profile is `config.yaml` merged with the
profile's `settings.yaml` (only the keys in `OVERLAY_KEYS`), with
`paths.database`, `paths.bibtex` and `backup.dir` pointing into the profile.
The web UI keeps the chosen profile in a signed cookie; a middleware sends
every request without a valid profile to the welcome screen and makes the
profile available to the routes through a context variable, so every page
and action works on that profile's database and settings. The command line
uses `--profile`, else the profile in `data/profiles/.current` (written by the
web UI and `profile use`), else the only profile. Without any profiles it
falls back to `paths.database`, the data from before profiles, which
`adopt_legacy` copies into a new profile.


## 12. Implementation and credits

How the tool was developed and how it works inside, with credits for every
library, data service and method it relies on.

### Purpose in one paragraph

litresearch reads a Paperpile library (BibTeX export) and enriches it with
open bibliographic data. On request it recommends up to five papers, ranked
by similarity to the library and to self-defined topics, by paper impact,
and by being either recent or a landmark. The user picks papers into a
short, ordered reading queue, decides how deeply to read each one, logs
ratings and notes, and is motivated by a game layer (XP, streaks, quests,
20 levels, 44 badges) whose rules are set once per profile and then locked.
Everything belongs to a profile; several profiles can live side by side. Everything runs locally, from the command
line or a web UI, using only free services and without sending personal data.

### How it was developed

- **AI-assisted.** The code, tests and documentation were written with
  Claude Code (Anthropic; model Claude Opus 5.5) in a dialogue with the user,
  who set the requirements and priorities and reviewed the results. Commits
  carry a `Co-Authored-By` line.
- **Written for this project.** No third-party source code was copied. All
  libraries are used through their public APIs (see Software dependencies).
  *zotero-arxiv-daily* served as a design reference only (see Related tools).
- **Research first.** Before any code, existing tools were compared:
  Semantic Scholar feeds, Scholar Inbox, ResearchRabbit, Litmaps,
  zotero-arxiv-daily and others. None combined Paperpile, own topics, impact
  weighting, a short reading list, reading-depth tracking and notes, so the
  decision was to build. The milestones ahead are in `ROADMAP.md`.
- **Driven by real data.** Each stage was run against the user's real
  Paperpile export (145 entries) and the live APIs before being finalised.
  Several design decisions came directly from what that revealed
  (see Key algorithms and design decisions), for example:
  - the export had no DOIs at first, so a DOI resolution step was added;
  - Paperpile's `month = "5\xa0" # apr` format needed its own date parser;
  - OpenAlex meters its API, so look-ups are batched by DOI;
  - a full-text search returned off-topic hits;
  - conference abstracts and unknown venues were slipping into the picks.
- **Small, verified steps.** Every feature was developed on a `feat-*`
  branch from `dev`, tested, linted and fast-forwarded into `dev` (CUBI
  conventions, [Development](#13-development)). The web UI was checked
  visually with headless-browser screenshots.

### Architecture

```
              ┌────────────────────────── litresearch ──────────────────────────┐
 Paperpile ─► │ ingest/      parse BibTeX ─► resolve DOIs ─► enrich (metrics,    │
 BibTeX       │              embeddings)                                         │
              │ recommend/   candidates ─► filters ─► score ─► MMR ─► batch      │
 Crossref ◄──►│ sources/     HTTP client (cache, rate limits, retries) + 3 APIs  │
 OpenAlex ◄──►│                                                                  │
 Sem.Scholar◄►│ reading.py   queue, reading log, notes, drop penalty             │
              │ rules.py     locked game rules · profile.py  the game profile    │
              │ game.py      XP, streaks, quests, levels, badges, summaries      │
              │ backup.py    snapshots, progress export/import, restore          │
              │ db.py        SQLite via SQLModel (one file)                      │
              │ cli*.py      Typer commands  ·  web/  FastAPI + Jinja2 UI        │
              └──────────────────────────────────────────────────────────────────┘
```

Layers, from the bottom up:

1. **Storage** (`db.py`): one SQLite file with all papers, embeddings,
   recommendations and personal progress.
2. **Sources** (`sources/`): thin clients for Crossref, OpenAlex and Semantic
   Scholar on top of one shared HTTP client.
3. **Domain logic**: `ingest/`, `recommend/`, `reading.py`, `game.py`,
   `backup.py`. These are plain functions on a database session; the scoring
   functions are pure (no I/O) and therefore easy to test.
4. **Interfaces**: the CLI (`cli.py`, `cli_reading.py`, `cli_data.py`) and the
   web UI (`web/`). Both call the same domain functions, so their behaviour
   is identical.

### Module guide

| Module | Lines | Responsibility |
|---|---:|---|
| `config.py` | ~190 | Pydantic models of `config.yaml`; `n/a` → None; validation (e.g. ≤ 5 papers); `ConfigError` with readable messages |
| `db.py` | ~220 | SQLModel tables; reading levels; embedding storage as float32 bytes; additive migration; durable SQLite connection settings |
| `ingest/bibtex.py` | ~180 | Paperpile BibTeX → `BibEntry`: LaTeX decoding, month/day parsing, DOI normalisation, folder/label split, duplicate keys |
| `ingest/pipeline.py` | ~260 | Library sync, DOI resolution, OpenAlex and Semantic Scholar enrichment; per-source error isolation; `IngestReport`; import history |
| `ingest/check.py` | ~140 | Checks of a Paperpile export (required fields, coverage, duplicates) and export instructions |
| `sources/http.py` | ~170 | JSON client: SQLite response cache with max age, per-host throttling, retries with `Retry-After`/exponential backoff, typed `HttpError` |
| `sources/crossref.py` | ~70 | Title/author/year → DOI with verification (type, title similarity, year, author, prefix rule) |
| `sources/openalex.py` | ~170 | Batched works/sources look-ups, title/abstract search, abstract reconstruction, tolerant parsing |
| `sources/semanticscholar.py` | ~90 | Batched paper look-up (SPECTER2, TLDR, influential citations), recommendations |
| `papers.py` | ~80 | Store OpenAlex works and search hits as papers without overwriting Paperpile metadata; links |
| `search.py` | ~220 | Structured paper search: one query translated into Europe PMC and PubMed syntax, merged results with your status; the same search offline in your library |
| `sources/europepmc.py`, `sources/pubmed.py`, `sources/hits.py` | ~250 | Europe PMC and PubMed clients; a common `Hit` record with merging |
| `recommend/engine.py` | ~330 | Candidate gathering with provenance, filters with funnel counts, embeddings, batch persistence with parameter snapshot |
| `recommend/scoring.py` | ~450 | Eligibility, relevance (kNN without self-matches + topic bonus), impact, timeliness, MMR, evidence tree |
| `recommend/text.py` | ~40 | Dependency-free TF-IDF fallback |
| `reading.py` | ~330 | Paper references (`r1`, `q2`, DOI …), queue operations, reading log, notes, drop penalty |
| `rules.py` | ~500 | `GameRules` (every reward, penalty, quest, level ladder, badge targets), badge catalog, rule fingerprint |
| `profile.py` | ~80 | Start the game profile, lock its rules, auto-start, active rules |
| `profiles.py` | ~370 | Profile folders: create, take over existing data, load saved files, settings overlay, current profile |
| `game.py` | ~620 | Points (always with the profile's rules), anti-farming, streaks, quest, levels, badges and their statistics, weekly summaries, ledger, calendar, bests, hints |
| `backup.py` | ~800 | Atomic writes, SQLite snapshots, rotation, restore, profile files (rules, progress, recommendations, checksum), load, merge, reset, comparison in an in-memory database, automatic backup thread |
| `cli.py`, `cli_reading.py`, `cli_data.py` | ~880 | Typer commands; `run()` turns expected errors into messages |
| `web/app.py` | ~1 300 | FastAPI routes, login, profile and same-origin middleware, welcome screen, celebration tokens, downloads |
| `web/auth.py` | ~80 | scrypt password hashes, HMAC-signed session cookies |
| `web/docs.py` | ~150 | renders `docs/documentation.md` as linked chapter pages for the web UI |
| `web/settings.py` | ~540 | Settings schema (labels, help, ranges), game-rule form, form and topic parsing |
| `web/templates/`, `web/static/` | ~1 800 | Jinja2 pages, CSS (themes, animations), JavaScript (toasts, confetti, rings) |

### Data model

| Table | Content |
|---|---|
| `paper` | library papers, candidates and manually added papers: identifiers (DOI, PMID, citation key, OpenAlex, S2), metadata, metrics, folders/tags |
| `embedding` | `(paper, model)` → float32 vector (SPECTER2, 768 dimensions) |
| `recommendationbatch` / `recommendation` | recommendation lists with funnel and settings snapshot; per paper: rank, category, score, evidence, reason |
| `queueitem` | the reading queue (position 1 = up next) and where each item came from |
| `reading` | current status, rating and notes per paper |
| `statusevent` | every status change, including whether the paper was at the top of the queue |
| `xpevent` | every point earned or lost, with kind and reason; the score is their sum |
| `achievement` | unlocked badges |
| `profile` | the game profile: name, start, a locked copy of the rules, fingerprint |
| `ingestrun` | every import of the Paperpile export: time, source, file hash, counts, check results |

Design choices:

- **Event sourcing for progress.** `statusevent` and `xpevent` are
  append-only logs. Statistics, streaks, quests and the ledger are computed
  from them, so the score is auditable.
- **Locked rules.** The profile stores its own copy of the rules; the config
  only supplies defaults for the next profile.
- **One database per profile.** Profiles never share tables, so they cannot
  mix; switching is just opening another file.
- **Timestamps** are naive local ISO 8601 (CUBI convention).
- **Embeddings are tagged by model**, so vectors from different models are
  never compared.

### Key algorithms and design decisions

#### Ingest

- **Paperpile quirks.**
  - Months are written as `"5\xa0" # apr` (day, non-breaking space,
    month macro) and get their own parser.
  - `keywords` mixes folder paths (containing `/`) with labels.
  - Titles may contain LaTeX braces; decoding uses bibtexparser's
    LaTeX middleware, which relies on pylatexenc.
  - A file that fails to parse aborts the import *before* the database is
    touched, so papers are never wrongly marked as removed.
- **DOI resolution.** Crossref's best hit is not trusted blindly: a
  "Faculty Opinions" record ranked first for one real paper. Candidates must
  - be a real paper type;
  - have a title similarity ≥ 0.9 (`difflib`), or be a ≥ 4-word title prefix
    confirmed by the first author's surname (for titles Paperpile truncated);
  - match the year ±1.
- **Metered OpenAlex.** OpenAlex charges credits: a search costs 10, a
  filter look-up of up to 50 DOIs costs 1. Enrichment is therefore batched,
  and one ingest of 145 papers uses about 6 credits.
- **Failure isolation.** Each source may fail (rate limit, outage, non-JSON
  answer); the failure becomes a warning and the other sources still run.

#### Recommendations

- **Candidates.** OpenAlex title/abstract search sorted by relevance
  (full-text search sorted by citations returned off-topic papers), plus
  Semantic Scholar recommendations. Positives are liked papers and recent
  library papers; negatives are dismissed papers, which closes the feedback
  loop.
- **Relevance.**
  - Cosine similarity of SPECTER2 embeddings, averaged over the k nearest
    library papers.
  - Converted to a percentile rank among candidates. This makes SPECTER2
    and the TF-IDF fallback comparable for papers without an embedding.
  - Blended with a topic-weight bonus.
- **Impact.**
  - Landmark papers use OpenAlex's field- and year-normalised citation
    percentile.
  - Fresh papers cannot have citations yet, so they use the journal's 2-year
    mean citedness (an open impact-factor proxy), log-scaled. This is
    distorted by supplements, e.g. *Cancer Research*, which is why it is
    not used for landmarks.
  - An unknown venue scores low, because preprint servers count as venues
    in OpenAlex.
- **Diversity.** Maximal marginal relevance (Carbonell & Goldstein 1998)
  over the candidates above a relative score floor. The floor was added
  after MMR pulled a weak meeting abstract into a real batch for the sake of
  diversity.
- **Transparency.** Every number is stored as an evidence tree per
  recommendation, plus a funnel and a settings snapshot per batch.

#### Reading and game

- **Short queue.** The queue is short and strictly ordered. Reading the top
  paper first earns the "frog" bonus, a nod to Brian Tracy's "eat that
  frog" productivity advice.
- **Anti-farming.**
  - Base points are paid only up to the deepest level ever reached.
  - Paper bonuses are paid once.
  - The frog bonus is proportional to newly earned base points.
  - A randomised test checks these invariants over long random sessions.
- **Drop penalty.** Putting a paper on the queue and abandoning it unread
  costs XP, wherever it came from, so the queue stays a commitment and is
  not used as a bookmark list.
- **No prescribed depth.** Recommendations are a ranked list; the reader's
  marking decides the points.
- **Locked, configurable rules.** All rewards, the penalty, the quest, the
  level ladder (20 levels) and badge targets (44 badges) form one
  `GameRules` object. Starting a profile freezes a copy in the database with
  a SHA-256-based fingerprint, so changing the config cannot inflate a
  running game and profiles can be compared fairly.
- **Profile files** contain the rules, all progress and all recommendation
  batches, plus a SHA-256 checksum over the content. Loading or merging an
  edited file is refused; merging progress earned under other rules too.
  Comparisons load each file into a throw-away in-memory SQLite database and
  compute the same statistics as the dashboard.
- **Streaks** count weeks rather than days and survive until the end of the
  following week, so they motivate without guilt.

#### Data safety

- Each action is one transaction, and commits use `PRAGMA synchronous=FULL`.
- A 30 s busy timeout lets the CLI and the server write concurrently.
- Snapshots use SQLite's online backup API and are integrity-checked.
- Config and export writes are atomic (temporary file, `fsync`, `os.replace`).
- The portable JSON export merges idempotently by DOI, PMID, citation key or
  title. Details: [Your data and backups](#10-your-data-and-backups).
- Uploaded BibTeX files are checked in a temporary file first; an invalid
  export never replaces the library, and the previous file is kept as `.bak`.

#### Security of the web UI

- **Network access** requires a password, stored as a salted scrypt hash
  (RFC 7914), with an HMAC-SHA256-signed (RFC 2104), HttpOnly, SameSite=Lax
  session cookie whose signature covers the password hash. Changing the
  password therefore logs out every device.
- **Request checks.** POST requests must come from the same origin; redirect
  targets must be local; templates auto-escape everything (tested with
  hostile titles and notes).

### Web UI technology

- Server-rendered HTML with **FastAPI** routes and **Jinja2** templates; forms
  post and redirect, so every action works without JavaScript.
- **No build step and no external assets**: no CDN, web fonts or trackers.
  Icons are emoji and inline SVG; the CSS (light/dark themes, animations) and
  about 200 lines of JavaScript (XP toasts with count-up, a canvas confetti
  burst, level-up and badge modals, progress rings, shortcuts) are
  hand-written.
- Reward feedback travels as a short-lived token in the redirect URL. The
  token is removed from the address bar after display, so a reload does not
  replay it.
- `prefers-reduced-motion` turns all animation off.

### Quality assurance

| Measure | Details |
|---|---|
| Test suite | 19 test modules plus shared fixtures (`conftest.py`), about 360 tests, run with `pytest` |
| Coverage | about 97 % of statements; core logic (scoring, game, reading, config, auth) 100 % |
| Offline by default | `FakeApis` serves recorded real API responses through `httpx.MockTransport`; a synthetic BibTeX fixture reproduces all Paperpile quirks |
| Live tests | `pytest --network` runs ingest and recommendations against the real APIs |
| Robustness | `test_robustness.py`: crashes mid-action, concurrent writers, restarts, broken inputs, hostile content, calendar edges, scale, randomised sessions with invariants |
| Strictness | warnings are errors (catches e.g. unclosed SQLite connections); `ruff` lint and format |
| Manual checks | real-data runs after each milestone; headless-browser screenshots of the UI |

| Test module | Focus |
|---|---|
| `test_config.py`, `test_db.py` | validation, schema, embeddings, migration |
| `test_bibtex.py`, `test_pipeline.py` | parsing quirks, ingest end to end |
| `test_http.py`, `test_sources.py` | cache, retries, throttling; API clients |
| `test_scoring.py`, `test_recommend.py` | scoring maths, evidence, filters, cooldown, funnel |
| `test_reading.py`, `test_game.py` | queue, notes, penalty; XP, streaks, quests, badges, summaries |
| `test_backup.py` | snapshots during writes, rotation, export/import merge, restore |
| `test_profile.py` | rule validation, locking, custom rules, save/load/merge/reset, checksums, comparison, new badges |
| `test_profiles.py` | profile folders, settings overlay, taking over existing data, loading saved files, welcome screen and profile gate, separation of profiles, topic set-up, reminders, merged pages, profile CLI, backups of all profiles |
| `test_paperpile.py` | export checks, import history, upload (check only, import, invalid), library browsing and filters, notices |
| `test_cli.py`, `test_web.py`, `test_auth.py` | interfaces, settings round trip, login |
| `test_robustness.py` | failure scenarios (see above) |
| `test_live.py` | real APIs (opt-in) |

### Software dependencies

Versions are those used during development (Python 3.13.9). All are open
source; their licenses permit use in this project.

#### Runtime (declared in `pyproject.toml`)

| Package | Version | License | Used for |
|---|---|---|---|
| [bibtexparser](https://github.com/sciunto-org/python-bibtexparser) | 2.1.0 | MIT | parsing the Paperpile BibTeX export (v2 API, middlewares for LaTeX decoding and name splitting) |
| [httpx](https://www.python-httpx.org) | 0.28.1 | BSD-3-Clause | HTTP client for all APIs; `MockTransport` in tests |
| [numpy](https://numpy.org) | 2.5.3 | BSD-3-Clause | embedding storage and vector maths (cosine similarity, kNN, MMR, TF-IDF) |
| [pydantic](https://github.com/pydantic/pydantic) | 2.13.5 | MIT | configuration models and validation |
| [PyYAML](https://pyyaml.org/) | 6.0.3 | MIT | reading `config.yaml` |
| [ruamel.yaml](https://sourceforge.net/projects/ruamel-yaml/) | 0.19.1 | MIT | writing `config.yaml` while keeping comments and order |
| [SQLModel](https://github.com/fastapi/sqlmodel) | 0.0.48 | MIT | table models on top of SQLAlchemy and Pydantic |
| [Typer](https://github.com/fastapi/typer) | 0.27.3 | MIT | command line interface |
| [Rich](https://github.com/Textualize/rich) | 15.0.0 | MIT | coloured terminal output |
| [FastAPI](https://github.com/fastapi/fastapi) | 0.142.4 | MIT | web routes, forms, test client |
| [Jinja2](https://jinja.palletsprojects.com/) | 3.1.6 | BSD-3-Clause | HTML templates (auto-escaping) |
| [Python-Markdown](https://python-markdown.github.io/) | 3.11 | BSD-3-Clause | renders this documentation in the web UI (tables, fenced code, table of contents) |
| [python-multipart](https://github.com/Kludex/python-multipart) | 0.0.32 | Apache-2.0 | form parsing for FastAPI |
| [Uvicorn](https://uvicorn.dev/) | 0.54.0 | BSD-3-Clause | ASGI web server |

#### Important indirect dependencies

| Package | Version | License | Role |
|---|---|---|---|
| [SQLAlchemy](https://www.sqlalchemy.org) | 2.1.3 | MIT | database engine under SQLModel |
| [Starlette](https://github.com/Kludex/starlette) | 1.7.0 | BSD-3-Clause | web toolkit under FastAPI (middleware, static files) |
| pydantic-core | 2.46.5 | MIT | validation engine of Pydantic |
| [Click](https://click.palletsprojects.com/) | 8.5.0 | BSD-3-Clause | CLI toolkit under Typer |
| [pylatexenc](https://github.com/phfaist/pylatexenc) | 2.11 | MIT | LaTeX → Unicode for bibtexparser |
| anyio, h11 | 4.15.1, 0.16.0 | MIT | async I/O and HTTP/1.1 for Starlette/Uvicorn/httpx |

#### Development

| Package | Version | License | Used for |
|---|---|---|---|
| [pytest](https://docs.pytest.org/) | 9.1.1 | MIT | tests |
| [pytest-cov](https://github.com/pytest-dev/pytest-cov) | 7.1.0 | MIT | coverage |
| [Ruff](https://docs.astral.sh/ruff) | 0.16.10 | MIT | lint and format |

#### Python standard library (notable uses)

`sqlite3` (database, online backup API, integrity check) · `hashlib.scrypt`,
`hmac`, `secrets` (login) · `difflib` (title similarity) · `tempfile`,
`os.replace`, `os.fsync` (atomic writes) · `threading` (automatic backups,
concurrency tests) · `json`, `csv` (exports) · `socket` (finding the LAN
address without sending packets).

### Data services

All are free; no API key is required and no personal data is sent (see Key algorithms and design decisions).

| Service | Used for | Terms |
|---|---|---|
| [OpenAlex](https://openalex.org) (OurResearch) | metadata, citation counts, FWCI, citation percentiles, topics, venue statistics, topic searches | data CC0; metered free API |
| [Semantic Scholar Academic Graph API](https://www.semanticscholar.org/product/api) (Allen Institute for AI) | SPECTER2 embeddings, TLDRs, influential citations, recommendations | API license agreement (linked from the API page); shared rate limit without a key |
| [Crossref REST API](https://www.crossref.org/documentation/retrieve-metadata/rest-api/) | DOI look-up by title, author and year (during ingest) | open metadata; polite use |
| [Europe PMC REST API](https://europepmc.org/RestfulWebService) (EMBL-EBI) | paper search (title and abstract, authors, journals, years, open access), abstracts, PMC ids, citation counts | free; article texts under their own licences |
| [NCBI E-utilities](https://www.ncbi.nlm.nih.gov/books/NBK25501/) (PubMed) | paper search, summaries by PMID | free; usage policy of 3 requests/s without key |
| [Paperpile](https://paperpile.com) | source of the library (BibTeX auto-export) | no API used |

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
  It inspired the general idea; no code was reused, and litresearch differs in
  input (Paperpile), sources, impact scoring, the reading workflow and the
  game.
- Scholar Inbox (Flicke et al., 2025, arXiv:2504.08385), Semantic Scholar
  Research Feeds, ResearchRabbit and Litmaps were reviewed as existing
  alternatives (see How it was developed).
- "Eat that frog" (Brian Tracy) gave the bonus for reading your planned top
  paper first its name.

### License and citation

litresearch is released under the MIT license with the Commons Clause
([LICENSE](../LICENSE)): no selling it or paid services based mainly on it. To cite
it, use [CITATION.cff](../CITATION.cff); please also cite OpenAlex and
Semantic Scholar/SPECTER2 (see Literature) when you report results based on it.


## 13. Development

### Development cycle

The full guide for contributors is [CONTRIBUTING.md](../CONTRIBUTING.md); in
short (CUBI conventions):

```
issue ─► feat-*/issueNN-* branch from dev ─► pull request into dev (CI green)
      ─► rebase and merge ─► dev ─► fast-forward main ─► tag vX.Y.Z ─► GitHub release
```

- `main`: releases only; `dev` is fast-forwarded into it, with no direct commits.
- `dev`: development branch, kept linear.
- Feature work goes on `feat-<short-name>` (or `issueNN-<short-name>` for
  issue NN), branched from `dev`. Clean up with an interactive rebase, open a
  pull request into `dev`, merge with *rebase and merge* and delete the
  branch. No merge commits, no squash merges.
- **CI** (`.github/workflows/ci.yml`) runs ruff and the offline test suite on
  Python 3.11, 3.12 and 3.13 for every push and pull request to `main` and
  `dev`.
- **Releases**: a version bump is its own commit (`pyproject.toml`,
  `__init__.py`, `CITATION.cff`), with no code changes. Pushing a tag `vX.Y.Z`
  runs `.github/workflows/release.yml`: tests, builds the wheel and source
  archive and publishes a GitHub release with the CHANGELOG section as notes
  (a pre-release for versions such as `0.1.0-alpha.1`). Development states
  carry the `-dev` suffix, e.g. `0.2.0-dev`.
- Issues use the templates in `.github/ISSUE_TEMPLATE/` (bug report, feature
  request); pull requests the checklist in `.github/pull_request_template.md`.
- Hosting: GitHub (public, issues and pull requests). Following the CUBI
  rules, a mirror on GitLab@HHU can be added as a second push target.

```bash
git switch dev && git switch -c feat-scoring
# ... commits ...
git rebase dev                   # keep history linear
git push -u origin feat-scoring  # pull request into dev
```

### Tests

```bash
.venv/bin/pytest                         # offline suite (default)
.venv/bin/pytest --network               # also hit the real APIs
.venv/bin/pytest --cov=litresearch --cov-report=term-missing
```

- The default suite never touches the network. `tests/conftest.py::FakeApis`
  serves Crossref, OpenAlex and Semantic Scholar from recorded responses in
  `tests/data/api_responses.json`, through `httpx.MockTransport`.
- `tests/data/paperpile_sample.bib` is a synthetic export that reproduces every
  Paperpile quirk seen so far: month/day values with non-breaking spaces,
  folder/label keywords, LaTeX, `@BOOK`/`@INCOLLECTION`, undated, PMID-only and
  DOI-in-URL entries. It uses public paper metadata with invented folder names.
  Add new quirks here when you meet them.
- Warnings are turned into errors (`filterwarnings = ["error"]`), which catches
  e.g. unclosed SQLite connections.
- Tests marked `@pytest.mark.network` only run with `--network`.
- Every test runs in its own temporary working directory (autouse fixture
  `isolated` in `conftest.py`), so relative default paths such as
  `data/backups` can never touch your real data, and `serve` never starts the
  real backup thread in tests.
- Never share ORM objects between threads in tests; pass plain ids.

| Test module | Covers |
|---|---|
| `test_bibtex.py` | parser helpers and the sample export |
| `test_http.py` | cache, retries, `Retry-After`, throttling, error handling |
| `test_sources.py` | Crossref match rules, OpenAlex/S2 parsing and batching |
| `test_pipeline.py` | end-to-end ingest, idempotency, removal, adoption, duplicates, source outages |
| `test_scoring.py` | classification, impact/timeliness, kNN, MMR, ranking, TF-IDF fallback |
| `test_recommend.py` | candidate gathering, filters, cooldown, recommendation lists, S2 negatives, outages |
| `test_reading.py` | references, queue order/limits, reading log, notes, ratings |
| `test_game.py` | XP rules, anti-farming, bonuses, streaks, quests, badges, levels, statistics |
| `test_cli.py` | all CLI commands (plain console, fake APIs) |
| `test_web.py` | every page, actions, celebrations, penalties, settings round trip (profile settings, comments kept), same-origin and redirect checks, TSV export |
| `test_auth.py` | password hashing, sessions, login/logout, network serving rules |
| `test_backup.py` | snapshots during writes, rotation, export/import merge (idempotent, notes kept), restore, automatic backups |
| `test_robustness.py` | crashes mid-action, concurrency, restarts, broken inputs, hostile content, calendar edges, scale, randomised sessions with invariants |
| `test_profile.py` | game rules, locking, save/load/merge/reset, checksums, comparison, badges |
| `test_profiles.py` | profile folders and settings, welcome screen, profile gate, separation of profiles, topic set-up, reminders, merged pages, profile CLI |
| `test_paperpile.py` | BibTeX export checks, import history, upload flows, library page, `check-bib` |
| `test_config.py`, `test_db.py` | config validation, status history |
| `test_live.py` | smoke test against the real APIs (`--network`) |

### Lint and format

```bash
.venv/bin/ruff check src tests
.venv/bin/ruff format src tests
```

### Recording new API fixtures

Run a real `litresearch ingest` once, then take the responses you need from
`data/cache/http_cache.sqlite` (table `cache`, JSON in column `body`). Trim them
before committing: shorten embeddings to a few dimensions and drop
affiliations.

Web tests choose a profile first: `conftest.enter_profile(client)` creates one
through the welcome screen (by default taking over the data a fixture
ingested), and `conftest.profile_cfg(config_path)` gives its configuration,
e.g. to open its database.
