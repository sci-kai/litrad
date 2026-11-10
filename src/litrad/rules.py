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


# The default ladder: 60 steps that get wider slowly, so a level-up comes every
# week or two for regular readers; fitted with tools/simulate_game.py (see docs,
# Gamification).
DEFAULT_LEVELS = [
    (0, "Curious Undergrad", "🎓"),
    (20, "First-Year Student", "✏️"),
    (70, "Bachelor Student", "📘"),
    (140, "Study Group Member", "👥"),
    (240, "Lab Intern", "🥼"),
    (400, "Lab Rotation Student", "🧪"),
    (560, "Tutor", "🧑‍🏫"),
    (730, "Bachelor Graduate", "🎉"),
    (910, "Master's Student", "📗"),
    (1090, "Student Assistant", "🗂️"),
    (1280, "Seminar Speaker", "🗣️"),
    (1475, "Master's Graduate", "🎊"),
    (1685, "Research Assistant", "🧫"),
    (1910, "PhD Candidate", "📚"),
    (2155, "Journal Club Regular", "☕"),
    (2415, "Lab Meeting Presenter", "📊"),
    (2690, "Poster Presenter", "🪧"),
    (2985, "Conference Speaker", "🎤"),
    (3285, "Thesis Defender", "🛡️"),
    (3610, "Doctor of Philosophy", "📜"),
    (3935, "Postdoc", "🔬"),
    (4295, "Peer Reviewer", "🔎"),
    (4655, "Fellowship Holder", "🏅"),
    (5050, "Senior Postdoc", "🧬"),
    (5445, "Grant Writer", "✍️"),
    (5840, "Research Fellow", "🧑‍🔬"),
    (6270, "Visiting Scholar", "✈️"),
    (6700, "Lecturer", "🎙️"),
    (7170, "Junior Group Leader", "🌱"),
    (7600, "Assistant Professor", "🪴"),
    (8070, "Group Leader", "👩‍🔬"),
    (8575, "Mentor", "🤝"),
    (9080, "Principal Investigator", "🧭"),
    (9585, "Associate Professor", "🏫"),
    (10125, "Invited Speaker", "📣"),
    (10630, "Keynote Speaker", "🌟"),
    (11170, "Grant Panelist", "📋"),
    (11745, "Professor", "🎩"),
    (12320, "Consortium Lead", "🕸️"),
    (12895, "Department Head", "🏛️"),
    (13470, "Review Editor", "🖋️"),
    (14080, "Associate Editor", "📝"),
    (14655, "Institute Director", "🏢"),
    (15305, "Dean", "🦉"),
    (15915, "Editor-in-Chief", "📰"),
    (16565, "Academy Member", "🏺"),
    (17175, "Distinguished Professor", "💫"),
    (17860, "Honorary Doctor", "🎖️"),
    (18510, "Provost", "🗝️"),
    (19160, "University President", "🏰"),
    (19845, "Academy President", "👑"),
    (20530, "Science Advisor", "🧑‍⚖️"),
    (21215, "Lifetime Fellow", "🌿"),
    (21935, "Prize Winner", "🥇"),
    (22620, "Medalist", "🏵️"),
    (23340, "Laureate", "🌠"),
    (24060, "Living Legend", "✨"),
    (24780, "Textbook Author", "📖"),
    (25535, "Field Founder", "🚀"),
    (26255, "Hallmarks Author", "🏆"),
]


