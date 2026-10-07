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
        ).all()
        if any(new_depth >= (3 if r.category == "detail" else 2) for r in recommended):
            add(
                RECOMMENDATION_XP, "recommended", "Followed a recommendation at the suggested depth"
            )
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
