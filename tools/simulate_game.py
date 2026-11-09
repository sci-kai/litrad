#!/usr/bin/env python3
"""Deterministic simulation of the reading game for typical reader personas.

Runs the real game code (``reading.record_reading``, the queue, the drop
penalty, badges, quests, levels) on a temporary database for each persona
and prints Markdown tables: XP and level over time, badges with the week
they unlock, the share of badges reached within one and two years, the
quest completion rate and how often the drop penalty hits. Nothing touches
the network or a real profile; the random choices come from a fixed seed,
so two runs give the same tables.

Personas:

- ``new``: bachelor/master student, 0-4 papers a week, mostly abstracts and
  skims at first, deeper later; holidays and busy weeks.
- ``experienced``: PhD/postdoc; on day 1 logs a backlog (35 read/thorough,
  150 skimmed; "read earlier" when the version has it), then reads like
  ``new`` but deeper; some recommendations are papers read before.
- ``binge``: reads a lot for three weeks, then stops.
- ``regular``: every week one deep read and two skims, top paper first.

Usage::

    tools/simulate_game.py                    # all personas, 104 weeks, to stdout
    tools/simulate_game.py --out DIR          # also write DIR/<persona>.md and summary.md
    tools/simulate_game.py --persona new --weeks 12
"""

import argparse
import inspect
import json
import random
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path

from sqlmodel import Session, select

from litresearch.config import Config
from litresearch.db import (
    Achievement,
    Category,
    Paper,
    QueueItem,
    ReadStatus,
    Recommendation,
    RecommendationBatch,
    XpEvent,
    depth,
    init_db,
)
from litresearch.game import level_for, stats, total_xp
from litresearch.profile import profile_rules, start_profile
from litresearch.reading import ReadingError, drop_from_queue, queue_add, record_reading
from litresearch.rules import GameRules

START = date(2027, 1, 4)  # a Monday
REPORT_WEEKS = [1, 2, 4, 8, 12, 26, 52, 104]
DEPTHS = [ReadStatus.abstract, ReadStatus.skimmed, ReadStatus.read, ReadStatus.thorough]
NOTE = "Key result, method and how it relates to my project. " * 4  # > 140 characters
# whether this version can log readings done before using litresearch
HAS_EARLIER = "earlier" in inspect.signature(record_reading).parameters


@dataclass
class Persona:
    name: str
    title: str
    # papers per week: weights for 0, 1, 2, ... papers
    per_week: list[float]
    # depth weights (abstract, skimmed, read, thorough) at week 1 and week 104
    depth_start: list[float]
    depth_end: list[float]
    topics_start: int = 3  # topics read in the first weeks; more join over time
    topics_max: int = 12
    p_queue: float = 0.6  # a reading goes through the queue
    p_top_first: float = 0.6  # a queued reading takes the top paper
    p_drop: float = 0.06  # per week: one queued paper is dropped unread
    p_rate: float = 0.3
    p_note: float = 0.3  # deep reads with substantial notes
    p_dismiss: float = 0.3  # per week: one recommendation marked not relevant
    p_from_rec: float = 0.5
    p_from_library: float = 0.3
    p_deepen: float = 0.1  # per week: a skimmed paper is read again more deeply
    p_seen_rec: float = 0.0  # a recommendation is a paper read before litresearch
    hours: list[int] = field(default_factory=lambda: [9, 10, 11, 14, 15, 16, 17, 20])
    weekend_share: float = 0.1
    holidays: set[int] = field(default_factory=set)  # weeks without reading
    busy_share: float = 0.12  # extra random weeks without reading
    stop_after: int | None = None
    backlog: tuple[int, int, int] | None = None  # (thorough, read, skimmed) on day 1


def _holidays(*ranges) -> set[int]:
    weeks = set()
    for year in (0, 52):
        for a, b in ranges:
            weeks.update(range(year + a, year + b + 1))
    return weeks


