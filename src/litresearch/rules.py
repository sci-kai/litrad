"""Game rules: points, bonuses, penalty, levels and badges.

The ``game`` section of ``config.yaml`` holds the defaults for a *new*
profile. When a profile starts, its rules are copied into the database and
locked: later changes to the config do not affect a running game, so points
cannot be inflated after the fact. The rules carry a fingerprint, so two
profiles can be compared fairly only when they played by the same rules.
"""

import hashlib
import json
from dataclasses import dataclass

from pydantic import BaseModel, Field, field_validator, model_validator


class LevelDef(BaseModel):
    model_config = {"extra": "forbid"}

    xp: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=60)
    icon: str = Field("⭐", max_length=8)


# The ladder of new profiles (rules format 2): 60 steps that get wider slowly,
# so a level-up comes every week or two for regular readers; fitted with
# tools/simulate_game.py (see docs, Gamification). The 20-level ladder of format 1
# stays in the profiles that locked it.
DEFAULT_LEVELS = [
    (0, "Curious Undergrad", "🎓"),
    (15, "First-Year Student", "✏️"),
    (55, "Bachelor Student", "📘"),
    (125, "Study Group Member", "👥"),
    (220, "Lab Intern", "🥼"),
    (340, "Lab Rotation Student", "🧪"),
    (485, "Tutor", "🧑‍🏫"),
    (655, "Bachelor Graduate", "🎉"),
    (855, "Master's Student", "📗"),
    (1070, "Student Assistant", "🗂️"),
    (1320, "Seminar Speaker", "🗣️"),
    (1590, "Master's Graduate", "🎊"),
    (1880, "Research Assistant", "🧫"),
    (2190, "PhD Candidate", "📚"),
    (2530, "Journal Club Regular", "☕"),
    (2890, "Lab Meeting Presenter", "📊"),
    (3270, "Poster Presenter", "🪧"),
    (3680, "Conference Speaker", "🎤"),
    (4100, "Thesis Defender", "🛡️"),
    (4550, "Doctor of Philosophy", "📜"),
    (5000, "Postdoc", "🔬"),
    (5500, "Peer Reviewer", "🔎"),
    (6000, "Fellowship Holder", "🏅"),
    (6550, "Senior Postdoc", "🧬"),
    (7100, "Grant Writer", "✍️"),
    (7650, "Research Fellow", "🧑‍🔬"),
    (8250, "Visiting Scholar", "✈️"),
    (8850, "Lecturer", "🎙️"),
    (9500, "Junior Group Leader", "🌱"),
    (10100, "Assistant Professor", "🪴"),
    (10750, "Group Leader", "👩‍🔬"),
    (11450, "Mentor", "🤝"),
    (12150, "Principal Investigator", "🧭"),
    (12850, "Associate Professor", "🏫"),
    (13600, "Invited Speaker", "📣"),
    (14300, "Keynote Speaker", "🌟"),
    (15050, "Grant Panelist", "📋"),
    (15850, "Professor", "🎩"),
    (16650, "Consortium Lead", "🕸️"),
    (17450, "Department Head", "🏛️"),
    (18250, "Review Editor", "🖋️"),
    (19100, "Associate Editor", "📝"),
    (19900, "Institute Director", "🏢"),
    (20800, "Dean", "🦉"),
    (21650, "Editor-in-Chief", "📰"),
    (22550, "Academy Member", "🏺"),
    (23400, "Distinguished Professor", "💫"),
    (24350, "Honorary Doctor", "🎖️"),
    (25250, "Provost", "🗝️"),
    (26150, "University President", "🏰"),
    (27100, "Academy President", "👑"),
    (28050, "Science Advisor", "🧑‍⚖️"),
    (29000, "Lifetime Fellow", "🌿"),
    (30000, "Prize Winner", "🥇"),
    (30950, "Medalist", "🏵️"),
    (31950, "Laureate", "🌠"),
    (32950, "Living Legend", "✨"),
    (33950, "Textbook Author", "📖"),
    (35000, "Field Founder", "🚀"),
    (36000, "Hallmarks Author", "🏆"),
]


@dataclass(frozen=True)
class BadgeDef:
    code: str
    name: str
    description: str  # may contain {target}
    stat: str
    target: int  # the default of rules format 1 (and of the format the badge came with)
    icon: str
    group: str
    # the rules format that added the badge: older profiles do not have it
    since: int = 1
    # the default target from rules format 2 on, if it differs from ``target``
    target2: int | None = None

    def default_target(self, rules_format: int) -> int:
        return self.target2 if rules_format >= 2 and self.target2 is not None else self.target


