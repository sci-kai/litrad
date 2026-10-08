# How papers are recommended

This page documents every rule that decides which papers you see. Each
number is a setting (shown as `selection.…`) that you can change in the UI
(**Settings**) or in `config/config.yaml`. The UI page **How it works** shows
the same rules with your current values, and each recommendation has an
**evidence tree** with the actual numbers for that paper.

The code lives in `src/litresearch/recommend/` (`engine.py`: candidates and
filters; `scoring.py`: scores and selection). Keep this document in sync
with it.

## Overview

```
topics ─► OpenAlex searches (fresh, landmark) ─┐
library ─► Semantic Scholar recommendations ───┴─► candidates
   ─► filters ─► fresh-or-landmark ─► score ─► score floor ─► diverse picks ─► ranked list
```

One batch per week (Monday to Sunday). Rebuilding on the same day reuses
cached searches and costs no extra API budget. Every batch stores:

- a **funnel**: how many candidates each step removed;
- a **snapshot of the settings** used, so old batches stay interpretable
  after you change settings.

## 1. Candidates

### OpenAlex topic searches (`sources.openalex`)

For every query of every topic (`topics[].queries`), two searches in titles
and abstracts, sorted by OpenAlex relevance, `selection.results_per_query`
results each (default 25):

| Search | Filter |
|---|---|
| fresh | published within `selection.fresh_window_months` (6); types article, review, preprint |
| landmark | published in the last `selection.landmark_max_age_years` (15) years, citation percentile above `selection.landmark_min_percentile` (95) |

A full-text search sorted by citations was tested and rejected: it returned
off-topic hits (e.g. a FASTQ preprocessing tool for "circulating tumor DNA").

### Semantic Scholar recommendations (`sources.semanticscholar`)

Semantic Scholar's recommendation service returns recently published papers
similar to *positive* and unlike *negative* example papers:

- positives (up to 100): papers you rated 4–5 stars or read thoroughly, then
  your most recent library papers;
- negatives (up to 50): papers you marked **not relevant**.

So your ratings and dismissals change what this source suggests. Works
without an API key; when the free service is busy, this step is skipped
with a warning.

## 2. Filters

A candidate is removed (counted in the funnel) if it

| Reason (funnel name) | Rule |
|---|---|
| `wrong_type` | is not an article, review or preprint (editorial, erratum, letter, …) |
| `meeting_abstract` | is a conference abstract from a journal supplement: titles like `0865 …`, `P-123: …`, `LB01. …` (but not `CD8 T cells…`, `53BP1…`, `1000 Genomes…`) |
| `read_or_dismissed` | was marked skimmed, read or thorough in litresearch, or not relevant (an abstract glance does **not** count as read) |
| `queued` | is on your reading queue |
| `recently_recommended` | was recommended in one of the last `selection.cooldown_weeks` (8) weeks; batches of the current week do not count, so a rebuild can show the same papers |
| `excluded_term` | contains one of `excluded_terms` in title or abstract (case-insensitive) |

Papers without a DOI are skipped, since the pipeline is keyed on DOIs
(`without_doi` in the funnel).

Papers in your **Paperpile library are not excluded**: adding a paper to
Paperpile does not mean you have read it. Only your markings in litresearch
count. A library paper that is recommended carries the note "in your
Paperpile, not read yet".

## 3. Eligibility: current *or* important

Only two kinds of papers are scored:

- **fresh**: published at most `fresh_window_months` × 30.5 days ago;
- **landmark**: older, but with an OpenAlex citation percentile (normalised by
  field and publication year) ≥ `landmark_min_percentile` and an age of at
  most `landmark_max_age_years`.

Papers without a date, and older papers below the threshold, are dropped
(`not_fresh_or_landmark` in the funnel).

## 4. Score

```
score = w_r × relevance + w_i × impact + w_t × timeliness
```

with `selection.weights` (default 0.5 / 0.3 / 0.2). All components are in
[0, 1]; only the ratio of the weights matters.

### Relevance

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

### Impact

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

### Timeliness

- fresh: `0.5 + 0.5 × (1 − age / fresh window)`, from 1.0 on publication day
  down to 0.5 at the end of the window;
- landmark: constant `selection.landmark_timeliness` (0.4).

## 5. Selection

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

## 6. How deeply to read is your choice

Recommendations do not prescribe a reading depth. After reading, you mark
the paper (abstract, skimmed, read, thorough) and earn the points your game
profile defines for that depth ([gamification.md](gamification.md)).
Batches built before this change still show their old detail/skim labels in
the data, but the UI ignores them.

## The evidence tree

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

## Feedback loop

- **Not relevant** (dismiss): never shown again; becomes a negative example
  for Semantic Scholar.
- **Rating 4–5 or thorough read**: becomes a positive example for Semantic
  Scholar.
- Your reading queue, read papers and the cooldown keep the list fresh.
