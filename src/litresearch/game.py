"""Gamification: experience points, levels, streaks, weekly quests, badges.

Design goals:

- Reward *depth* and *discipline* (reading the top paper of the queue first),
  not just volume.
- Points can never be farmed: each paper pays its base points only up to the
  deepest level it has ever reached, and every bonus is paid once.
- Every point is stored as an ``XpEvent`` with a reason, so the score stays
  traceable even if the rules change later.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlmodel import Session, func, select

from litresearch.config import Config
from litresearch.db import (
    READ_LEVELS,
    Achievement,
    Paper,
    QueueItem,
    Reading,
    ReadStatus,
    Recommendation,
    StatusEvent,
    XpEvent,
    depth,
)

BASE_XP = {
    ReadStatus.abstract: 5,
    ReadStatus.skimmed: 15,
    ReadStatus.read: 40,
    ReadStatus.thorough: 80,
}
FROG_BONUS = 0.5  # share of the base points for reading the queue's top paper
STREAK_STEP = 0.05  # +5 % per consecutive reading week ...
STREAK_MAX_STEPS = 10  # ... up to +50 %
CLASSIC_PERCENTILE = 99.0
CLASSIC_XP = 20
HOT_DAYS = 30
HOT_XP = 10
RECOMMENDATION_XP = 15
RATING_XP = 2
REFLECTION_CHARS = 140
REFLECTION_XP = 10
QUEST_XP = 50
BADGE_XP = 25
INBOX_ZERO_XP = 10

LEVELS = [
    (0, "Curious Undergrad"),
    (100, "Lab Rotation Student"),
    (300, "PhD Candidate"),
    (700, "Journal Club Regular"),
    (1300, "Postdoc"),
    (2200, "Senior Scientist"),
    (3500, "Group Leader"),
    (5500, "Professor"),
    (8000, "Review Editor"),
    (12000, "Hallmarks Author"),
]


@dataclass(frozen=True)
class Badge:
    code: str
    name: str
    description: str
    stat: str
    target: int


BADGES = [
    Badge(
        "first_read", "First Steps", "Read your first paper (skimmed or deeper)", "papers_read", 1
    ),
    Badge("bookworm", "Bookworm", "Read 25 papers", "papers_read", 25),
    Badge("centurion", "Centurion", "Read 100 papers", "papers_read", 100),
    Badge("deep_diver", "Deep Diver", "Read 5 papers thoroughly", "thorough", 5),
    Badge(
        "frog_eater", "Frog Eater", "Read the top paper of your queue first, 10 times", "frogs", 10
    ),
    Badge("classicist", "Classicist", "Read 5 landmark papers (top 1 % cited)", "classics", 5),
    Badge("early_bird", "Early Bird", "Read 5 papers within 30 days of publication", "hot", 5),
    Badge("polymath", "Polymath", "Read papers from 8 different research topics", "topics", 8),
    Badge("scribe", "Scribe", "Write substantial notes on 10 papers", "reflections", 10),
    Badge("critic", "Critic", "Rate 20 papers", "ratings", 20),
    Badge("streak_4", "Month of Momentum", "Read something 4 weeks in a row", "best_streak", 4),
    Badge("streak_12", "Quarter Marathon", "Read something 12 weeks in a row", "best_streak", 12),
    Badge("streak_26", "Half-Year Hero", "Read something 26 weeks in a row", "best_streak", 26),
    Badge("quest_master", "Quest Master", "Complete 10 weekly quests", "quests", 10),
    Badge("inbox_zero", "Inbox Zero", "Empty your reading queue by reading it", "inbox_zero", 1),
]


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


@dataclass
class Level:
    number: int
    title: str
    floor: int
    next_floor: int | None

    def progress(self, xp: int) -> float:
        if self.next_floor is None:
            return 1.0
        return (xp - self.floor) / (self.next_floor - self.floor)


def level_for(xp: int) -> Level:
    xp = max(xp, 0)  # penalties can push the total below zero
    number = max(i for i, (floor, _) in enumerate(LEVELS) if xp >= floor)
    floor, title = LEVELS[number]
    next_floor = LEVELS[number + 1][0] if number + 1 < len(LEVELS) else None
    return Level(number + 1, title, floor, next_floor)


# --- statistics ------------------------------------------------------------


def total_xp(session: Session) -> int:
    return session.exec(select(func.coalesce(func.sum(XpEvent.points), 0))).one()


def reading_weeks(session: Session) -> list[date]:
    """Weeks (Mondays) with at least one paper skimmed or read deeper."""
    events = session.exec(select(StatusEvent)).all()
    return sorted({week_start(e.at.date()) for e in events if depth(e.status) >= 2})


def streaks(weeks: list[date], today: date) -> tuple[int, int]:
    """(current, best) streak of consecutive reading weeks.

    The current streak survives until the end of the week after the last
    reading week, so a new week does not reset it on Monday morning.
    """
    best = run = 0
    previous = None
    for week in weeks:
        run = run + 1 if previous and week - previous == timedelta(weeks=1) else 1
        best = max(best, run)
        previous = week
    this_week = week_start(today)
    alive = previous is not None and this_week - previous <= timedelta(weeks=1)
    return (run if alive else 0), best


def streak_multiplier(streak: int) -> float:
    return 1 + STREAK_STEP * min(max(streak - 1, 0), STREAK_MAX_STEPS)


def week_progress(session: Session, cfg: Config, day: date) -> dict:
    """Quest progress for the week containing ``day``."""
    start = datetime.combine(week_start(day), datetime.min.time())
    events = session.exec(
        select(StatusEvent).where(
            StatusEvent.at >= start, StatusEvent.at < start + timedelta(weeks=1)
        )
    ).all()
    deepest: dict[int, int] = {}
    for event in events:
        deepest[event.paper_id] = max(deepest.get(event.paper_id, 0), depth(event.status))
    detail = sum(d >= 3 for d in deepest.values())
    total = sum(d >= 2 for d in deepest.values())
    thorough = sum(d >= 4 for d in deepest.values())
    goal_detail, goal_skim = cfg.game.weekly_detail_goal, cfg.game.weekly_skim_goal
    counted_detail = min(detail, goal_detail)
    counted_skim = min(total - counted_detail, goal_skim)
    goal = goal_detail + goal_skim
    fraction = (counted_detail + counted_skim) / goal if goal else 1.0
    xp = session.exec(
        select(func.coalesce(func.sum(XpEvent.points), 0)).where(
            XpEvent.at >= start, XpEvent.at < start + timedelta(weeks=1)
        )
    ).one()
    return {
        "week_start": week_start(day),
        "detail": detail,
        "total": total,
        "thorough": thorough,
        "goal_detail": goal_detail,
        "goal_skim": goal_skim,
        "fraction": fraction,
        "done": fraction >= 1,
        "xp": xp,
    }


def grade(progress: dict, week_over: bool) -> tuple[str, str]:
    """Weekly report-card grade and a short comment."""
    if progress["done"] and progress["thorough"]:
        return "S", "Outstanding: quest done with a deep read"
    if progress["done"]:
        return "A", "Quest complete"
    if progress["fraction"] >= 0.5:
        return "B", "Over halfway there"
    if progress["total"] or progress["fraction"] > 0:
        return "C", "Off to a start"
    return ("-", "Rest week") if week_over else ("-", "Nothing read yet this week")


def _count_kind(session: Session, kind: str) -> int:
    return session.exec(select(func.count()).select_from(XpEvent).where(XpEvent.kind == kind)).one()


def stats(session: Session, today: date) -> dict:
    readings = session.exec(select(Reading, Paper).join(Paper, Paper.id == Reading.paper_id)).all()
    deepest = {}
    for event in session.exec(select(StatusEvent)).all():
        deepest[event.paper_id] = max(deepest.get(event.paper_id, 0), depth(event.status))
    read_ids = {pid for pid, d in deepest.items() if d >= 2}
    topics = set()
    for _, paper in readings:
        if paper.id in read_ids:
            label = (paper.topics or paper.folders or [None])[0]
            if label:
                topics.add(label)
    current, best = streaks(reading_weeks(session), today)
    frogs = len(
        {
            e.paper_id
            for e in session.exec(select(StatusEvent).where(StatusEvent.was_queue_top)).all()
            if depth(e.status) >= 2
        }
    )
    return {
        "xp": total_xp(session),
        "papers_read": len(read_ids),
        "deep_reads": sum(d >= 3 for d in deepest.values()),
        "thorough": sum(d >= 4 for d in deepest.values()),
        "frogs": frogs,
        "classics": _count_kind(session, "classic"),
        "hot": _count_kind(session, "hot"),
        "topics": len(topics),
        "reflections": _count_kind(session, "reflection"),
        "ratings": sum(r.rating is not None for r, _ in readings),
        "streak": current,
        "best_streak": best,
        "quests": _count_kind(session, "quest"),
        "inbox_zero": _count_kind(session, "inbox_zero"),
    }


def reading_impact_factor(session: Session, today: date, weeks: int = 4) -> float:
    """Average XP per week over the last ``weeks`` weeks (current week included)."""
    since = datetime.combine(week_start(today) - timedelta(weeks=weeks - 1), datetime.min.time())
    xp = session.exec(
        select(func.coalesce(func.sum(XpEvent.points), 0)).where(XpEvent.at >= since)
    ).one()
    return xp / weeks


# --- awarding --------------------------------------------------------------


@dataclass
class Award:
    events: list[XpEvent] = field(default_factory=list)
    badges: list[Badge] = field(default_factory=list)
    level_before: Level | None = None
    level_after: Level | None = None

    @property
    def points(self) -> int:
        return sum(e.points for e in self.events)


def _already(session: Session, kind: str, paper_id: int | None = None, reason: str | None = None):
    query = select(XpEvent).where(XpEvent.kind == kind)
    if paper_id is not None:
        query = query.where(XpEvent.paper_id == paper_id)
    if reason is not None:
        query = query.where(XpEvent.reason == reason)
    return session.exec(query).first() is not None


def award_reading(
    session: Session,
    cfg: Config,
    paper: Paper,
    previous_depth: int,
    status: ReadStatus,
    was_queue_top: bool,
    rated: bool,
    queue_emptied: bool,
    at: datetime,
) -> Award:
    """Points for a status change that has already been stored.

    ``previous_depth`` is the deepest level this paper had reached before.
    """
    award = Award(level_before=level_for(total_xp(session)))
    events = award.events
    today = at.date()

    def add(points: int, kind: str, reason: str, paper_id: int | None = paper.id):
        if points:
            events.append(
                XpEvent(paper_id=paper_id, points=points, kind=kind, reason=reason, at=at)
            )

    new_depth = depth(status)
    base = 0
    if new_depth > previous_depth:
        previous_xp = BASE_XP[READ_LEVELS[previous_depth - 1]] if previous_depth else 0
        base = BASE_XP[status] - previous_xp
        add(base, "read", f"{status.value}: {paper.title[:60]}")
        current_streak, _ = streaks(reading_weeks(session), today)
        multiplier = streak_multiplier(current_streak)
        add(
            round(base * (multiplier - 1)),
            "streak",
            f"{current_streak}-week streak x{multiplier:.2f}",
        )
        if was_queue_top and new_depth >= 2:
            add(round(base * FROG_BONUS), "frog", "Ate the frog: read your top paper first")

    if new_depth >= 3 and (paper.citation_percentile or 0) >= CLASSIC_PERCENTILE:
        if not _already(session, "classic", paper.id):
            add(CLASSIC_XP, "classic", "Read a landmark paper (top 1 % cited)")
    if new_depth >= 2 and paper.pub_date and (today - paper.pub_date).days <= HOT_DAYS:
        if not _already(session, "hot", paper.id):
            add(HOT_XP, "hot", "Hot off the press: published within 30 days")
    if new_depth >= 2 and not _already(session, "recommended", paper.id):
        recommended = session.exec(
            select(Recommendation).where(Recommendation.paper_id == paper.id)
        ).first()
        if recommended:
            add(RECOMMENDATION_XP, "recommended", "Read a paper from your recommendations")
    if rated and not _already(session, "rating", paper.id):
        add(RATING_XP, "rating", "Rated a paper")
    reading = session.get(Reading, paper.id)
    if reading and len(reading.notes) >= REFLECTION_CHARS:
        if not _already(session, "reflection", paper.id):
            add(REFLECTION_XP, "reflection", "Wrote substantial notes")
    if queue_emptied and not _already(session, "inbox_zero"):
        add(INBOX_ZERO_XP, "inbox_zero", "Inbox zero: emptied the reading queue", None)

    for event in events:
        session.add(event)
    session.flush()

    progress = week_progress(session, cfg, today)
    quest = f"Weekly quest {progress['week_start'].isoformat()}"
    if progress["done"] and not _already(session, "quest", reason=quest):
        event = XpEvent(points=QUEST_XP, kind="quest", reason=quest, at=at)
        session.add(event)
        events.append(event)
        session.flush()

    award.badges = unlock_badges(session, today, at, events)
    session.flush()
    award.level_after = level_for(total_xp(session))
    return award


def unlock_badges(
    session: Session, today: date, at: datetime, events: list[XpEvent]
) -> list[Badge]:
    current = stats(session, today)
    owned = set(session.exec(select(Achievement.code)).all())
    unlocked = []
    for badge in BADGES:
        if badge.code not in owned and current[badge.stat] >= badge.target:
            session.add(Achievement(code=badge.code, unlocked_at=at))
            event = XpEvent(points=BADGE_XP, kind="badge", reason=f"Badge: {badge.name}", at=at)
            session.add(event)
            events.append(event)
            unlocked.append(badge)
    return unlocked


def badge_progress(session: Session, today: date) -> list[tuple[Badge, int, bool]]:
    """(badge, current value, unlocked) for every badge."""
    current = stats(session, today)
    owned = set(session.exec(select(Achievement.code)).all())
    return [(b, min(current[b.stat], b.target), b.code in owned) for b in BADGES]


def queue_top_waiting_days(session: Session, today: date) -> int | None:
    top = session.exec(select(QueueItem).where(QueueItem.position == 1)).first()
    return (today - top.added_at.date()).days if top else None


# --- summaries for the dashboard ------------------------------------------------

KIND_LABELS = {
    "read": "Reading",
    "streak": "Streak bonus",
    "frog": "Ate the frog",
    "classic": "Classic paper",
    "hot": "Hot off the press",
    "recommended": "Followed a recommendation",
    "rating": "Rating",
    "reflection": "Reflection",
    "inbox_zero": "Inbox zero",
    "quest": "Weekly quest",
    "badge": "Badge",
    "penalty": "Penalty",
}


def weekly_summary(session: Session, cfg: Config, monday: date, today: date) -> dict:
    """Everything that happened in one week, for the weekly report."""
    start = datetime.combine(monday, datetime.min.time())
    end = start + timedelta(weeks=1)
    progress = week_progress(session, cfg, monday)
    letter, comment = grade(progress, week_over=end.date() <= today)
    events = session.exec(
        select(StatusEvent, Paper)
        .join(Paper, Paper.id == StatusEvent.paper_id)
        .where(StatusEvent.at >= start, StatusEvent.at < end)
        .order_by(StatusEvent.at)
    ).all()
    papers: dict[int, dict] = {}
    for event, paper in events:
        entry = papers.setdefault(paper.id, {"paper": paper, "status": event.status, "depth": 0})
        if depth(event.status) >= entry["depth"]:
            entry.update(status=event.status, depth=depth(event.status))
    xp_events = session.exec(select(XpEvent).where(XpEvent.at >= start, XpEvent.at < end)).all()
    by_kind: dict[str, int] = {}
    for event in xp_events:
        by_kind[event.kind] = by_kind.get(event.kind, 0) + event.points
    badges = session.exec(
        select(Achievement).where(Achievement.unlocked_at >= start, Achievement.unlocked_at < end)
    ).all()
    return {
        "week_start": monday,
        "grade": letter,
        "comment": comment,
        "progress": progress,
        "xp": sum(by_kind.values()),
        "xp_by_kind": dict(sorted(by_kind.items(), key=lambda kv: -kv[1])),
        "papers": sorted(papers.values(), key=lambda e: -e["depth"]),
        "badges": [b for b in BADGES if b.code in {a.code for a in badges}],
        "current": monday == week_start(today),
    }


def weekly_summaries(session: Session, cfg: Config, today: date, weeks: int = 12) -> list[dict]:
    """Most recent week first."""
    this_week = week_start(today)
    return [
        weekly_summary(session, cfg, this_week - timedelta(weeks=i), today) for i in range(weeks)
    ]


def ledger(
    session: Session, kind: str | None = None, limit: int = 50, offset: int = 0
) -> tuple[list[dict], int]:
    """XP transactions, newest first, with the running balance after each one."""
    events = session.exec(select(XpEvent).order_by(XpEvent.at, XpEvent.id)).all()
    papers = {p.id: p for p in session.exec(select(Paper)).all()}
    balance, rows = 0, []
    for event in events:
        balance += event.points
        rows.append({"event": event, "balance": balance, "paper": papers.get(event.paper_id)})
    rows.reverse()
    if kind:
        rows = [r for r in rows if r["event"].kind == kind]
    return rows[offset : offset + limit], len(rows)


def reading_calendar(session: Session, today: date, weeks: int = 26) -> list[list[dict]]:
    """Weeks (columns) x weekdays (rows) of reading activity, oldest week first."""
    first = week_start(today) - timedelta(weeks=weeks - 1)
    counts: dict[date, int] = {}
    xp: dict[date, int] = {}
    for event in session.exec(
        select(StatusEvent).where(StatusEvent.at >= datetime.combine(first, datetime.min.time()))
    ).all():
        if depth(event.status) >= 1:
            counts[event.at.date()] = counts.get(event.at.date(), 0) + 1
    for event in session.exec(
        select(XpEvent).where(XpEvent.at >= datetime.combine(first, datetime.min.time()))
    ).all():
        xp[event.at.date()] = xp.get(event.at.date(), 0) + event.points
    columns = []
    for w in range(weeks):
        monday = first + timedelta(weeks=w)
        column = []
        for d in range(7):
            day = monday + timedelta(days=d)
            column.append(
                {
                    "date": day,
                    "count": counts.get(day, 0),
                    "xp": xp.get(day, 0),
                    "future": day > today,
                }
            )
        columns.append(column)
    return columns


def personal_bests(session: Session, cfg: Config, today: date) -> dict:
    events = session.exec(select(XpEvent)).all()
    per_week: dict[date, int] = {}
    for event in events:
        monday = week_start(event.at.date())
        per_week[monday] = per_week.get(monday, 0) + event.points
    papers_per_week: dict[date, set[int]] = {}
    for event in session.exec(select(StatusEvent)).all():
        if depth(event.status) >= 2:
            papers_per_week.setdefault(week_start(event.at.date()), set()).add(event.paper_id)
    best_week = max(per_week.items(), key=lambda kv: kv[1], default=(None, 0))
    busiest = max(papers_per_week.items(), key=lambda kv: len(kv[1]), default=(None, set()))
    s = stats(session, today)
    return {
        "best_week": best_week[0],
        "best_week_xp": best_week[1],
        "busiest_week": busiest[0],
        "busiest_week_papers": len(busiest[1]),
        "longest_streak": s["best_streak"],
        "papers_read": s["papers_read"],
        "thorough": s["thorough"],
        "quests": s["quests"],
    }


def next_goals(session: Session, cfg: Config, today: date) -> list[str]:
    """Short, concrete hints on what to do next."""
    hints = []
    progress = week_progress(session, cfg, today)
    if not progress["done"]:
        deep_missing = max(progress["goal_detail"] - progress["detail"], 0)
        total_missing = max(progress["goal_detail"] + progress["goal_skim"] - progress["total"], 0)
        parts = []
        if deep_missing:
            parts.append(f"{deep_missing} deep read" + ("s" if deep_missing > 1 else ""))
        extra = max(total_missing - deep_missing, 0)
        if extra:
            parts.append(f"{extra} more skim" + ("s" if extra > 1 else ""))
        if parts:
            hints.append(f"Weekly quest: {' and '.join(parts)} to go (+{QUEST_XP} XP)")
    xp = total_xp(session)
    level = level_for(xp)
    if level.next_floor:
        hints.append(f"{level.next_floor - xp} XP until {LEVELS[level.number][1]}")
    current, _ = streaks(reading_weeks(session), today)
    this_week_read = week_start(today) in reading_weeks(session)
    if current and not this_week_read:
        hints.append(f"Read something this week to keep your {current}-week streak alive")
    closest = sorted(
        ((b, v) for b, v, unlocked in badge_progress(session, today) if not unlocked and v > 0),
        key=lambda bv: bv[0].target - bv[1],
    )[:1]
    for badge, value in closest:
        hints.append(f"{badge.target - value} more for the '{badge.name}' badge")
    return hints