# The statistics behind ``stat`` are computed in ``game.stats``.
BADGE_CATALOG = [
    # reading volume
    BadgeDef(
        "first_read",
        "First Steps",
        "Read your first paper (skimmed or deeper)",
        "papers_read",
        1,
        "🌱",
        "Reading",
    ),
    BadgeDef(
        "page_turner", "Page Turner", "Read {target} papers", "papers_read", 10, "📄", "Reading"
    ),
    BadgeDef("bookworm", "Bookworm", "Read {target} papers", "papers_read", 25, "🐛", "Reading"),
    BadgeDef(
        "bibliophile", "Bibliophile", "Read {target} papers", "papers_read", 50, "📚", "Reading"
    ),
    BadgeDef("centurion", "Centurion", "Read {target} papers", "papers_read", 100, "💯", "Reading"),
    BadgeDef(
        "living_library",
        "Living Library",
        "Read {target} papers",
        "papers_read",
        250,
        "🏛️",
        "Reading",
    ),
    # depth
    BadgeDef(
        "deep_diver", "Deep Diver", "Read {target} papers thoroughly", "thorough", 5, "🤿", "Depth"
    ),
    BadgeDef(
        "abyss_explorer",
        "Abyss Explorer",
        "Read {target} papers thoroughly",
        "thorough",
        25,
        "🐙",
        "Depth",
    ),
    BadgeDef(
        "scribe",
        "Scribe",
        "Write substantial notes on {target} papers",
        "reflections",
        10,
        "✍️",
        "Depth",
    ),
    BadgeDef(
        "novelist",
        "Novelist",
        "Write {target} characters of notes in total",
        "notes_chars",
        20000,
        "📝",
        "Depth",
    ),
    BadgeDef("critic", "Critic", "Rate {target} papers", "ratings", 20, "⭐", "Depth"),
    BadgeDef(
        "fan_club", "Fan Club", "Give {target} five-star ratings", "five_star", 10, "🌟", "Depth"
    ),
    # discipline
    BadgeDef(
        "frog_eater",
        "Frog Eater",
        "Read your top paper first, {target} times",
        "frogs",
        10,
        "🐸",
        "Discipline",
    ),
    BadgeDef(
        "frog_prince",
        "Frog Prince",
        "Read your top paper first, {target} times",
        "frogs",
        50,
        "🤴",
        "Discipline",
    ),
    BadgeDef(
        "inbox_zero",
        "Inbox Zero",
        "Empty your reading queue by reading it",
        "inbox_zero",
        1,
        "📭",
        "Discipline",
    ),
    BadgeDef(
        "minimalist",
        "Minimalist",
        "Empty your reading queue {target} times",
        "inbox_zero",
        5,
        "🧘",
        "Discipline",
    ),
    BadgeDef(
        "curator",
        "Curator",
        "Mark {target} papers as not relevant (teaches the recommender)",
        "dismissed",
        10,
        "🗂️",
        "Discipline",
    ),
    # streaks and quests
    BadgeDef(
        "streak_4",
        "Month of Momentum",
        "Read something {target} weeks in a row",
        "best_streak",
        4,
        "🔥",
        "Consistency",
    ),
    BadgeDef(
        "streak_12",
        "Quarter Marathon",
        "Read something {target} weeks in a row",
        "best_streak",
        12,
        "🏃",
        "Consistency",
    ),
    BadgeDef(
        "streak_26",
        "Half-Year Hero",
        "Read something {target} weeks in a row",
        "best_streak",
        26,
        "🦸",
        "Consistency",
    ),
    BadgeDef(
        "streak_52",
        "Year of Reading",
        "Read something {target} weeks in a row",
        "best_streak",
        52,
        "🗓️",
        "Consistency",
    ),
    BadgeDef(
        "questing", "Questing", "Complete your first weekly quest", "quests", 1, "🧭", "Consistency"
    ),
    BadgeDef(
        "quest_master",
        "Quest Master",
        "Complete {target} weekly quests",
        "quests",
        10,
        "🗺️",
        "Consistency",
    ),
    BadgeDef(
        "quest_legend",
        "Quest Legend",
        "Complete {target} weekly quests",
        "quests",
        25,
        "🐉",
        "Consistency",
    ),
    BadgeDef(
        "straight_a",
        "Straight A",
        "Get grade A or S in {target} weeks",
        "a_weeks",
        5,
        "🅰️",
        "Consistency",
    ),
    BadgeDef(
        "perfectionist",
        "Perfectionist",
        "Get grade S in {target} weeks",
        "s_weeks",
        3,
        "💎",
        "Consistency",
    ),
    BadgeDef(
        "comeback",
        "Comeback Kid",
        "Read again after a break of 4 weeks or more",
        "comebacks",
        1,
        "🔄",
        "Consistency",
    ),
    # pace
    BadgeDef(
        "marathon", "Marathon Day", "Read {target} papers in one day", "max_day", 4, "🏅", "Pace"
    ),
    BadgeDef(
        "binge_week", "Binge Week", "Read {target} papers in one week", "max_week", 8, "🍿", "Pace"
    ),
    BadgeDef(
        "night_owl",
        "Night Owl",
        "Read {target} different papers between 22:00 and 04:00",
        "night_reads",
        5,
        "🦉",
        "Pace",
    ),
    BadgeDef(
        "early_riser",
        "Early Riser",
        "Read {target} different papers between 05:00 and 08:00",
        "early_reads",
        5,
        "🌅",
        "Pace",
    ),
    BadgeDef(
        "weekend_warrior",
        "Weekend Warrior",
        "Read {target} different papers on weekends",
        "weekend_reads",
        5,
        "🏖️",
        "Pace",
    ),
    # what you read
    BadgeDef(
        "classicist",
        "Classicist",
        "Read {target} landmark papers (top 1 % cited)",
        "classics",
        5,
        "🏺",
        "Breadth",
    ),
    BadgeDef(
        "canon_keeper",
        "Canon Keeper",
        "Read {target} landmark papers",
        "classics",
        20,
        "📜",
        "Breadth",
    ),
    BadgeDef(
        "early_bird",
        "Early Bird",
        "Read {target} papers within 30 days of publication",
        "hot",
        5,
        "🐦",
        "Breadth",
    ),
    BadgeDef(
        "scoop",
        "Scoop",
        "Read {target} papers within 7 days of publication",
        "scoops",
        3,
        "📰",
        "Breadth",
    ),
    BadgeDef(
        "archaeologist",
        "Archaeologist",
        "Read a paper that is 25+ years old",
        "old_papers",
        1,
        "🦴",
        "Breadth",
    ),
    BadgeDef(
        "polymath",
        "Polymath",
        "Read papers from {target} different research topics",
        "topics",
        8,
        "🧩",
        "Breadth",
    ),
    BadgeDef(
        "renaissance",
        "Renaissance Mind",
        "Read papers from {target} different topics",
        "topics",
        20,
        "🎨",
        "Breadth",
    ),
    BadgeDef(
        "trailblazer",
        "Trailblazer",
        "Read {target} papers from your recommendations",
        "from_recommendations",
        10,
        "🧗",
        "Breadth",
    ),
    BadgeDef(
        "library_diver",
        "Library Diver",
        "Read {target} papers from your libraries",
        "from_library",
        10,
        "📁",
        "Breadth",
    ),
    BadgeDef(
        "free_spirit",
        "Free Spirit",
        "Read {target} papers you added yourself",
        "manual_read",
        5,
        "🪁",
        "Breadth",
    ),
    # experience
    BadgeDef("thousand_club", "Thousand Club", "Earn {target} XP", "xp", 1000, "💰", "Experience"),
    BadgeDef("high_five", "High Five", "Earn {target} XP", "xp", 5000, "🖐️", "Experience"),
]