PERSONAS = {
    "new": Persona(
        "new",
        "New researcher (bachelor/master student)",
        per_week=[0.1, 0.3, 0.3, 0.2, 0.1],
        depth_start=[0.45, 0.4, 0.12, 0.03],
        depth_end=[0.25, 0.4, 0.25, 0.1],
        holidays=_holidays((30, 32), (51, 52)),
    ),
    "experienced": Persona(
        "experienced",
        "Experienced researcher (PhD/postdoc) with a backlog",
        per_week=[0.05, 0.15, 0.3, 0.3, 0.15, 0.05],
        depth_start=[0.15, 0.4, 0.3, 0.15],
        depth_end=[0.15, 0.35, 0.35, 0.15],
        topics_start=8,
        topics_max=18,
        p_from_rec=0.5,
        p_from_library=0.4,
        p_seen_rec=0.15,
        p_rate=0.4,
        holidays=_holidays((31, 33), (52, 52)),
        backlog=(15, 20, 150),
    ),
    "binge": Persona(
        "binge",
        "Binge, then quit",
        per_week=[0, 0, 0, 0, 0, 0, 0, 0, 0, 0.3, 0.4, 0.3],
        depth_start=[0.3, 0.4, 0.2, 0.1],
        depth_end=[0.3, 0.4, 0.2, 0.1],
        busy_share=0,
        stop_after=3,
        hours=[9, 14, 20, 22, 23],
        weekend_share=0.3,
    ),
    "regular": Persona(
        "regular",
        "Very regular reader (1 deep read + 2 skims every week)",
        per_week=[0, 0, 0, 1],
        depth_start=[0, 1, 0, 0],
        depth_end=[0, 1, 0, 0],
        p_queue=1.0,
        p_top_first=1.0,
        p_drop=0.0,
        busy_share=0,
        hours=[8, 9],
        weekend_share=0,
    ),
}


