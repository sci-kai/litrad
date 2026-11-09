"""Gamification: experience points, levels, streaks, weekly quests, badges.

Design goals:

- Reward *depth* and *discipline* (reading the top paper of the queue first),
  not just volume. How deeply you read is your choice; deeper reads pay more.
- Points can never be farmed: each paper pays its base points only up to the
  deepest level it has ever reached, and paper bonuses are paid once.
- Every number comes from the profile's locked ``GameRules`` (see
  ``rules.py``), so the rules cannot change during a game.
- Every point is stored as an ``XpEvent`` with a reason, so the score stays
  traceable.

Functions that need rules accept either ``GameRules`` or a ``Config`` (whose
``game`` section is then used, e.g. before a profile exists). The app always
passes the profile's rules (``profile.active_rules``).
"""

from collections import Counter
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
from litresearch.rules import Badge, GameRules

NIGHT_HOURS = {22, 23, 0, 1, 2, 3}
EARLY_HOURS = {5, 6, 7}
SCOOP_DAYS = 7
OLD_PAPER_YEARS = 25
COMEBACK_WEEKS = 4
# XP kind of the base points for a paper read before litresearch ("read earlier")
EARLIER_KIND = "earlier"


def as_rules(rules_or_cfg) -> GameRules:
    if isinstance(rules_or_cfg, GameRules):
        return rules_or_cfg
    if isinstance(rules_or_cfg, Config):
        return rules_or_cfg.game
    if rules_or_cfg is None:
        return GameRules()
    raise TypeError(f"expected GameRules or Config, got {type(rules_or_cfg).__name__}")


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


@dataclass
class Level:
    number: int
    title: str
    icon: str
    floor: int
    next_floor: int | None
    next_title: str | None

    def progress(self, xp: int) -> float:
        if self.next_floor is None:
            return 1.0
        return max(0.0, min(1.0, (xp - self.floor) / (self.next_floor - self.floor)))


def level_for(xp: int, rules=None) -> Level:
    """The level reached with ``xp`` points under ``rules``."""
    levels = as_rules(rules).levels
    xp = max(xp, 0)  # penalties can push the total below zero
    index = max(i for i, level in enumerate(levels) if xp >= level.xp)
    level = levels[index]
    following = levels[index + 1] if index + 1 < len(levels) else None
    return Level(
        index + 1,
        level.title,
        level.icon,
        level.xp,
        following.xp if following else None,
        following.title if following else None,
    )


# --- statistics ------------------------------------------------------------


def total_xp(session: Session) -> int:
    return session.exec(select(func.coalesce(func.sum(XpEvent.points), 0))).one()


# statuses that count as reading (skimmed or deeper)
READ_STATUSES = READ_LEVELS[1:]


def deeper_reads(events) -> list[tuple]:
    """``(event, level)`` for the events that took a paper deeper than ever before.

    ``events`` must be in time order. Logging a paper again at the same or a
    shallower level (also after it was recommended again) is not progress: it
    counts for no quest, streak, report card, badge or calendar.
    """
    deepest: dict[int, int] = {}
    found = []
    for event in events:
        level = depth(event.status)
        if level > deepest.get(event.paper_id, 0):
            deepest[event.paper_id] = level
            found.append((event, level))
    return found


def _events_in_order(session: Session, *where):
    return session.exec(
        select(
            StatusEvent.paper_id,
            StatusEvent.status,
            StatusEvent.at,
            StatusEvent.was_queue_top,
            StatusEvent.earlier,
            StatusEvent.read_on,
        )
        .where(*where)
        .order_by(StatusEvent.at, StatusEvent.id)
    ).all()


def reading_weeks(session: Session) -> list[date]:
    """Weeks (Mondays) in which a paper first reached skimmed or a deeper level.

    Readings logged as read earlier (before using litresearch) make no reading week.
    """
    events = _events_in_order(session, StatusEvent.status.in_(READ_STATUSES))
    return sorted(
        {
            week_start(e.at.date())
            for e, level in deeper_reads(events)
            if level >= 2 and not e.earlier
        }
    )


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


def next_read_streak(session: Session, today: date) -> int:
    """The streak a read logged today counts with (this week included)."""
    weeks = reading_weeks(session)
    current, _ = streaks(weeks, today)
    return current if week_start(today) in weeks else current + 1