def _v2(code, name, description, stat, target, icon, group) -> BadgeDef:
    return BadgeDef(code, name, description, stat, target, icon, group, since=2)


# Added by rules format 2: more tiers, so a badge or a level comes every week or two
# (profiles locked earlier do not have them).
BADGES_V2 = [
    _v2(
        "first_glance",
        "First Glance",
        "Look at {target} paper (abstract or deeper)",
        "papers_seen",
        1,
        "👁️",
        "Reading",
    ),
    _v2(
        "browser",
        "Browser",
        "Look at {target} papers (abstract or deeper)",
        "papers_seen",
        10,
        "🛍️",
        "Reading",
    ),
    _v2(
        "abstract_thinker",
        "Abstract Thinker",
        "Look at {target} papers (abstract or deeper)",
        "papers_seen",
        50,
        "💡",
        "Reading",
    ),
    _v2("warm_up", "Warm-Up", "Read {target} papers", "papers_read", 3, "🔆", "Reading"),
    _v2("reading_list", "Reading List", "Read {target} papers", "papers_read", 5, "🔖", "Reading"),
    _v2("regular", "Regular Reader", "Read {target} papers", "papers_read", 15, "📑", "Reading"),
    _v2(
        "lit_review", "Literature Review", "Read {target} papers", "papers_read", 75, "🗃️", "Reading"
    ),
    _v2(
        "systematic",
        "Systematic Review",
        "Read {target} papers",
        "papers_read",
        150,
        "🔎",
        "Reading",
    ),
    _v2("well_read", "Well-Read", "Read {target} papers", "papers_read", 200, "🧾", "Reading"),
    _v2("erudite", "Erudite", "Read {target} papers", "papers_read", 300, "🎩", "Reading"),
    _v2(
        "meta_analyst", "Meta-Analyst", "Read {target} papers", "papers_read", 350, "📊", "Reading"
    ),
    _v2(
        "encyclopedia", "Encyclopedia", "Read {target} papers", "papers_read", 500, "🌍", "Reading"
    ),
    _v2(
        "close_reader",
        "Close Reader",
        "Read {target} paper at least fully",
        "deep_reads",
        1,
        "🔍",
        "Depth",
    ),
    _v2(
        "careful",
        "Careful Reader",
        "Read {target} papers at least fully",
        "deep_reads",
        10,
        "🧐",
        "Depth",
    ),
    _v2(
        "journal_club",
        "Journal Club",
        "Read {target} papers at least fully",
        "deep_reads",
        25,
        "🫖",
        "Depth",
    ),
    _v2(
        "deep_thinker",
        "Deep Thinker",
        "Read {target} papers at least fully",
        "deep_reads",
        50,
        "💭",
        "Depth",
    ),
    _v2(
        "critical_mind",
        "Critical Mind",
        "Read {target} papers at least fully",
        "deep_reads",
        75,
        "⚗️",
        "Depth",
    ),
    _v2(
        "scholar",
        "Scholar",
        "Read {target} papers at least fully",
        "deep_reads",
        100,
        "🧠",
        "Depth",
    ),
    _v2("sage", "Sage", "Read {target} papers at least fully", "deep_reads", 250, "🧙", "Depth"),
    _v2("first_dive", "First Dive", "Read {target} paper thoroughly", "thorough", 1, "🏊", "Depth"),
    _v2(
        "free_diver", "Free Diver", "Read {target} papers thoroughly", "thorough", 10, "🐬", "Depth"
    ),
    _v2(
        "pearl_diver",
        "Pearl Diver",
        "Read {target} papers thoroughly",
        "thorough",
        50,
        "🦪",
        "Depth",
    ),
    _v2(
        "trench",
        "Mariana Trench",
        "Read {target} papers thoroughly",
        "thorough",
        100,
        "🌊",
        "Depth",
    ),
    _v2(
        "note_taker",
        "Note Taker",
        "Write substantial notes on {target} paper",
        "reflections",
        1,
        "🗒️",
        "Depth",
    ),
    _v2(
        "annotator",
        "Annotator",
        "Write substantial notes on {target} papers",
        "reflections",
        25,
        "🖍️",
        "Depth",
    ),
    _v2(
        "chronicler",
        "Chronicler",
        "Write substantial notes on {target} papers",
        "reflections",
        50,
        "🪶",
        "Depth",
    ),
    _v2(
        "essayist",
        "Essayist",
        "Write {target} characters of notes in total",
        "notes_chars",
        5000,
        "🖊️",
        "Depth",
    ),
    _v2(
        "monograph",
        "Monograph",
        "Write {target} characters of notes in total",
        "notes_chars",
        50000,
        "📕",
        "Depth",
    ),
    _v2("referee", "Referee", "Rate {target} papers", "ratings", 5, "⚖️", "Depth"),
    _v2("connoisseur", "Connoisseur", "Rate {target} papers", "ratings", 50, "🍷", "Depth"),
    _v2(
        "tadpole",
        "Tadpole",
        "Read your top paper first, {target} time",
        "frogs",
        1,
        "🥚",
        "Discipline",
    ),
    _v2(
        "bullfrog",
        "Bullfrog",
        "Read your top paper first, {target} times",
        "frogs",
        25,
        "🐊",
        "Discipline",
    ),
    _v2(
        "frog_king",
        "Frog King",
        "Read your top paper first, {target} times",
        "frogs",
        100,
        "👑",
        "Discipline",
    ),
    _v2(
        "zen",
        "Zen Master",
        "Empty your reading queue {target} times",
        "inbox_zero",
        15,
        "☯️",
        "Discipline",
    ),
    _v2(
        "gatekeeper",
        "Gatekeeper",
        "Mark {target} papers as not relevant",
        "dismissed",
        25,
        "🚧",
        "Discipline",
    ),
    _v2(
        "streak_2",
        "Back to Back",
        "Read something {target} weeks in a row",
        "best_streak",
        2,
        "✌️",
        "Consistency",
    ),
    _v2(
        "streak_8",
        "Two-Month Run",
        "Read something {target} weeks in a row",
        "best_streak",
        8,
        "🚴",
        "Consistency",
    ),
    _v2(
        "streak_78",
        "Unstoppable",
        "Read something {target} weeks in a row",
        "best_streak",
        78,
        "🚂",
        "Consistency",
    ),
    _v2(
        "streak_104",
        "Two Years Strong",
        "Read something {target} weeks in a row",
        "best_streak",
        104,
        "🏔️",
        "Consistency",
    ),
    _v2(
        "habit",
        "Habit Former",
        "Read something in {target} different weeks",
        "active_weeks",
        5,
        "🌀",
        "Consistency",
    ),
    _v2(
        "semester",
        "Semester",
        "Read something in {target} different weeks",
        "active_weeks",
        13,
        "🏫",
        "Consistency",
    ),
    _v2(
        "two_semesters",
        "Two Semesters",
        "Read something in {target} different weeks",
        "active_weeks",
        26,
        "🌗",
        "Consistency",
    ),
    _v2(
        "academic_year",
        "Academic Year",
        "Read something in {target} different weeks",
        "active_weeks",
        40,
        "📆",
        "Consistency",
    ),
    _v2(
        "full_year",
        "Full Year",
        "Read something in {target} different weeks",
        "active_weeks",
        52,
        "🎆",
        "Consistency",
    ),
    _v2(
        "seasoned",
        "Seasoned",
        "Read something in {target} different weeks",
        "active_weeks",
        75,
        "🍂",
        "Consistency",
    ),
    _v2(
        "veteran",
        "Veteran",
        "Read something in {target} different weeks",
        "active_weeks",
        100,
        "🎗️",
        "Consistency",
    ),
    _v2(
        "adventurer",
        "Adventurer",
        "Complete {target} weekly quests",
        "quests",
        3,
        "🎒",
        "Consistency",
    ),
    _v2(
        "pathfinder",
        "Pathfinder",
        "Complete {target} weekly quests",
        "quests",
        5,
        "🥾",
        "Consistency",
    ),
    _v2("odyssey", "Odyssey", "Complete {target} weekly quests", "quests", 50, "⛵", "Consistency"),
    _v2("saga", "Saga", "Complete {target} weekly quests", "quests", 75, "📯", "Consistency"),
    _v2("epic", "Epic", "Complete {target} weekly quests", "quests", 100, "🏰", "Consistency"),
    _v2(
        "top_marks",
        "Top Marks",
        "Get grade A or S in {target} week",
        "a_weeks",
        1,
        "✅",
        "Consistency",
    ),
    _v2(
        "honor_roll",
        "Honor Roll",
        "Get grade A or S in {target} weeks",
        "a_weeks",
        15,
        "🎖️",
        "Consistency",
    ),
    _v2(
        "deans_list",
        "Dean's List",
        "Get grade A or S in {target} weeks",
        "a_weeks",
        30,
        "📋",
        "Consistency",
    ),
    _v2(
        "valedictorian",
        "Valedictorian",
        "Get grade A or S in {target} weeks",
        "a_weeks",
        52,
        "🎤",
        "Consistency",
    ),
    _v2(
        "distinction",
        "Distinction",
        "Get grade S in {target} week",
        "s_weeks",
        1,
        "✨",
        "Consistency",
    ),
    _v2(
        "summa",
        "Summa cum Laude",
        "Get grade S in {target} weeks",
        "s_weeks",
        10,
        "🏵️",
        "Consistency",
    ),
    _v2(
        "dawn_patrol",
        "Dawn Patrol",
        "Read {target} different papers between 05:00 and 08:00",
        "early_reads",
        20,
        "🐓",
        "Pace",
    ),
    _v2(
        "night_shift",
        "Night Shift",
        "Read {target} different papers between 22:00 and 04:00",
        "night_reads",
        20,
        "🌙",
        "Pace",
    ),
    _v2(
        "weekend_scholar",
        "Weekend Scholar",
        "Read {target} different papers on weekends",
        "weekend_reads",
        20,
        "⛱️",
        "Pace",
    ),
    _v2(
        "landmark",
        "Landmark",
        "Read {target} landmark paper (top 1 % cited)",
        "classics",
        1,
        "🗿",
        "Breadth",
    ),
    _v2(
        "canon_scholar",
        "Canon Scholar",
        "Read {target} landmark papers",
        "classics",
        50,
        "🏯",
        "Breadth",
    ),
    _v2(
        "trend_spotter",
        "Trend Spotter",
        "Read {target} papers within 30 days of publication",
        "hot",
        15,
        "📈",
        "Breadth",
    ),
    _v2(
        "cutting_edge",
        "Cutting Edge",
        "Read {target} papers within 30 days of publication",
        "hot",
        30,
        "🔪",
        "Breadth",
    ),
    _v2(
        "breaking_news",
        "Breaking News",
        "Read {target} papers within 7 days of publication",
        "scoops",
        10,
        "🗞️",
        "Breadth",
    ),
    _v2(
        "historian",
        "Historian",
        "Read {target} papers that are 25+ years old",
        "old_papers",
        5,
        "⏳",
        "Breadth",
    ),
    _v2(
        "curious",
        "Curious Mind",
        "Read papers from {target} different topics",
        "topics",
        3,
        "🌿",
        "Breadth",
    ),
    _v2(
        "wanderer",
        "Wanderer",
        "Read papers from {target} different topics",
        "topics",
        5,
        "🚶",
        "Breadth",
    ),
    _v2(
        "bridge_builder",
        "Bridge Builder",
        "Read papers from {target} different topics",
        "topics",
        12,
        "🌉",
        "Breadth",
    ),
    _v2(
        "universal",
        "Universal Scholar",
        "Read papers from {target} different topics",
        "topics",
        30,
        "🌌",
        "Breadth",
    ),
    _v2(
        "scout",
        "Scout",
        "Read {target} papers from your recommendations",
        "from_recommendations",
        3,
        "🔭",
        "Breadth",
    ),
    _v2(
        "pioneer",
        "Pioneer",
        "Read {target} papers from your recommendations",
        "from_recommendations",
        25,
        "🚀",
        "Breadth",
    ),
    _v2(
        "expedition",
        "Expedition Leader",
        "Read {target} papers from your recommendations",
        "from_recommendations",
        50,
        "🗻",
        "Breadth",
    ),
    _v2(
        "cartographer",
        "Cartographer",
        "Read {target} papers from your recommendations",
        "from_recommendations",
        100,
        "🗺",
        "Breadth",
    ),
    _v2(
        "archivist",
        "Archivist",
        "Read {target} papers from your libraries",
        "from_library",
        25,
        "🗄️",
        "Breadth",
    ),
    _v2(
        "librarian",
        "Librarian",
        "Read {target} papers from your libraries",
        "from_library",
        50,
        "🏷️",
        "Breadth",
    ),
    _v2(
        "head_librarian",
        "Head Librarian",
        "Read {target} papers from your libraries",
        "from_library",
        100,
        "🗝️",
        "Breadth",
    ),
    _v2(
        "bibliographer",
        "Bibliographer",
        "Read {target} papers from your libraries",
        "from_library",
        200,
        "📇",
        "Breadth",
    ),
    _v2(
        "maverick",
        "Maverick",
        "Read {target} papers you added yourself",
        "manual_read",
        15,
        "🦄",
        "Breadth",
    ),
    _v2("rising_star", "Rising Star", "Earn {target} XP", "xp", 500, "🌠", "Experience"),
    _v2("silver_club", "Silver Club", "Earn {target} XP", "xp", 2500, "🥈", "Experience"),
    _v2("ten_thousand", "Ten Thousand", "Earn {target} XP", "xp", 10000, "🔟", "Experience"),
    _v2("gold_club", "Gold Club", "Earn {target} XP", "xp", 15000, "🥇", "Experience"),
    _v2("platinum_club", "Platinum Club", "Earn {target} XP", "xp", 20000, "💠", "Experience"),
    _v2("hall_of_fame", "Hall of Fame", "Earn {target} XP", "xp", 25000, "🏆", "Experience"),
]


