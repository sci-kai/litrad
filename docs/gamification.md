# Gamification

The point system rewards **depth** and **discipline** (reading the paper you
planned to read first), not volume. All values are constants at the top of
`src/litresearch/game.py`.

## Experience points (XP)

| Action | XP |
|---|---|
| Read the abstract | 5 |
| Skimmed | 15 |
| Read | 40 |
| Read thoroughly | 80 |

- Upgrading a paper pays only the difference (abstract → read = 35).
- Re-logging a paper, or downgrading and upgrading it again, pays nothing.
  Base points go up to the deepest level ever reached.
- Dismissing a paper earns nothing.

### Bonuses

| Bonus | XP | Condition |
|---|---|---|
| 🐸 **Eat the frog** | +50 % of the base points | the paper was **q1** (up next) and you read it to at least `skimmed` |
| 🔥 **Streak** | +5 % per consecutive reading week, max +50 % | applied to base points |
| 🏛 **Classic** | +20 | read (or thoroughly) a paper in the top 1 % cited |
| 📰 **Hot off the press** | +10 | skimmed or deeper within 30 days of publication |
| 🎯 **Followed a recommendation** | +15 | detail recommendation read ≥ `read`, skim recommendation ≥ `skimmed` |
| ✍ **Reflection** | +10 | notes on the paper reach 140 characters |
| ⭐ **Rating** | +2 | first rating of a paper |
| 📭 **Inbox zero** | +10 | the queue becomes empty because you *read* its last paper (dropping and dismissing do not count) |
| 🗓 **Weekly quest** | +50 | once per week, see below |
| 🏅 **Badge** | +25 | per unlocked badge |

Paper-specific bonuses are paid once per paper.

## Streaks

A *reading week* is a week (Monday–Sunday) in which at least one paper
reached `skimmed` or deeper. The streak counts consecutive reading weeks. It
stays alive through the following week, so it does not reset on Monday morning
before you had a chance to read.

## Weekly quest and report card

Default quest (configurable under `game:`): **1 paper read at least at
`read` depth + 2 more papers at least skimmed**.

| Grade | Meaning |
|---|---|
| S | quest complete, including a thorough read |
| A | quest complete |
| B | at least half of the quest |
| C | something read |
| - | nothing read (a rest week) |

## Levels

| Level | Title | XP |
|---|---|---|
| 1 | Curious Undergrad | 0 |
| 2 | Lab Rotation Student | 100 |
| 3 | PhD Candidate | 300 |
| 4 | Journal Club Regular | 700 |
| 5 | Postdoc | 1 300 |
| 6 | Senior Scientist | 2 200 |
| 7 | Group Leader | 3 500 |
| 8 | Professor | 5 500 |
| 9 | Review Editor | 8 000 |
| 10 | Hallmarks Author | 12 000 |

A typical week (quest done, one paper read thoroughly from the top of the queue,
a few bonuses) is worth about 200–300 XP, so you reach Postdoc in roughly two
months.

## Badges

| Badge | Requirement |
|---|---|
| First Steps | first paper skimmed or deeper |
| Bookworm / Centurion | 25 / 100 papers read |
| Deep Diver | 5 papers read thoroughly |
| Frog Eater | 10 times read the top paper first |
| Classicist | 5 landmark papers (top 1 % cited) read |
| Early Bird | 5 papers read within 30 days of publication |
| Polymath | papers from 8 different research topics (OpenAlex topic, else Paperpile folder) |
| Scribe | substantial notes on 10 papers |
| Critic | 20 papers rated |
| Month of Momentum / Quarter Marathon / Half-Year Hero | 4 / 12 / 26-week streak |
| Quest Master | 10 weekly quests completed |
| Inbox Zero | emptied the queue by reading |

## Performance metrics

- **Reading Impact Factor**: average XP per week over the last four weeks,
  the current one included. Like a journal impact factor, it reflects recent
  output, not the lifetime total.
- **Discipline**: share of papers you read while they were at the top of the
  queue. High values mean you follow your own priorities.
- **Depth**: share of papers read at least at `read` depth, as opposed to
  only skimmed.
- **Queue nudge**: `queue` and `score` warn when the top paper has been
  waiting 14 days or more. Read it, or drop it if it no longer matters.

Every point is stored as an `XpEvent` with its reason, so the score can be
audited (`history`) and is not affected by later rule changes.
