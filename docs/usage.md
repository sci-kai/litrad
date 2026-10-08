# Usage: the weekly reading loop

The easiest way is the web UI: `litresearch serve` (see [web-ui.md](web-ui.md)).
Everything below also works on the command line.

```
recommend  ──pick──>  queue (max 5, ordered)  ──next──>  read ... ──>  score
   ^                       ^                                  │
   │                  manual adds                             │
   └──── dismissals and good ratings refine the next batch ───┘
```

The tool proposes papers, but **you** decide what goes on the queue. The
queue is short and ordered, so there is always exactly one paper to read
next and it never grows into a backlog you will not read.

## 1. Get this week's recommendations

```bash
litresearch recommend
```

The first call in a week builds a new batch, which takes about a minute.
Later calls that week show the same batch without any API requests.
`--refresh` rebuilds it, e.g. after you change your topics.

```
Recommendations for the week of 2026-10-05 (313 candidates checked)

Read in detail
r1  A single-cell lens into the co-evolution of genotypes and phenotypes in cancer
     Franco Izzo et al. (2026-09-08) · Nature reviews. Cancer
     fresh · Nature reviews. Cancer · topic: tumor-evolution
     https://doi.org/10.1038/s41568-026-00970-8
r2  Identifying structural variants using linked-read sequencing data
     ...
     landmark · top 2.9% cited · 92 citations · Bioinformatics · topic: long-read-sv
Skim
r3  ...
```

Every paper is either **fresh** (published within `fresh_window_months`) or a
**landmark** (top `100 - landmark_min_percentile` % cited in its field and
year). Papers you have read, dismissed or queued never show up. A paper that
was recommended but not picked can come back after `cooldown_weeks`.

To run it automatically every Monday morning, add a cron job
(`crontab -e`):

```
0 7 * * 1  cd /path/to/litresearch-tool && .venv/bin/litresearch ingest && .venv/bin/litresearch recommend > data/this-week.txt
```

## 2. Fill your reading queue

```bash
litresearch queue add r1              # a recommendation
litresearch queue add r3 --top        # ... straight to position 1
litresearch queue add Barkley2022-ri  # a paper from your Paperpile library
litresearch queue add 10.1038/s41586-024-07894-z   # any DOI (metadata from OpenAlex)
litresearch queue add --title "Preprint from the lab meeting" --url https://...
litresearch queue                     # show it
litresearch queue move q4 1           # reorder
litresearch queue drop q3             # remove (costs XP for picked recommendations left unread)
```

When the queue is full (`queue.max_size`, default 5) nothing can be added
until you read or drop something. That limit is intentional. Removing a paper
you picked from the recommendations before at least skimming it costs
`game.drop_penalty` XP (default 15); papers you added yourself are free to
remove.

### Referring to papers

| Form | Meaning |
|---|---|
| `r2` | rank 2 in the latest recommendation batch |
| `q1` | position 1 in the queue (positions renumber as papers leave) |
| `#123` | internal paper id (shown by `show`) |
| `10.1038/...`, `https://doi.org/...` | DOI |
| `pmid:35931863` | PubMed ID |
| `Barkley2022-ri` | Paperpile citation key |
| `clonal melanoma` | words from the title (must match exactly one paper) |

## 3. Read

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

## 4. Check your score

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

For how points, levels and badges work, see [gamification.md](gamification.md).