def _display_order(badges: list[BadgeDef]) -> list[BadgeDef]:
    """Groups, then statistics, in order of first appearance; tiers by target."""
    groups, stats = {}, {}
    for badge in badges:
        groups.setdefault(badge.group, len(groups))
        stats.setdefault(badge.stat, len(stats))
    return sorted(badges, key=lambda b: (groups[b.group], stats[b.stat], b.target))


BADGE_CATALOG = _display_order(BADGE_CATALOG + BADGES_V2)
BADGE_CODES = {b.code for b in BADGE_CATALOG}
_CATALOG = {b.code: b for b in BADGE_CATALOG}


@dataclass(frozen=True)
class Badge:
    code: str
    name: str
    description: str
    stat: str
    target: int
    icon: str
    group: str


# the newest rules format (see ``GameRules.rules_format``)
RULES_FORMAT = 2


class GameRules(BaseModel):
    """Every reward and penalty. Locked into a profile when the game starts."""

    model_config = {"extra": "forbid", "allow_inf_nan": False}

    # rules format: 1 = the rules of litresearch 0.1 to 1.0 (20 levels, 44 badges);
    # 2 = the rebalanced defaults with more badges. Rules stored without this key
    # are format 1 (see ``_legacy_format``); new profiles get format 2.
    rules_format: int = Field(RULES_FORMAT, ge=1, le=RULES_FORMAT)
    # share of the base points for a paper read before litresearch ("read earlier")
    backlog_share: float = Field(1.0, ge=0, le=1)
    # base points per reading level (paid up to the deepest level ever reached)
    xp_abstract: int = Field(5, ge=0, le=10000)
    xp_skimmed: int = Field(15, ge=0, le=10000)
    xp_read: int = Field(40, ge=0, le=10000)
    xp_thorough: int = Field(80, ge=0, le=10000)
    # bonus: share of the base points for reading the top paper of the queue
    frog_bonus: float = Field(0.5, ge=0, le=10)
    # bonus: +step per consecutive reading week, up to max_steps steps
    streak_step: float = Field(0.05, ge=0, le=1)
    streak_max_steps: int = Field(10, ge=0, le=100)
    # bonus: landmark paper read at least at "read" depth
    classic_percentile: float = Field(99.0, ge=0, le=100)
    classic_xp: int = Field(20, ge=0, le=10000)
    # bonus: read soon after publication
    hot_days: int = Field(30, ge=0, le=3650)
    hot_xp: int = Field(10, ge=0, le=10000)
    # bonus: read a paper from the recommendations (any real depth)
    recommendation_xp: int = Field(15, ge=0, le=10000)
    # bonus: first rating of a paper
    rating_xp: int = Field(2, ge=0, le=10000)
    # bonus: notes on a paper reach this length
    reflection_chars: int = Field(140, ge=1, le=100000)
    reflection_xp: int = Field(10, ge=0, le=10000)
    # bonus: the queue becomes empty because you read its last paper (once per day)
    inbox_zero_xp: int = Field(10, ge=0, le=10000)
    # weekly quest: papers at least "read" + further papers at least skimmed
    weekly_detail_goal: int = Field(1, ge=0, le=100)
    weekly_skim_goal: int = Field(1, ge=0, le=100)
    quest_xp: int = Field(50, ge=0, le=10000)
    # per unlocked badge
    badge_xp: int = Field(25, ge=0, le=10000)
    # penalty: a queued paper leaves the queue before it was at least skimmed
    drop_penalty: int = Field(15, ge=0, le=10000)
    levels: list[LevelDef] = Field(
        default_factory=lambda: [LevelDef(xp=x, title=t, icon=i) for x, t, i in DEFAULT_LEVELS]
    )
    # badge code -> target; missing codes use the catalog default
    badge_targets: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _legacy_format(cls, data):
        """Rules stored before rules formats existed are format 1.

        Every profile stores the complete rule set; one without ``rules_format``
        that holds every rule of format 1 was locked by litresearch 1.0 or
        earlier, and keeps the badges and default targets of that time.
        """
        if (
            isinstance(data, dict)
            and "rules_format" not in data
            and FINGERPRINT_FIELDS[GameRules] <= data.keys()
        ):
            data = {**data, "rules_format": 1}
        return data

    @field_validator("levels")
    @classmethod
    def _levels_ascending(cls, levels):
        if not levels or levels[0].xp != 0:
            raise ValueError("the first level must start at 0 XP")
        xps = [level.xp for level in levels]
        if xps != sorted(set(xps)):
            raise ValueError("level XP thresholds must be strictly increasing")
        if len({level.title for level in levels}) != len(levels):
            raise ValueError("level titles must be unique")
        return levels

    @field_validator("badge_targets")
    @classmethod
    def _known_badges(cls, targets):
        unknown = set(targets) - BADGE_CODES
        if unknown:
            raise ValueError(f"unknown badge(s): {', '.join(sorted(unknown))}")
        if any(value < 1 for value in targets.values()):
            raise ValueError("badge targets must be at least 1")
        return targets

    @model_validator(mode="after")
    def _depth_pays_more(self):
        values = [self.xp_abstract, self.xp_skimmed, self.xp_read, self.xp_thorough]
        if values != sorted(values):
            raise ValueError("deeper reading levels must not earn fewer points")
        return self

    def base_xp(self) -> dict[str, int]:
        return {
            "abstract": self.xp_abstract,
            "skimmed": self.xp_skimmed,
            "read": self.xp_read,
            "thorough": self.xp_thorough,
        }

    def badges(self) -> list[Badge]:
        """The badges of this profile's rules format with this profile's targets."""
        result = []
        for definition in BADGE_CATALOG:
            if definition.since > self.rules_format:
                continue
            target = self.badge_targets.get(
                definition.code, definition.default_target(self.rules_format)
            )
            result.append(
                Badge(
                    definition.code,
                    definition.name,
                    definition.description.format(target=target),
                    definition.stat,
                    target,
                    definition.icon,
                    definition.group,
                )
            )
        return result

    def fingerprint(self) -> str:
        """Short hash of the complete rule set (equal rules = equal fingerprint).

        Stable across versions: see ``FINGERPRINT_FIELDS``.
        """
        return rules_fingerprint(self)