@dataclass(frozen=True)
class BadgeDef:
    code: str
    name: str
    description: str  # may contain {target}
    stat: str
    target: int  # the default target
    icon: str
    group: str


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
        "cheese_taster",
        "Cheese Taster",
        "Get the cheese: read your top paper first, {target} times",
        "cheeses",
        12,
        "🧀",
        "Discipline",
    ),
    BadgeDef(
        "big_cheese",
        "Big Cheese",
        "Get the cheese: read your top paper first, {target} times",
        "cheeses",
        50,
        "🐀",
        "Discipline",
    ),
    BadgeDef(
        "inbox_zero",
        "Inbox Zero",
        "Empty your reading queue by reading it, {target} times",
        "inbox_zero",
        3,
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
        "questing", "Questing", "Complete {target} weekly quests", "quests", 2, "🧭", "Consistency"
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
        "binge_week", "Binge Week", "Read {target} papers in one week", "max_week", 10, "🍿", "Pace"
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
        "Read {target} papers that are 25+ years old",
        "old_papers",
        2,
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
        15,
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


# more tiers, so a badge or a level comes every week or two
MORE_TIERS = [
    BadgeDef(
        "first_glance",
        "Glancer",
        "Look at {target} papers (abstract or deeper)",
        "papers_seen",
        15,
        "👁️",
        "Reading",
    ),
    BadgeDef(
        "browser",
        "Browser",
        "Look at {target} papers (abstract or deeper)",
        "papers_seen",
        25,
        "🛍️",
        "Reading",
    ),
    BadgeDef(
        "abstract_thinker",
        "Abstract Thinker",
        "Look at {target} papers (abstract or deeper)",
        "papers_seen",
        50,
        "💡",
        "Reading",
    ),
    BadgeDef("warm_up", "Warm-Up", "Read {target} papers", "papers_read", 6, "🔆", "Reading"),
    BadgeDef(
        "reading_list", "Reading List", "Read {target} papers", "papers_read", 8, "🔖", "Reading"
    ),
    BadgeDef(
        "regular", "Regular Reader", "Read {target} papers", "papers_read", 15, "📑", "Reading"
    ),
    BadgeDef(
        "lit_review", "Literature Review", "Read {target} papers", "papers_read", 75, "🗃️", "Reading"
    ),
    BadgeDef(
        "systematic",
        "Systematic Review",
        "Read {target} papers",
        "papers_read",
        150,
        "🔎",
        "Reading",
    ),
    BadgeDef("well_read", "Well-Read", "Read {target} papers", "papers_read", 200, "🧾", "Reading"),
    BadgeDef("erudite", "Erudite", "Read {target} papers", "papers_read", 300, "🎩", "Reading"),
    BadgeDef(
        "meta_analyst", "Meta-Analyst", "Read {target} papers", "papers_read", 350, "📊", "Reading"
    ),
    BadgeDef(
        "encyclopedia", "Encyclopedia", "Read {target} papers", "papers_read", 500, "🌍", "Reading"
    ),
    BadgeDef(
        "close_reader",
        "Close Reader",
        "Read {target} papers at least fully",
        "deep_reads",
        7,
        "🔍",
        "Depth",
    ),
    BadgeDef(
        "careful",
        "Careful Reader",
        "Read {target} papers at least fully",
        "deep_reads",
        10,
        "🧐",
        "Depth",
    ),
    BadgeDef(
        "journal_club",
        "Journal Club",
        "Read {target} papers at least fully",
        "deep_reads",
        25,
        "🫖",
        "Depth",
    ),
    BadgeDef(
        "deep_thinker",
        "Deep Thinker",
        "Read {target} papers at least fully",
        "deep_reads",
        50,
        "💭",
        "Depth",
    ),
    BadgeDef(
        "critical_mind",
        "Critical Mind",
        "Read {target} papers at least fully",
        "deep_reads",
        75,
        "⚗️",
        "Depth",
    ),
    BadgeDef(
        "scholar",
        "Scholar",
        "Read {target} papers at least fully",
        "deep_reads",
        100,
        "🧠",
        "Depth",
    ),
    BadgeDef(
        "sage", "Sage", "Read {target} papers at least fully", "deep_reads", 250, "🧙", "Depth"
    ),
    BadgeDef(
        "first_dive", "Diving In", "Read {target} papers thoroughly", "thorough", 2, "🏊", "Depth"
    ),
    BadgeDef(
        "free_diver", "Free Diver", "Read {target} papers thoroughly", "thorough", 10, "🐬", "Depth"
    ),
    BadgeDef(
        "pearl_diver",
        "Pearl Diver",
        "Read {target} papers thoroughly",
        "thorough",
        50,
        "🦪",
        "Depth",
    ),
    BadgeDef(
        "trench",
        "Mariana Trench",
        "Read {target} papers thoroughly",
        "thorough",
        100,
        "🌊",
        "Depth",
    ),
    BadgeDef(
        "note_taker",
        "Note Taker",
        "Write substantial notes on {target} papers",
        "reflections",
        5,
        "🗒️",
        "Depth",
    ),
    BadgeDef(
        "annotator",
        "Annotator",
        "Write substantial notes on {target} papers",
        "reflections",
        25,
        "🖍️",
        "Depth",
    ),
    BadgeDef(
        "chronicler",
        "Chronicler",
        "Write substantial notes on {target} papers",
        "reflections",
        50,
        "🪶",
        "Depth",
    ),
    BadgeDef(
        "essayist",
        "Essayist",
        "Write {target} characters of notes in total",
        "notes_chars",
        5000,
        "🖊️",
        "Depth",
    ),
    BadgeDef(
        "monograph",
        "Monograph",
        "Write {target} characters of notes in total",
        "notes_chars",
        50000,
        "📕",
        "Depth",
    ),
    BadgeDef("referee", "Referee", "Rate {target} papers", "ratings", 6, "⚖️", "Depth"),
    BadgeDef("connoisseur", "Connoisseur", "Rate {target} papers", "ratings", 50, "🍷", "Depth"),
    BadgeDef(
        "first_bite",
        "First Bite",
        "Get the cheese: read your top paper first, {target} times",
        "cheeses",
        3,
        "🐭",
        "Discipline",
    ),
    BadgeDef(
        "cheese_scholar",
        "Cheese Scholar",
        "Get the cheese: read your top paper first, {target} times",
        "cheeses",
        25,
        "📜",
        "Discipline",
    ),
    BadgeDef(
        "chair_of_cheese",
        "Chair of Cheese Studies",
        "Get the cheese: read your top paper first, {target} times",
        "cheeses",
        100,
        "👑",
        "Discipline",
    ),
    BadgeDef(
        "zen",
        "Zen Master",
        "Empty your reading queue {target} times",
        "inbox_zero",
        15,
        "☯️",
        "Discipline",
    ),
    BadgeDef(
        "gatekeeper",
        "Gatekeeper",
        "Mark {target} papers as not relevant",
        "dismissed",
        25,
        "🚧",
        "Discipline",
    ),
    BadgeDef(
        "streak_2",
        "Back to Back",
        "Read something {target} weeks in a row",
        "best_streak",
        2,
        "✌️",
        "Consistency",
    ),
    BadgeDef(
        "streak_8",
        "Two-Month Run",
        "Read something {target} weeks in a row",
        "best_streak",
        8,
        "🚴",
        "Consistency",
    ),
    BadgeDef(
        "streak_78",
        "Unstoppable",
        "Read something {target} weeks in a row",
        "best_streak",
        78,
        "🚂",
        "Consistency",
    ),
    BadgeDef(
        "streak_104",
        "Two Years Strong",
        "Read something {target} weeks in a row",
        "best_streak",
        104,
        "🏔️",
        "Consistency",
    ),
    BadgeDef(
        "habit",
        "Habit Former",
        "Read something in {target} different weeks",
        "active_weeks",
        5,
        "🌀",
        "Consistency",
    ),
    BadgeDef(
        "semester",
        "Semester",
        "Read something in {target} different weeks",
        "active_weeks",
        13,
        "🏫",
        "Consistency",
    ),
    BadgeDef(
        "two_semesters",
        "Two Semesters",
        "Read something in {target} different weeks",
        "active_weeks",
        26,
        "🌗",
        "Consistency",
    ),
    BadgeDef(
        "academic_year",
        "Academic Year",
        "Read something in {target} different weeks",
        "active_weeks",
        40,
        "📆",
        "Consistency",
    ),
    BadgeDef(
        "full_year",
        "Full Year",
        "Read something in {target} different weeks",
        "active_weeks",
        52,
        "🎆",
        "Consistency",
    ),
    BadgeDef(
        "seasoned",
        "Seasoned",
        "Read something in {target} different weeks",
        "active_weeks",
        75,
        "🍂",
        "Consistency",
    ),
    BadgeDef(
        "veteran",
        "Veteran",
        "Read something in {target} different weeks",
        "active_weeks",
        100,
        "🎗️",
        "Consistency",
    ),
    BadgeDef(
        "adventurer",
        "Adventurer",
        "Complete {target} weekly quests",
        "quests",
        3,
        "🎒",
        "Consistency",
    ),
    BadgeDef(
        "pathfinder",
        "Pathfinder",
        "Complete {target} weekly quests",
        "quests",
        5,
        "🥾",
        "Consistency",
    ),
    BadgeDef(
        "odyssey", "Odyssey", "Complete {target} weekly quests", "quests", 50, "⛵", "Consistency"
    ),
    BadgeDef("saga", "Saga", "Complete {target} weekly quests", "quests", 75, "📯", "Consistency"),
    BadgeDef("epic", "Epic", "Complete {target} weekly quests", "quests", 100, "🏰", "Consistency"),
    BadgeDef(
        "top_marks",
        "Top Marks",
        "Get grade A or S in {target} weeks",
        "a_weeks",
        2,
        "✅",
        "Consistency",
    ),
    BadgeDef(
        "honor_roll",
        "Honor Roll",
        "Get grade A or S in {target} weeks",
        "a_weeks",
        15,
        "🎖️",
        "Consistency",
    ),
    BadgeDef(
        "deans_list",
        "Dean's List",
        "Get grade A or S in {target} weeks",
        "a_weeks",
        30,
        "📋",
        "Consistency",
    ),
    BadgeDef(
        "valedictorian",
        "Valedictorian",
        "Get grade A or S in {target} weeks",
        "a_weeks",
        52,
        "🎤",
        "Consistency",
    ),
    BadgeDef(
        "distinction",
        "Distinction",
        "Get grade S in {target} weeks",
        "s_weeks",
        2,
        "✨",
        "Consistency",
    ),
    BadgeDef(
        "summa",
        "Summa cum Laude",
        "Get grade S in {target} weeks",
        "s_weeks",
        10,
        "🏵️",
        "Consistency",
    ),
    BadgeDef(
        "dawn_patrol",
        "Dawn Patrol",
        "Read {target} different papers between 05:00 and 08:00",
        "early_reads",
        20,
        "🐓",
        "Pace",
    ),
    BadgeDef(
        "night_shift",
        "Night Shift",
        "Read {target} different papers between 22:00 and 04:00",
        "night_reads",
        20,
        "🌙",
        "Pace",
    ),
    BadgeDef(
        "weekend_scholar",
        "Weekend Scholar",
        "Read {target} different papers on weekends",
        "weekend_reads",
        20,
        "⛱️",
        "Pace",
    ),
    BadgeDef(
        "landmark",
        "Landmark",
        "Read {target} landmark paper (top 1 % cited)",
        "classics",
        1,
        "🗿",
        "Breadth",
    ),
    BadgeDef(
        "canon_scholar",
        "Canon Scholar",
        "Read {target} landmark papers",
        "classics",
        50,
        "🏯",
        "Breadth",
    ),
    BadgeDef(
        "trend_spotter",
        "Trend Spotter",
        "Read {target} papers within 30 days of publication",
        "hot",
        15,
        "📈",
        "Breadth",
    ),
    BadgeDef(
        "cutting_edge",
        "Cutting Edge",
        "Read {target} papers within 30 days of publication",
        "hot",
        30,
        "🔪",
        "Breadth",
    ),
    BadgeDef(
        "breaking_news",
        "Breaking News",
        "Read {target} papers within 7 days of publication",
        "scoops",
        10,
        "🗞️",
        "Breadth",
    ),
    BadgeDef(
        "historian",
        "Historian",
        "Read {target} papers that are 25+ years old",
        "old_papers",
        5,
        "⏳",
        "Breadth",
    ),
    BadgeDef(
        "curious",
        "Curious Mind",
        "Read papers from {target} different topics",
        "topics",
        4,
        "🌿",
        "Breadth",
    ),
    BadgeDef(
        "wanderer",
        "Wanderer",
        "Read papers from {target} different topics",
        "topics",
        5,
        "🚶",
        "Breadth",
    ),
    BadgeDef(
        "bridge_builder",
        "Bridge Builder",
        "Read papers from {target} different topics",
        "topics",
        12,
        "🌉",
        "Breadth",
    ),
    BadgeDef(
        "universal",
        "Universal Scholar",
        "Read papers from {target} different topics",
        "topics",
        30,
        "🌌",
        "Breadth",
    ),
    BadgeDef(
        "scout",
        "Scout",
        "Read {target} papers from your recommendations",
        "from_recommendations",
        3,
        "🔭",
        "Breadth",
    ),
    BadgeDef(
        "pioneer",
        "Pioneer",
        "Read {target} papers from your recommendations",
        "from_recommendations",
        25,
        "🚀",
        "Breadth",
    ),
    BadgeDef(
        "expedition",
        "Expedition Leader",
        "Read {target} papers from your recommendations",
        "from_recommendations",
        50,
        "🗻",
        "Breadth",
    ),
    BadgeDef(
        "cartographer",
        "Cartographer",
        "Read {target} papers from your recommendations",
        "from_recommendations",
        100,
        "🗺",
        "Breadth",
    ),
    BadgeDef(
        "archivist",
        "Archivist",
        "Read {target} papers from your libraries",
        "from_library",
        25,
        "🗄️",
        "Breadth",
    ),
    BadgeDef(
        "librarian",
        "Librarian",
        "Read {target} papers from your libraries",
        "from_library",
        50,
        "🏷️",
        "Breadth",
    ),
    BadgeDef(
        "head_librarian",
        "Head Librarian",
        "Read {target} papers from your libraries",
        "from_library",
        100,
        "🗝️",
        "Breadth",
    ),
    BadgeDef(
        "bibliographer",
        "Bibliographer",
        "Read {target} papers from your libraries",
        "from_library",
        200,
        "📇",
        "Breadth",
    ),
    BadgeDef(
        "maverick",
        "Maverick",
        "Read {target} papers you added yourself",
        "manual_read",
        15,
        "🦄",
        "Breadth",
    ),
    BadgeDef("rising_star", "Rising Star", "Earn {target} XP", "xp", 750, "🌠", "Experience"),
    BadgeDef("silver_club", "Silver Club", "Earn {target} XP", "xp", 2500, "🥈", "Experience"),
    BadgeDef("ten_thousand", "Ten Thousand", "Earn {target} XP", "xp", 10000, "🔟", "Experience"),
    BadgeDef("gold_club", "Gold Club", "Earn {target} XP", "xp", 15000, "🥇", "Experience"),
    BadgeDef("platinum_club", "Platinum Club", "Earn {target} XP", "xp", 20000, "💠", "Experience"),
    BadgeDef("hall_of_fame", "Hall of Fame", "Earn {target} XP", "xp", 25000, "🏆", "Experience"),
]


def _display_order(badges: list[BadgeDef]) -> list[BadgeDef]:
    """Groups, then statistics, in order of first appearance; tiers by target."""
    groups, stats = {}, {}
    for badge in badges:
        groups.setdefault(badge.group, len(groups))
        stats.setdefault(badge.stat, len(stats))
    return sorted(badges, key=lambda b: (groups[b.group], stats[b.stat], b.target))


BADGE_CATALOG = _display_order(BADGE_CATALOG + MORE_TIERS)
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
RULES_FORMAT = 1


class GameRules(BaseModel):
    """Every reward and penalty. Locked into a profile when the game starts."""

    model_config = {"extra": "forbid", "allow_inf_nan": False}

    # rules format: 1 = the rules of litrad 1.0; a later release that changes what
    # the stored rules mean raises it (and reads every earlier format)
    rules_format: int = Field(RULES_FORMAT, ge=1, le=RULES_FORMAT)
    # share of the base points for a paper read before litrad ("read earlier")
    backlog_share: float = Field(0.8, ge=0, le=1)
    # base points per reading level (paid up to the deepest level ever reached)
    xp_abstract: int = Field(5, ge=0, le=10000)
    xp_skimmed: int = Field(15, ge=0, le=10000)
    xp_read: int = Field(40, ge=0, le=10000)
    xp_thorough: int = Field(80, ge=0, le=10000)
    # bonus ("got the cheese"): share of the base points for reading the top paper of the queue
    cheese_bonus: float = Field(0.5, ge=0, le=10)
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
    recommendation_xp: int = Field(8, ge=0, le=10000)
    # bonus: first rating of a paper
    rating_xp: int = Field(2, ge=0, le=10000)
    # bonus: notes on a paper reach this length
    reflection_chars: int = Field(140, ge=1, le=100000)
    reflection_xp: int = Field(5, ge=0, le=10000)
    # bonus: the queue becomes empty because you read its last paper (once per day)
    inbox_zero_xp: int = Field(5, ge=0, le=10000)
    # weekly quest: papers at least "read" + further papers at least skimmed
    weekly_detail_goal: int = Field(1, ge=0, le=100)
    weekly_skim_goal: int = Field(1, ge=0, le=100)
    quest_xp: int = Field(20, ge=0, le=10000)
    # per unlocked badge
    badge_xp: int = Field(8, ge=0, le=10000)
    # penalty: a queued paper leaves the queue before it was at least skimmed
    drop_penalty: int = Field(15, ge=0, le=10000)
    levels: list[LevelDef] = Field(
        default_factory=lambda: [LevelDef(xp=x, title=t, icon=i) for x, t, i in DEFAULT_LEVELS]
    )
    # badge code -> target; missing codes use the catalog default
    badge_targets: dict[str, int] = Field(default_factory=dict)

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
        """The badges with this profile's targets."""
        result = []
        for definition in BADGE_CATALOG:
            target = self.badge_targets.get(definition.code, definition.target)
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

    def locked(self) -> "GameRules":
        """A copy with every badge target written out, as a profile stores it.

        Every rule value is stored anyway; with the resolved targets, a later
        change of a catalogue default never changes a locked profile.
        The fingerprint stays the same (it is computed from resolved targets).
        """
        targets = {badge.code: badge.target for badge in self.badges()}
        return self.model_copy(update={"badge_targets": targets})

    def fingerprint(self) -> str:
        """Short hash of the complete rule set (equal rules = equal fingerprint).

        Stable across versions: see ``FINGERPRINT_FIELDS``.
        """
        return rules_fingerprint(self)


# The fields every fingerprint covers: the rules of litrad 1.0. A rule added
# later (with a default) enters the hash only when a profile sets it to a value
# other than its default, so rules locked by an older 1.x keep their fingerprint
# in every newer one. Never remove or rename a name here (2.0 only).
FINGERPRINT_FIELDS = {
    GameRules: frozenset(
        {
            "rules_format",
            "backlog_share",
            "xp_abstract",
            "xp_skimmed",
            "xp_read",
            "xp_thorough",
            "cheese_bonus",
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

# the badges of litrad 1.0: a badge added later counts only with a target other
# than its catalogue default (its presence follows from the release)
BASELINE_BADGES = frozenset(
    """abstract_thinker abyss_explorer academic_year adventurer annotator archaeologist
    archivist bibliographer bibliophile big_cheese binge_week bookworm breaking_news
    bridge_builder browser canon_keeper canon_scholar careful cartographer centurion
    chair_of_cheese cheese_scholar cheese_taster chronicler classicist close_reader
    comeback connoisseur critic critical_mind curator curious cutting_edge dawn_patrol
    deans_list deep_diver deep_thinker distinction early_bird early_riser encyclopedia
    epic erudite essayist expedition fan_club first_bite first_dive first_glance
    first_read free_diver free_spirit full_year gatekeeper gold_club habit hall_of_fame
    head_librarian high_five historian honor_roll inbox_zero journal_club landmark
    librarian library_diver lit_review living_library marathon maverick meta_analyst
    minimalist monograph night_owl night_shift note_taker novelist odyssey page_turner
    pathfinder pearl_diver perfectionist pioneer platinum_club polymath quest_legend
    quest_master questing reading_list referee regular renaissance rising_star saga sage
    scholar scoop scout scribe seasoned semester silver_club straight_a streak_104
    streak_12 streak_2 streak_26 streak_4 streak_52 streak_78 streak_8 summa systematic
    ten_thousand thousand_club top_marks trailblazer trench trend_spotter two_semesters
    universal valedictorian veteran wanderer warm_up weekend_scholar weekend_warrior
    well_read zen""".split()
)


def _canonical(model: BaseModel):
    """The model as JSON data without later-added fields that hold their default."""
    baseline = next(
        (fields for cls, fields in FINGERPRINT_FIELDS.items() if isinstance(model, cls)),
        None,
    )
    data = {}
    for name, info in type(model).model_fields.items():
        value = getattr(model, name)
        if baseline is not None and name not in baseline:
            if value == info.get_default(call_default_factory=True):
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
    resolved = {badge.code: badge.target for badge in rules.badges()}
    data["badge_targets"] = {
        code: target
        for code, target in resolved.items()
        if code in BASELINE_BADGES or target != _CATALOG[code].target
    }
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]
