# Setup

## 1. Install

Requires Python ≥ 3.11 on Linux (tested on a Raspberry Pi, aarch64, Python 3.13).

```bash
git clone <repo-url> litresearch-tool
cd litresearch-tool
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/litresearch --help
```

## 2. Export the Paperpile library

Paperpile has no public API, so the tool reads a BibTeX file that Paperpile
keeps up to date automatically:

1. In Paperpile open *Settings → Workflows and Integrations* and add a
   **BibTeX export** of the whole library (or of a folder/label) to
   **Google Drive**.
2. Make sure the export includes **DOI** and **PMID** fields. Without them every
   paper has to be matched by title, which is slower and less reliable.
3. Prefer a file name without spaces, e.g. `library.bib`.

What the tool uses from the export:

| BibTeX field | Used for |
|---|---|
| `doi`, `pmid`, `url` | identifiers (a DOI inside `url` is picked up as well) |
| `title`, `author`, `year`, `month`, `journal`, `abstract` | metadata; BibTeX wins over API data |
| `keywords` | Paperpile **folders** (items containing `/`) and **labels** / author keywords (other items) |

The export does not contain the date a paper was added to Paperpile.

## 3. Sync the export to the machine running the tool

Use [rclone](https://rclone.org/drive/) to pull the file from Google Drive:

```bash
rclone config                     # once: create a remote called "gdrive"
rclone copyto "gdrive:<path in Drive>/library.bib" data/paperpile/library.bib
```

Run this before each ingest, e.g. from cron or a systemd timer
(deployment files follow in a later milestone).

## 4. Configure

```bash
cp config/config.example.yaml config/config.yaml
```

Edit at least `paths.bibtex` and your `topics`; see
[configuration.md](configuration.md). `config/config.yaml` and `data/` are
git-ignored, so private settings and API keys stay local.

Recommended (both free):

- **Semantic Scholar API key**, from
  <https://www.semanticscholar.org/product/api>. Without one, all anonymous
  users share a single rate limit and requests often fail with HTTP 429.
- **Contact e-mail** (`api.contact_email`), which puts requests into the
  Crossref and OpenAlex "polite pools".

## 5. Import the library

```bash
.venv/bin/litresearch ingest      # add -v to see every HTTP request
.venv/bin/litresearch status
```

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

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `WARNING: Semantic Scholar: ... HTTP 429` | Shared anonymous rate limit. Add `api.semanticscholar_key`, or rerun later. Everything else still gets imported. |
| A paper is listed under "Not found anywhere" | It has no DOI/PMID and its title could not be matched. Typical causes: conference talks without a DOI, or a broken title in Paperpile (e.g. the abstract pasted into the title field). Fix the entry in Paperpile; the next ingest picks it up. |
| `Duplicate DOIs: key (doi)` | Two Paperpile entries share a DOI. The second one is kept without DOI; merge the duplicates in Paperpile. |
| `duplicate BibTeX key ...` | Two entries share a citation key; only the first is used. |
| `ValueError: ... BibTeX block(s) failed to parse` | The export is broken (e.g. truncated download). Nothing is changed in the database; re-sync the file. |