# Rules format 1: the fields every fingerprint covers, as of litresearch 1.0 (and
# unchanged since 0.1). A rule added later (with a default) enters the hash only
# when a profile sets it to a value other than its default, so rules locked by
# an older version keep their fingerprint in every newer one. Never remove or
# rename a name here (that would change all fingerprints: 2.0 only).
FINGERPRINT_FIELDS = {
    GameRules: frozenset(
        {
            "xp_abstract",
            "xp_skimmed",
            "xp_read",
            "xp_thorough",
            "frog_bonus",
            "streak_step",
            "streak_max_steps",
            "classic_percentile",
            "classic_xp",
            "hot_days",
            "hot_xp",
            "recommendation_xp",
            "rating_xp",
            "reflection_chars",
            "reflection_xp",
            "inbox_zero_xp",
            "weekly_detail_goal",
            "weekly_skim_goal",
            "quest_xp",
            "badge_xp",
            "drop_penalty",
            "levels",
            "badge_targets",
        }
    ),
    LevelDef: frozenset({"xp", "title", "icon"}),
}
# the badge catalogue of rules format 1: a badge added later counts only with a
# target other than its catalogue default
BADGE_CODES_V1 = frozenset(
    """first_read page_turner bookworm bibliophile centurion living_library deep_diver
    abyss_explorer scribe novelist critic fan_club frog_eater frog_prince inbox_zero
    minimalist curator streak_4 streak_12 streak_26 streak_52 questing quest_master
    quest_legend straight_a perfectionist comeback marathon binge_week night_owl
    early_riser weekend_warrior classicist canon_keeper early_bird scoop archaeologist
    polymath renaissance trailblazer library_diver free_spirit thousand_club
    high_five""".split()
)