def streak_multiplier(streak: int, rules=None) -> float:
    rules = as_rules(rules)
    return 1 + rules.streak_step * min(max(streak - 1, 0), rules.streak_max_steps)


def week_progress(session: Session, rules, day: date) -> dict:
    """Quest progress for the week containing ``day``."""
    rules = as_rules(rules)
    start = datetime.combine(week_start(day), datetime.min.time())
    events = _events_in_order(
        session, StatusEvent.at >= start, StatusEvent.at < start + timedelta(weeks=1)
    )
    deepest = _week_depths(session, events, start)
    xp = session.exec(
        select(func.coalesce(func.sum(XpEvent.points), 0)).where(
            XpEvent.at >= start, XpEvent.at < start + timedelta(weeks=1)
        )
    ).one()
    return {"week_start": week_start(day), **quest_progress(deepest.values(), rules), "xp": xp}


def _week_depths(session: Session, events, start: datetime) -> dict[int, int]:
    """Deepest new level each paper of one week's ``events`` reached in that week.

    Readings logged as read earlier do not count for the week (quest, report card).
    """
    before: list = []
    for chunk in _chunks(sorted({e.paper_id for e in events})):
        before += _events_in_order(session, StatusEvent.paper_id.in_(chunk), StatusEvent.at < start)
    week: dict[int, int] = {}
    for event, level in deeper_reads([*before, *events]):
        if event.at >= start and not event.earlier:
            week[event.paper_id] = max(week.get(event.paper_id, 0), level)
    return week


