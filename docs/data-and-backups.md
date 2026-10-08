# Your data: where it lives, backups, moving machines

## Where everything is stored

| What | Where (default) | Rebuildable? |
|---|---|---|
| **Your progress**: reading statuses, ratings, notes, history, queue, XP, badges, recommendation batches | `data/litresearch.sqlite` (`paths.database`) | **no**, back it up |
| Library metadata, metrics, embeddings | same database | yes, `litresearch ingest` |
| Settings and topics | `config/config.yaml` | no; small, keep a copy |
| Cached API answers | `data/cache/http_cache.sqlite` | yes, delete any time |
| Login cookie key | `data/web_secret` | yes; deleting it logs everyone out |
| Backups | `data/backups/` (`backup.dir`) | they *are* the copy |

All paths are settings, so you can move everything into one folder of your
choice (e.g. `~/litresearch-data/`).

## Recommended setup

```
Raspberry Pi (always on)                       laptop / phone
┌──────────────────────────────┐   LAN       ┌───────────────┐
│ litresearch serve            │◄───────────►│ browser       │
│ data/litresearch.sqlite (live)│             └───────────────┘
│ backup.dir ──► cloud folder ─┼──► Nextcloud / Dropbox / Google Drive / Syncthing
└──────────────────────────────┘
```

1. **One live database on one machine.** Use it from your other devices
   through the web UI over the network ([web-ui.md](web-ui.md)). That way
   there is never a second copy to get out of sync.
2. **Point `backup.dir` to a cloud-synced folder**, not the database itself:

   ```yaml
   backup:
     dir: /home/kai/Nextcloud/litresearch-backups   # or a Syncthing / rclone folder
     keep: 14
     interval_hours: 24
   ```

   While `litresearch serve` runs, it writes a backup at start and then every
   24 h. `litresearch backup` does it on demand, as does the **Back up now**
   button under Settings → Data & backups. A weekly cron job works as well:

   ```
   0 7 * * 1  cd /path/to/litresearch-tool && .venv/bin/litresearch backup
   ```

3. Check now and then: `litresearch check` shows the database location, an
   integrity check, its contents and the age of the last backup.

### Why not sync the live database file?

SQLite writes in place. A sync client (Dropbox, Google Drive, Nextcloud,
OneDrive) can copy the file while a write is half done, which yields a
corrupt copy. If two machines use the "same" synced file, the client
creates conflict copies, and one machine's progress silently goes missing.
The snapshots below avoid both problems: each is complete and consistent,
and never changes again after it is written.

If you do want a database on several machines, use **Syncthing** (it
handles changing files better) **and** only ever run litresearch on one
machine at a time. The JSON export/import below is the safer route.

## What a backup contains

Every backup writes two files (file names follow `YYYYMMDD_HHMMSS`):

| File | Content | Use |
|---|---|---|
| `litresearch_20261008_070000.sqlite` | complete database snapshot, made with SQLite's online backup API (consistent even while the server writes) and integrity-checked | full restore: `litresearch restore FILE` |
| `litresearch_progress_20261008_070000.json` | your whole **profile**: the profile with its locked game rules, statuses, ratings, notes, history, queue, XP, badges and all recommendation batches, plus enough paper metadata (DOI, PMID, citation key, title, …) to find each paper again, and a checksum | readable; load as your profile (`litresearch profile load`) or merge into another installation (`litresearch import-progress`) |

Profiles you save yourself (Profile page, `litresearch profile save`) have the
same content and are named `litresearch_profile_<name>_<date>_<time>.json`;
they are never deleted by rotation. Files of format version 1 (before game
profiles existed) can still be imported.

The oldest files beyond `backup.keep` (default 14 of each) are deleted.

## Restoring

```bash
# stop `litresearch serve` first
litresearch restore /path/to/backups/litresearch_20261008_070000.sqlite
```

The current database is saved as `…_before_restore.sqlite` first, so a
restore can itself be undone. Files that are not valid litresearch
databases are refused.

## Loading a profile, or starting over

- `litresearch profile load FILE` (or Profile → Load) **replaces** your
  progress with a saved profile, including its locked rules and its
  recommendations; your current state is saved first.
- `litresearch profile reset` (or Profile → Start over) saves the profile and
  then deletes all progress, so you can start a new game with other rules. The
  Paperpile library stays.
- A file that was edited outside litresearch (checksum mismatch) is refused.

## Moving to another machine, or merging two

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

## How progress is protected against crashes

- Every action (logging a reading, a note, a queue change) is **one database
  transaction**: either everything is saved or nothing.
  `tests/test_robustness.py` simulates crashes in the middle of actions.
- Commits are written with `PRAGMA synchronous=FULL`: a confirmed change is
  on the disk, even if the power drops right afterwards (relevant on a
  Raspberry Pi SD card).
- The CLI and the web UI can write at the same time; a writer waits up to 30
  seconds for the other instead of failing.
- Config changes and exports are written to a temporary file and then renamed,
  so a crash never leaves a half-written `config.yaml`.
- A failed automatic backup is logged and never stops the server.