# Rules format 2 (litresearch 0.2): its rules always enter the fingerprint of a
# format-2 profile; for a format-1 profile only with a value other than the one
# below (rules_format) or than their default. Same rule as above: never remove
# or rename a name, never change a default here once released.
FORMAT2_FIELDS = frozenset({"rules_format", "backlog_share"})
FORMAT1_VALUES = {"rules_format": 1}


def _canonical(model: BaseModel):
    """The model as JSON data without later-added fields that hold their default."""
    baseline = next(
        (fields for cls, fields in FINGERPRINT_FIELDS.items() if isinstance(model, cls)),
        None,
    )
    if isinstance(model, GameRules) and model.rules_format >= 2:
        baseline = baseline | FORMAT2_FIELDS
    data = {}
    for name, info in type(model).model_fields.items():
        value = getattr(model, name)
        if baseline is not None and name not in baseline:
            if name in FORMAT1_VALUES:
                if value == FORMAT1_VALUES[name]:
                    continue
            elif value == info.get_default(call_default_factory=True):
                continue
        if isinstance(value, BaseModel):
            value = _canonical(value)
        elif isinstance(value, list):
            value = [_canonical(v) if isinstance(v, BaseModel) else v for v in value]
        data[name] = value
    return data


def rules_fingerprint(rules: "GameRules") -> str:
    """The fingerprint of ``rules``; see ``FINGERPRINT_FIELDS``."""
    data = _canonical(rules)
    # resolved targets: writing a default explicitly must not change the fingerprint;
    # a badge added to the catalogue later enters only with a non-default target
    # (its presence follows from the rules format)
    resolved = {badge.code: badge.target for badge in rules.badges()}
    data["badge_targets"] = {
        code: target
        for code, target in resolved.items()
        if code in BADGE_CODES_V1 or target != _CATALOG[code].default_target(rules.rules_format)
    }
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]