class Sim:
    def __init__(self, persona: Persona, folder: Path, seed: int, rules: GameRules | None):
        self.p = persona
        self.rng = random.Random(f"{persona.name}-{seed}")
        self.cfg = Config.model_validate(
            {
                "paths": {
                    "bibtex": str(folder / "none.bib"),
                    "database": str(folder / "sim.sqlite"),
                    "cache_dir": str(folder / "cache"),
                    "base_dir": str(folder),
                },
                "backup": {"dir": str(folder / "backups")},
            }
        )
        self.engine = init_db(self.cfg.paths.database)
        self.session = Session(self.engine)
        start_profile(self.session, self.cfg, rules=rules, at=datetime.combine(START, time(8, 0)))
        self.rules = profile_rules(self._profile())
        self.counter = 0
        self.badge_weeks: dict[str, int] = {}
        self.timeline: dict[int, tuple[int, int, str]] = {}
        self.penalties = 0
        self.seen: list[Paper] = []  # read before litresearch, not logged yet
        self.seen_ids: set[int] = set()
        self.logged_earlier: list[Paper] = []
        self.backlog_level = None

    def _profile(self):
        from litresearch.profile import get_profile

        return get_profile(self.session)

    # --- papers ---------------------------------------------------------------------

    def paper(self, today: date, week: int, in_library=False, hot=None) -> Paper:
        self.counter += 1
        n_topics = min(self.p.topics_max, self.p.topics_start + week * self.p.topics_max // 80)
        topic = f"Topic {self.rng.randrange(n_topics) + 1:02d}"
        roll = self.rng.random() if hot is None else hot
        if roll < 0.05:
            published = today - timedelta(days=self.rng.randrange(0, 7))
        elif roll < 0.3:
            published = today - timedelta(days=self.rng.randrange(7, 30))
        else:
            published = today - timedelta(days=self.rng.randrange(60, 365 * 30))
        paper = Paper(
            title=f"Simulated paper {self.counter}",
            topics=[topic],
            pub_date=published,
            year=published.year,
            citation_percentile=round(self.rng.uniform(0, 100), 1),
            in_library=in_library,
        )
        self.session.add(paper)
        self.session.commit()  # a rolled-back queue_add must not take the paper with it
        return paper

    def batch(self, today: date, week: int) -> list[Paper]:
        batch = RecommendationBatch(week_start=today, n_candidates=50)
        self.session.add(batch)
        self.session.flush()
        papers = []
        for rank in range(1, 6):
            roll = self.rng.random()
            if self.seen and roll < self.p.p_seen_rec:
                paper = self.seen.pop(self.rng.randrange(len(self.seen)))
            elif self.logged_earlier and roll < self.p.p_seen_rec * 1.3:
                # a paper of the backlog comes back: logging it again earns nothing new
                paper = self.logged_earlier.pop(self.rng.randrange(len(self.logged_earlier)))
            else:
                paper = self.paper(today, week)
            self.session.add(
                Recommendation(
                    batch_id=batch.id,
                    paper_id=paper.id,
                    rank=rank,
                    category=Category.open,
                    score=0.5,
                )
            )
            papers.append(paper)
        self.session.commit()
        return papers

    # --- actions --------------------------------------------------------------------

    def depth_for(self, week: int) -> ReadStatus:
        share = min(week / 104, 1)
        weights = [
            a + (b - a) * share for a, b in zip(self.p.depth_start, self.p.depth_end, strict=True)
        ]
        return self.rng.choices(DEPTHS, weights)[0]

    def moment(self, monday: date) -> datetime:
        weekend = self.rng.random() < self.p.weekend_share
        day = monday + timedelta(days=self.rng.choice([5, 6] if weekend else [0, 1, 2, 3, 4]))
        return datetime.combine(day, time(self.rng.choice(self.p.hours), self.rng.randrange(60)))

    def read(self, paper: Paper, status: ReadStatus, at: datetime, earlier=False):
        rating = self.rng.randint(2, 5) if self.rng.random() < self.p.p_rate else None
        note = NOTE if depth(status) >= 3 and self.rng.random() < self.p.p_note else None
        extra = {"earlier": True} if earlier and HAS_EARLIER else {}
        try:
            record_reading(
                self.session, self.cfg, paper, status, rating=rating, note=note, at=at, **extra
            )
        except ReadingError:
            self.session.rollback()

    def log_backlog(self):
        thorough, read, skimmed = self.p.backlog
        at = datetime.combine(START, time(10, 0))
        plan = (
            [ReadStatus.thorough] * thorough
            + [ReadStatus.read] * read
            + [ReadStatus.skimmed] * skimmed
        )
        self.rng.shuffle(plan)
        papers = [self.paper(START, 40, in_library=True, hot=1.0) for _ in plan]
        self.logged_earlier += papers
        if HAS_EARLIER:  # the bulk action of a library page, one depth at a time
            from litresearch.reading import mark_read_earlier

            for status in DEPTHS[::-1]:
                chosen = [p for p, s in zip(papers, plan, strict=True) if s == status]
                if chosen:
                    mark_read_earlier(self.session, self.cfg, chosen, status, at=at)
        else:  # one by one, as a reading of today
            for paper, status in zip(papers, plan, strict=True):
                self.read(paper, status, at)
                at += timedelta(seconds=20)
        # papers read before but not logged: they come back as recommendations
        for _ in range(60):
            paper = self.paper(START, 40, hot=1.0)
            self.seen.append(paper)
            self.seen_ids.add(paper.id)
        self.session.commit()
        xp = total_xp(self.session)
        self.backlog_level = (xp, level_for(xp, self.rules))

    def week(self, week: int):
        monday = START + timedelta(weeks=week - 1)
        recs = self.batch(monday, week)
        if self.p.stop_after and week > self.p.stop_after:
            return
        if week in self.p.holidays or self.rng.random() < self.p.busy_share:
            return
        if self.p.name == "regular":
            plan = [ReadStatus.read, ReadStatus.skimmed, ReadStatus.skimmed]
            if week % 4 == 0:
                plan[0] = ReadStatus.thorough
        else:
            n = self.rng.choices(range(len(self.p.per_week)), self.p.per_week)[0]
            plan = [self.depth_for(week) for _ in range(n)]
        sources = []
        for _ in plan:
            roll = self.rng.random()
            if roll < self.p.p_from_rec and recs:
                sources.append(recs.pop(0))
            elif roll < self.p.p_from_rec + self.p.p_from_library:
                sources.append(self.paper(monday, week, in_library=True))
            else:
                sources.append(self.paper(monday, week))
        # the queue: plan a few papers, read the top one first
        queued = []
        for paper in sources:
            if self.rng.random() < self.p.p_queue:
                try:
                    source = "recommendation" if paper.id in {r.id for r in recs} else None
                    queue_add(self.session, self.cfg, paper, source=source or "manual")
                    queued.append(paper)
                except ReadingError:
                    self.session.rollback()
        moments = sorted(self.moment(monday) for _ in plan)
        for status, paper, at in zip(plan, sources, moments, strict=True):
            if paper in queued and self.rng.random() < self.p.p_top_first:
                top = self.session.exec(select(QueueItem).where(QueueItem.position == 1)).first()
                if top is not None:
                    paper = self.session.get(Paper, top.paper_id)
            # a recommendation you had read before litresearch: logged as read earlier
            self.read(paper, status, at, earlier=paper.id in self.seen_ids)
        if self.rng.random() < self.p.p_deepen:
            self.deepen(monday)
        if self.rng.random() < self.p.p_drop:
            item = self.session.exec(select(QueueItem).order_by(QueueItem.position.desc())).first()
            if item is not None:
                paper = self.session.get(Paper, item.paper_id)
                at = datetime.combine(monday + timedelta(days=4), time(17))
                if drop_from_queue(self.session, self.cfg, paper, at):
                    self.penalties += 1
        if recs and self.rng.random() < self.p.p_dismiss:
            at = datetime.combine(monday + timedelta(days=4), time(18))
            self.read(recs[-1], ReadStatus.dismissed, at)

    def deepen(self, monday: date):
        from litresearch.db import Reading

        shallow = self.session.exec(
            select(Reading).where(Reading.status == ReadStatus.skimmed).limit(50)
        ).all()
        if shallow:
            reading = self.rng.choice(shallow)
            paper = self.session.get(Paper, reading.paper_id)
            self.read(paper, ReadStatus.read, self.moment(monday))

    def snapshot(self, week: int):
        today = START + timedelta(weeks=week) - timedelta(days=1)
        xp = total_xp(self.session)
        level = level_for(xp, self.rules)
        self.timeline[week] = (xp, level.number, level.title)
        for code, unlocked_at in self.session.exec(
            select(Achievement.code, Achievement.unlocked_at)
        ).all():
            self.badge_weeks.setdefault(code, (unlocked_at.date() - START).days // 7 + 1)
        self.today = today


def run(persona: Persona, weeks: int, seed: int = 1, rules: GameRules | None = None) -> dict:
    with tempfile.TemporaryDirectory(prefix="litresearch-sim-") as folder:
        sim = Sim(persona, Path(folder), seed, rules)
        try:
            if persona.backlog:
                sim.log_backlog()
            for week in range(1, weeks + 1):
                sim.week(week)
                sim.snapshot(week)
            result = summarize(sim, weeks)
        finally:
            sim.session.close()
            sim.engine.dispose()
    return result


def summarize(sim: Sim, weeks: int) -> dict:
    session = sim.session
    final = stats(session, sim.today, sim.rules)
    names = {b.code: b for b in sim.rules.badges()}
    quests = final["quests"]
    active = len({w for w in range(1, weeks + 1) if _active(sim, w)})
    penalty_events = session.exec(select(XpEvent).where(XpEvent.kind == "penalty")).all()
    return {
        "persona": sim.p,
        "weeks": weeks,
        "timeline": sim.timeline,
        "badges": sorted(
            ((week, names[code]) for code, week in sim.badge_weeks.items() if code in names),
            key=lambda wb: (wb[0], wb[1].group, wb[1].code),
        ),
        "n_badges": len(names),
        "quests": quests,
        "active_weeks": active,
        "penalties": len(penalty_events),
        "penalty_xp": -sum(e.points for e in penalty_events),
        "stats": final,
        "backlog": sim.backlog_level,
        "levels": len(sim.rules.levels),
        "level_floors": [level.xp for level in sim.rules.levels],
    }


def milestone_gaps(result: dict) -> tuple[list[int], float, int]:
    """Weeks with a level-up or a new badge, the median and the longest gap between them.

    A gap is the number of weeks from one milestone week to the next (1 = a
    milestone every week); the stretch after the last milestone up to the end
    of the run counts too.
    """
    timeline, weeks = result["timeline"], result["weeks"]
    marks = {week for week, _ in result["badges"]}
    marks |= {w for w in range(2, weeks + 1) if timeline[w][1] > timeline[w - 1][1]}
    if timeline[1][1] > 1:
        marks.add(1)
    marks = sorted(marks)
    if not marks:
        return [], float(weeks), weeks
    gaps = [b - a for a, b in zip(marks, marks[1:], strict=False)]
    gaps.append(weeks + 1 - marks[-1])
    gaps.sort()
    median = (
        gaps[len(gaps) // 2]
        if len(gaps) % 2
        else (gaps[len(gaps) // 2 - 1] + gaps[len(gaps) // 2]) / 2
    )
    return marks, float(median), max(gaps)


def _active(sim: Sim, week: int) -> bool:
    if week == 1:
        return sim.timeline[1][0] > 0
    return sim.timeline[week][0] != sim.timeline[week - 1][0]


def markdown(result: dict) -> str:
    p = result["persona"]
    weeks = result["weeks"]
    lines = [f"## {p.title}", ""]
    if result["backlog"]:
        xp, level = result["backlog"]
        lines += [
            f"After the day-1 backlog ({sum(p.backlog)} papers): {xp} XP, "
            f"level {level.number} {level.title}"
            + (f" (next: {level.next_title} at {level.next_floor} XP)" if level.next_floor else ""),
            "",
        ]
    lines += ["| Week | XP | Level |", "|---:|---:|---|"]
    for week in REPORT_WEEKS:
        if week <= weeks:
            xp, number, title = result["timeline"][week]
            lines.append(f"| {week} | {xp} | {number} {title} |")
    # first week at which each level was reached
    reached = {}
    for week in range(1, weeks + 1):
        reached.setdefault(result["timeline"][week][1], week)
    lines += [
        "",
        "Weeks to reach a level: " + ", ".join(f"L{n} w{w}" for n, w in sorted(reached.items())),
        "",
        "| Week | Badges unlocked |",
        "|---:|---|",
    ]
    by_week: dict[int, list[str]] = {}
    for week, badge in result["badges"]:
        by_week.setdefault(week, []).append(badge.name)
    for week, names in sorted(by_week.items()):
        lines.append(f"| {week} | {', '.join(names)} |")
    n = result["n_badges"]
    marks, median, longest = milestone_gaps(result)
    within = {
        limit: sum(1 for week, _ in result["badges"] if week <= limit) for limit in (4, 52, 104)
    }
    lines += [
        "",
        "| Measure | Value |",
        "|---|---:|",
        f"| Badges in month 1 | {within[4]} of {n} |",
        f"| Badges within 1 year | {within[52]} of {n} ({within[52] / n:.0%}) |",
        f"| Badges within 2 years | {within[104]} of {n} ({within[104] / n:.0%}) |",
        f"| Quests completed | {result['quests']} of {weeks} weeks "
        f"({result['quests'] / weeks:.0%}; {result['quests'] / max(result['active_weeks'], 1):.0%}"
        " of active weeks) |",
        f"| Drop penalties | {result['penalties']} ({result['penalty_xp']} XP, "
        f"{result['penalties'] * 52 / weeks:.1f} per year) |",
        "| Weeks with a level-up or badge | "
        f"{len(marks)} of {weeks} (median gap {median:g} weeks, longest {longest}) |",
        f"| Papers read (skimmed+) | {result['stats']['papers_read']} |",
        f"| Deep reads (read+) | {result['stats']['deep_reads']} |",
        f"| Best streak (weeks) | {result['stats']['best_streak']} |",
        "",
    ]
    return "\n".join(lines)


def summary_table(results: list[dict]) -> str:
    weeks = [w for w in REPORT_WEEKS if w <= results[0]["weeks"]]
    head = (
        "| Persona | "
        + " | ".join(f"w{w}" for w in weeks)
        + " | Badges 1y | Badges 2y | Gap median | Gap max |"
    )
    lines = [head, "|---|" + "---:|" * (len(weeks) + 4)]
    for r in results:
        cells = [f"L{r['timeline'][w][1]} ({r['timeline'][w][0]})" for w in weeks]
        b1 = sum(1 for week, _ in r["badges"] if week <= 52)
        b2 = sum(1 for week, _ in r["badges"] if week <= 104)
        _, median, longest = milestone_gaps(r)
        lines.append(
            f"| {r['persona'].name} | "
            + " | ".join(cells)
            + f" | {b1}/{r['n_badges']} | {b2}/{r['n_badges']} | {median:g} | {longest} |"
        )
    return "\n".join(lines) + "\n"


def _run_named(name: str, weeks: int, seed: int) -> dict:
    return run(PERSONAS[name], weeks, seed)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--persona", choices=sorted(PERSONAS), action="append")
    parser.add_argument("--weeks", type=int, default=104)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--out", type=Path, help="folder for the Markdown tables")
    parser.add_argument("--jobs", type=int, default=1, help="personas simulated in parallel")
    args = parser.parse_args(argv)
    names = args.persona or list(PERSONAS)
    tasks = [(name, args.weeks, args.seed) for name in names]
    if args.jobs > 1:
        from multiprocessing import Pool

        with Pool(min(args.jobs, len(tasks))) as pool:
            results = pool.starmap(_run_named, tasks)
    else:
        results = [_run_named(*task) for task in tasks]
    from litresearch import __version__

    rules = GameRules()
    header = (
        f"# Game simulation (litresearch {__version__}, rules {rules.fingerprint()}, "
        f"{args.weeks} weeks, seed {args.seed}, read earlier: {'yes' if HAS_EARLIER else 'no'})\n"
    )
    summary = header + "\nLevel (XP) by week:\n\n" + summary_table(results)
    texts = {r["persona"].name: markdown(r) for r in results}
    print(summary)
    for text in texts.values():
        print(text)
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "summary.md").write_text(summary, encoding="utf-8")
        for name, text in texts.items():
            (args.out / f"{name}.md").write_text(header + "\n" + text, encoding="utf-8")
        raw = {
            r["persona"].name: {
                "xp": [r["timeline"][w][0] for w in range(1, r["weeks"] + 1)],
                "badges": {b.code: week for week, b in r["badges"]},
                "backlog_xp": r["backlog"][0] if r["backlog"] else None,
            }
            for r in results
        }
        (args.out / "raw.json").write_text(json.dumps(raw, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
