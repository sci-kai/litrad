"""The web UI (FastAPI + Jinja2, server-rendered, no external assets).

How a request flows:

1. ``require_login`` (middleware): with ``web.password_hash`` set, every page
   except /login, /static and the phone-app files (see pwa.py) needs the
   signed session cookie.
2. The same middleware sends requests without a valid profile cookie to
   /welcome and otherwise stores the chosen profile in ``CURRENT_PROFILE``.
   Every route then works on that profile: ``current_cfg()`` merges
   ``config.yaml`` with the profile's settings.yaml, ``open_session()`` opens
   the profile's database.
3. ``same_origin_only`` rejects POST requests from other websites;
   ``trusted_host_only`` (without password) rejects requests for other host
   names (DNS rebinding).
   In desktop mode (``litresearch app``, see desktop.py) ``/app/ping`` is
   open and ``POST /app/quit`` (password if set, no profile, same origin
   only) stops the server.
4. Pages are rendered with ``render()``, which adds the header (level, XP,
   streak, queue size, recommendation reminder). Actions post a form and
   redirect back (post/redirect/get); rewards travel as a short-lived token
   (``Celebrations``) and are shown as toasts by static/app.js.

Sections in ``create_app``: login · profile selection · pages · libraries ·
profile · documentation · actions.
"""

import asyncio
import contextvars
import csv
import hmac
import io
import json
import os
import secrets
import shutil
import tempfile
from collections import OrderedDict
from collections.abc import Callable
from datetime import date, datetime
from itertools import groupby
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode, urlsplit

from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Query, Request
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import case, func
from sqlmodel import Session, select
from starlette.exceptions import HTTPException as StarletteHTTPException

from litresearch import __version__, examples, export, history, libraries, profiles
from litresearch import notes as note_files
from litresearch.backup import (
    BackupError,
    export_progress,
    latest_backup,
    profile_settings,
    run_backup,
    save_profile_file,
    saved_files,
    slug,
    summarize,
    summarize_session,
)
from litresearch.config import load_config
from litresearch.db import (
    Category,
    IngestRun,
    Library,
    LibraryPaper,
    Paper,
    QueueItem,
    Reading,
    ReadStatus,
    Recommendation,
    RecommendationBatch,
    XpEvent,
    depth,
    init_db,
)
from litresearch.export import FORMATS as EXPORT_FORMATS
from litresearch.game import (
    KIND_LABELS,
    Award,
    badge_progress,
    header_stats,
    ledger,
    level_for,
    next_goals,
    next_read_streak,
    personal_bests,
    queue_top_waiting_days,
    reading_calendar,
    reading_impact_factor,
    stats,
    streak_multiplier,
    week_progress,
    weekly_summaries,
    weeks_of_history,
)
from litresearch.ingest.check import FIELDS as BIB_FIELDS
from litresearch.ingest.check import Finding, check_file
from litresearch.ingest.formats import FORMATS as INPUT_FORMATS
from litresearch.ingest.pipeline import MODES as IMPORT_MODES
from litresearch.ingest.pipeline import import_into
from litresearch.papers import paper_link
from litresearch.profile import ProfileError, active_rules, get_profile
from litresearch.profiles import ProfilesError
from litresearch.reading import (
    ReadingError,
    add_note,
    drop_from_queue,
    drop_would_cost,
    fetch_paper,
    find_paper,
    queue_add,
    queue_items,
    queue_move,
    record_reading,
    source_of,
)
from litresearch.recommend.engine import (
    batch_items,
    build_recommendations,
    days_since_last_batch,
    latest_batch,
    no_library_used,
)
from litresearch.search import SORTS, SOURCES, SearchQuery, enabled_sources, search_library
from litresearch.search import search as literature_search
from litresearch.web import pwa
from litresearch.web.auth import (
    COOKIE,
    SESSION_DAYS,
    check_session,
    load_secret,
    make_session,
    sign_value,
    verify_password,
)
from litresearch.web.docs import DocsCache
from litresearch.web.network import is_trusted_host
from litresearch.web.settings import (
    GROUPS,
    RULE_GROUPS,
    SettingsError,
    describe_problem,
    friendly_error,
    levels_text,
    parse_form,
    parse_rules_form,
    parse_topics,
)

HERE = Path(__file__).parent
PROFILE_COOKIE = "litresearch_profile"
# the profile of the current request (set by the middleware)
CURRENT_PROFILE: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "litresearch_profile", default=None
)
QUEUE_NUDGE_DAYS = 14
LEDGER_PAGE = 50
MAX_UPLOAD = 50 * 2**20  # bytes
LIBRARY_PAGE = 50
MAX_WEEKS = 104  # weekly reports on the dashboard: how far back they go
WEEKS_PER_PAGE = 4
STALE_LIBRARY_DAYS = 30

KIND_ICONS = {
    "read": "📖",
    "streak": "🔥",
    "frog": "🐸",
    "classic": "🏛️",
    "hot": "📰",
    "recommended": "🎯",
    "rating": "⭐",
    "reflection": "✍️",
    "inbox_zero": "📭",
    "quest": "🗓️",
    "badge": "🏅",
    "penalty": "⚠️",
}
STATUS_ICONS = {
    "abstract": "👀",
    "skimmed": "📄",
    "read": "📖",
    "thorough": "🔬",
    "dismissed": "🚫",
}


class Celebrations:
    """Short-lived messages shown after a redirect (single-user app)."""

    def __init__(self, size: int = 50):
        self._items: OrderedDict[str, dict] = OrderedDict()
        self._size = size

    def add(self, payload: dict) -> str:
        token = secrets.token_urlsafe(8)
        self._items[token] = payload
        while len(self._items) > self._size:
            self._items.popitem(last=False)
        return token

    def pop(self, token: str | None) -> dict | None:
        return self._items.pop(token, None) if token else None


def award_payload(award: Award | None, message: str, session: Session, kind="success") -> dict:
    """The toast/confetti data of an action: points, events, level-ups, badges."""
    payload = {"kind": kind, "message": message, "events": [], "points": 0}
    if award is None:
        return payload
    payload["events"] = [
        {
            "icon": KIND_ICONS.get(e.kind, "✨"),
            "points": e.points,
            "reason": e.reason,
            "kind": e.kind,
        }
        for e in award.events
    ]
    payload["points"] = award.points
    payload["badges"] = [
        {"name": b.name, "description": b.description, "icon": b.icon} for b in award.badges
    ]
    if award.level_before and award.level_after.number > award.level_before.number:
        payload["level_up"] = {
            "number": award.level_after.number,
            "title": award.level_after.title,
            "icon": award.level_after.icon,
        }
    if award.profile_started:
        payload["message"] += " · Your game has started; its rules are now locked."
    payload["quest"] = any(e.kind == "quest" for e in award.events)
    payload["penalty"] = any(e.points < 0 for e in award.events)
    return payload


