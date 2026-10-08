# Gamification

The point system rewards **depth** and **discipline** (reading the paper you
planned to read first), not volume. All rules are stored in your **game
profile** and are **locked** once the game starts.

## Your profile and its locked rules

1. **Set up**: on the **Profile** page (or `litresearch profile start`) you
   name your profile and choose every reward and penalty, the level ladder
   and the badge targets. The form is pre-filled with the defaults from the
   `game` section of `config.yaml`.
2. **Start**: the rules are copied into the database and locked. Their
   **fingerprint** (a short hash of the complete rule set) is shown on the
   profile page. From then on, all points are computed with these stored
   rules; editing `config.yaml` or the settings can no longer change them,
   which prevents inflating points after the fact.
3. If you log a reading before setting up a profile, the game starts
   automatically with the configured defaults, and you get a notice.
4. **Different rules** need a new game: *Start over* saves the current profile
   and resets your progress (the Paperpile library stays). You can load the
   saved profile again at any time.

## Rules you can set (defaults)

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
| `drop_penalty` | 15 | ⚠️ a paper you picked **from the recommendations** leaves the queue (removed or marked not relevant) before you at least skimmed it |
| `levels` | 20 levels | the ladder below |
| `badge_targets` | see below | how much each badge needs |

Fairness rules that always apply:

- Base points per paper are paid only up to the deepest level ever reached.
  Upgrading pays the difference; re-logging or downgrading and upgrading
  again pays nothing.
- Paper bonuses (landmark, fresh, recommendation, rating, notes) are paid
  once per paper. The frog bonus is a share of *newly* earned base points.
- Papers you added yourself or from your library can be removed from the
  queue for free; dismissing a recommendation you never picked is free.
- Penalties can push the total, and therefore the level, down; the level
  never drops below 1.

Recommendations never prescribe how deeply to read: you decide, and your
marking determines the points.

## Streaks

A *reading week* is a week (Monday to Sunday) in which at least one paper
reached "skimmed" or deeper. The streak counts consecutive reading weeks. It
stays alive through the following week, so it does not reset on Monday
morning before you had a chance to read.

## Weekly quest and report card

| Grade | Meaning |
|---|---|
| S | quest complete, including a thorough read |
| A | quest complete |
| B | at least half of the quest |
| C | something read |
| - | nothing read (a rest week) |

## Levels (default ladder)

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

## Badges (default targets)

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

## Saving, loading and comparing profiles

- **Save** (Profile page, `litresearch profile save`) writes a profile file:
  the profile and its locked rules, every reading status, rating and note,
  the reading history, queue, XP transactions, badges and all recommendation
  batches with their evidence. Automatic backups contain the same data.
- **Load** (`litresearch profile load FILE`) replaces your current progress
  with a saved profile; the current state is saved first.
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

## Performance metrics

- **Reading Impact Factor**: average XP per week over the last four weeks.
- **Discipline**: share of papers read while they were at the top of the
  queue.
- **Depth**: share of papers read at least at "read" depth.
- **Queue nudge**: a warning when the top paper has been waiting 14 days or
  more.

## Overviews in the web UI

Weekly reports, the XP ledger with TSV export, XP per week, the reading
calendar, personal bests, next-goal hints, and the badge gallery grouped by
theme ([web-ui.md](web-ui.md)).
