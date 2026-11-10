# litrad documentation

**litrad, the Literature Radar Game**, is a personal literature radar built on your reference-manager
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

- **Part I: Using litrad:** [1. Setup](#1-setup) · [2. The weekly loop](#2-the-weekly-loop) · [3. Profiles](#3-profiles) · [4. Web UI](#4-web-ui)
- **Part II: How it works:** [5. How papers are recommended](#5-how-papers-are-recommended) · [6. Gamification](#6-gamification)
- **Part III: Reference:** [7. Commands](#7-commands) · [8. Data sources and privacy](#8-data-sources-and-privacy) · [9. Configuration](#9-configuration) · [10. Your data and backups](#10-your-data-and-backups) · [11. Data formats](#11-data-formats)
- **Part IV: How it is built:** [12. Architecture](#12-architecture) · [13. Implementation and credits](#13-implementation-and-credits) · [14. Development](#14-development)

---

# Part I: Using litrad


## 1. Setup

### 1. Install

Two ways to run litrad:

- **Desktop app (default):** on your own computer, for you alone. Double-click
  it; it opens in your web browser.
- **Server mode:** on a home server, NAS or lab server that is always on,
  reachable from your laptop and phone with a password.

#### Desktop app

Download the file for your system from the **Releases** page of the
litrad repository on GitHub (latest release, *Assets*):

| System | File | Install |
|---|---|---|
| Windows 10/11 (64-bit) | `litrad-<version>-windows-x86_64.exe` | run it; installs for you only, no administrator rights; Start menu entry, optional desktop shortcut |
| macOS (Apple silicon) | `litrad-<version>-macos-arm64.dmg` | open it, drag **litrad** to *Applications* |
| Linux (x86_64 or ARM) | `litrad-<version>-linux-x86_64.AppImage` / `-linux-aarch64.AppImage` | `chmod +x litrad-*.AppImage`, then double-click or run it |

The builds are not signed (signing costs money), so the system warns the
first time:

- **Windows** SmartScreen: *Windows protected your PC* → **More info** →
  **Run anyway**.
- **macOS**: right-click (or Ctrl-click) litrad in *Applications* →
  **Open** → **Open**. On newer macOS versions: try to open it once, then
  *System Settings → Privacy & Security* → **Open Anyway**.
- **Linux**: make the AppImage executable (above). If it says FUSE is
  missing, install `libfuse2` (Debian/Ubuntu: `sudo apt install libfuse2t64`)
  or run it with `--appimage-extract-and-run`.

**First start:** litrad creates your configuration and data folder,
starts on this computer only (`http://127.0.0.1:47290/`, another free port
if that one is taken) and opens it in your default browser, where you create
your profile ([step 5](#5-create-your-profile-and-import-your-library)).
Nothing is reachable from other computers. Starting it again while it runs
just opens the browser on the running one.

**Quit:** **⏻ Quit** at the bottom of the sidebar (or on the welcome page).
Closing the browser tab does not stop litrad; started from a terminal,
Ctrl+C also quits. On macOS litrad has no Dock icon: it runs in the
background until you quit it; if it is already running, open
`http://127.0.0.1:47290/` (or your bookmark) instead of starting it again.

**Where your data lives** (`litrad where` prints it):

| System | Configuration | Data (profiles, backups, cache, log) |
|---|---|---|
| Windows | `%LOCALAPPDATA%\litrad\config.yaml` | `%LOCALAPPDATA%\litrad\` |
| macOS | `~/Library/Application Support/litrad/config.yaml` | `~/Library/Application Support/litrad/` |
| Linux | `~/.config/litrad/config.yaml` | `~/.local/share/litrad/` |

The desktop app writes a log to `logs/app.log` in the data folder; if it
cannot start, it opens a page in the browser that names the problem and the
log file.

**Update:** download and install the new version over the old one (quit
litrad first; the Windows installer closes it). Your data stays; the
CHANGELOG says when a version changes the data format. **Uninstall:**
Windows *Settings → Apps*, macOS move the app to the Bin, Linux delete the
AppImage. Your data folder is kept; delete it yourself if you no longer need
it.

With a Python installation, `litrad app` (or `litrad-app`) starts
the same desktop mode ([commands](#7-commands)).

#### Server mode

Three ways, all for a machine that is always on. litrad listens on the
network only with a password ([Web UI](#using-it-from-your-laptop-local-network)).

**Docker** (the image runs on ARM and x86). The image is
`ghcr.io/<owner>/litrad` (`<owner>`: the GitHub account of the repository):

```bash
docker run --rm -it -v litrad:/data ghcr.io/<owner>/litrad litrad set-password   # once
docker run -d --name litrad --restart unless-stopped -p 8080:8080 \
    -v litrad:/data ghcr.io/<owner>/litrad
```

Open `http://<host>:8080` and log in. The server listens on all networks of
the container and refuses to start without a password, so set it first; for a
script or a Docker secret use `set-password --password-file FILE` (first line
of the file) instead of the prompt. Everything lives in the volume `/data`
(configuration `/data/config.yaml`, created on first start, profiles,
backups in `/data/backups`); to edit the file, find the volume
with `docker volume inspect litrad`. **Update:** run
`docker exec litrad litrad backup`, then `docker pull` the image,
`docker rm -f litrad` and start it again with the same volume. The port is
plain HTTP: keep it in your home network or behind a VPN.

**As a package** (`pipx install litrad`, or the wheel of a GitHub release:
`pip install litrad-*.whl`; Python ≥ 3.11): no git folder needed, run it
from any folder. The first command creates your configuration and data in
your per-user folders:

| System | Configuration | Data (profiles, backups, cache) |
|---|---|---|
| Linux | `~/.config/litrad/config.yaml` | `~/.local/share/litrad/` |
| macOS | `~/Library/Application Support/litrad/config.yaml` | `~/Library/Application Support/litrad/` |
| Windows | `%LOCALAPPDATA%\litrad\config.yaml` | `%LOCALAPPDATA%\litrad\` |

**From git with the installer.** Requires Linux, Python ≥ 3.11, `git` and an
internet connection for the installation. On Debian or Ubuntu:
`sudo apt install git python3-venv`.

```bash
git clone <repository-url> litrad && cd litrad
python3 install.py                # about 1–3 minutes; offers to start the web UI at the end
.venv/bin/litrad serve       # later: start the web UI, open the address it prints
```

`install.py` checks the system, creates `.venv`, installs litrad,
creates `config/config.yaml` from the example (an existing one is never
overwritten) and checks that litrad starts. If something is missing it
stops with the fix (e.g. the `apt` command); it never says "Done" over a
broken installation. Run litrad commands from the litrad folder:
there `config/config.yaml` is found, and `data/` lives next to `config/`.

| Option | Does |
|---|---|
| `--service` | start the web UI automatically (systemd user service), on the network set as `web.host`, without asking |
| `--remove-service` | stop starting it automatically (data stays) |
| `--repair` | reinstall all packages without pip's cache, e.g. after an interrupted download ("litrad does not start") |
| `--dev` | add test and lint tools |

- **Update:** `litrad backup`, then `git pull && python3 install.py`.
  `config/` and `data/` are kept; the CHANGELOG says when a version changes
  the data format.
- **Without the installer:** `python3 -m venv .venv && .venv/bin/pip install -e .`
  and `cp config/config.example.yaml config/config.yaml`.

`litrad where` shows which configuration and data folders are in use
and why ([lookup order](#9-configuration)).

### 2. Export your library from your reference manager

litrad reads your library from an export file, so it works with any
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
  `litrad check-bib FILE`.

### 3. Keep the export up to date (optional)

Update a library from its file at any time (*Update from file* on its page,
or `litrad ingest` for all libraries). For a file your reference manager
keeps up to date, e.g. Paperpile's auto-export to Google Drive, fetch it
with [rclone](https://rclone.org/drive/):

```bash
rclone config                     # once: create a remote called "gdrive"
rclone copyto "gdrive:<path in Drive>/library.bib" ~/litrad-exports/paperpile.bib
litrad library import ~/litrad-exports/paperpile.bib --new "Paperpile"   # once
litrad ingest                # then e.g. daily from cron
```

Watching the file and re-importing automatically is planned (roadmap 1.1).

### 4. Configure

`config/config.yaml` (or your per-user config, see `litrad where`)
holds the machine settings (APIs, web server, backups) and the defaults for
new profiles ([Configuration](#9-configuration)). In a git folder it and
`data/` are git-ignored, so API keys stay local. Recommended, both free:

- a **Semantic Scholar API key** (<https://www.semanticscholar.org/product/api>):
  without it all anonymous users share one rate limit and requests often
  fail with HTTP 429;
- a **contact e-mail** (`api.contact_email`) for the Crossref and OpenAlex
  "polite pools".

### 5. Create your profile and import your library

In the web UI: `litrad serve`, open the printed address, create a
profile, then **Library → Import**: choose the file, name the new library and
press *Check & import*. On the command line:

```bash
litrad profile create "Kai"      # needed before any other command; game rules are locked from now on
litrad library import library.bib --new "Paperpile" --description "everything"
litrad status
```

Every command needs a profile. Without one the command line says *There is
no profile yet. Create one with 'litrad profile create NAME' or in the web
UI*. The import report lists entries added, updated and removed, DOIs taken from
the file or found via Crossref/PMID, entries **not found anywhere** (with
their citation keys) and how many papers got OpenAlex metrics and SPECTER2
embeddings. Imports can be repeated safely; API answers are cached, so
repeated runs make almost no network requests. The first library of a
profile reads from `library.bib` in the profile folder unless the profile's
`settings.yaml` sets `paths.bibtex` to another file.

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
litrad recommend          # latest list; no API requests
litrad recommend --new    # a new list (about a minute), e.g. after changing topics
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
  `0 7 * * 1  cd /path/to/litrad-tool && .venv/bin/litrad ingest && .venv/bin/litrad recommend --new > data/this-week.txt`

### 2. Fill your reading queue

```bash
litrad queue add r1              # a recommendation
litrad queue add r3 --top        # ... straight to position 1
litrad queue add Barkley2022-ri  # a paper from your libraries (citation key)
litrad queue add 10.1038/s41586-024-07894-z   # any DOI
litrad queue add pmid:35931863   # any PubMed ID, or a PMC id: PMC5812436
litrad queue add --title "Preprint from the lab meeting" --url https://...
litrad queue                     # show it
litrad queue move q4 1           # reorder
litrad queue drop q3             # remove (costs XP if not at least skimmed)
```

- Web: one box (Reading queue, Find & add, the example lists) takes a DOI,
  `pmid:N`, `PMCn`, a citation key or title words. Unknown DOIs and PubMed
  IDs are fetched (OpenAlex, Europe PMC, PubMed). Title words that match one
  of your papers add it; several matches show them, no match searches Europe
  PMC and PubMed for the title in **Find & add**, where you pick a result or
  add the title **without DOI** (with an optional link).
- When the queue is full (`queue.max_size`, default 5) nothing can be added
  until you read or drop something.
- A paper that leaves the queue before you at least skimmed it (removed or
  marked not relevant) costs the penalty of your profile (default 15 XP),
  wherever it came from. Dismissing a recommendation you never queued is
  free. The queue's Remove button shows the cost and asks before removing.

#### Find papers in Europe PMC and PubMed

Web: **Library → Find & add**. CLI:

```bash
litrad search "circulating tumor DNA" --journal "Nature Medicine" --from 2023
litrad search --author "Swanton C; Turajlic S" --title evolution --sort cited
litrad search "ctDNA" --oa --source pubmed --limit 20     # free full texts, PubMed only
litrad search "ctDNA" --author "Swanton C" --add 2        # put result 2 on the queue
litrad search clonal -s library                          # only your own libraries
```

Keywords are searched in title and abstract; `--title`, `--author`,
`--journal`, `--from`/`--to` and `--oa` narrow it; separate several authors or
journals with `;`; keywords accept `AND`, `OR` and quotes. Only what you
type is sent. Both databases are searched (those enabled in `sources`)
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
| `clonal melanoma` | words from the title (must match exactly one paper; in the web UI, none or several matches open Find & add to pick from) |

### 3. Read

```bash
litrad next                      # top paper: abstract, TL;DR, link
litrad read q1 thorough --rating 5 --note "Key figure 3; method usable for our SCC data"
litrad read q2 abstract          # an abstract read keeps it on the queue
litrad note q1 "Follow-up: check their supplementary table S4"
litrad dismiss r4                # not relevant; teaches the recommender
litrad show Barkley2022-ri       # details, status, all notes
litrad history
```

Reading levels: `abstract` → `skimmed` → `read` → `thorough`. Reaching
`skimmed` or deeper, or dismissing, removes the paper from the queue. A note
given with `read`/`note` (or in the *Log reading* dialog) is added to the
paper's note with an ISO 8601 timestamp. In the web UI, log a reading on the
queue or the dashboard (level, star rating, note). Notes are Markdown files
you can also edit in Obsidian: [Notes and Obsidian](#notes-and-obsidian).

#### Notes and Obsidian

Each paper's notes are **one Markdown file** (UTF-8, LF line endings). Edit
it on the paper page (**✏️ Edit**, **👁 Preview**, **💾 Save**) or in any
editor; litrad picks up the change.

**Where the files are.** `<profile folder>/notes/`, or, with
`paths.obsidian_vault` set (in `config.yaml` or the profile's
`settings.yaml`), `<vault>/<paths.notes_subfolder>/<profile id>/` (default
subfolder `litrad`; the profile id is the name of the profile folder).
Obsidian then edits the same files; nothing is copied. Every profile has
a notes folder of its own, also when all profiles use one vault: a hidden
file `.litrad-profile` in the folder names the profile's database, and a
folder that belongs to another existing profile (e.g. of a second
installation) is refused with a message. Subfolders inside the notes
folder are allowed (move or rename files freely).
`litrad notes path REF` prints a paper's file; Settings → Data &
backups shows the folder.

**File name.** The paper's citation key (from its first library) with
everything but `a-z A-Z 0-9 - _ .` replaced by `-` and accents removed
(`Müller2020:ab` → `Muller2020-ab.md`); without a key `doi-<doi>.md`, else
`paper-<id>.md`. A name taken by another paper gets `-2`, `-3` …

**Format `litrad-note/1`.** YAML front matter, then your note:

```markdown
---
litrad_id: 42
title: Clonal evolution in melanoma
authors:
  - Smith, Jane
  - Doe, A
year: 2020
journal: Nature
doi: 10.1038/xyz
pmid: '31234567'
citekey: Smith2020-ab
status: thorough
rating: 5
tags:
  - tumour-evolution
updated: '2026-11-09T08:30:00'
format: litrad-note/1
---

## Summary
Key figure 3 shows … see also [[Barkley2022-ri]].
```

| Key | Meaning |
|---|---|
| `litrad_id` | the paper's id in this profile; finds the file again after a rename (checked against DOI and PMID) |
| `title`, `authors`, `year`, `journal`, `doi`, `pmid`, `citekey` | paper metadata; empty values stay empty (`doi:`) |
| `status` | deepest reading level reached (`abstract` … `thorough`), `dismissed`, or empty |
| `rating` | 1–5 stars or empty |
| `tags` | your library labels (spaces → `-`); tags you add are kept, litrad never removes a tag |
| `updated` | last time litrad wrote the file (ISO 8601) |
| `format` | `litrad-note/1` |

litrad rewrites only these keys and only when a value changed; keys,
comments and order you add stay. The **body** (everything after the front
matter) is written only when you save the note or add a note while logging a
reading (then your entry goes below the file's current text). Windows line
endings are converted to LF. Invalid front matter is left as it is and
reported. Changing `status` or `rating` in the file has no effect: set them
in litrad.

**Links.** `[[citation-key]]`, `[[file-name]]`, `[[key|text]]` link to the
paper's page in the preview (Obsidian resolves them by file name). Unknown
keys are shown as text with `(?)`. Raw HTML is shown as text; images are
shown as links (nothing is loaded from other sites).

**Template.** A new note starts from `note_template.md` in the notes
folder, if there is one, else from a short built-in outline (`## Summary`,
`## Notes`). Placeholders: `{title}`, `{authors}` (separated by `;`),
`{year}`, `{journal}`, `{doi}`, `{pmid}`, `{citekey}`, `{date}` (today,
`YYYY-MM-DD`); other braces stay as they are.

**Sync and conflicts.** The file is the source of truth; the database keeps a
copy for search, Read papers, XP and backups. The copy is refreshed when you
open a paper, Read papers, Settings → **🔄 Sync notes**, `notes sync`, and
before backups and exports. litrad remembers the version both had at
the last sync:

- only the file changed → its text is taken over (notes bonus if it is now
  long enough, once per paper);
- only litrad's copy changed (log dialog, progress import, restore) →
  the file is updated;
- both changed → **conflict**: nothing is overwritten. The paper page shows
  both versions side by side: **Keep file**, **Keep litrad's** (or
  **Keep mine** after an edit), or edit the merged text and **Save merged**.
- The editor remembers the version it was opened from; if the file changed
  meanwhile (e.g. in Obsidian), saving shows the same conflict view.

A missing file is written again from the copy; a renamed or moved file is
found by its `litrad_id`. A file whose text differs from litrad's copy is
a conflict, never overwritten. Nothing is deleted automatically.

```bash
litrad notes sync          # import edits, write missing files, list conflicts and orphans
litrad notes check         # the same as a report only; exit code 1 on conflicts or problems
litrad notes path q1       # the file of a paper
```

*Orphans* are files that match no paper (by `litrad_id`, DOI, PMID,
citation key or file name); *waiting* files belong to a paper you have not
logged yet: log a reading and they are linked.

#### Read papers

Web: **Reading → Read papers** (`/read`) lists every paper you marked, one
row each: deepest depth reached (a paper read and later marked not relevant
stays *read*), dates of the last and the first status change, rating, the
first 120 characters of your notes, the XP it earned and where it came from
(recommendation, library, added by you). Sort by last read (default, newest
first), first read (oldest first), depth, rating, XP or title. Filter by depth,
minimum rating, period (last read), library and *Has notes*; the search
looks in title, authors and notes, ignoring case (umlauts included).
Papers marked not relevant are hidden unless you tick *Include not
relevant*. 50 per page; **Export TSV** writes all rows matching the filters,
not only the page.

```bash
litrad read-papers --depth thorough --rating 4 --since 2026-01-01
litrad read-papers --search "clonal" --library Paperpile --tsv clonal.tsv
```

TSV columns: title, authors, year, journal, doi, pmid, deepest depth, first
read, last read, rating, notes, xp, source. Tabs and newlines in a cell are
written as `\t` and `\n`, empty values as `n/a`.

### 4. Check your score

Web: **Progress & XP** and the dashboard. CLI: `litrad score` shows
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
   activity) and offers *Create a new profile* and *Load a saved profile*
   (a `.zip` archive).
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
litrad profile list                     # * marks the current profile
litrad profile create "Grace" --yes     # rules from config.yaml, locked
litrad profile use grace
litrad --profile Kai queue              # one command for another profile
litrad profile save                    # archive in backup.dir
litrad profile load saved.zip --name "Kai (laptop)"
```

Without any profile every command stops with *There is no profile yet*.
Saving, loading and comparing profiles: [Gamification](#saving-loading-and-comparing-profiles);
where the files live: [Your data and backups](#10-your-data-and-backups).


## 4. Web UI

```bash
litrad serve                    # asks where to serve; Enter keeps web.host
litrad serve --network wlan0    # no question: only the WLAN (or eth0, all, local, an IP)
litrad serve --no-choose        # no question: web.host / web.port from the config
litrad serve --list-networks    # the networks of this computer
```

This chapter is about **server mode**. The [desktop app](#desktop-app)
(`litrad app`) needs none of it: it is always local and opens the
browser itself.

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

1. Set a password once: `litrad set-password` (stored only as a salted
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
- Only for yourself on one computer: the [desktop app](#desktop-app).

### Using it on your phone

litrad can be installed on the phone's home screen as an app (server
mode). The phone only shows pages from your server; nothing is stored on it
except the icon, style files and a small "You are offline" page, never your
papers or ratings.

**At home (same Wi-Fi).**

1. Serve on the network with a password (see above), e.g.
   `litrad set-password` once, then `litrad serve`.
2. Open the printed address (`http://<server-ip>:8080/`) in the phone's
   browser and log in.
3. Browser menu, **Add to Home Screen** (Safari: Share, then **Add to Home
   Screen**; Chrome: menu, **Add to Home screen** or **Install app**).

Browsers only allow the full installation and the offline page on HTTPS (or
`localhost`). Over plain `http://` the result is a home-screen shortcut that
opens litrad in the browser, without the offline page.

**From anywhere, with HTTPS (Tailscale).**

1. Install Tailscale on the server and on the phone and sign in with the same
   account; in the Tailscale admin console enable MagicDNS and HTTPS
   certificates.
2. On the server, with litrad running locally (`litrad serve`, the
   default address `127.0.0.1:8080`), run `tailscale serve --bg 8080`. It
   prints an address like `https://<machine>.<tailnet>.ts.net`; only your
   own devices can reach it.
3. Open that address on the phone, log in, and add it to the home screen as
   above.

A password is required: without one litrad only answers IP addresses
and the computer's own names and answers the `*.ts.net` name with "Unknown
host name" (HTTP 421). With a password set, any host name is accepted and the
login protects your data. Never open the port to the internet directly.


### Pages

| Menu | Page | What you can do |
|---|---|---|
| | 👋 **Welcome** | choose, create or load a profile |
| 🏠 | **Dashboard** | the up-next paper with one-click logging, weekly quest, hints, a reminder when recommendations are due, latest recommendations, queue, recent XP, **weekly report cards** |
| Reading | 📚 **Reading queue** | log a reading (level, rating, note), reorder, remove; add papers by DOI, PubMed ID, citation key or title (one box) |
| Reading | ✅ **Read papers** | every paper you marked with depth, dates, rating, notes snippet, XP and source; sort, filter, search, **Export TSV** ([details](#read-papers)) |
| Reading | ✨ **Recommendations** | set up topics (first visit); **Get recommendations**; the ranked list with score bars, an **evidence tree** per paper, the filter **funnel**, the settings used and earlier lists; queue or mark not relevant; **How recommendations are made** with your current values |
| Progress | 🏆 **Progress & XP** | level, streak, Reading Impact Factor, discipline and depth, XP per week, reading calendar, personal bests, badges, career ladder, the **XP ledger** (paginated; filter, totals, TSV export) and the locked rules |
| Library | 🗂️ **Libraries** | all libraries (papers, read, last update), plus *Reading queue* and *Recommended*; per library: papers (search, folder/label and status filters, 50 per page), **Export**, **Update from file**, edit, delete, import history |
| Library | ⬆️ **Import** | import a BibTeX, RIS or CSL-JSON file into a new or an existing library; *Check only* first if you like |
| Library | 🔍 **Find & add** | search Europe PMC, PubMed or your own library and queue a result ([details](#find-papers-in-europe-pmc-and-pubmed)); add any paper by DOI, PubMed ID or title |
| | ⚙️ **Settings** | topics, excluded terms, every recommendation and queue setting; data & backups |
| | 📖 **Documentation** | this documentation |
| (chip) | 🎮 **Profile** | locked rules and fingerprint; save, compare, export everything; switch or create a profile |
| | 📄 **Paper** | abstract, TL;DR, metrics, notes (Markdown editor with preview, [conflicts](#notes-and-obsidian)), status, every recommendation of it with its evidence tree, XP earned |

- A new profile's dashboard shows a five-step **Getting started** checklist:
  import a library, set topics, get recommendations, queue a paper, log a
  reading.
- Weekly reports on the dashboard are paged, 4 weeks per page (newer/older
  links), back to the first week with activity.
- Earlier recommendation sets are grouped by month.
- Library search results say which ones are shown ("Showing 51–100 · page 2
  of 12"). Deleting a library asks once (type its name); its papers stay in
  your other libraries, queue and reading history.
- Compare lists your saved profile archives with dates (newest first); you
  can also upload a `.zip`.
- Unknown pages and invalid values show an error page with links back; form
  errors name the field and its limit.
- On phones the menu is a sideways-scrolling bar at the top; switching
  profile and logging out are on the Profile page.
- Keyboard shortcuts outside text fields: `?` opens the shortcut help (also
  the "⌨️ Shortcuts" button in the sidebar), `n` next paper in the queue,
  `r` recommendations, `p` progress; `Esc` closes dialogs.
- Icon links: → opens the page, ⚙️ the settings, ? the matching
  documentation section; hovering shows what they do.

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
| **Export** | library page, `library export NAME [FILE] --format bibtex\|ris [--status]` | BibTeX (Paperpile, Zotero, Mendeley, JabRef) or RIS (EndNote, Citavi); *with reading status* adds keywords such as `litrad-read`, `litrad-rating-4` (labels in your reference manager) |
| 🎯 **Used for recommendations** | Libraries page (checkboxes, *Save*), `library use NAME… [--off]` | which libraries form "your library": the similarity score and the pool of the positive-example rules `recent`, `impact`, `fwci`, `cited` (not `liked`, `read` or papers you add). With none checked, the similarity is 0 for every candidate; the Libraries page and the recommendation warnings say so |

Every file is checked first; an invalid file changes nothing
([what an export must contain](#what-an-export-must-contain)). Two libraries
are kept by litrad: **Recommended** (a recommendation joins it when you
queue it, and stays after reading; remove with ✖) and **Reading queue**
(always exactly the current queue; exportable).

**Positive examples** and **Negative examples** are the papers sent to
Semantic Scholar's recommendations ([how they are chosen](#1-candidates)).
litrad regenerates them before every list and whenever you open them;
each paper shows its origin (*added by you* or *rule: …*, also exported as a
label). On their pages you can **remove** a paper (the rules then skip it;
*Removed by you* → **Restore**), **add a paper** (DOI, `pmid:N`, `PMCn`,
citation key or title words), choose the **rules**, their order and the
**number of examples** (*Save rules*, saved in the profile's
settings), and **Undo my changes**. Every paper page has *Use as positive /
negative example* (👍 Positive / 👎 Negative) and *Not an example*. These two lists never count as your
library, cannot be deleted or imported into, and are not saved in profile
files (your choices are, and the lists are rebuilt from them).

### Settings

Settings belong to the profile and are saved to its `settings.yaml`
(`config.yaml` is not changed). Comments and order are kept, the previous
version is kept as `settings.yaml.bak`, and invalid values (e.g. more than 5
papers, all weights 0, infinite or NaN numbers) are rejected with a message,
leaving the file unchanged. Paths, API keys, embeddings, LLM and web settings
are only editable in the file.

**Data & backups** shows where the data lives, runs **Back up now**, syncs the
notes and offers **Download everything (zip)** (`/profile/export-all`), the
same archive as `litrad export-all`.


---

# Part II: How it works


## 5. How papers are recommended

Every rule that decides which papers you see. Each number is a setting
(`selection.…`) of your profile, editable under **Settings**. The
Recommendations page shows the same rules with your current values (**How
recommendations are made**), and each recommendation has an **evidence
tree** with the actual numbers. Code: `src/litrad/recommend/`
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
`litrad examples`) and are built in this order, without duplicates,
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

1. **Create** a profile (welcome screen, `litrad profile create`) and
   choose every reward, the penalty, the level ladder and badge targets; the
   form is pre-filled from the `game` section of `config.yaml`.
2. The rules are copied into the profile's database and **locked**. Their
   **fingerprint** (a short hash of the rule set) is on the profile page. All
   points use these stored rules; editing the config cannot inflate them.
   The fingerprint covers every rule and badge of 1.0. A rule or badge
   added in a later 1.x version enters it only when a profile sets it to a
   non-default value, so fingerprints stay stable across 1.x and profiles
   stay comparable and mergeable.
3. **Different rules** need another profile; the old one stays and you can
   switch back at any time.
4. **One rules format.** Every profile stores `"rules_format": 1`: the
   defaults below (60 levels, 129 badges, `backlog_share`).

### Rules you can set (defaults)

| Rule | Default | Meaning |
|---|---|---|
| `xp_abstract` / `xp_skimmed` / `xp_read` / `xp_thorough` | 5 / 15 / 40 / 80 | base points for the depth **you** choose; deeper may never pay less |
| `backlog_share` | 1.0 | 🕰️ share of the base points for a paper you **read earlier**, before litrad ([below](#starting-with-papers-you-already-read)) |
| `cheese_bonus` | 0.5 | 🧀 *Got the cheese: read your top paper first*: +50 % of the base points when the paper was **q1** (up next) and read at least skimmed (XP kind `cheese`) |
| `streak_step` / `streak_max_steps` | 0.05 / 10 | 🔥 +5 % per consecutive reading week, at most +50 % ("next reads earn ×N") |
| `classic_percentile` / `classic_xp` | 99 / 20 | 🏛️ landmark (top 1 % cited) read at least at "read" depth |
| `hot_days` / `hot_xp` | 30 / 10 | 📰 read within 30 days of publication |
| `recommendation_xp` | 15 | 🎯 read (≥ skimmed) a paper from your recommendations; it counts only if it was recommended before you first read it |
| `rating_xp` | 2 | ⭐ first rating of a paper |
| `reflection_chars` / `reflection_xp` | 140 / 10 | ✍️ notes on a paper reach 140 characters |
| `inbox_zero_xp` | 10 | 📭 the queue becomes empty because you *read* its last paper (once per day) |
| `weekly_detail_goal` / `weekly_skim_goal` / `quest_xp` | 1 / 1 / 50 | 🗓️ weekly quest: 1 paper at least "read" + 1 more at least skimmed |
| `badge_xp` | 25 | 🏅 per unlocked badge |
| `drop_penalty` | 15 | ⚠️ a queued paper (from anywhere) leaves the queue, removed or marked not relevant, before you at least skimmed it |
| `levels` | 60 levels | the ladder below |
| `badge_targets` | see below | how much each badge needs |

Fairness rules that always apply:

- **Only a deeper level counts.** A reading counts only when the paper
  reaches a level it never had before. Upgrading pays the difference;
  logging a paper again at the same or a shallower level earns nothing and
  counts for no quest, streak, report card, badge or calendar, even if the
  paper was recommended again.
- Paper bonuses (landmark, fresh, recommendation, rating, notes) are paid
  once per paper; the reading bonuses only with a step to a deeper level. The
  cheese bonus is a share of *newly* earned base points.
- Dismissing a recommendation you never queued is free.
- Penalties can lower the total and the level, but never below level 1.

### Starting with papers you already read

Mark the papers you read before using litrad as **read earlier**: the
🕰️ checkbox (and an optional date) in the *Log reading* dialog,
`litrad read REF LEVEL --earlier [--date YYYY-MM-DD]`, or on a library
page tick the papers and use *🕰️ Mark as read earlier* with one depth
(papers already read at least that deeply are skipped).

- They earn the base points of their depth times `backlog_share` (default:
  all of them) and count for papers read, depth, topics, landmark papers,
  library papers, badges of counts and levels: your experience shows from
  day one.
- They count for nothing that measures *when* or *how* you read here:
  streaks, weekly quests, report cards, the calendar, pace and time badges
  (marathon, binge week, night owl, early riser, weekend), the cheese,
  fresh-paper, recommendation and inbox-zero bonuses, the Reading Impact
  Factor and the best week.
- Reading such a paper again more deeply pays the difference as usual; the
  same depth again earns nothing (also when it is recommended again).
- The history shows them as *read earlier* (with the date, if given).

With the defaults, a backlog of about 35 papers read in detail and 150
skimmed lands at level 21 (🔬 Postdoc) with about 40 badges of counts; the
next level is a few weeks of normal reading away, and the badges of time and
the higher tiers are still ahead.

### Streaks, weekly quest and metrics

- A **reading week** (Monday to Sunday) has at least one paper that reached
  skimmed or a deeper level for the first time. The **weekly quest** and
  the report card count each paper's new level in that week. The **streak** counts consecutive reading weeks and stays alive
  through the following week, so it does not reset on Monday morning.
- **Weekly grade:** S = quest complete including a thorough read; A = quest
  complete; B = at least half the quest; C = something read; - = rest week
  (also the tooltip of each grade badge).
- **Reading Impact Factor**: average XP per week over the last four weeks.
- **Discipline**: share of papers read as the top paper of the queue first (*cheeses*).
- **Depth**: share of papers read at least at "read" depth.
- **Queue nudge**: a warning when the top paper has waited 14 days or more.

### Levels (default ladder)

| Level | Title | XP | Level | Title | XP | Level | Title | XP |
|---|---|---|---|---|---|---|---|---|
| 1 | 🎓 Curious Undergrad | 0 | 21 | 🔬 Postdoc | 5 000 | 41 | 🖋️ Review Editor | 18 250 |
| 2 | ✏️ First-Year Student | 15 | 22 | 🔎 Peer Reviewer | 5 500 | 42 | 📝 Associate Editor | 19 100 |
| 3 | 📘 Bachelor Student | 55 | 23 | 🏅 Fellowship Holder | 6 000 | 43 | 🏢 Institute Director | 19 900 |
| 4 | 👥 Study Group Member | 125 | 24 | 🧬 Senior Postdoc | 6 550 | 44 | 🦉 Dean | 20 800 |
| 5 | 🥼 Lab Intern | 220 | 25 | ✍️ Grant Writer | 7 100 | 45 | 📰 Editor-in-Chief | 21 650 |
| 6 | 🧪 Lab Rotation Student | 340 | 26 | 🧑‍🔬 Research Fellow | 7 650 | 46 | 🏺 Academy Member | 22 550 |
| 7 | 🧑‍🏫 Tutor | 485 | 27 | ✈️ Visiting Scholar | 8 250 | 47 | 💫 Distinguished Professor | 23 400 |
| 8 | 🎉 Bachelor Graduate | 655 | 28 | 🎙️ Lecturer | 8 850 | 48 | 🎖️ Honorary Doctor | 24 350 |
| 9 | 📗 Master's Student | 855 | 29 | 🌱 Junior Group Leader | 9 500 | 49 | 🗝️ Provost | 25 250 |
| 10 | 🗂️ Student Assistant | 1 070 | 30 | 🪴 Assistant Professor | 10 100 | 50 | 🏰 University President | 26 150 |
| 11 | 🗣️ Seminar Speaker | 1 320 | 31 | 👩‍🔬 Group Leader | 10 750 | 51 | 👑 Academy President | 27 100 |
| 12 | 🎊 Master's Graduate | 1 590 | 32 | 🤝 Mentor | 11 450 | 52 | 🧑‍⚖️ Science Advisor | 28 050 |
| 13 | 🧫 Research Assistant | 1 880 | 33 | 🧭 Principal Investigator | 12 150 | 53 | 🌿 Lifetime Fellow | 29 000 |
| 14 | 📚 PhD Candidate | 2 190 | 34 | 🏫 Associate Professor | 12 850 | 54 | 🥇 Prize Winner | 30 000 |
| 15 | ☕ Journal Club Regular | 2 530 | 35 | 📣 Invited Speaker | 13 600 | 55 | 🏵️ Medalist | 30 950 |
| 16 | 📊 Lab Meeting Presenter | 2 890 | 36 | 🌟 Keynote Speaker | 14 300 | 56 | 🌠 Laureate | 31 950 |
| 17 | 🪧 Poster Presenter | 3 270 | 37 | 📋 Grant Panelist | 15 050 | 57 | ✨ Living Legend | 32 950 |
| 18 | 🎤 Conference Speaker | 3 680 | 38 | 🎩 Professor | 15 850 | 58 | 📖 Textbook Author | 33 950 |
| 19 | 🛡️ Thesis Defender | 4 100 | 39 | 🕸️ Consortium Lead | 16 650 | 59 | 🚀 Field Founder | 35 000 |
| 20 | 📜 Doctor of Philosophy | 4 550 | 40 | 🏛️ Department Head | 17 450 | 60 | 🏆 Hallmarks Author | 36 000 |

A typical week (quest done, one thorough read from the top of the queue, a
few bonuses) is worth about 200–300 XP. The steps grow slowly from 15 to
about 1 000 XP, so a level-up comes in the first week and, for a regular
reader, every week or two; the top takes about 2.4 years of
steady reading. The ladder was fitted with `tools/simulate_game.py`, which
plays typical readers on the real game code ([Balancing](#balancing)).

### Badges (default targets)

"Read" means skimmed or deeper, reached for the first time (re-logging a
paper counts once); "looked at" includes abstracts; "fully" means at least
"read".

| Group | Badge | Requirement | Code |
|---|---|---|---|
| Reading | 🌱 First Steps | Read your first paper (skimmed or deeper) | `first_read` |
| Reading | 🔆 Warm-Up | Read 3 papers | `warm_up` |
| Reading | 🔖 Reading List | Read 5 papers | `reading_list` |
| Reading | 📄 Page Turner | Read 10 papers | `page_turner` |
| Reading | 📑 Regular Reader | Read 15 papers | `regular` |
| Reading | 🐛 Bookworm | Read 25 papers | `bookworm` |
| Reading | 📚 Bibliophile | Read 50 papers | `bibliophile` |
| Reading | 🗃️ Literature Review | Read 75 papers | `lit_review` |
| Reading | 💯 Centurion | Read 100 papers | `centurion` |
| Reading | 🔎 Systematic Review | Read 150 papers | `systematic` |
| Reading | 🧾 Well-Read | Read 200 papers | `well_read` |
| Reading | 🏛️ Living Library | Read 250 papers | `living_library` |
| Reading | 🎩 Erudite | Read 300 papers | `erudite` |
| Reading | 📊 Meta-Analyst | Read 350 papers | `meta_analyst` |
| Reading | 🌍 Encyclopedia | Read 500 papers | `encyclopedia` |
| Reading | 👁️ First Glance | Look at 1 paper (abstract or deeper) | `first_glance` |
| Reading | 🛍️ Browser | Look at 10 papers (abstract or deeper) | `browser` |
| Reading | 💡 Abstract Thinker | Look at 50 papers (abstract or deeper) | `abstract_thinker` |
| Depth | 🏊 First Dive | Read 1 paper thoroughly | `first_dive` |
| Depth | 🤿 Deep Diver | Read 5 papers thoroughly | `deep_diver` |
| Depth | 🐬 Free Diver | Read 10 papers thoroughly | `free_diver` |
| Depth | 🐙 Abyss Explorer | Read 25 papers thoroughly | `abyss_explorer` |
| Depth | 🦪 Pearl Diver | Read 50 papers thoroughly | `pearl_diver` |
| Depth | 🌊 Mariana Trench | Read 100 papers thoroughly | `trench` |
| Depth | 🗒️ Note Taker | Write substantial notes on 1 paper | `note_taker` |
| Depth | ✍️ Scribe | Write substantial notes on 10 papers | `scribe` |
| Depth | 🖍️ Annotator | Write substantial notes on 25 papers | `annotator` |
| Depth | 🪶 Chronicler | Write substantial notes on 50 papers | `chronicler` |
| Depth | 🖊️ Essayist | Write 5000 characters of notes in total | `essayist` |
| Depth | 📝 Novelist | Write 20000 characters of notes in total | `novelist` |
| Depth | 📕 Monograph | Write 50000 characters of notes in total | `monograph` |
| Depth | ⚖️ Referee | Rate 5 papers | `referee` |
| Depth | ⭐ Critic | Rate 20 papers | `critic` |
| Depth | 🍷 Connoisseur | Rate 50 papers | `connoisseur` |
| Depth | 🌟 Fan Club | Give 10 five-star ratings | `fan_club` |
| Depth | 🔍 Close Reader | Read 1 paper at least fully | `close_reader` |
| Depth | 🧐 Careful Reader | Read 10 papers at least fully | `careful` |
| Depth | 🫖 Journal Club | Read 25 papers at least fully | `journal_club` |
| Depth | 💭 Deep Thinker | Read 50 papers at least fully | `deep_thinker` |
| Depth | ⚗️ Critical Mind | Read 75 papers at least fully | `critical_mind` |
| Depth | 🧠 Scholar | Read 100 papers at least fully | `scholar` |
| Depth | 🧙 Sage | Read 250 papers at least fully | `sage` |
| Discipline | 🐭 First Bite | Get the cheese: read your top paper first, 1 time | `first_bite` |
| Discipline | 🧀 Cheese Taster | Get the cheese: read your top paper first, 10 times | `cheese_taster` |
| Discipline | 📜 Cheese Scholar | Get the cheese: read your top paper first, 25 times | `cheese_scholar` |
| Discipline | 🐀 Big Cheese | Get the cheese: read your top paper first, 50 times | `big_cheese` |
| Discipline | 👑 Chair of Cheese Studies | Get the cheese: read your top paper first, 100 times | `chair_of_cheese` |
| Discipline | 📭 Inbox Zero | Empty your reading queue by reading it | `inbox_zero` |
| Discipline | 🧘 Minimalist | Empty your reading queue 5 times | `minimalist` |
| Discipline | ☯️ Zen Master | Empty your reading queue 15 times | `zen` |
| Discipline | 🗂️ Curator | Mark 10 papers as not relevant (teaches the recommender) | `curator` |
| Discipline | 🚧 Gatekeeper | Mark 25 papers as not relevant | `gatekeeper` |
| Consistency | ✌️ Back to Back | Read something 2 weeks in a row | `streak_2` |
| Consistency | 🔥 Month of Momentum | Read something 4 weeks in a row | `streak_4` |
| Consistency | 🚴 Two-Month Run | Read something 8 weeks in a row | `streak_8` |
| Consistency | 🏃 Quarter Marathon | Read something 12 weeks in a row | `streak_12` |
| Consistency | 🦸 Half-Year Hero | Read something 26 weeks in a row | `streak_26` |
| Consistency | 🗓️ Year of Reading | Read something 52 weeks in a row | `streak_52` |
| Consistency | 🚂 Unstoppable | Read something 78 weeks in a row | `streak_78` |
| Consistency | 🏔️ Two Years Strong | Read something 104 weeks in a row | `streak_104` |
| Consistency | 🧭 Questing | Complete your first weekly quest | `questing` |
| Consistency | 🎒 Adventurer | Complete 3 weekly quests | `adventurer` |
| Consistency | 🥾 Pathfinder | Complete 5 weekly quests | `pathfinder` |
| Consistency | 🗺️ Quest Master | Complete 10 weekly quests | `quest_master` |
| Consistency | 🐉 Quest Legend | Complete 25 weekly quests | `quest_legend` |
| Consistency | ⛵ Odyssey | Complete 50 weekly quests | `odyssey` |
| Consistency | 📯 Saga | Complete 75 weekly quests | `saga` |
| Consistency | 🏰 Epic | Complete 100 weekly quests | `epic` |
| Consistency | ✅ Top Marks | Get grade A or S in 1 week | `top_marks` |
| Consistency | 🅰️ Straight A | Get grade A or S in 5 weeks | `straight_a` |
| Consistency | 🎖️ Honor Roll | Get grade A or S in 15 weeks | `honor_roll` |
| Consistency | 📋 Dean's List | Get grade A or S in 30 weeks | `deans_list` |
| Consistency | 🎤 Valedictorian | Get grade A or S in 52 weeks | `valedictorian` |
| Consistency | ✨ Distinction | Get grade S in 1 week | `distinction` |
| Consistency | 💎 Perfectionist | Get grade S in 3 weeks | `perfectionist` |
| Consistency | 🏵️ Summa cum Laude | Get grade S in 10 weeks | `summa` |
| Consistency | 🔄 Comeback Kid | Read again after a break of 4 weeks or more | `comeback` |
| Consistency | 🌀 Habit Former | Read something in 5 different weeks | `habit` |
| Consistency | 🏫 Semester | Read something in 13 different weeks | `semester` |
| Consistency | 🌗 Two Semesters | Read something in 26 different weeks | `two_semesters` |
| Consistency | 📆 Academic Year | Read something in 40 different weeks | `academic_year` |
| Consistency | 🎆 Full Year | Read something in 52 different weeks | `full_year` |
| Consistency | 🍂 Seasoned | Read something in 75 different weeks | `seasoned` |
| Consistency | 🎗️ Veteran | Read something in 100 different weeks | `veteran` |
| Pace | 🏅 Marathon Day | Read 4 papers in one day | `marathon` |
| Pace | 🍿 Binge Week | Read 8 papers in one week | `binge_week` |
| Pace | 🦉 Night Owl | Read 5 different papers between 22:00 and 04:00 | `night_owl` |
| Pace | 🌙 Night Shift | Read 20 different papers between 22:00 and 04:00 | `night_shift` |
| Pace | 🌅 Early Riser | Read 5 different papers between 05:00 and 08:00 | `early_riser` |
| Pace | 🐓 Dawn Patrol | Read 20 different papers between 05:00 and 08:00 | `dawn_patrol` |
| Pace | 🏖️ Weekend Warrior | Read 5 different papers on weekends | `weekend_warrior` |
| Pace | ⛱️ Weekend Scholar | Read 20 different papers on weekends | `weekend_scholar` |
| Breadth | 🗿 Landmark | Read 1 landmark paper (top 1 % cited) | `landmark` |
| Breadth | 🏺 Classicist | Read 5 landmark papers (top 1 % cited) | `classicist` |
| Breadth | 📜 Canon Keeper | Read 20 landmark papers | `canon_keeper` |
| Breadth | 🏯 Canon Scholar | Read 50 landmark papers | `canon_scholar` |
| Breadth | 🐦 Early Bird | Read 5 papers within 30 days of publication | `early_bird` |
| Breadth | 📈 Trend Spotter | Read 15 papers within 30 days of publication | `trend_spotter` |
| Breadth | 🔪 Cutting Edge | Read 30 papers within 30 days of publication | `cutting_edge` |
| Breadth | 📰 Scoop | Read 3 papers within 7 days of publication | `scoop` |
| Breadth | 🗞️ Breaking News | Read 10 papers within 7 days of publication | `breaking_news` |
| Breadth | 🦴 Archaeologist | Read a paper that is 25+ years old | `archaeologist` |
| Breadth | ⏳ Historian | Read 5 papers that are 25+ years old | `historian` |
| Breadth | 🌿 Curious Mind | Read papers from 3 different topics | `curious` |
| Breadth | 🚶 Wanderer | Read papers from 5 different topics | `wanderer` |
| Breadth | 🧩 Polymath | Read papers from 8 different research topics | `polymath` |
| Breadth | 🌉 Bridge Builder | Read papers from 12 different topics | `bridge_builder` |
| Breadth | 🎨 Renaissance Mind | Read papers from 20 different topics | `renaissance` |
| Breadth | 🌌 Universal Scholar | Read papers from 30 different topics | `universal` |
| Breadth | 🔭 Scout | Read 3 papers from your recommendations | `scout` |
| Breadth | 🧗 Trailblazer | Read 10 papers from your recommendations | `trailblazer` |
| Breadth | 🚀 Pioneer | Read 25 papers from your recommendations | `pioneer` |
| Breadth | 🗻 Expedition Leader | Read 50 papers from your recommendations | `expedition` |
| Breadth | 🗺 Cartographer | Read 100 papers from your recommendations | `cartographer` |
| Breadth | 📁 Library Diver | Read 10 papers from your libraries | `library_diver` |
| Breadth | 🗄️ Archivist | Read 25 papers from your libraries | `archivist` |
| Breadth | 🏷️ Librarian | Read 50 papers from your libraries | `librarian` |
| Breadth | 🗝️ Head Librarian | Read 100 papers from your libraries | `head_librarian` |
| Breadth | 📇 Bibliographer | Read 200 papers from your libraries | `bibliographer` |
| Breadth | 🪁 Free Spirit | Read 5 papers you added yourself | `free_spirit` |
| Breadth | 🦄 Maverick | Read 15 papers you added yourself | `maverick` |
| Experience | 🌠 Rising Star | Earn 500 XP | `rising_star` |
| Experience | 💰 Thousand Club | Earn 1000 XP | `thousand_club` |
| Experience | 🥈 Silver Club | Earn 2500 XP | `silver_club` |
| Experience | 🖐️ High Five | Earn 5000 XP | `high_five` |
| Experience | 🔟 Ten Thousand | Earn 10000 XP | `ten_thousand` |
| Experience | 🥇 Gold Club | Earn 15000 XP | `gold_club` |
| Experience | 💠 Platinum Club | Earn 20000 XP | `platinum_club` |
| Experience | 🏆 Hall of Fame | Earn 25000 XP | `hall_of_fame` |

### Balancing

`tools/simulate_game.py` plays four readers for two years on a temporary
database with the real game code (offline, fixed seed) and prints XP and
level by week, badges by week, the badges reached within one and two years,
the quest rate, drop penalties and the longest stretch without a level-up
or badge. The defaults above give:

| Reader | Week 1 | Week 4 | Week 52 | Week 104 | Badges in 2 years | Longest gap (reading weeks) |
|---|---|---|---|---|---|---|
| New researcher, 0–4 papers a week, mostly abstracts at first | level 4 | level 8 | level 19 | level 26 | 68 | 5 |
| Experienced researcher, 185-paper backlog on day 1 | level 21 | level 22 | level 34 | level 41 | 93 | 5 |
| Very regular, 1 deep read + 2 skims every week | level 6 | level 11 | level 37 | level 53 | 105 | 4 |

### Saving, loading and comparing profiles

- **Save** (Profile page, `litrad profile save [FILE]`): an
  [archive](#the-archive-export-everything) (`.zip`) with the profile and its
  locked rules, every status, rating and note (also the note files),
  history, queue, XP, badges, recommendation batches with evidence, and the
  libraries with their papers. Default file:
  `litrad_profile_<name>_<date>_<time>.zip` in `backup.dir`. Automatic
  backups contain the same.
- **Load** (welcome screen, `profile load FILE.zip [--name NAME]`): an
  archive becomes a *new* profile; nothing is replaced.
- **Compare** (Profile → Compare, `profile compare FILE.zip ...`): level, XP,
  papers read, thorough reads, longest streak, quests, badges, discipline,
  depth, Reading Impact Factor, notes and active weeks side by side, the best
  value highlighted; says whether profiles played by the **same rules**
  (fingerprint).
- Archives are verified by the SHA-256 of every file in `manifest.json`
  when they are read; a file whose content no longer matches is refused.
  This catches hand edits; it is not a cryptographic proof. Loading and
  comparing also check the archive against its schemas and for consistency
  (every reading and XP event points to a paper in the archive, totals add
  up).
- **Export everything** (Profile page, `export-all`) writes the same archive
  under another name ([Data formats](#11-data-formats)).
- Merging progress from another machine (`import-progress`, `import-all
  --into`) only works between profiles with the same rules.

---

# Part III: Reference


## 7. Commands

| Command | Purpose |
|---|---|
| `profile list\|create\|use\|show\|save\|load\|compare` | profiles: list, create (rules locked), choose the current one, show rules, save an archive (`save [FILE]`), load an archive as a new profile (`load FILE.zip [--name]`), compare archives (`compare FILE.zip…`) |
| `library list\|import\|export\|edit\|use\|delete` | `import FILE --new NAME` or `--to NAME [--replace]` (`--check` only checks); `export NAME\|queue [FILE] [--format bibtex\|ris] [--status]`; `edit NAME [--name] [--description] [--recommendations/--no-recommendations]`; `use NAME… [--off]` (libraries used for recommendations) |
| `examples [positive\|negative]` | example papers for Semantic Scholar with their origin; `add\|remove\|restore positive\|negative REF`; `reset positive\|negative`; `rules positive\|negative [RULE…] [--none] [--count N]` (saved in the profile's settings) |
| `ingest [--no-s2]` | update every library from its file; resolve DOIs; fetch metrics and embeddings |
| `check-bib [FILE]` | check an export (BibTeX, RIS, CSL-JSON) without importing |
| `status` | libraries: coverage, top folders, papers per library |
| `recommend [--new] [--no-s2]` | latest ≤ 5 papers; `--new` gets new ones |
| `search [KEYWORDS] [--title] [--author] [--journal] [--from] [--to] [--oa] [--source all\|europepmc\|pubmed\|library] [--sort relevance\|newest\|cited] [--limit N] [--add N]` | find papers in Europe PMC and PubMed and queue them |
| `queue [add\|drop\|move]` | the reading queue |
| `next`, `show REF` | top paper / any paper with abstract, status, notes |
| `read REF LEVEL [--rating] [--note] [--earlier [--date YYYY-MM-DD]]` | log a reading and earn XP; `--earlier`: read before litrad ([details](#starting-with-papers-you-already-read)) |
| `note REF TEXT`, `dismiss REF` | add a note / mark as not relevant |
| `notes sync\|check`, `notes path REF` | note files: import edits, write missing files, list conflicts and orphans (`check` changes nothing) / print a paper's file ([Notes and Obsidian](#notes-and-obsidian)) |
| `score`, `history` | level, streak, grade, badges / reading log |
| `read-papers [--depth LEVEL]… [--rating N] [--since DATE] [--until DATE] [--library NAME] [--notes] [--dismissed] [--search WORDS] [--sort last\|first\|depth\|rating\|xp\|title] [--limit N] [--tsv FILE]` | your reading history; `--tsv` writes all matching rows |
| `app [--port N] [--no-browser]` | desktop mode: the web UI on this computer only, opened in your browser; Quit in the sidebar. `--no-browser` or `LITRAD_NO_BROWSER=1`: do not open a browser. Also as `litrad-app` |
| `serve [--network local\|all\|eth0\|wlan0\|IP] [--no-choose] [--port] [--list-networks]` | the web UI (server mode) |
| `set-password [--remove] [--password-file FILE]` | password for network access |
| `backup`, `restore FILE` | snapshot (`litrad_YYYYMMDD_HHMMSS.sqlite`) plus archive (`litrad_backup_YYYYMMDD_HHMMSS.zip`) in `backup.dir` / restore a snapshot |
| `export-progress FILE.zip`, `import-progress FILE.zip [--dry-run]` | the same as `export-all FILE.zip` / `import-all FILE.zip --into` the current profile |
| `export-all [FILE.zip]` | everything of the profile in one documented archive ([Data formats](#the-archive-export-everything)); default: a new file in `backup.dir` |
| `import-all FILE.zip [--new NAME \| --into PROFILE] [--dry-run] [--partial]` | import an archive as a new profile or merge it; checked first, all or nothing ([details](#importing-an-archive)) |
| `check` | database integrity, schema version, references, contents, last backup |
| `where` | the config file, data folder, profiles, backups and cache in use, and why |
| `version` | show the version |

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
| [Europe PMC](https://europepmc.org/RestfulWebService) | paper search, look-up by DOI/PMID/PMC id | free; litrad waits 0.1 s between requests |
| [PubMed](https://www.ncbi.nlm.nih.gov/books/NBK25501/) (NCBI E-utilities) | paper search, look-up by PMID | 3 requests/s (10 with `api.ncbi_key`) |

**What leaves your machine:** titles/authors of library papers without DOI
(Crossref); DOIs/PMIDs (OpenAlex, Semantic Scholar, Europe PMC, PubMed); your
topic queries (OpenAlex); what you type into the paper search (Europe PMC,
PubMed); the DOIs of the positive and negative example papers (Semantic
Scholar recommendations; switch off with `sources.semanticscholar`). PubMed
requests carry `tool=litrad` but no e-mail; `api.contact_email` stays
`n/a` unless you want the polite pools, and if set it is sent only to
Crossref and OpenAlex (as their `mailto` parameter), never in the
User-Agent (`litrad/<version>`) that every service sees.

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
  `paths.bibtex` (the profile's Paperpile export; default `library.bib` in the
  profile folder). The web UI writes it; you can edit it by hand. A new
  profile starts without topics.

**Which `config.yaml`** (the first that applies; `litrad where` shows
the choice):

1. `-c/--config PATH`;
2. the environment variable `LITRAD_CONFIG` (a missing file is created on the first run, with the data in the same folder; the Docker image sets `/data/config.yaml`);
3. `config/config.yaml` in the current folder (a git installation);
4. your per-user config file ([Setup](#1-install)). If it does not exist
   yet, the first command creates it from the example, with the data in your
   per-user data folder, and prints where.

**Relative paths** (`paths.*`, `backup.dir`, a profile's `paths.bibtex`) are
resolved against the *data folder*, not the folder you run the command in:
`paths.base_dir` if set (relative to the config file's folder); otherwise
the folder that contains `config/` when the file is `.../config/config.yaml`
(a git installation keeps `data/` next to `config/`); otherwise
the config file's folder. `~` stands for your home folder.

Use `n/a` for "not set". Unknown keys, and infinite or NaN numbers, are
rejected; so are `paths.database` and `paths.bibtex` in `config.yaml`
(the error names the line): both belong to the profile. Sections marked *(planned)* are validated but not used yet.

### `paths`

| Key | Default | Meaning |
|---|---|---|
| `base_dir` | `n/a` | folder relative paths start from (see above); a new per-user config sets it to your per-user data folder |
| `profiles_dir` | `data/profiles` | one folder per profile |
| `cache_dir` | `data/cache` | HTTP response cache |
| `obsidian_vault` | `n/a` | Obsidian vault for the note files; `n/a`: `<profile folder>/notes/` ([Notes and Obsidian](#notes-and-obsidian)) |
| `notes_subfolder` | `litrad` | folder inside the vault for the profiles' note folders (`<subfolder>/<profile id>/`; `""` = the vault itself); only used with `obsidian_vault` |

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
| `n_recommendations` | 5 | papers per list (1–5) |
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
| `backup.dir` | `data/backups` | snapshots and archives, one subfolder per profile; a cloud-synced folder is ideal |
| `backup.keep` | 14 | newest snapshots (and backup archives) to keep, plus one of each of the 8 weeks before them |
| `backup.interval_hours` | 24 | `serve` backs up a profile whose newest backup is N hours old (checked at start and hourly); 0 = off |

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
(without the `data/` part, e.g. `~/.local/share/litrad/profiles/`); in
the Docker image the volume `/data` (also holding `config.yaml`; find it on
the host with `docker volume inspect litrad`).
`litrad where` prints the actual folders. Each profile has its own
folder `data/profiles/<profile>/`; `data/profiles/.current` names the
profile the command line uses.

| What | Where (default) | Rebuildable? |
|---|---|---|
| **Your progress**: statuses, ratings, notes, history, queue, XP, badges, recommendations, example choices, locked rules | `data/profiles/<profile>/litrad.sqlite` | **no**, back it up |
| Libraries, paper metadata, metrics, embeddings | same database | yes, from the export files |
| Uploaded export files; the profile's Paperpile export `library.bib` | `data/profiles/<profile>/libraries/`, `data/profiles/<profile>/library.bib` | yes, export again |
| **Your notes** (Markdown files) | `data/profiles/<profile>/notes/` or your Obsidian vault | **no**; in every backup (zip), copy in the database |
| Topics and settings | `data/profiles/<profile>/settings.yaml` | no; in every backup |
| Machine settings | `config/config.yaml` or the per-user config file | no; keep a copy |
| Cached API answers (all profiles) | `data/cache/http_cache.sqlite` | yes, delete any time |
| Login cookie key | `data/web_secret` | yes; deleting it logs everyone out |
| Desktop app log, running-instance lock | `logs/app.log`, `app-instance.json` in the per-user data folder | yes |
| Backups | `data/backups/<profile>/` (`backup.dir`) | they *are* the copy |
| Copies made before a database upgrade | `data/profiles/<profile>/upgrade-backups/` | they *are* the copy ([Database upgrades](#database-upgrades)) |

All paths are settings, so everything can live in one folder of your choice.

### Recommended setup

1. **One live database on one machine** (e.g. a home server or NAS);
   laptop and phone use the web UI over the network, so there is never a
   second copy to get out of sync.
2. **Point `backup.dir` to a cloud-synced folder**, not the database:

   ```yaml
   backup:
     dir: /home/kai/Nextcloud/litrad-backups
     keep: 14
     interval_hours: 24
   ```

   While `serve` runs it checks at start and then hourly, and backs up
   every profile whose newest backup is 24 h old (restarting the server
   adds no backup).
   Backups keep running even if a profile's `settings.yaml` or
   `config.yaml` is invalid (the problem is logged). On demand:
   `litrad backup` or **Back up now** under Settings → Data & backups.
3. Now and then run `litrad check`: location, integrity check,
   contents and age of the last backup.

**Do not sync the live database file**: a sync client may copy it mid-write
(a corrupt copy), and two machines using it create conflict copies that lose
progress. Snapshots are complete and never change. Use the
archives below to move progress.

### What a backup contains

Two files per backup, named with `YYYYMMDD_HHMMSS`:

| File | Content | Use |
|---|---|---|
| `litrad_20261008_070000.sqlite` | complete database snapshot (SQLite online backup API, consistent while the server writes, integrity-checked) | `litrad restore FILE` |
| `litrad_backup_20261008_070000.zip` | the whole **profile** as an [archive](#the-archive-export-everything) (the same as *Export everything*): rules, settings, statuses, ratings, notes and note files, history, queue, XP, badges, recommendations, libraries, example choices | `profile load` (new profile), `import-all --into` (merge); `restore` takes the note files from it |

Of each kind, the newest `backup.keep` (default 14) are kept, plus the
newest one of each of the 8 calendar weeks before them; older ones are
deleted. Only files named exactly like these two are rotated: profiles you
save yourself (`litrad_profile_<name>_<date>_<time>.zip`), *Export
everything* files and any other file in `backup.dir` are never deleted.

### Restoring

```bash
# stop `litrad serve` first
litrad restore /path/to/backups/litrad_20261008_070000.sqlite
```

The current database is first saved as `…_before_restore.sqlite`, so a
restore can be undone; the newest 5 of these copies are kept, apart from
the snapshots (ordinary backups never push them out). Before anything changes, the snapshot is
checked: integrity, a litrad database at all, a schema version this
version understands (a snapshot of a newer litrad is refused; an older
1.x one is upgraded when it is next opened) and no rows referring to papers,
libraries or batches that do not exist. Paths that do not exist are refused
too. Note files:
missing ones are written back from the archive of the same backup (or from
the restored copy); an existing file whose text differs from the restored
copy is never overwritten but becomes a [conflict](#notes-and-obsidian).

### Moving to another machine, or merging two

```bash
litrad export-all export.zip                     # old machine (or take the latest backup archive)
litrad import-all export.zip --into PROFILE --dry-run   # new machine: preview
litrad import-all export.zip --into PROFILE
```

`import-progress FILE.zip` is `import-all FILE.zip --into` the current
profile.

How the merge works:

- papers are matched by DOI, then PMID, then citation key, then title; the
  title match ignores case and spacing, needs the same year when both have
  one, and never matches a paper with a different DOI/PMID. Unknown papers
  are created from the stored metadata;
- reading status and rating: the newer change wins; notes from both sides are
  kept (paragraphs that are new are added) and written to the note files;
  a note file changed meanwhile becomes a conflict;
- history, XP, badges and recommendation batches are added if not present;
  queue entries are appended while there is room;
- the profile is taken over if the target has none; otherwise both must have
  the same rules (fingerprint), or the merge is refused;
- importing the same archive twice changes nothing; the target is backed up
  first; the archive is checked against its schemas and for consistency
  before anything changes ([details](#importing-an-archive)), so an invalid
  archive changes nothing;
- the libraries with all metadata and the note files come along.

To start a profile from a saved archive instead, use *Load a saved profile*
on the welcome screen or `profile load FILE.zip [--name NAME]`.

### How progress is protected against crashes

Every action is one database transaction (all saved or nothing), written
durably even if the power drops right after (`PRAGMA synchronous=FULL`). The
CLI and the web UI can write at the same time. Settings and exports are
written to a temporary file and renamed, so a crash never leaves a
half-written file. A failed automatic backup is logged and never stops the
server.


## 11. Data formats

Everything you build up in litrad is stored and exported in plain,
documented formats, so that later versions keep reading it and other tools
can use it.

### Versions

Three version numbers are independent of each other:

| Version | Where | Changes when |
|---|---|---|
| app version (`1.0.0`) | `litrad version` | every release |
| **data format** (`1`) | `format_version` in `manifest.json` of an archive | the content of the archive changes in any way: a new file, a new field, a changed meaning |
| **database schema** (`1`) | `PRAGMA user_version` of each profile database | a table, column or index changes |

A litrad version reads every data format and database schema up to its
own and refuses newer ones with a clear message, so an older version never
damages data of a newer one. **Compatibility promise:** every
1.x version reads and upgrades all data, settings and archives of earlier
1.x versions; new features only add (optional fields, new files, new tables).
The promise is forward only: a newer version reads older files, not the
other way round.

### Database upgrades

The database schema version is `PRAGMA user_version`; **version 1 is the
1.0 baseline**. Upgrades in 1.x are numbered steps (step 2 upgrades 1 to 2),
one transaction each, so an interrupted upgrade resumes where it stopped.
Before upgrading, litrad saves a copy to
`data/profiles/<profile>/upgrade-backups/litrad_<date>_<time>_before-schema-<old>-to-<new>.sqlite`
(the newest 3 copies are kept; `litrad where` shows the folder). Nothing is
ever reset. A database with a newer schema version is refused: update
litrad, or restore a backup made with this version. A database without a
version (0) that already has tables is refused as not written by litrad
1.x. `litrad check` shows the schema version.

### The archive ("Export everything")

`litrad export-all [FILE.zip]` or **📦 Export everything** on the Profile
page writes one zip file per profile. All text is UTF-8, dates and times are
ISO 8601 local time (`2026-10-09T08:42:05`), every JSON file has a JSON Schema
(draft 2020-12) in `schemas/`, and papers are referred to by their key `p<id>`
from `papers.json`.

| File | Content (main fields) |
|---|---|
| `manifest.json` | `format` (`litrad-export`), `format_version`, `app_version`, `schema_version`, `created`, `profile`, `counts`, and `files`: SHA-256 and size of every other file |
| `README.md` | what each file contains (generated) |
| `papers.json` | `papers`: every paper any other file refers to: `key`, identifiers (`doi`, `pmid`, `pmcid`, `arxiv_id`, `s2_id`, `openalex_id`, `bibtex_key`), `title`, `authors`, `year`, `pub_date`, `venue`, `abstract`, metrics, `topics`, `folders`, `tags`, `in_library` |
| `libraries.json` | `libraries`: `name`, `description`, `kind` (imported, recommended), `format`, `use_for_recommendations`, dates, `files`, and `papers` (`paper`, `citation_key`, `folders`, `tags`, `added_at`) |
| `libraries/<name>.bib`, `.json` | each library as BibTeX and CSL-JSON for reference managers (views of `libraries.json`, checked on import but not read) |
| `readings.json` | `readings`: `paper`, `status` (abstract, skimmed, read, thorough, dismissed), `rating`, `notes`, `updated_at` |
| `status_events.json` | `status_events`: the reading log: `paper`, `status`, `was_queue_top`, `at`; optional: `earlier: true` (read before litrad) and `read_on` (`YYYY-MM-DD` or null) |
| `xp_ledger.json` | `total` and `events`: `points`, `kind`, `reason`, `at`, `paper`, running `balance` |
| `badges.json` | `badges`: `code`, `name`, `unlocked_at` |
| `queue.json` | `queue`: `paper`, `position` (1 = next), `source`, `added_at` |
| `recommendations.json` | `batches`: `week_start`, `created_at`, `n_candidates`, `warnings`, `funnel`, `params`, `items` (`paper`, `rank`, `category`, `score`, `breakdown` with the evidence, `reason`) |
| `example_choices.json` | `example_choices`: `paper`, `kind` (positive, negative), `action` (include, exclude), `at` |
| `profile.json` | `profile` (`name`, `created_at`, locked `rules`, `fingerprint`, `app_version`) and `xp_total` |
| `settings.yaml` | the profile's `settings.yaml` (topics, selection, sources, queue, library, paths) |
| `reading_log.tsv`, `xp_ledger.tsv` | the reading log and the XP ledger as tables for spreadsheets (not read on import) |
| `notes/…` | the [note files](#notes-and-obsidian) as they are, with `note_template.md` |
| `schemas/*.schema.json` | the JSON Schemas |

Not included, because litrad recomputes them or they belong to one
machine: embeddings, the example lists themselves (your choices are), the
import log, the sync state of note files. The schemas are also in the
package (`litrad/schemas/`).

### Importing an archive

`litrad import-all FILE.zip` creates a new profile (named as in the
archive, or `--new NAME`); `--into PROFILE` merges into an existing one
(after a backup); on the web: *Import an archive* on the welcome screen (new
profile) or on the Profile page (merge). **Before anything changes**, the
archive is checked:

1. every file against its SHA-256 in `manifest.json`;
2. every JSON file against its JSON Schema;
3. consistency: every paper key used in readings, history, XP, queue,
   recommendations, example choices, library members and note files
   (`litrad_id`) is in `papers.json`; no key, reading, queue entry,
   badge, library or library member twice; the `.bib`/`.json` copies list
   the papers of `libraries.json`; the XP `balance` and `total` add up and
   match `xp_total`; badge XP belongs to an unlocked badge; reading XP to a
   reading; every badge exists in the rules; the rules are valid; all
   dates parse; the settings are valid. A stored rules fingerprint that
   differs from the one computed from the rules is only a note (the string
   was edited); merges and comparisons always use the computed one.

Problems are listed by file and record, e.g.
`readings.json[12]: paper p77 not in papers.json`, and nothing is imported
(all or nothing: one database transaction; note files are written only
after it succeeded; a note file that cannot be written then, e.g. on a full
disk, is listed, its note stays in the database and `litrad notes sync`
writes it later). `--partial` (*Only the consistent part*) imports the
consistent records and lists what was skipped, including everything that
refers to a skipped record. `--dry-run` checks and shows what would change.

Merging uses the rules of [moving and merging](#moving-to-another-machine-or-merging-two):
papers are matched by DOI, PMID, citation key, then title and year; the
newer reading wins and notes of both sides are kept; importing the same
archive twice changes nothing. The settings of the archive are only used for
a new profile, and paths from another machine never.

**Unknown fields** in the JSON files are allowed and ignored with a note
(e.g. fields a script added). Files of a newer data format are refused.

Everything moves between machines and profiles only as zip archives
(format `litrad-export`, data format version `1`); there is no other
exchange format.

### Reading the database directly

Each profile database is an ordinary SQLite file; the tables are described in
the [data model](#data-model). Read it without changing it, ideally from a
backup snapshot:

```bash
sqlite3 -readonly data/profiles/<profile>/litrad.sqlite \
  "SELECT p.title, r.status, r.rating FROM reading r JOIN paper p ON p.id = r.paper_id"
```

Times are stored as local ISO 8601 text, lists and settings as JSON text.
Do not write into the live database while litrad runs; use the archive
or the commands instead.


---

# Part IV: How it is built


## 12. Architecture

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
| `db.py` | SQLModel tables, reading levels, embeddings as float32 bytes, durable SQLite settings; `init_db` runs the migrations |
| `migrations.py` | schema version (`PRAGMA user_version`), numbered upgrade steps, snapshot before an upgrade, refusing newer databases |
| `dataformat.py`, `schemas/` | data format version, JSON Schemas, schema validation with record locations, unknown fields, consistency checks |
| `archive.py` | "Export everything": writing the archive, checking it (checksums, schemas, consistency), importing it as a new profile or merging (all or nothing, partial, dry run) |
| `ingest/bibtex.py`, `ingest/formats.py` | BibTeX (LaTeX decoding, Paperpile's month/day format, DOI normalisation, folders/labels), RIS and CSL-JSON parsers, format detection |
| `ingest/check.py` | checks of an export (required fields, coverage, duplicates) |
| `ingest/pipeline.py` | import into a library (new, add, replace), matching, DOI resolution, OpenAlex and S2 enrichment, per-source error isolation, import history |
| `libraries.py`, `export.py`, `cli_library.py` | libraries and memberships, Recommended and queue libraries, uploads; BibTeX/RIS/CSL-JSON export; `library` and `examples` commands |
| `examples.py` | the positive and negative example lists for Semantic Scholar: rules, your choices, regeneration |
| `sources/http.py` | JSON client: SQLite cache with max age and clean-up, per-host throttling, retries (`Retry-After`, backoff) |
| `sources/crossref.py`, `openalex.py`, `semanticscholar.py`, `europepmc.py`, `pubmed.py`, `hits.py` | API clients; a common search `Hit` with merging |
| `papers.py`, `search.py` | storing works and search hits without overwriting library metadata; one search query translated for Europe PMC, PubMed and the library |
| `recommend/engine.py`, `scoring.py`, `text.py` | candidates with provenance, filters and funnel, batches with settings snapshot; eligibility, relevance, impact, timeliness, MMR, evidence tree; TF-IDF fallback |
| `reading.py` | paper references (`r1`, `q2`, DOI …), queue, reading log, notes, drop penalty |
| `notes.py` | notes as Markdown files: names, front matter (ruamel.yaml round trip), three-way sync with the copy in `reading.notes`, conflicts, Markdown preview without raw HTML, template, notes archive |
| `history.py` | reading history: filter, sort and aggregate in SQL, TSV export (Read papers tab, `read-papers`) |
| `rules.py`, `profile.py`, `game.py` | `GameRules`, badge catalog, fingerprint; locking; points, anti-farming, streaks, quests, levels, badges, summaries, ledger, calendar |
| `profiles.py` | profile folders, settings overlay, current profile, loading saved archives |
| `backup.py` | atomic writes, snapshots, rotation, restore, profile archives, merge, comparison in an in-memory database, automatic backup thread |
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
| `reading` | current status, rating and notes per paper (`notes`: copy of the note file's body) |
| `notefile` | each note file: path in the notes folder, body hash at the last sync, size and time, front matter hash, conflict flag |
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

Rules in [Gamification](#6-gamification). Design notes: the cheese bonus fits the rat
in the logo; anti-farming invariants (base points
only up to the deepest level, paper bonuses once, cheese bonus on new base
points) are checked by a randomised test over long sessions; all rules form
one `GameRules` object frozen per profile with a SHA-256-based fingerprint
(over every 1.0 rule and badge, `rules.FINGERPRINT_FIELDS`; rules and badges
added in a later 1.x enter only with a non-default value, so the fingerprint
never changes for a locked profile); archives carry a SHA-256 per file, and
comparisons load each archive into a throw-away in-memory SQLite database; streaks count weeks, not days, to
motivate without guilt.

### Profiles

`profiles.py` keeps one folder per profile under `paths.profiles_dir`. A
profile's effective configuration is `config.yaml` merged with its
`settings.yaml` (the keys in `OVERLAY_KEYS`), with database, bibtex path and
backup dir pointing into the profile. The web UI keeps the chosen profile in
a signed cookie; a middleware sends requests without a valid profile to the
welcome screen and passes the profile to the routes in a context variable.
The CLI uses `--profile`, else `data/profiles/.current`, else the only
profile; without any profile it stops and says how to create one.

### Data safety and web security

- One transaction per action, `PRAGMA synchronous=FULL`, a 30 s busy
  timeout; snapshots via SQLite's online backup API, integrity-checked;
  atomic writes (temporary file, `fsync`, `os.replace`).
- Uploads are checked in a temporary file, limited to 50 MB, archives for
  *Import an archive* to 1 GB (HTTP 413 for larger requests, 411 for a
  request body without a length), and stored under safe names; an invalid export never
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


## 13. Implementation and credits

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
  [Development](#14-development)).

### Quality assurance

The test suite runs offline by default with recorded real API
responses (live tests with `--network`) and covers failure scenarios, hostile
input, data integrity, scale and years of use.
Warnings are errors; ruff lints and formats; CI runs on Python 3.11–3.13.
Each milestone was also run on real data and checked with headless-browser
screenshots. Details: [Development](#14-development).

### Software dependencies

Versions used during development (Python 3.13.9). All are open source with
licenses that permit use in this project.

| Package | Version | License | Used for |
|---|---|---|---|
| [bibtexparser](https://github.com/sciunto-org/python-bibtexparser) | 2.1.0 | MIT | parsing BibTeX (v2 API, LaTeX decoding and name-splitting middlewares) |
| [httpx](https://www.python-httpx.org) | 0.28.1 | BSD-3-Clause | HTTP client for all APIs; `MockTransport` in tests |
| [jsonschema](https://github.com/python-jsonschema/jsonschema) | 4.26.0 | MIT | checking archives against their JSON Schemas (draft 2020-12) before importing |
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
| *indirect:* referencing, jsonschema-specifications, rpds-py, attrs | 0.37.0, 2025.9.1, 2026.9.1, 26.1.0 | MIT | schema references and meta-schemas for jsonschema |
| *dev:* [pytest](https://docs.pytest.org/) | 9.1.1 | MIT | tests |
| *dev:* [pytest-cov](https://github.com/pytest-dev/pytest-cov) | 7.1.0 | MIT | coverage |
| *dev:* [Ruff](https://docs.astral.sh/ruff) | 0.16.10 | MIT | lint and format |
| *build:* [PyInstaller](https://pyinstaller.org) | 6.22.3 | GPL-2.0 with bootloader exception | desktop app folders and `.app` (the exception allows bundling non-GPL apps) |
| *build:* [Pillow](https://python-pillow.github.io) | CI | MIT-CMU | converting the icon for Windows and macOS builds |
| *build:* [appimagetool](https://github.com/AppImage/appimagetool), [Inno Setup](https://jrsoftware.org/isinfo.php) | 1.9.1, 6 | MIT, Inno Setup license | Linux AppImage, Windows installer |

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
  It inspired the general idea; no code was reused, and litrad differs
  in input, sources, impact scoring, the reading workflow and the game.
- Scholar Inbox (Flicke et al., 2025, arXiv:2504.08385), Semantic Scholar
  Research Feeds, ResearchRabbit and Litmaps were reviewed as alternatives.

### License and citation

MIT license with the Commons Clause ([LICENSE](../LICENSE)): you may use, change
and share litrad, but not sell it or a paid product or service (including
hosting, consulting or support) whose value comes mainly from it. To cite litrad use
[CITATION.cff](../CITATION.cff); please also cite OpenAlex and Semantic
Scholar/SPECTER2 (see Literature) when you report results based on it.


## 14. Development

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
  `.github/workflows/release.yml` (tests, [desktop builds](#desktop-builds),
  wheel and source archive, GitHub release with the CHANGELOG section;
  pre-release for `-rc.N`/`-dev`).
- Issues use `.github/ISSUE_TEMPLATE/` (bug report, feature request, testing
  feedback); pull requests `.github/pull_request_template.md`. Hosting:
  GitHub; a GitLab@HHU mirror can be added as a second push target.

### Tests

```bash
.venv/bin/pytest                         # offline suite (default)
.venv/bin/pytest --network               # also the real APIs
.venv/bin/pytest --cov=litrad --cov-report=term-missing
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
| `test_bibtex.py`, `test_pipeline.py`, `test_libraries.py` | parsing quirks; ingest end to end; formats, import modes, exports, special libraries, `library` CLI |
| `test_http.py`, `test_sources.py`, `test_search.py` | cache, retries, throttling; API clients and match rules; paper search |
| `test_scoring.py`, `test_recommend.py` | scoring maths, evidence; candidates, filters, cooldown, outages |
| `test_reading.py`, `test_game.py`, `test_papers.py` | queue, notes, ratings; XP, anti-farming, streaks, quests, badges; paper storage |
| `test_backup.py`, `test_profile.py`, `test_profiles.py` | snapshots, rotation, merge, restore; rules, locking, checksums, comparison; profile folders, welcome screen, separation |
| `test_cli.py`, `test_web.py`, `test_web_usecases.py`, `test_web_polish.py`, `test_auth.py` | interfaces, every page, settings round trip, user flows, login |
| `test_config.py`, `test_db.py` | validation, schema, additive upgrade steps |
| `test_migrations.py`, `test_compat.py` | schema versions, upgrade steps, snapshots, refusing newer databases, restore checks; files and databases of every 1.x release (`tests/data/compat/`, made by `tools/make_compat_fixtures.py`; until the first exists, of the current code) |
| `test_archive.py` | Export everything: contents, schemas, round trip, merge, every consistency rule, all or nothing, `--partial`, dry run, CLI, web, 5000 papers |
| `test_robustness.py`, `test_data_integrity.py` | crashes, concurrency, broken and hostile inputs; matching and merge correctness |
| `test_scale.py`, `test_import_scale.py`, `test_longterm.py` | large libraries and years of use (`web_bigdata.py` builds the data) |
| `test_network.py`, `test_live.py` | network interfaces for `serve`; real APIs (`--network`) |
| `test_desktop.py` | desktop mode: port, single instance, Quit, browser, log; packaging files |

### Compatibility fixtures

`tests/data/compat/<version>/` holds what each released version wrote: a
small profile database, an archive and the counts that version computed
(`expected.json`).
`tools/make_compat_fixtures.py --tags vX.Y.Z` checks the tag out into a
temporary worktree, installs it into a fresh virtual environment and builds
the profile offline with that version's own code. **Every release adds its
fixtures; fixtures of a released version are never regenerated.** A change
of the database models needs a new migration step, a higher
`SCHEMA_VERSION` and its entry in `tests/data/compat/schema.json`
(`test_migrations.py` fails otherwise).

### Desktop builds

`.github/workflows/release.yml` builds the desktop apps for every tag (and
on *Run workflow*, without releasing): PyInstaller (`packaging/litrad.spec`,
one folder, entry `packaging/desktop_entry.py`), then an AppImage
(`tools/build_appimage.py`), an Inno Setup installer
(`packaging/windows/litrad.iss`) or a `.dmg` (`hdiutil`). Each build is
checked with `tools/smoke_desktop.py BINARY`: start in a temporary home,
create a profile, load pages, second start, Quit, lock removed. Locally on
Linux:

```bash
pip install pyinstaller
pyinstaller --distpath /tmp/lr/dist --workpath /tmp/lr/work packaging/litrad.spec
python tools/smoke_desktop.py /tmp/lr/dist/litrad/litrad
python tools/build_appimage.py /tmp/lr/dist/litrad --output /tmp/lr/litrad.AppImage \
    --appimagetool PATH/appimagetool-$(uname -m).AppImage
```

### Recording new API fixtures

Run a real `litrad ingest`, then take the responses from
`data/cache/http_cache.sqlite` (table `cache`, JSON in column `body`). Trim
them before committing: shorten embeddings and drop affiliations.