def create_app(
    config_path: Path,
    trusted_names: frozenset[str] | None = None,
    on_quit: Callable[[], None] | None = None,
) -> FastAPI:
    """Build the FastAPI app for the config file at ``config_path``.

    With ``trusted_names`` (``litresearch serve`` passes them), a server without
    password only answers requests addressed to an IP address or one of these
    names. ``on_quit`` (desktop mode only) adds the Quit button and the
    ``/app/ping`` and ``/app/quit`` routes; it is called after the "stopped"
    page has been sent.
    """
    config_path = Path(config_path)
    app = FastAPI(title="litresearch", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    pwa.register(app, templates, HERE)  # manifest, service worker, offline page
    celebrations = Celebrations()

    env = templates.env
    env.globals.update(
        version=__version__,
        kind_icons=KIND_ICONS,
        kind_labels=KIND_LABELS,
        status_icons=STATUS_ICONS,
        desktop=on_quit is not None,
    )
    desktop_open = frozenset({"/app/ping"} if on_quit else ())  # no login, no profile
    desktop_no_profile = frozenset({"/app/quit"} if on_quit else ())  # login if set
    env.filters["authors"] = lambda a: (
        "unknown authors" if not a else a[0].split(",")[0] + (" et al." if len(a) > 1 else "")
    )
    env.filters["pct"] = lambda v: f"{v * 100:.0f}%"

    env.globals["paper_link"] = paper_link
    env.globals["note_files_merge"] = note_files.merge

    def base_config():
        return load_config(config_path)

    def current_cfg():
        """Configuration of the profile chosen for this request."""
        slug = CURRENT_PROFILE.get()
        if slug is None:
            raise RuntimeError("no profile selected")
        return profiles.profile_config(base_config(), slug)

    def save_profile_settings(updates: dict):
        """Settings belong to the profile (its settings.yaml), not to config.yaml."""
        weights = (updates.get("selection") or {}).get("weights") or {}
        if weights and sum(weights.values()) <= 0:
            raise SettingsError("At least one score weight must be above 0")
        return profiles.write_overlay(base_config(), CURRENT_PROFILE.get(), updates)

    def open_session():
        cfg = current_cfg()
        engine = init_db(cfg.paths.database)
        return cfg, engine

    def secret() -> bytes:
        return load_secret(base_config().paths.database.parent / "web_secret")

    def profile_cookie(slug: str) -> str:
        return f"{slug}.{sign_value(secret(), 'profile:' + slug)}"

    def profile_from_cookie(request: Request) -> str | None:
        slug, _, signature = (request.cookies.get(PROFILE_COOKIE) or "").partition(".")
        if not slug or not hmac.compare_digest(signature, sign_value(secret(), "profile:" + slug)):
            return None
        return slug if profiles.exists(base_config(), slug) else None

    @app.middleware("http")
    async def require_login(request: Request, call_next):
        """Password (for network access) first, then a chosen profile."""
        base = base_config()
        path = request.url.path
        open_paths = path in ("/login", "/logout") or path in pwa.OPEN_PATHS
        open_paths = open_paths or path.startswith("/static/") or path in desktop_open
        password_hash = base.web.password_hash
        if password_hash and not open_paths:
            if not check_session(request.cookies.get(COOKIE), secret(), password_hash):
                if request.method == "GET":
                    target = path + (f"?{request.url.query}" if request.url.query else "")
                    return RedirectResponse(
                        f"/login?{urlencode({'next': target})}", status_code=303
                    )
                return PlainTextResponse("Login required", status_code=401)
        if open_paths or path == "/welcome" or path.startswith("/welcome/"):
            return await call_next(request)
        if path in desktop_no_profile:
            return await call_next(request)
        slug = profile_from_cookie(request)
        if slug is None:
            if request.method == "GET":
                return RedirectResponse("/welcome", status_code=303)
            return PlainTextResponse("Choose a profile first", status_code=401)
        token = CURRENT_PROFILE.set(slug)
        try:
            return await call_next(request)
        finally:
            CURRENT_PROFILE.reset(token)

    @app.middleware("http")
    async def limit_body_size(request: Request, call_next):
        """Refuse huge uploads before they are spooled to the (small) temp folder."""
        length = request.headers.get("content-length") or "0"
        if not length.isdigit() or int(length) > MAX_UPLOAD + 2**20:
            return PlainTextResponse(
                f"Request too large (at most {MAX_UPLOAD // 2**20} MB)", status_code=413
            )
        return await call_next(request)

    @app.middleware("http")
    async def same_origin_only(request: Request, call_next):
        if request.method == "POST":
            origin = request.headers.get("origin") or request.headers.get("referer")
            if origin and urlsplit(origin).netloc != request.url.netloc:
                return PlainTextResponse("Cross-origin request rejected", status_code=403)
        return await call_next(request)

    @app.middleware("http")
    async def trusted_host_only(request: Request, call_next):
        """Without password, refuse host names a malicious website could rebind."""
        if trusted_names is not None and not base_config().web.password_hash:
            if not is_trusted_host(request.headers.get("host", ""), trusted_names):
                return PlainTextResponse(
                    "Unknown host name: open litresearch by its IP address or localhost",
                    status_code=421,
                )
        return await call_next(request)

    def render(request: Request, name: str, session: Session, cfg, **context):
        """Render a page with the header data every page needs (profile, XP, reminder)."""
        token = request.query_params.get("c")
        today = date.today()
        rules = active_rules(session, cfg)
        head = header_stats(session, today, rules)
        since = days_since_last_batch(session, today)
        context.update(
            request=request,
            cfg=cfg,
            today=today,
            rules=rules,
            profile=get_profile(session),
            base_xp=rules.base_xp(),
            levels=rules.levels,
            header={
                "xp": head["xp"],
                "level": head["level"],
                "progress": head["level"].progress(head["xp"]),
                "streak": head["streak"],
                "queue": len(queue_items(session)),
                "since_batch": since,
                # time to ask for new recommendations
                "reminder": bool(cfg.selection.reminder_days)
                and (since is None or since >= cfg.selection.reminder_days),
            },
            celebration=celebrations.pop(token),
        )
        return templates.TemplateResponse(request, name, context)

    def back(url: str, payload: dict | None = None) -> RedirectResponse:
        if payload:
            # the token goes into the query, which must come before any #fragment
            url, _, fragment = url.partition("#")
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}{urlencode({'c': celebrations.add(payload)})}"
            if fragment:
                url += f"#{fragment}"
        return RedirectResponse(url, status_code=303)

    def error(url: str, message: str) -> RedirectResponse:
        return back(url, {"kind": "error", "message": message, "events": [], "points": 0})

    # --- error pages -------------------------------------------------------------
    # unknown addresses and invalid values get a page in the app's style, not JSON

    def wants_html(request: Request) -> bool:
        accept = request.headers.get("accept", "")
        return "application/json" not in accept or "text/html" in accept

    def error_page(request: Request, status: int, title: str, message: str, problems=()):
        context = {
            "status": status,
            "title": title,
            "message": message,
            "problems": list(problems),
            "path": request.url.path,
            "icon": "🧭" if status == 404 else "🤔",
        }
        if CURRENT_PROFILE.get() is not None:
            cfg, engine = open_session()
            try:
                with Session(engine) as session:
                    response = render(request, "error.html", session, cfg, **context)
                    response.status_code = status
                    return response
            finally:
                engine.dispose()
        return templates.TemplateResponse(request, "error.html", context, status_code=status)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        if not wants_html(request) or exc.status_code < 400:
            return await http_exception_handler(request, exc)
        if exc.status_code == 404:
            detail = exc.detail if exc.detail != "Not Found" else None
            return error_page(
                request,
                404,
                "Page not found",
                detail or "There is nothing at this address. It may be an old bookmark.",
            )
        return error_page(request, exc.status_code, "Something is not right", str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        if not wants_html(request):
            return await request_validation_exception_handler(request, exc)
        problems = []
        for item in exc.errors():
            loc = [str(p) for p in item.get("loc", ()) if p not in ("query", "path", "body")]
            value = item.get("input")
            shown = f" (got '{value}')" if isinstance(value, str) else ""
            problems.append(f"'{' '.join(loc)}' {describe_problem(item)}{shown}")
        if any((item.get("loc") or ("",))[0] == "path" for item in exc.errors()):
            # /papers/abc: such a page cannot exist
            return error_page(
                request, 404, "Page not found", "This address does not exist.", problems
            )
        return error_page(
            request,
            422,
            "Invalid address or form",
            "A value in the request is not valid.",
            problems,
        )

    # --- login -------------------------------------------------------------------

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request, next: str = "/", failed: bool = False):
        return templates.TemplateResponse(
            request, "login.html", {"next": safe(next), "failed": failed}
        )

    @app.post("/login")
    async def login(password: str = Form(...), next: str = Form("/")):
        """Check the password; a wrong one costs a second, a right one sets the cookie."""
        password_hash = load_config(config_path).web.password_hash
        if not password_hash:
            return RedirectResponse("/", status_code=303)
        if not verify_password(password, password_hash):
            await asyncio.sleep(1)  # slows down guessing
            return RedirectResponse(
                f"/login?{urlencode({'next': safe(next), 'failed': 'true'})}", status_code=303
            )
        response = RedirectResponse(safe(next), status_code=303)
        response.set_cookie(
            COOKIE,
            make_session(secret(), password_hash),
            max_age=SESSION_DAYS * 86400,
            httponly=True,
            samesite="lax",
        )
        return response

    @app.post("/logout")
    def logout():
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(COOKIE)
        return response

    # --- profile selection (shown before anything else) -----------------------------

    def enter(slug: str, payload: dict | None = None, url: str = "/") -> RedirectResponse:
        response = back(url, payload)
        response.set_cookie(
            PROFILE_COOKIE, profile_cookie(slug), max_age=365 * 86400, httponly=True, samesite="lax"
        )
        profiles.set_current(base_config(), slug)  # the CLI follows the last choice
        return response

    def all_saved_files(base) -> list[Path]:
        root = base.backup.dir
        files = saved_files(root)
        if root.exists():
            for sub in sorted(p for p in root.iterdir() if p.is_dir()):
                files += saved_files(sub)
        return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)[:40]

    @app.get("/welcome", response_class=HTMLResponse)
    def welcome(request: Request):
        """The first screen: choose, create, take over or load a profile."""
        base = base_config()
        return templates.TemplateResponse(
            request,
            "welcome.html",
            {
                "profiles": profiles.list_profiles(base),
                "legacy": profiles.legacy_available(base),
                "saves": [(p, p.relative_to(base.backup.dir)) for p in all_saved_files(base)],
                "rule_groups": RULE_GROUPS,
                "form_rules": base.game,
                "levels_text": levels_text(base.game),
                "celebration": celebrations.pop(request.query_params.get("c")),
                "current": profile_from_cookie(request),
            },
        )

    @app.post("/welcome/select")
    def welcome_select(slug: str = Form(...)):
        if not profiles.exists(base_config(), slug):
            return error("/welcome", "That profile does not exist")
        return enter(slug)

    async def _new_profile(request: Request, adopt: bool):
        form = await request.form()
        base = base_config()
        try:
            rules = parse_rules_form(dict(form), base.game)
            name = str(form.get("name", ""))
            if adopt:
                slug = profiles.adopt_legacy(base, name, rules)
            else:
                slug = profiles.create_profile(base, name, rules)
        except (SettingsError, ProfilesError, ProfileError, BackupError) as exc:
            return error("/welcome", f"Not created: {friendly_error(exc)}")
        info = profiles.describe(base, slug)
        return enter(
            slug,
            {
                "kind": "success",
                "events": [],
                "points": 0,
                "sparkle": True,
                "message": f"Welcome, {info.name}! Your rules are locked "
                f"(fingerprint {info.fingerprint}).",
            },
        )

    @app.post("/welcome/create")
    async def welcome_create(request: Request):
        return await _new_profile(request, adopt=False)

    @app.post("/welcome/adopt")
    async def welcome_adopt(request: Request):
        return await _new_profile(request, adopt=True)

    @app.post("/welcome/load")
    async def welcome_load(request: Request):
        """A saved profile file (uploaded or from the backups) becomes a new profile."""
        form = await request.form()
        base = base_config()
        try:
            uploads = await read_upload(request)
            saved = str(form.get("saved", "")).strip()
            if saved:
                path = next((f for f in all_saved_files(base) if f.name == saved), None)
                if path is None:
                    raise BackupError("Saved file not found")
                uploads = [{"name": saved, "data": json.loads(path.read_text("utf-8"))}]
            if not uploads:
                raise BackupError("Choose a saved profile or upload a file")
            name = str(form.get("name", "")).strip() or None
            slug = profiles.import_profile_file(base, uploads[0]["data"], name)
        except (BackupError, ProfilesError, json.JSONDecodeError) as exc:
            return error("/welcome", f"Not loaded: {friendly_error(exc)}")
        info = profiles.describe(base, slug)
        return enter(
            slug,
            {
                "kind": "success",
                "events": [],
                "points": 0,
                "sparkle": True,
                "message": f"Loaded profile {info.name} ({info.xp} XP).",
            },
        )

    @app.post("/welcome/leave")
    def welcome_leave():
        response = RedirectResponse("/welcome", status_code=303)
        response.delete_cookie(PROFILE_COOKIE)
        return response

    # --- pages -------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request, weeks: int = WEEKS_PER_PAGE, wpage: int = 1):
        """Up next, weekly quest, latest recommendations, queue and weekly reports.

        The weekly reports are paged: ``weeks`` per page (at most 12), ``wpage``
        counts back from this week.
        """
        weeks = min(max(weeks, 1), 12)
        cfg, engine = open_session()
        try:
            with Session(engine) as session:
                today = date.today()
                items = queue_items(session)
                batch = latest_batch(session)
                recs = batch_items(session, batch) if batch else []
                rules = active_rules(session, cfg)
                progress = week_progress(session, rules, today)
                recent, _ = ledger(session, limit=6)
                figures = stats(session, today, rules)
                history = min(weeks_of_history(session, today), MAX_WEEKS)
                # a new profile gets a checklist of first steps instead of empty cards
                steps = None
                if not figures["papers_read"]:
                    has_library = session.exec(
                        select(LibraryPaper.paper_id)
                        .join(Library, Library.id == LibraryPaper.library_id)
                        .where(Library.kind.not_in(libraries.EXAMPLE_KINDS))
                        .limit(1)
                    ).first()
                    steps = [
                        ("Import your library", "/library/import", has_library is not None),
                        (
                            "Name your topics",
                            "/recommendations?topics=1#topics",
                            bool(cfg.topics) or topics_onboarded(),
                        ),
                        ("Get recommendations", "/recommendations", batch is not None),
                        ("Queue a paper", "/queue", bool(items)),
                        ("Log your first reading", "/queue#top", False),
                    ]
                pages = max(1, -(-history // weeks))
                wpage = min(max(wpage, 1), pages)
                return render(
                    request,
                    "dashboard.html",
                    session,
                    cfg,
                    up_next=items[0] if items else None,
                    queue=items,
                    batch=batch,
                    recs=recs,
                    rec_status=recommendation_status(session, recs),
                    week=progress,
                    hints=next_goals(session, rules, today, figures),
                    recent=recent,
                    waiting=queue_top_waiting_days(session, today),
                    nudge_days=QUEUE_NUDGE_DAYS,
                    stats=figures,
                    multiplier=streak_multiplier(next_read_streak(session, today), rules),
                    # never older than the first week with any activity
                    summaries=weekly_summaries(
                        session,
                        rules,
                        today,
                        weeks=min(weeks, history - (wpage - 1) * weeks),
                        offset=(wpage - 1) * weeks,
                    ),
                    weeks=weeks,
                    wpage=wpage,
                    wpages=pages,
                    steps=steps,
                )
        finally:
            engine.dispose()

    def recommendation_status(session: Session, recs) -> dict[int, str]:
        status = {}
        queued = set(session.exec(select(QueueItem.paper_id)).all())
        for _, paper in recs:
            reading = session.get(Reading, paper.id)
            if paper.id in queued:
                status[paper.id] = "queued"
            elif reading is not None:
                status[paper.id] = reading.status.value
        return status

    @app.get("/recommendations", response_class=HTMLResponse)
    def recommendations(request: Request, batch: int | None = None, topics: bool = False):
        """A recommendation set (the newest, or ``?batch=``), or the topic set-up."""
        cfg, engine = open_session()
        try:
            with Session(engine) as session:
                batches = session.exec(
                    select(RecommendationBatch).order_by(RecommendationBatch.id.desc())
                ).all()
                current = (
                    session.get(RecommendationBatch, batch)
                    if batch
                    else (batches[0] if batches else None)
                )
                recs = batch_items(session, current) if current else []
                status = recommendation_status(session, recs)
                return render(
                    request,
                    "recommendations.html",
                    session,
                    cfg,
                    batch=current,
                    batches=batches,
                    # the menu of earlier sets, newest first, one group per month
                    batch_groups=[
                        (month, list(members))
                        for month, members in groupby(
                            batches, key=lambda b: b.created_at.strftime("%Y-%m")
                        )
                    ],
                    recs=recs,
                    rec_status=status,
                    # every paper of the set is handled (queued, read or not relevant):
                    # the page shows a "finished" note; ?batch= still shows the list
                    finished=bool(recs) and len(status) == len(recs) and batch is None,
                    queue_full=len(queue_items(session)) >= cfg.queue.max_size,
                    # first visit: ask for the topics of interest
                    onboarding=not cfg.topics and not topics_onboarded(),
                    edit_topics=topics,
                )
        finally:
            engine.dispose()

    def topics_onboarded() -> bool:
        info = profiles.meta(base_config(), CURRENT_PROFILE.get())
        return bool((info.get("onboarding") or {}).get("topics_done"))

    @app.post("/recommendations/topics")
    async def topics_save(request: Request):
        """Save the topics of this profile (also ends the first-visit set-up)."""
        form = await request.form()
        data = {"excluded_terms": form.get("excluded_terms", "")}
        for key in ("topic_name", "topic_weight", "topic_queries"):
            data[key] = form.getlist(key)
        try:
            updates = parse_topics(data)
            profiles.write_overlay(
                base_config(),
                CURRENT_PROFILE.get(),
                {**updates, "onboarding": {"topics_done": True}},
            )
        except (SettingsError, ProfilesError, ValueError) as exc:
            return error("/recommendations?topics=1#topics", f"Not saved: {friendly_error(exc)}")
        n = len(updates["topics"])
        return back(
            "/recommendations",
            {
                "kind": "success",
                "message": f"Saved {n} topic{'s' if n != 1 else ''} for this profile.",
                "events": [],
                "points": 0,
            },
        )

    @app.post("/recommendations/topics/skip")
    def topics_skip():
        profiles.write_overlay(
            base_config(), CURRENT_PROFILE.get(), {"onboarding": {"topics_done": True}}
        )
        return back(
            "/recommendations",
            {
                "kind": "info",
                "message": "No topics: recommendations come from papers similar to your library.",
                "events": [],
                "points": 0,
            },
        )

    @app.post("/recommendations/build")
    def build(s2: bool = Form(True)):
        """Get a new recommendation set now (takes about a minute)."""
        cfg, engine = open_session()
        try:
            with Session(engine) as session:
                http = make_http(cfg)
                try:
                    batch = build_recommendations(cfg, session, http, use_s2=s2)
                finally:
                    http.close()
                n = len(batch_items(session, batch))
                return back(
                    "/recommendations",
                    {
                        "kind": "info",
                        "message": f"New batch: {n} papers from {batch.n_candidates} candidates",
                        "events": [],
                        "points": 0,
                        "sparkle": True,
                    },
                )
        finally:
            engine.dispose()

    @app.get("/queue", response_class=HTMLResponse)
    def queue_page(request: Request):
        """The reading queue with read forms and removal costs."""
        cfg, engine = open_session()
        try:
            with Session(engine) as session:
                items = queue_items(session)
                costs = {paper.id: drop_would_cost(session, cfg, paper) for _, paper in items}
                readings = {paper.id: session.get(Reading, paper.id) for _, paper in items}
                return render(
                    request,
                    "queue.html",
                    session,
                    cfg,
                    queue=items,
                    costs=costs,
                    readings=readings,
                    today=date.today(),
                    nudge_days=QUEUE_NUDGE_DAYS,
                )
        finally:
            engine.dispose()

    # --- read papers (reading history) -------------------------------------------------

    def history_filter(
        sort: str,
        depth: list[str] | None,
        rating: str,
        since: str,
        until: str,
        library: str,
        notes: str,
        dismissed: str,
        q: str,
    ) -> history.HistoryFilter:
        return history.HistoryFilter(
            sort=sort if sort in history.SORTS else "last",
            depths=[d for d in depth or [] if d in history.DEPTHS],
            min_rating=int(rating) if rating in ("1", "2", "3", "4", "5") else 0,
            since=history.parse_date(since),
            until=history.parse_date(until),
            library=int(library) if library.isdigit() else None,
            has_notes=bool(notes),
            dismissed=bool(dismissed),
            q=q,
        )

    @app.get("/read", response_class=HTMLResponse)
    def read_page(
        request: Request,
        sort: str = "last",
        depth: Annotated[list[str] | None, Query()] = None,
        rating: str = "",
        since: str = "",
        until: str = "",
        library: str = "",
        notes: str = "",
        dismissed: str = "",
        q: str = "",
        page: int = 1,
    ):
        """Every paper you marked, filtered and sorted; 50 per page."""
        cfg, engine = open_session()
        try:
            with Session(engine) as session:
                note_files.refresh_quietly(session, cfg)  # the search sees edits in note files
                f = history_filter(sort, depth, rating, since, until, library, notes, dismissed, q)
                found = history.rows(session, f)
                pages = max(1, -(-len(found) // history.PAGE_SIZE))
                page = min(max(page, 1), pages)
                shown = found[(page - 1) * history.PAGE_SIZE : page * history.PAGE_SIZE]
                query = urlencode(
                    [
                        ("sort", f.sort),
                        *(("depth", d) for d in f.depths),
                        ("rating", rating),
                        ("since", since),
                        ("until", until),
                        ("library", library),
                        ("notes", notes),
                        ("dismissed", dismissed),
                        ("q", q),
                    ]
                )
                return render(
                    request,
                    "read.html",
                    session,
                    cfg,
                    items=history.entries(session, shown),
                    n_rows=len(found),
                    counts=history.counts(found),
                    page=page,
                    pages=pages,
                    f=f,
                    form=dict(since=since, until=until, q=q),
                    here=f"/read?{query}",
                    tsv=f"/read.tsv?{query}",
                    libs=session.exec(select(Library).order_by(Library.name)).all(),
                    sorts=history.SORTS,
                    depths=history.DEPTHS,
                    sources=history.SOURCES,
                    first_shown=(page - 1) * history.PAGE_SIZE + 1,
                )
        finally:
            engine.dispose()

    @app.get("/read.tsv")
    def read_tsv(
        sort: str = "last",
        depth: Annotated[list[str] | None, Query()] = None,
        rating: str = "",
        since: str = "",
        until: str = "",
        library: str = "",
        notes: str = "",
        dismissed: str = "",
        q: str = "",
    ):
        """All rows matching the filters (not only the page) as TSV."""
        cfg, engine = open_session()
        try:
            with Session(engine) as session:
                f = history_filter(sort, depth, rating, since, until, library, notes, dismissed, q)
                text = history.to_tsv(history.entries(session, history.rows(session, f)))
        finally:
            engine.dispose()
        return Response(
            text,
            media_type="text/tab-separated-values",
            headers={"Content-Disposition": "attachment; filename=litresearch_read_papers.tsv"},
        )

    def _year(text: str) -> int | None:
        return int(text) if text.strip().isdigit() else None

    @app.get("/search", response_class=HTMLResponse)
    def search_page(
        request: Request,
        q: str = "",
        title: str = "",
        author: str = "",
        journal: str = "",
        year_from: str = "",
        year_to: str = "",
        oa: bool = False,
        where: str = "",
        sort: str = "relevance",
        page: int = 1,
    ):
        """Find papers in your library, Europe PMC or PubMed and put them on the queue."""
        cfg, engine = open_session()
        query = SearchQuery(
            keywords=q,
            title=title,
            author=author,
            journal=journal,
            year_from=_year(year_from),
            year_to=_year(year_to),
            open_access=oa,
        )
        where = where if where in ("library", "databases", *SOURCES) else "databases"
        enabled = enabled_sources(cfg)
        sort = sort if sort in SORTS else "relevance"
        try:
            with Session(engine) as session:
                local, found = [], None
                if not query.is_empty() and where == "library":
                    local = search_library(session, query)
                elif not query.is_empty():
                    sources = enabled if where == "databases" else [where]
                    http = make_http(cfg)
                    try:
                        found = literature_search(query, cfg, http, session, sources, sort=sort)
                    finally:
                        http.close()
                queued = set(session.exec(select(QueueItem.paper_id)).all())
                readings = {r.paper_id: r for r in session.exec(select(Reading)).all()}
                pages = max(1, -(-len(local) // LIBRARY_PAGE))
                page = min(max(page, 1), pages)
                # this search without its page number, for the pager
                params = [(k, v) for k, v in request.query_params.multi_items() if k != "page"]
                page_url = "/search?" + urlencode(params)
                return render(
                    request,
                    "search.html",
                    session,
                    cfg,
                    query=query,
                    where=where,
                    sort=sort,
                    sources=SOURCES,
                    sorts=SORTS,
                    # where to search: all enabled databases, each one, or offline
                    places=[("databases", "🌍 " + " + ".join(SOURCES[s] for s in enabled))]
                    + ([(s, SOURCES[s]) for s in enabled] if len(enabled) > 1 else [])
                    + [("library", "📁 My library")],
                    results=local[(page - 1) * LIBRARY_PAGE : page * LIBRARY_PAGE],
                    n_results=len(local),
                    page=page,
                    pages=pages,
                    page_url=page_url,
                    first_shown=(page - 1) * LIBRARY_PAGE + 1,
                    last_shown=min(page * LIBRARY_PAGE, len(local)),
                    found=found,
                    queued=queued,
                    readings=readings,
                    here=str(request.url.path)
                    + (f"?{request.url.query}" if request.url.query else ""),
                    queue_full=len(queued) >= cfg.queue.max_size,
                )
        finally:
            engine.dispose()

    def render_paper(request: Request, session: Session, cfg, paper: Paper, **note_view):
        """The paper page; ``note_view``: draft, preview or conflict of the note editor."""
        paper_id = paper.id
        note = note_view.pop("note", None) or note_files.refresh_paper(session, cfg, paper)
        reading = session.get(Reading, paper_id)
        draft = note_view.get("draft")
        if draft is None:
            draft = note.body or (note_files.template(cfg, session, paper) if reading else "")
        recs = session.exec(
            select(Recommendation, RecommendationBatch)
            .join(RecommendationBatch, RecommendationBatch.id == Recommendation.batch_id)
            .where(Recommendation.paper_id == paper_id)
            .order_by(RecommendationBatch.id.desc())
        ).all()
        xp_events = session.exec(
            select(XpEvent).where(XpEvent.paper_id == paper_id).order_by(XpEvent.at)
        ).all()
        item = session.get(QueueItem, paper_id)
        lists = examples.refresh(session, cfg)
        session.commit()
        return render(
            request,
            "paper.html",
            session,
            cfg,
            paper=paper,
            reading=reading,
            note=note,
            note_html=note_files.render_html(session, note.body),
            note_draft=draft,
            note_preview=note_view.get("preview"),
            note_conflict=note_view.get("conflict"),
            note_merged=note_view.get("merged"),
            recs=recs,
            xp_events=xp_events,
            queue_item=item,
            drop_cost=drop_would_cost(session, cfg, paper) if item else 0,
            papers_by_id=lambda pid: session.get(Paper, pid),
            example_role=examples.role(lists, paper_id),
            example_choices={
                c.kind: c.action for c in examples.choices(session) if c.paper_id == paper_id
            },
        )

    @app.get("/papers/{paper_id}", response_class=HTMLResponse)
    def paper_page(request: Request, paper_id: int):
        cfg, engine = open_session()
        try:
            with Session(engine) as session:
                paper = session.get(Paper, paper_id)
                if paper is None:
                    raise HTTPException(404, f"There is no paper with the number {paper_id}.")
                return render_paper(request, session, cfg, paper)
        finally:
            engine.dispose()

    def ledger_context(session: Session, kind: str, page: int) -> dict:
        page = max(page, 1)
        rows, total = ledger(session, kind or None, LEDGER_PAGE, (page - 1) * LEDGER_PAGE)
        pages = max(1, -(-total // LEDGER_PAGE))
        if page > pages:  # an old link past the end: show the last page
            page = pages
            rows, total = ledger(session, kind or None, LEDGER_PAGE, (page - 1) * LEDGER_PAGE)
        totals = session.exec(
            select(XpEvent.kind, func.count(), func.sum(XpEvent.points)).group_by(XpEvent.kind)
        ).all()
        earned, lost = session.exec(
            select(
                func.sum(case((XpEvent.points > 0, XpEvent.points), else_=0)),
                func.sum(case((XpEvent.points < 0, XpEvent.points), else_=0)),
            )
        ).one()
        return {
            "rows": rows,
            "total": total,
            "kind": kind,
            "page": page,
            "pages": pages,
            "totals": {k: [n, points] for k, n, points in sorted(totals, key=lambda t: -t[2])},
            "earned": earned or 0,
            "lost": lost or 0,
        }

    @app.get("/progress", response_class=HTMLResponse)
    def progress_page(request: Request, kind: str = "", page: int = 1):
        cfg, engine = open_session()
        try:
            with Session(engine) as session:
                today = date.today()
                rules = active_rules(session, cfg)
                s = stats(session, today, rules)
                summaries = weekly_summaries(session, rules, today, weeks=12)
                # stats() reads every reading event: compute it once per page
                badges = badge_progress(session, today, rules, s)
                return render(
                    request,
                    "progress.html",
                    session,
                    cfg,
                    stats=s,
                    level=level_for(s["xp"], rules),
                    badges=badges,
                    # badges with the largest share of their target reached
                    closest=sorted(
                        (b for b in badges if not b[2]), key=lambda b: -b[1] / b[0].target
                    )[:6],
                    bests=personal_bests(session, rules, today, s),
                    calendar=reading_calendar(session, today, weeks=26),
                    rif=reading_impact_factor(session, today),
                    weeks=list(reversed(summaries)),
                    multiplier=streak_multiplier(next_read_streak(session, today), rules),
                    hints=next_goals(session, rules, today, s),
                    **ledger_context(session, kind, page),
                )
        finally:
            engine.dispose()

    docs_cache = DocsCache()

    def docs_page(request: Request, slug: str | None):
        cfg, engine = open_session()
        try:
            with Session(engine) as session:
                docs = docs_cache.get()
                if docs is None:
                    return render(request, "docs.html", session, cfg, docs=None, chapter=None)
                chapter = docs.chapter(slug) if slug else None
                if slug and chapter is None:
                    return error("/docs", f"No chapter '{slug}' in the documentation")
                before, after = docs.neighbours(slug) if chapter else (None, docs.chapters[0])
                return render(
                    request,
                    "docs.html",
                    session,
                    cfg,
                    docs=docs,
                    chapter=chapter,
                    before=before,
                    after=after,
                )
        finally:
            engine.dispose()

    @app.get("/docs", response_class=HTMLResponse)
    def docs_index(request: Request):
        return docs_page(request, None)

    @app.get("/docs/{slug}", response_class=HTMLResponse)
    def docs_chapter(request: Request, slug: str):
        return docs_page(request, slug)

    # pages that were merged into others; old links and bookmarks keep working
    @app.get("/weekly")
    def weekly_page():
        return RedirectResponse("/#weekly", status_code=307)

    @app.get("/ledger")
    def ledger_page(kind: str = "", page: int = 1):
        query = urlencode({k: v for k, v in (("kind", kind), ("page", page)) if v and v != 1})
        return RedirectResponse(f"/progress{'?' + query if query else ''}#ledger", 307)

    @app.get("/how")
    def how_page():
        return RedirectResponse("/recommendations#how", status_code=307)

    @app.get("/ledger.tsv")
    def ledger_tsv():
        cfg, engine = open_session()
        try:
            with Session(engine) as session:
                rows, _ = ledger(session, limit=10**9)
                buffer = io.StringIO()
                writer = csv.writer(buffer, delimiter="\t", lineterminator="\n")
                writer.writerow(["timestamp", "points", "balance", "kind", "reason", "paper_doi"])
                for row in reversed(rows):
                    e = row["event"]
                    writer.writerow(
                        [
                            e.at.isoformat(),
                            e.points,
                            row["balance"],
                            e.kind,
                            e.reason,
                            (row["paper"].doi if row["paper"] else "") or "n/a",
                        ]
                    )
                return Response(
                    buffer.getvalue(),
                    media_type="text/tab-separated-values",
                    headers={
                        "Content-Disposition": "attachment; filename=litresearch_xp_ledger.tsv"
                    },
                )
        finally:
            engine.dispose()

    # --- libraries ---------------------------------------------------------------------

    EXPORT_REQUIREMENTS = "/docs/1-setup#what-an-export-must-contain"

    def library_index(session: Session, library: Library, q: str, folder: str, status: str):
        """Members of a library matching the filter, as light rows sorted by title.

        Only the columns the filter needs are loaded: full papers are read for
        the shown page only (``library_page_rows``), which matters for libraries
        with thousands of papers.
        """
        levels = {
            pid: depth(s) for pid, s in session.exec(select(Reading.paper_id, Reading.status))
        }
        members = session.exec(
            select(
                LibraryPaper.paper_id,
                LibraryPaper.folders,
                LibraryPaper.tags,
                LibraryPaper.citation_key,
                Paper.title,
                Paper.authors,
                Paper.doi,
                Paper.venue,
            )
            .join(Paper, Paper.id == LibraryPaper.paper_id)
            .where(LibraryPaper.library_id == library.id)
            .order_by(Paper.title)
        ).all()
        words = q.lower().split()
        folders: dict[str, int] = {}
        ids, n_read = [], 0
        for pid, entry_folders, tags, key, title, authors, doi, venue in members:
            labels = (entry_folders or []) + (tags or [])
            for name in labels:
                folders[name] = folders.get(name, 0) + 1
            level = levels.get(pid, 0)
            n_read += level >= 2
            if folder and not any(f == folder or f.startswith(folder + "/") for f in labels):
                continue
            if status == "unread" and level >= 2:
                continue
            if status == "read" and level < 2:
                continue
            if words:
                haystack = " ".join(
                    [title, " ".join(authors or []), key or "", doi or "", venue or ""]
                ).lower()
                if not all(w in haystack for w in words):
                    continue
            ids.append(pid)
        return {"ids": ids, "n_library": len(members), "n_read": n_read, "folders": folders}

    def library_page_rows(session: Session, library: Library, ids: list[int]) -> list[dict]:
        found = {
            paper.id: (paper, entry)
            for paper, entry in session.exec(
                select(Paper, LibraryPaper)
                .join(LibraryPaper, LibraryPaper.paper_id == Paper.id)
                .where(LibraryPaper.library_id == library.id, Paper.id.in_(ids))
            )
        }
        readings = {
            r.paper_id: r for r in session.exec(select(Reading).where(Reading.paper_id.in_(ids)))
        }
        queued = set(session.exec(select(QueueItem.paper_id)).all())
        return [
            {
                "paper": found[pid][0],
                "entry": found[pid][1],
                "reading": readings.get(pid),
                "queued": pid in queued,
            }
            for pid in ids
            if pid in found
        ]

    @app.get("/paperpile")
    def paperpile_moved():
        return RedirectResponse("/library", status_code=307)

    @app.get("/library", response_class=HTMLResponse)
    def libraries_page(request: Request):
        """All libraries with their size, status and source; the queue as a library."""
        if request.url.query:  # an old "Find & add" link (/library?q=...)
            return RedirectResponse(f"/search?{request.url.query}", status_code=307)
        cfg, engine = open_session()
        try:
            with Session(engine) as session:
                libraries.migrate(session, cfg)
                lists = examples.refresh(session, cfg)
                session.commit()
                queue = queue_items(session)
                return render(
                    request,
                    "libraries.html",
                    session,
                    cfg,
                    example_lists=lists,
                    example_rules=examples.RULES,
                    no_library_used=no_library_used(session),
                    summaries=libraries.summaries(session),
                    queue=queue,
                    formats=INPUT_FORMATS,
                    export_formats=EXPORT_FORMATS,
                    stale_days=STALE_LIBRARY_DAYS,
                )
        finally:
            engine.dispose()

    @app.get("/library/import", response_class=HTMLResponse)
    def import_page(request: Request, to: int | None = None):
        cfg, engine = open_session()
        try:
            with Session(engine) as session:
                libraries.migrate(session, cfg)
                return render_import(
                    request, session, cfg, {"target": "existing" if to else "new", "library_id": to}
                )
        finally:
            engine.dispose()

    def render_import(request, session, cfg, form: dict, check=None, upload_name=None):
        choices = [
            lib for lib in libraries.all_libraries(session) if lib.kind == libraries.IMPORTED
        ]
        return render(
            request,
            "library_import.html",
            session,
            cfg,
            choices=choices,
            form=form,
            check=check,
            upload_name=upload_name,
            field_rules=BIB_FIELDS,
            formats=INPUT_FORMATS,
            modes=IMPORT_MODES,
            requirements=EXPORT_REQUIREMENTS,
        )

    @app.post("/library/import", response_class=HTMLResponse)
    async def import_action(request: Request):
        """Check an uploaded export; import it into a new or an existing library."""
        form = await request.form()
        cfg = current_cfg()
        fields = {
            "target": str(form.get("target") or "new"),
            "name": str(form.get("name") or "").strip(),
            "description": str(form.get("description") or "").strip(),
            "library_id": int(form.get("library_id"))
            if str(form.get("library_id") or "").isdigit()
            else None,
            "mode": str(form.get("mode") or "add"),
        }
        upload = form.get("file")
        if not hasattr(upload, "read") or not upload.filename:
            return error("/library/import", "Choose an export file to upload")
        try:
            raw = await upload.read(MAX_UPLOAD + 1)
        finally:
            await upload.close()
        if len(raw) > MAX_UPLOAD:
            return error("/library/import", f"The file is larger than {MAX_UPLOAD // 2**20} MB")
        engine = init_db(cfg.paths.database)
        tmp_dir = tempfile.mkdtemp(prefix="litresearch-import-")
        # a name such as ".." would point at the folder itself
        name = Path(upload.filename).name.replace(" ", "_").strip(".") or "upload"
        tmp = Path(tmp_dir) / name
        try:
            tmp.write_bytes(raw)
            check = check_file(tmp, cfg.library.ignore_labels)
            with Session(engine) as session:
                libraries.migrate(session, cfg)
                try:
                    if fields["target"] == "new":
                        if not fields["name"]:
                            raise libraries.LibraryError("Give the new library a name")
                        libraries.check_name(session, fields["name"])
                    else:
                        library = session.get(Library, fields["library_id"] or -1)
                        if library is None or library.kind != libraries.IMPORTED:
                            raise libraries.LibraryError("Choose the library to import into")
                except libraries.LibraryError as exc:
                    check.findings.insert(0, Finding("error", str(exc)))
                if form.get("action") != "import" or not check.ok:
                    return render_import(
                        request, session, cfg, fields, check=check, upload_name=upload.filename
                    )
                if fields["target"] == "new":
                    library = libraries.create(session, fields["name"], fields["description"])
                    mode = "new"
                else:
                    mode = "replace" if fields["mode"] == "replace" else "add"
                # the upload is kept as the library's file when it is the complete library
                path = (
                    libraries.store_upload(cfg, library, raw, check.format)
                    if mode in ("new", "replace")
                    else tmp
                )
                http = make_http(cfg)
                try:
                    report = import_into(
                        cfg, session, http, library, path, mode, "upload", fmt=check.format
                    )
                except ValueError as exc:
                    session.rollback()
                    return error("/library/import", f"Not imported: {exc}")
                finally:
                    http.close()
                library_id = library.id
        finally:
            engine.dispose()
            shutil.rmtree(tmp_dir, ignore_errors=True)
        return back(f"/library/{library_id}", ingest_payload(report))

    def ingest_payload(report) -> dict:
        warnings = len(report.errors)
        return {
            "kind": "success" if not warnings else "warning",
            "events": [],
            "points": 0,
            "sparkle": True,
            "message": f"{report.library}: {report.entries} entries imported ({report.added} new"
            + (f", {report.removed} removed" if report.removed else "")
            + f"), {report.openalex_enriched} with metrics, {report.embeddings} with embeddings"
            + (f"; {warnings} warning(s), see the import history" if warnings else ""),
        }

    def export_response(text: str, name: str, fmt: str):
        media = {"bibtex": "application/x-bibtex", "ris": "application/x-research-info-systems"}
        file_name = export.file_name(name, fmt, date.today())
        return Response(
            text,
            media_type=f"{media[fmt]}; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename={file_name}"},
        )

    @app.get("/library/queue/export")
    def export_queue(format: str = "bibtex", status: bool = False):
        """The reading queue as a file: always exactly the papers on it now."""
        if format not in EXPORT_FORMATS:
            return error("/library", "Unknown export format")
        cfg, engine = open_session()
        try:
            with Session(engine) as session:
                papers = [paper for _, paper in queue_items(session)]
                text = export.render(export.paper_items(session, papers), format, status)
        finally:
            engine.dispose()
        return export_response(text, libraries.QUEUE_NAME, format)

    @app.get("/library/{library_id}", response_class=HTMLResponse)
    def library_page(
        request: Request,
        library_id: int,
        q: str = "",
        folder: str = "",
        status: str = "",
        page: int = 1,
    ):
        cfg, engine = open_session()
        try:
            with Session(engine) as session:
                libraries.migrate(session, cfg)
                library = session.get(Library, library_id)
                if library is None:
                    return error("/library", "That library does not exist")
                if library.kind in libraries.EXAMPLE_KINDS:
                    return examples_page(request, session, cfg, library)
                index = library_index(session, library, q, folder, status)
                ids = index["ids"]
                pages = max(1, -(-len(ids) // LIBRARY_PAGE))
                page = min(max(page, 1), pages)
                shown = ids[(page - 1) * LIBRARY_PAGE : page * LIBRARY_PAGE]
                runs = session.exec(
                    select(IngestRun)
                    .where(IngestRun.library_id == library.id)
                    .order_by(IngestRun.id.desc())
                    .limit(10)
                ).all()
                return render(
                    request,
                    "library.html",
                    session,
                    cfg,
                    library=library,
                    rows=library_page_rows(session, library, shown),
                    n_rows=len(ids),
                    n_library=index["n_library"],
                    n_read=index["n_read"],
                    page=page,
                    pages=pages,
                    q=q,
                    folder=folder,
                    status=status,
                    folders=sorted(index["folders"].items()),
                    runs=runs,
                    last_run=runs[0] if runs else None,
                    file_modified=libraries.source_modified(library),
                    stale_days=STALE_LIBRARY_DAYS,
                    formats=INPUT_FORMATS,
                    export_formats=EXPORT_FORMATS,
                    requirements=EXPORT_REQUIREMENTS,
                )
        finally:
            engine.dispose()

    @app.post("/library/{library_id}/edit")
    def library_edit(
        library_id: int,
        name: str = Form(""),
        description: str = Form(""),
        use_for_recommendations: bool = Form(False),
    ):
        cfg, engine = open_session()
        try:
            with Session(engine) as session:
                library = session.get(Library, library_id)
                if library is None:
                    return error("/library", "That library does not exist")
                if library.kind in libraries.EXAMPLE_KINDS:
                    use_for_recommendations = False
                try:
                    libraries.update(session, library, name, description, use_for_recommendations)
                    examples.refresh(session, cfg)
                except libraries.LibraryError as exc:
                    return error(f"/library/{library_id}", str(exc))
                session.commit()
                return back(
                    f"/library/{library_id}",
                    {"kind": "success", "message": "Library saved.", "events": [], "points": 0},
                )
        finally:
            engine.dispose()

    @app.post("/library/{library_id}/reimport")
    def library_reimport(library_id: int):
        """Update a library from its file (e.g. Paperpile's auto-export synced by rclone)."""
        cfg, engine = open_session()
        try:
            with Session(engine) as session:
                library = session.get(Library, library_id)
                if library is None or not library.source_path:
                    return error(
                        f"/library/{library_id}", "This library has no file to update from"
                    )
                path = Path(library.source_path)
                check = check_file(path, cfg.library.ignore_labels)
                if not check.ok:
                    return error(
                        f"/library/{library_id}", "Not imported: " + check.findings[0].message
                    )
                http = make_http(cfg)
                try:
                    report = import_into(cfg, session, http, library, path, "replace", "file")
                finally:
                    http.close()
        finally:
            engine.dispose()
        return back(f"/library/{library_id}", ingest_payload(report))

    @app.post("/library/{library_id}/delete")
    def library_delete(library_id: int, confirm_name: str = Form("")):
        cfg, engine = open_session()
        try:
            with Session(engine) as session:
                library = session.get(Library, library_id)
                if library is None:
                    return error("/library", "That library does not exist")
                if confirm_name.strip().lower() != library.name.lower():
                    return error(
                        f"/library/{library_id}", "Type the exact library name to delete it"
                    )
                name = library.name
                try:
                    count = libraries.delete(session, library)
                    examples.refresh(session, cfg)
                except libraries.LibraryError as exc:
                    return error(f"/library/{library_id}", str(exc))
                session.commit()
                return back(
                    "/library",
                    {
                        "kind": "info",
                        "message": f"Deleted the library {name} ({count} papers). Papers in "
                        "your other libraries, your queue and your reading history are kept.",
                        "events": [],
                        "points": 0,
                    },
                )
        finally:
            engine.dispose()

    @app.post("/library/{library_id}/remove/{paper_id}")
    def library_remove(library_id: int, paper_id: int):
        cfg, engine = open_session()
        try:
            with Session(engine) as session:
                library = session.get(Library, library_id)
                if library is not None and library.kind in libraries.EXAMPLE_KINDS:
                    return example_remove(session, cfg, library, paper_id)
                if library is None or not libraries.unlink(session, library, paper_id):
                    return error(f"/library/{library_id}", "Not in this library")
                session.commit()
                return back(
                    f"/library/{library_id}",
                    {
                        "kind": "info",
                        "message": "Removed from the library.",
                        "events": [],
                        "points": 0,
                    },
                )
        finally:
            engine.dispose()

    @app.get("/library/{library_id}/export")
    def library_export(library_id: int, format: str = "bibtex", status: bool = False):
        if format not in EXPORT_FORMATS:
            return error(f"/library/{library_id}", "Unknown export format")
        cfg, engine = open_session()
        try:
            with Session(engine) as session:
                library = session.get(Library, library_id)
                if library is None:
                    return error("/library", "That library does not exist")
                if library.kind in libraries.EXAMPLE_KINDS:
                    examples.refresh(session, cfg)
                    session.commit()
                text = export.render(export.library_items(session, library), format, status)
                name = library.name
        finally:
            engine.dispose()
        return export_response(text, name, format)

    @app.post("/library/use")
    async def library_use(request: Request):
        """Choose the libraries used for recommendations (all at once)."""
        form = await request.form()
        chosen = {int(v) for v in form.getlist("library") if str(v).isdigit()}
        next_url = safe(str(form.get("next_url") or "/library"))
        cfg, engine = open_session()
        try:
            with Session(engine) as session:
                used = libraries.set_used(session, chosen)
                examples.refresh(session, cfg)
                session.commit()
                if used:
                    payload = toast(
                        "Used for recommendations: " + ", ".join(lib.name for lib in used) + "."
                    )
                else:
                    payload = toast(
                        "No library is used for recommendations now: the similarity to your "
                        "library is 0 for every candidate and the library rules of the "
                        "positive examples find nothing.",
                        "warning",
                    )
                return back(next_url, payload)
        finally:
            engine.dispose()

    def toast(message: str, kind: str = "success") -> dict:
        return {"kind": kind, "message": message, "events": [], "points": 0}

    @app.get("/library/examples/{kind}")
    def example_library_link(kind: str):
        """Stable address of the positive or negative examples."""
        if kind not in libraries.EXAMPLE_KINDS:
            raise HTTPException(404, "There are only positive and negative examples.")
        cfg, engine = open_session()
        try:
            with Session(engine) as session:
                library = libraries.example_library(session, kind)
                session.commit()
                return RedirectResponse(f"/library/{library.id}", status_code=303)
        finally:
            engine.dispose()

    def examples_page(request: Request, session: Session, cfg, library: Library):
        """A library of example papers: members in order, your choices and the rules."""
        lists = examples.refresh(session, cfg)
        session.commit()
        current = lists[library.kind]
        papers = current.papers
        ids = [p.id for p in papers] + [p.id for p, _ in current.removed]
        readings = {
            r.paper_id: r for r in session.exec(select(Reading).where(Reading.paper_id.in_(ids)))
        }
        rules = examples.RULES[library.kind]
        order = current.rules + [r for r in rules if r not in current.rules]
        return render(
            request,
            "examples.html",
            session,
            cfg,
            library=library,
            current=current,
            other=lists[
                examples.NEGATIVE if library.kind == examples.POSITIVE else examples.POSITIVE
            ],
            readings=readings,
            rule_rows=[(name, *rules[name], name in current.rules) for name in order],
            library_rules=examples.LIBRARY_RULES,
            choosable=[
                lib
                for lib in libraries.all_libraries(session)
                if lib.kind not in libraries.EXAMPLE_KINDS
            ],
            no_library_used=no_library_used(session),
            export_formats=EXPORT_FORMATS,
        )

    def example_library_of(session: Session, library_id: int) -> Library:
        library = session.get(Library, library_id)
        if library is None or library.kind not in libraries.EXAMPLE_KINDS:
            raise HTTPException(404, "That list of examples does not exist.")
        return library

    def example_remove(session: Session, cfg, library: Library, paper_id: int):
        url = f"/library/{library.id}"
        if session.get(Paper, paper_id) is None:
            return error(url, "Unknown paper")
        if not examples.remove(session, cfg, paper_id, library.kind):
            return error(url, "Not in this list")
        examples.refresh(session, cfg)
        session.commit()
        return back(url, toast("Removed from the list. “Removed by you” can restore it.", "info"))

    @app.post("/library/{library_id}/restore/{paper_id}")
    def example_restore(library_id: int, paper_id: int):
        cfg, engine = open_session()
        try:
            with Session(engine) as session:
                library = example_library_of(session, library_id)
                if not examples.restore(session, paper_id, library.kind):
                    return error(f"/library/{library_id}", "You did not remove this paper")
                examples.refresh(session, cfg)
                session.commit()
                return back(
                    f"/library/{library_id}",
                    toast("Restored: the rules may choose this paper again.", "info"),
                )
        finally:
            engine.dispose()

    @app.post("/library/{library_id}/add")
    def example_add(library_id: int, ref: str = Form("")):
        """Add a paper by DOI, pmid:N, PMCn, citation key or title words."""
        cfg, engine = open_session()
        url = f"/library/{library_id}"
        try:
            with Session(engine) as session:
                library = example_library_of(session, library_id)
                try:
                    if not ref.strip():
                        raise ReadingError("Enter a DOI, pmid:N, PMCn or title words")
                    paper = find_paper(session, ref)
                    if paper is None:
                        http = make_http(cfg)
                        try:
                            paper = fetch_paper(session, cfg, http, ref)
                        finally:
                            http.close()
                except ReadingError as exc:
                    session.rollback()
                    return error(url, str(exc))
                examples.include(session, paper, library.kind)
                lists = examples.refresh(session, cfg)
                session.commit()
                message = f"Added: {paper.title}"
                kind = "success"
                if not paper.doi:
                    message += " (it has no DOI, so Semantic Scholar cannot use it)"
                    kind = "warning"
                elif paper.id not in lists[library.kind].origins:
                    message += " (not in the list: you added more papers than it holds)"
                    kind = "warning"
                return back(url, toast(message, kind))
        finally:
            engine.dispose()

    @app.post("/library/{library_id}/rules")
    async def example_rules(request: Request, library_id: int):
        """Save the rules and the size of a list in the profile's settings."""
        form = await request.form()
        url = f"/library/{library_id}"
        cfg, engine = open_session()
        try:
            with Session(engine) as session:
                library = example_library_of(session, library_id)
                kind = library.kind
                chosen = []
                for position, name in enumerate(examples.RULES[kind]):
                    if name not in form.getlist("rule"):
                        continue
                    raw = str(form.get(f"order_{name}") or "").strip()
                    try:
                        order = float(raw) if raw else 1000 + position
                    except ValueError:
                        return error(url, f"Not saved: the order of '{name}' must be a number")
                    chosen.append((order, position, name))
                rules = [name for *_, name in sorted(chosen)]
                raw_count = str(form.get("count") or "").strip()
                if not raw_count.isdigit():
                    return error(url, "Not saved: the number of examples must be a whole number")
                try:
                    cfg = save_profile_settings(
                        {
                            "selection": {
                                f"s2_{kind}_rules": rules,
                                f"s2_{kind}_count": int(raw_count),
                            }
                        }
                    )
                except (SettingsError, ProfilesError, ValueError) as exc:
                    return error(url, f"Not saved: {friendly_error(exc)}")
                lists = examples.refresh(session, cfg)
                session.commit()
                return back(
                    url,
                    toast(f"Saved. The list now has {len(lists[kind].papers)} papers."),
                )
        finally:
            engine.dispose()

    @app.post("/library/{library_id}/reset")
    def example_reset(library_id: int):
        cfg, engine = open_session()
        try:
            with Session(engine) as session:
                library = example_library_of(session, library_id)
                undone = examples.reset(session, library.kind)
                examples.refresh(session, cfg)
                session.commit()
                return back(
                    f"/library/{library_id}",
                    toast(
                        f"Undid {undone} change{'s' if undone != 1 else ''}: the list follows "
                        "the rules again.",
                        "info",
                    ),
                )
        finally:
            engine.dispose()

    # --- profile -------------------------------------------------------------------

    @app.get("/profile", response_class=HTMLResponse)
    def profile_page(request: Request):
        cfg, engine = open_session()
        try:
            with Session(engine) as session:
                base = base_config()
                slug = CURRENT_PROFILE.get()
                return render(
                    request,
                    "profile.html",
                    session,
                    cfg,
                    rule_groups=RULE_GROUPS,
                    folder=profiles.folder(base, slug),
                    others=[i for i in profiles.list_profiles(base) if i.slug != slug],
                )
        finally:
            engine.dispose()

    @app.post("/profile/save")
    def profile_save():
        cfg, engine = open_session()
        try:
            with Session(engine) as session:
                note_files.refresh_quietly(session, cfg)
                path = save_profile_file(session, cfg.backup.dir, settings=profile_settings(cfg))
            return back(
                "/profile",
                {
                    "kind": "success",
                    "events": [],
                    "points": 0,
                    "message": f"Profile saved as {path.name}",
                },
            )
        finally:
            engine.dispose()

    @app.get("/profile/download")
    def profile_download():
        cfg, engine = open_session()
        try:
            with Session(engine) as session:
                note_files.refresh_quietly(session, cfg)
                data = export_progress(session, settings=profile_settings(cfg))
            name = slug((data["profile"] or {}).get("name", "profile"))
            filename = f"litresearch_profile_{name}_{date.today().strftime('%Y%m%d')}.json"
            return Response(
                json.dumps(data, indent=1),
                media_type="application/json",
                headers={"Content-Disposition": f"attachment; filename={filename}"},
            )
        finally:
            engine.dispose()

    async def read_upload(request: Request, field_name: str = "file") -> list[dict]:
        form = await request.form()
        files = [f for f in form.getlist(field_name) if hasattr(f, "read")]
        result = []
        for upload in files:
            try:
                raw = await upload.read(MAX_UPLOAD + 1)
            finally:
                await upload.close()  # release the temporary file right away
            if len(raw) > MAX_UPLOAD:
                raise BackupError(f"{upload.filename} is larger than {MAX_UPLOAD // 2**20} MB")
            try:
                data = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise BackupError(f"{upload.filename} is not a JSON file") from exc
            if not isinstance(data, dict):
                raise BackupError(f"{upload.filename} is not a litresearch profile file")
            result.append({"name": upload.filename, "data": data})
        return result

    @app.get("/profile/compare", response_class=HTMLResponse)
    async def compare_page(request: Request):
        return await compare(request, [])

    @app.post("/profile/compare", response_class=HTMLResponse)
    async def compare_post(request: Request):
        form = await request.form()
        cfg = current_cfg()
        entries = []
        try:
            for name in form.getlist("saved"):
                path = next((f for f in saved_files(cfg.backup.dir) if f.name == name), None)
                if path:
                    entries.append({"name": name, "data": json.loads(path.read_text("utf-8"))})
            entries += await read_upload(request)
        except (BackupError, json.JSONDecodeError) as exc:
            return error("/profile/compare", str(exc))
        return await compare(request, entries)

    async def compare(request: Request, entries: list[dict]):
        cfg, engine = open_session()
        try:
            with Session(engine) as session:
                rows = [current_summary(session, cfg)]
                problems = []
                for entry in entries:
                    try:
                        rows.append(summarize(entry["data"], cfg, entry["name"]))
                    except BackupError as exc:
                        problems.append(f"{entry['name']}: {exc}")
                saves = [
                    (path.name, datetime.fromtimestamp(path.stat().st_mtime).replace(microsecond=0))
                    for path in sorted(
                        saved_files(cfg.backup.dir), key=lambda p: p.stat().st_mtime, reverse=True
                    )[:100]
                ]
                return render(
                    request, "compare.html", session, cfg, rows=rows, problems=problems, saves=saves
                )
        finally:
            engine.dispose()

    def current_summary(session: Session, cfg) -> dict:
        """The figures of ``summarize()`` for the open profile, read from its database.

        ``summarize()`` first loads an export into a throw-away database, which
        takes minutes for a profile with years of data.
        """
        return summarize_session(
            session,
            cfg,
            datetime.now().replace(microsecond=0),
            label="current",
            checksum_ok=True,
        )

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request):
        cfg, engine = open_session()
        try:
            with Session(engine) as session:
                return render(
                    request,
                    "settings.html",
                    session,
                    cfg,
                    groups=GROUPS,
                    config_path=config_path,
                    settings_file=profiles.folder(base_config(), CURRENT_PROFILE.get())
                    / profiles.PROFILE_SETTINGS,
                    last_backup=latest_backup(cfg),
                    notes_status=note_files.status(session, cfg),
                )
        finally:
            engine.dispose()

    @app.post("/backup")
    def backup_action():
        cfg, engine = open_session()
        try:
            with Session(engine) as session:
                try:
                    result = run_backup(cfg, session, settings=profile_settings(cfg))
                except BackupError as exc:
                    return error("/settings#data", f"Backup failed: {exc}")
                return back(
                    "/settings#data",
                    {
                        "kind": "success",
                        "message": f"Backup written: {result.snapshot.name}",
                        "events": [],
                        "points": 0,
                    },
                )
        finally:
            engine.dispose()

    @app.get("/progress.json")
    def progress_download():
        cfg, engine = open_session()
        try:
            with Session(engine) as session:
                note_files.refresh_quietly(session, cfg)
                data = export_progress(session, settings=profile_settings(cfg))
            name = f"litresearch_progress_{date.today().strftime('%Y%m%d')}.json"
            return Response(
                json.dumps(data, indent=1),
                media_type="application/json",
                headers={"Content-Disposition": f"attachment; filename={name}"},
            )
        finally:
            engine.dispose()

    @app.post("/settings")
    async def settings_save(request: Request):
        form = await request.form()
        data = {key: form.get(key) for key in form.keys()}
        for key in ("topic_name", "topic_weight", "topic_queries"):
            data[key] = form.getlist(key)
        try:
            save_profile_settings(parse_form(data))
        except (SettingsError, ProfilesError, ValueError) as exc:
            return error("/settings", f"Not saved: {friendly_error(exc)}")
        return back(
            "/settings",
            {
                "kind": "success",
                "message": "Settings saved; they apply to the next recommendations.",
                "events": [],
                "points": 0,
            },
        )

    # --- actions -----------------------------------------------------------------

    @app.post("/queue/add")
    def queue_add_action(
        ref: str = Form(""),
        title: str = Form(""),
        url: str = Form(""),
        source: str = Form(""),
        top: bool = Form(False),
        next_url: str = Form("/queue"),
    ):
        cfg, engine = open_session()
        try:
            with Session(engine) as session:
                try:
                    if title.strip():
                        paper = Paper(title=title.strip(), url=url.strip() or None)
                        session.add(paper)
                        session.flush()
                    elif ref.strip():
                        paper = find_paper(session, ref)
                        if paper is None:
                            http = make_http(cfg)
                            try:
                                paper = fetch_paper(session, cfg, http, ref)
                            finally:
                                http.close()
                    else:
                        raise ReadingError("Enter a DOI, a reference or a title")
                    origin = source_of(session, paper, ref, source or None)
                    queue_add(session, cfg, paper, origin, 1 if top else None)
                except ReadingError as exc:
                    session.rollback()
                    return error(safe(next_url), str(exc))
                return back(
                    safe(next_url),
                    {
                        "kind": "success",
                        "message": f"Added to your queue: {paper.title}",
                        "events": [],
                        "points": 0,
                        "sparkle": True,
                    },
                )
        finally:
            engine.dispose()

    @app.post("/queue/{paper_id}/move")
    def queue_move_action(paper_id: int, position: int = Form(...)):
        cfg, engine = open_session()
        try:
            with Session(engine) as session:
                paper = session.get(Paper, paper_id)
                try:
                    if paper is None:
                        raise ReadingError("Unknown paper")
                    queue_move(session, paper, max(position, 1))
                except ReadingError as exc:
                    return error("/queue", str(exc))
                return back("/queue")
        finally:
            engine.dispose()

    @app.post("/queue/{paper_id}/drop")
    def queue_drop_action(paper_id: int):
        cfg, engine = open_session()
        try:
            with Session(engine) as session:
                paper = session.get(Paper, paper_id)
                try:
                    if paper is None:
                        raise ReadingError("Unknown paper")
                    penalty = drop_from_queue(session, cfg, paper)
                except ReadingError as exc:
                    return error("/queue", str(exc))
                award = Award(events=[penalty]) if penalty else None
                payload = award_payload(
                    award,
                    f"Removed from the queue: {paper.title}",
                    session,
                    kind="warning" if penalty else "info",
                )
                return back("/queue", payload)
        finally:
            engine.dispose()

    @app.post("/papers/{paper_id}/read")
    def read_action(
        paper_id: int,
        level: str = Form(...),
        rating: str = Form(""),
        note: str = Form(""),
        note_base: str = Form(""),
        next_url: str = Form("/queue"),
    ):
        cfg, engine = open_session()
        try:
            with Session(engine) as session:
                paper = session.get(Paper, paper_id)
                try:
                    if paper is None:
                        raise ReadingError("Unknown paper")
                    status = ReadStatus(level)
                    if status == ReadStatus.dismissed:
                        raise ReadingError("Use 'not relevant' to dismiss")
                    if rating.strip() and not rating.strip().isdigit():
                        raise ReadingError("Rating must be between 1 and 5")
                    changed = False
                    if note.strip() and note_base:
                        # appending is safe: the entry goes below the file's current text
                        current = note_files.refresh_paper(session, cfg, paper, award=False)
                        changed = current.base != note_base
                    outcome = record_reading(
                        session,
                        cfg,
                        paper,
                        status,
                        int(rating) if rating.strip() else None,
                        note.strip() or None,
                    )
                except (ReadingError, ValueError) as exc:
                    return error(safe(next_url), str(exc))
                label = f"{STATUS_ICONS[status.value]} Marked as {status.value}: {paper.title}"
                if changed:
                    label += " · the note file had changed: your note was added below its text"
                return back(safe(next_url), award_payload(outcome.award, label, session))
        finally:
            engine.dispose()

    @app.post("/papers/{paper_id}/dismiss")
    def dismiss_action(paper_id: int, next_url: str = Form("/recommendations")):
        cfg, engine = open_session()
        try:
            with Session(engine) as session:
                paper = session.get(Paper, paper_id)
                if paper is None:
                    return error(safe(next_url), "Unknown paper")
                outcome = record_reading(session, cfg, paper, ReadStatus.dismissed)
                payload = award_payload(
                    outcome.award,
                    f"Marked as not relevant: {paper.title}",
                    session,
                    kind="warning" if outcome.award.points < 0 else "info",
                )
                return back(safe(next_url), payload)
        finally:
            engine.dispose()

    @app.post("/papers/{paper_id}/example")
    def example_action(paper_id: int, role: str = Form(...)):
        """Use a paper as positive or negative example, or as none."""
        url = f"/papers/{paper_id}"
        cfg, engine = open_session()
        try:
            with Session(engine) as session:
                paper = session.get(Paper, paper_id)
                if paper is None:
                    return error("/", "Unknown paper")
                if role in libraries.EXAMPLE_KINDS:
                    examples.include(session, paper, role)
                    message = f"Used as {role} example for Semantic Scholar's recommendations."
                elif role == "none":
                    examples.not_an_example(session, cfg, paper_id)
                    message = "Not used as an example any more."
                else:
                    return error(url, "Choose positive, negative or none")
                lists = examples.refresh(session, cfg)
                session.commit()
                kind = "success"
                if role in libraries.EXAMPLE_KINDS and not paper.doi:
                    message += " It has no DOI, so Semantic Scholar cannot use it."
                    kind = "warning"
                elif role in libraries.EXAMPLE_KINDS and paper_id not in lists[role].origins:
                    message += " It is not in the list: you added more papers than it holds."
                    kind = "warning"
                return back(url, toast(message, kind))
        finally:
            engine.dispose()

    @app.post("/papers/{paper_id}/note")
    def note_action(
        request: Request,
        paper_id: int,
        text: str = Form(""),
        base: str | None = Form(None),
        action: str = Form("save"),
    ):
        """Without ``base``: append a timestamped note. With it: the editor (save/preview)."""
        cfg, engine = open_session()
        url = f"/papers/{paper_id}"
        try:
            with Session(engine) as session:
                paper = session.get(Paper, paper_id)
                try:
                    if paper is None:
                        url = "/"  # the paper page would be a 404
                        raise ReadingError("Unknown paper")
                    if base is None:
                        if not text.strip():
                            raise ReadingError("The note is empty")
                        award = add_note(session, cfg, paper, text)
                    elif action == "preview":
                        preview = note_files.render_html(session, text)
                        return render_paper(
                            request, session, cfg, paper, draft=text, preview=preview
                        )
                    else:
                        award = note_files.save(session, cfg, paper, text, base)
                except note_files.NoteConflict as exc:
                    session.rollback()
                    note = note_files.refresh_paper(session, cfg, paper)
                    merged = note_files.merge(exc.theirs, exc.mine)
                    return render_paper(
                        request,
                        session,
                        cfg,
                        paper,
                        note=note,
                        draft=exc.mine,
                        conflict=exc,
                        merged=merged,
                    )
                except (ReadingError, note_files.NotesError) as exc:
                    return error(url, str(exc))
                return back(url, award_payload(award, "Note saved", session))
        finally:
            engine.dispose()

    @app.post("/notes/sync")
    def notes_sync_action():
        cfg, engine = open_session()
        try:
            with Session(engine) as session:
                report = note_files.sync(session, cfg)
            if report.problem:
                return error("/settings#notes", report.problem)
            counts = ", ".join(f"{k} {v}" for k, v in sorted(report.counts.items()))
            message = (
                f"Notes synced: {counts or 'nothing to do'} · conflicts {len(report.conflicts)}"
                f" · orphans {len(report.orphans)}"
            )
            kind = "warning" if report.conflicts or report.orphans or report.problems else "success"
            return back("/settings#notes", toast(message, kind))
        finally:
            engine.dispose()

    def safe(url: str) -> str:
        """Only allow local redirect targets."""
        return url if url.startswith("/") and not url.startswith("//") else "/"

    # ----- desktop mode -----
    if on_quit is not None:

        @app.get("/app/ping")
        def ping():
            """Lets a second start find this instance (see desktop.running_instance)."""
            return {"app": "litresearch", "pid": os.getpid(), "version": __version__}

        @app.post("/app/quit", response_class=HTMLResponse)
        def quit_app(request: Request, background: BackgroundTasks):
            """Stop the server; only from a litresearch page (Origin or Referer required)."""
            origin = request.headers.get("origin") or request.headers.get("referer")
            if not origin or urlsplit(origin).netloc != request.url.netloc:
                return PlainTextResponse("Cross-origin request rejected", status_code=403)
            background.add_task(on_quit)
            return templates.TemplateResponse(request, "stopped.html", {})

    app.state.celebrations = celebrations
    env.globals.update(Category=Category, depth=depth)
    return app


def make_http(cfg):
    # imported lazily so that tests can patch the CLI's factory
    from litresearch import cli

    return cli.make_http(cfg)