def quest_progress(depths, rules: GameRules) -> dict:
    """Quest figures from the deepest new level each paper reached within one week."""
    depths = list(depths)
    detail = sum(d >= 3 for d in depths)
    total = sum(d >= 2 for d in depths)
    thorough = sum(d >= 4 for d in depths)
    goal_detail, goal_skim = rules.weekly_detail_goal, rules.weekly_skim_goal
    counted_detail = min(detail, goal_detail)
    counted_skim = min(total - counted_detail, goal_skim)
    goal = goal_detail + goal_skim
    fraction = (counted_detail + counted_skim) / goal if goal else 1.0
    return {
        "detail": detail,
        "total": total,
        "thorough": thorough,
        "goal_detail": goal_detail,
        "goal_skim": goal_skim,
        "fraction": fraction,
        "done": fraction >= 1,
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


def stats(session: Session, today: date, rules=None) -> dict:
    """All statistics behind the dashboard and the badges."""
    rules = as_rules(rules)
    # only the papers you read: a library can hold tens of thousands of papers
    read_papers = select(StatusEvent.paper_id).where(StatusEvent.status.in_(READ_STATUSES))
    # plain columns instead of ORM objects: several times faster with thousands of rows
    papers = {
        p.id: p
        for p in session.exec(
            select(
                Paper.id, Paper.topics, Paper.folders, Paper.pub_date, Paper.year, Paper.in_library
            ).where(Paper.id.in_(read_papers))
        ).all()
    }
    readings = session.exec(select(Reading.notes, Reading.rating, Reading.status)).all()
    events = _events_in_order(session)
    recommended = set(session.exec(select(Recommendation.paper_id).distinct()).all())

    deepest: dict[int, int] = {}
    first_read: dict[int, StatusEvent] = {}
    per_day: dict[date, set[int]] = {}
    per_week: dict[date, set[int]] = {}
    # deepest level per paper within each week (for the report-card grades)
    week_depths: dict[date, dict[int, int]] = {}
    # papers, not log entries: re-logging one paper at night must not earn a badge
    night: set[int] = set()
    early: set[int] = set()
    weekend: set[int] = set()
    # only steps to a deeper level count; re-logging a paper earns nothing
    for event, level in deeper_reads(events):
        deepest[event.paper_id] = level
        if event.earlier:
            # read before litresearch: counts for papers, depth and topics, not for time
            if level >= 2:
                first_read.setdefault(event.paper_id, event)
            continue
        in_week = week_depths.setdefault(week_start(event.at.date()), {})
        in_week[event.paper_id] = max(in_week.get(event.paper_id, 0), level)
        if level < 2:
            continue
        first_read.setdefault(event.paper_id, event)
        per_day.setdefault(event.at.date(), set()).add(event.paper_id)
        per_week.setdefault(week_start(event.at.date()), set()).add(event.paper_id)
        if event.at.hour in NIGHT_HOURS:
            night.add(event.paper_id)
        if event.at.hour in EARLY_HOURS:
            early.add(event.paper_id)
        if event.at.weekday() >= 5:
            weekend.add(event.paper_id)
    read_ids = {pid for pid, d in deepest.items() if d >= 2}

    topics = set()
    scoops = old = from_library = from_recs = manual = 0
    for pid in read_ids:
        paper = papers.get(pid)
        if paper is None:
            continue
        label = (paper.topics or paper.folders or [None])[0]
        if label:
            topics.add(label)
        first = first_read[pid]
        read_at = (first.read_on or first.at.date()) if first.earlier else first.at.date()
        if (
            not first.earlier
            and paper.pub_date
            and 0 <= (read_at - paper.pub_date).days <= SCOOP_DAYS
        ):
            scoops += 1
        published = paper.pub_date or (date(paper.year, 7, 1) if paper.year else None)
        if published and (read_at - published).days >= OLD_PAPER_YEARS * 365.25:
            old += 1
        if paper.in_library:
            from_library += 1
        elif pid in recommended and not first.earlier:
            from_recs += 1
        else:
            manual += 1

    weeks = sorted(per_week)
    current, best = streaks(weeks, today)
    comebacks = sum(
        1
        for a, b in zip(weeks, weeks[1:], strict=False)
        if b - a >= timedelta(weeks=COMEBACK_WEEKS + 1)
    )
    grades = Counter(
        grade(quest_progress(week_depths[monday].values(), rules), True)[0] for monday in weeks
    )
    frogs = len(
        {
            e.paper_id
            for e, level in deeper_reads(events)
            if e.was_queue_top and level >= 2 and not e.earlier
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
        "scoops": scoops,
        "old_papers": old,
        "topics": len(topics),
        "reflections": _count_kind(session, "reflection"),
        "notes_chars": sum(len(r.notes) for r in readings),
        "ratings": sum(r.rating is not None for r in readings),
        "five_star": sum(r.rating == 5 for r in readings),
        "dismissed": sum(r.status == ReadStatus.dismissed for r in readings),
        "streak": current,
        "best_streak": best,
        "comebacks": comebacks,
        "quests": _count_kind(session, "quest"),
        "a_weeks": grades["A"] + grades["S"],
        "s_weeks": grades["S"],
        "inbox_zero": _count_kind(session, "inbox_zero"),
        "max_day": max((len(v) for v in per_day.values()), default=0),
        "max_week": max((len(v) for v in per_week.values()), default=0),
        "night_reads": len(night),
        "early_reads": len(early),
        "weekend_reads": len(weekend),
        "from_library": from_library,
        "from_recommendations": from_recs,
        "manual_read": manual,
    }


def header_stats(session: Session, today: date, rules=None) -> dict:
    """The few numbers every page shows (cheaper than ``stats``)."""
    xp = total_xp(session)
    current, _ = streaks(reading_weeks(session), today)
    return {"xp": xp, "streak": current, "level": level_for(xp, rules)}


def reading_impact_factor(session: Session, today: date, weeks: int = 4) -> float:
    """Average XP per week over the last ``weeks`` weeks (current week included).

    XP for papers read earlier (before litresearch) is not part of the pace.
    """
    since = datetime.combine(week_start(today) - timedelta(weeks=weeks - 1), datetime.min.time())
    until = datetime.combine(week_start(today) + timedelta(weeks=1), datetime.min.time())
    xp = session.exec(
        select(func.coalesce(func.sum(XpEvent.points), 0)).where(
            XpEvent.at >= since, XpEvent.at < until, XpEvent.kind != EARLIER_KIND
        )
    ).one()
    return xp / weeks


# --- awarding --------------------------------------------------------------


@dataclass
class Award:
    events: list[XpEvent] = field(default_factory=list)
    badges: list[Badge] = field(default_factory=list)
    level_before: Level | None = None
    level_after: Level | None = None
    # True when this action started the game with the configured default rules
    profile_started: bool = False

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
    rules,
    paper: Paper,
    previous_depth: int,
    status: ReadStatus,
    was_queue_top: bool,
    rated: bool,
    queue_emptied: bool,
    at: datetime,
    earlier: bool = False,
    unlock: bool = True,
) -> Award:
    """Points for a status change that has already been stored.

    ``previous_depth`` is the deepest level this paper had reached before.
    ``earlier``: the paper was read before litresearch; it earns the base points
    at ``rules.backlog_share`` and none of the bonuses for when and how you read
    (streak, frog, fresh paper, recommendation, inbox zero). ``unlock=False``
    leaves the badges to the caller (one ``unlock_badges`` after many readings).
    """
    rules = as_rules(rules)
    base_xp = rules.base_xp()
    award = Award(level_before=level_for(total_xp(session), rules))
    events = award.events
    today = at.date()

    def add(points: int, kind: str, reason: str, paper_id: int | None = paper.id):
        if points:
            events.append(
                XpEvent(paper_id=paper_id, points=points, kind=kind, reason=reason, at=at)
            )

    new_depth = depth(status)
    # reading bonuses need a step to a deeper level, never a re-log of the same one
    deeper = new_depth > previous_depth
    if deeper and earlier:
        previous_xp = base_xp[READ_LEVELS[previous_depth - 1].value] if previous_depth else 0
        base = round((base_xp[status.value] - previous_xp) * rules.backlog_share)
        add(base, EARLIER_KIND, f"read earlier, {status.value}: {paper.title[:60]}")
    elif deeper:
        previous_xp = base_xp[READ_LEVELS[previous_depth - 1].value] if previous_depth else 0
        base = base_xp[status.value] - previous_xp
        add(base, "read", f"{status.value}: {paper.title[:60]}")
        current_streak, _ = streaks(reading_weeks(session), today)
        multiplier = streak_multiplier(current_streak, rules)
        add(
            round(base * (multiplier - 1)),
            "streak",
            f"{current_streak}-week streak x{multiplier:.2f}",
        )
        if was_queue_top and new_depth >= 2:
            add(round(base * rules.frog_bonus), "frog", "Ate the frog: read your top paper first")

    if deeper and new_depth >= 3 and (paper.citation_percentile or 0) >= rules.classic_percentile:
        if not _already(session, "classic", paper.id):
            add(
                rules.classic_xp,
                "classic",
                f"Read a landmark paper (top {100 - rules.classic_percentile:g} % cited)",
            )
    if (
        deeper
        and not earlier
        and new_depth >= 2
        and paper.pub_date
        and 0 <= (today - paper.pub_date).days <= rules.hot_days
    ):
        if not _already(session, "hot", paper.id):
            add(rules.hot_xp, "hot", f"Hot off the press: published within {rules.hot_days} days")
    if deeper and not earlier and new_depth >= 2 and not _already(session, "recommended", paper.id):
        recommended = session.exec(
            select(Recommendation).where(Recommendation.paper_id == paper.id)
        ).first()
        if recommended:
            add(rules.recommendation_xp, "recommended", "Read a paper from your recommendations")
    if rated and not _already(session, "rating", paper.id):
        add(rules.rating_xp, "rating", "Rated a paper")
    reading = session.get(Reading, paper.id)
    if reading and len(reading.notes) >= rules.reflection_chars:
        if not _already(session, "reflection", paper.id):
            add(rules.reflection_xp, "reflection", "Wrote substantial notes")
    inbox = f"Inbox zero {today.isoformat()}"
    if queue_emptied and not earlier and not _already(session, "inbox_zero", reason=inbox):
        add(rules.inbox_zero_xp, "inbox_zero", inbox, None)

    for event in events:
        session.add(event)
    session.flush()

    progress = week_progress(session, rules, today)
    quest = f"Weekly quest {progress['week_start'].isoformat()}"
    if progress["done"] and rules.quest_xp and not _already(session, "quest", reason=quest):
        event = XpEvent(points=rules.quest_xp, kind="quest", reason=quest, at=at)
        session.add(event)
        events.append(event)
        session.flush()

    if unlock:
        award.badges = unlock_badges(session, rules, today, at, events)
    session.flush()
    award.level_after = level_for(total_xp(session), rules)
    return award


def unlock_badges(
    session: Session, rules, today: date, at: datetime, events: list[XpEvent]
) -> list[Badge]:
    """Award newly reached badges (with their XP); returns them."""
    rules = as_rules(rules)
    current = stats(session, today, rules)
    owned = set(session.exec(select(Achievement.code)).all())
    unlocked = []
    for badge in rules.badges():
        if badge.code not in owned and current[badge.stat] >= badge.target:
            session.add(Achievement(code=badge.code, unlocked_at=at))
            owned.add(badge.code)
            unlocked.append(badge)
            if rules.badge_xp:
                event = XpEvent(
                    points=rules.badge_xp, kind="badge", reason=f"Badge: {badge.name}", at=at
                )
                session.add(event)
                events.append(event)
                session.flush()
                current["xp"] = total_xp(session)  # XP badges may follow from badge XP
    return unlocked


def badge_progress(
    session: Session, today: date, rules=None, current: dict | None = None
) -> list[tuple[Badge, int, bool]]:
    """(badge, current value, unlocked) for every badge.

    ``current`` is the result of ``stats`` when the caller already has it (one
    ``stats`` call per page: it reads every reading event).
    """
    rules = as_rules(rules)
    if current is None:
        current = stats(session, today, rules)
    owned = set(session.exec(select(Achievement.code)).all())
    return [(b, min(current[b.stat], b.target), b.code in owned) for b in rules.badges()]


def queue_top_waiting_days(session: Session, today: date) -> int | None:
    top = session.exec(select(QueueItem).where(QueueItem.position == 1)).first()
    return (today - top.added_at.date()).days if top else None


# --- summaries for the dashboard ------------------------------------------------

KIND_LABELS = {
    "read": "Reading",
    EARLIER_KIND: "Read earlier",
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


def weekly_summary(session: Session, rules, monday: date, today: date) -> dict:
    """Everything that happened in one week, for the weekly report."""
    rules = as_rules(rules)
    start = datetime.combine(monday, datetime.min.time())
    end = start + timedelta(weeks=1)
    progress = week_progress(session, rules, monday)
    letter, comment = grade(progress, week_over=end.date() <= today)
    # the papers that reached a new level this week (re-logged papers do not count)
    reached = _week_depths(
        session, _events_in_order(session, StatusEvent.at >= start, StatusEvent.at < end), start
    )
    papers: dict[int, dict] = {}
    for chunk in _chunks(sorted(reached)):
        for paper in session.exec(select(Paper).where(Paper.id.in_(chunk))):
            level = reached[paper.id]
            papers[paper.id] = {"paper": paper, "status": READ_LEVELS[level - 1], "depth": level}
    xp_events = session.exec(select(XpEvent).where(XpEvent.at >= start, XpEvent.at < end)).all()
    by_kind: dict[str, int] = {}
    for event in xp_events:
        by_kind[event.kind] = by_kind.get(event.kind, 0) + event.points
    unlocked = {
        a.code
        for a in session.exec(
            select(Achievement).where(
                Achievement.unlocked_at >= start, Achievement.unlocked_at < end
            )
        ).all()
    }
    return {
        "week_start": monday,
        "grade": letter,
        "comment": comment,
        "progress": progress,
        "xp": sum(by_kind.values()),
        "xp_by_kind": dict(sorted(by_kind.items(), key=lambda kv: -kv[1])),
        "papers": sorted(papers.values(), key=lambda e: -e["depth"]),
        "badges": [b for b in rules.badges() if b.code in unlocked],
        "current": monday == week_start(today),
    }


def weekly_summaries(
    session: Session, rules, today: date, weeks: int = 12, offset: int = 0
) -> list[dict]:
    """Most recent week first; ``offset`` skips that many recent weeks (paging)."""
    this_week = week_start(today)
    return [
        weekly_summary(session, rules, this_week - timedelta(weeks=i), today)
        for i in range(offset, offset + weeks)
    ]


def weeks_of_history(session: Session, today: date) -> int:
    """Weeks from the first reading or XP event to this week (at least 1)."""
    first = [
        session.exec(select(func.min(StatusEvent.at))).one(),
        session.exec(select(func.min(XpEvent.at))).one(),
    ]
    first = [at for at in first if at is not None]
    if not first:
        return 1
    return max((week_start(today) - week_start(min(first).date())).days // 7 + 1, 1)


def ledger(
    session: Session, kind: str | None = None, limit: int = 50, offset: int = 0
) -> tuple[list[dict], int]:
    """XP transactions, newest first, with the running balance after each one."""
    # running balances from plain columns; full events and papers only for the page shown
    balance, rows = 0, []
    for event_id, points, event_kind in session.exec(
        select(XpEvent.id, XpEvent.points, XpEvent.kind).order_by(XpEvent.at, XpEvent.id)
    ):
        balance += points
        if not kind or event_kind == kind:
            rows.append((event_id, balance))
    rows.reverse()
    shown = rows[offset : offset + limit]
    events = {}
    for chunk in _chunks([event_id for event_id, _ in shown]):
        events.update({e.id: e for e in session.exec(select(XpEvent).where(XpEvent.id.in_(chunk)))})
    paper_ids = {e.paper_id for e in events.values() if e.paper_id is not None}
    papers = {}
    for chunk in _chunks(sorted(paper_ids)):
        papers.update({p.id: p for p in session.exec(select(Paper).where(Paper.id.in_(chunk)))})
    page = [
        {"event": events[event_id], "balance": bal, "paper": papers.get(events[event_id].paper_id)}
        for event_id, bal in shown
    ]
    return page, len(rows)


def _chunks(values: list, size: int = 500):
    """Slices of ``values``: SQLite limits the number of parameters per query."""
    for start in range(0, len(values), size):
        yield values[start : start + size]


def reading_calendar(session: Session, today: date, weeks: int = 26) -> list[list[dict]]:
    """Weeks (columns) x weekdays (rows) of reading activity, oldest week first."""
    first = week_start(today) - timedelta(weeks=weeks - 1)
    counts: dict[date, int] = {}
    xp: dict[date, int] = {}
    since = datetime.combine(first, datetime.min.time())
    for event, _ in deeper_reads(_events_in_order(session)):
        if event.at >= since and not event.earlier:
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


def personal_bests(session: Session, rules, today: date, current: dict | None = None) -> dict:
    """Best week, busiest week, longest streak and totals (``current``: see badge_progress)."""
    rules = as_rules(rules)
    # the pace of reading here: XP for papers read earlier is left out
    events = session.exec(
        select(XpEvent.at, XpEvent.points).where(XpEvent.kind != EARLIER_KIND)
    ).all()
    per_week: dict[date, int] = {}
    for event in events:
        monday = week_start(event.at.date())
        per_week[monday] = per_week.get(monday, 0) + event.points
    best_week = max(per_week.items(), key=lambda kv: kv[1], default=(None, 0))
    papers_per_week: dict[date, set[int]] = {}
    for event, level in deeper_reads(_events_in_order(session)):
        if level >= 2 and not event.earlier:
            papers_per_week.setdefault(week_start(event.at.date()), set()).add(event.paper_id)
    busiest = max(papers_per_week.items(), key=lambda kv: len(kv[1]), default=(None, set()))
    s = current if current is not None else stats(session, today, rules)
    return {
        "best_week": best_week[0],
        "best_week_xp": best_week[1],
        "busiest_week": busiest[0],
        "busiest_week_papers": len(busiest[1]),
        "busiest_day_papers": s["max_day"],
        "longest_streak": s["best_streak"],
        "papers_read": s["papers_read"],
        "thorough": s["thorough"],
        "quests": s["quests"],
    }


def next_goals(session: Session, rules, today: date, current: dict | None = None) -> list[str]:
    """Short, concrete hints on what to do next (``current``: see badge_progress)."""
    rules = as_rules(rules)
    hints = []
    progress = week_progress(session, rules, today)
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
            hints.append(f"Weekly quest: {' and '.join(parts)} to go (+{rules.quest_xp} XP)")
    xp = total_xp(session)
    level = level_for(xp, rules)
    if level.next_floor:
        hints.append(f"{level.next_floor - xp} XP until {level.next_title}")
    weeks = reading_weeks(session)
    streak, _ = streaks(weeks, today)
    if streak and week_start(today) not in weeks:
        hints.append(f"Read something this week to keep your {streak}-week streak alive")
    closest = sorted(
        (
            (b, v)
            for b, v, unlocked in badge_progress(session, today, rules, current)
            if not unlocked and v > 0
        ),
        key=lambda bv: (bv[0].target - bv[1]) / bv[0].target,
    )[:1]
    for badge, value in closest:
        hints.append(f"{badge.target - value} more for the '{badge.name}' badge")
    return hints
