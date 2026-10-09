"""Local web UI (FastAPI + Jinja2, no external assets).

Single-user and meant for 127.0.0.1. POST requests from other origins are
rejected so that other websites cannot trigger actions in the background.
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
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from litresearch import __version__, profiles
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
)
from litresearch.config import load_config
from litresearch.db import (
    Category,
    IngestRun,
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
from litresearch.game import (
    KIND_LABELS,
    Award,
    badge_progress,
    header_stats,
    ledger,
    level_for,
    next_goals,
    personal_bests,
    queue_top_waiting_days,
    reading_calendar,
    reading_impact_factor,
    stats,
    streak_multiplier,
    week_progress,
    weekly_summaries,
)
from litresearch.ingest.bibtex import normalize_doi
from litresearch.ingest.check import FIELDS as BIB_FIELDS
from litresearch.ingest.check import INSTRUCTIONS as BIB_INSTRUCTIONS
from litresearch.ingest.check import check_bibtex
from litresearch.papers import upsert_work
from litresearch.profile import ProfileError, active_rules, get_profile
from litresearch.profiles import ProfilesError
from litresearch.reading import (
    ReadingError,
    add_note,
    drop_from_queue,
    drop_would_cost,
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
)
from litresearch.sources.http import HttpError
from litresearch.sources.openalex import OpenAlex
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
from litresearch.web.settings import (
    GROUPS,
    RULE_GROUPS,
    SettingsError,
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
        payload["message"] += (
            " · Your game has started with your configured rules; they are now locked."
        )
    payload["quest"] = any(e.kind == "quest" for e in award.events)
    payload["penalty"] = any(e.points < 0 for e in award.events)
    return payload


def create_app(config_path: Path) -> FastAPI:
    config_path = Path(config_path)
    app = FastAPI(title="litresearch", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    celebrations = Celebrations()

    env = templates.env
    env.globals.update(
        version=__version__,
        kind_icons=KIND_ICONS,
        kind_labels=KIND_LABELS,
        status_icons=STATUS_ICONS,
    )
    env.filters["authors"] = lambda a: (
        "unknown authors" if not a else a[0].split(",")[0] + (" et al." if len(a) > 1 else "")
    )
    env.filters["pct"] = lambda v: f"{v * 100:.0f}%"

    def paper_link(paper: Paper) -> str:
        if paper.doi:
            return f"https://doi.org/{paper.doi}"
        if paper.pmid:
            return f"https://pubmed.ncbi.nlm.nih.gov/{paper.pmid}"
        return paper.url or ""

    env.globals["paper_link"] = paper_link

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
        open_paths = path in ("/login", "/logout") or path.startswith("/static/")
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
    async def same_origin_only(request: Request, call_next):
        if request.method == "POST":
            origin = request.headers.get("origin") or request.headers.get("referer")
            if origin and urlsplit(origin).netloc != request.url.netloc:
                return PlainTextResponse("Cross-origin request rejected", status_code=403)
        return await call_next(request)

    def render(request: Request, name: str, session: Session, cfg, **context):
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

    # --- login -------------------------------------------------------------------

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request, next: str = "/", failed: bool = False):
        return templates.TemplateResponse(
            request, "login.html", {"next": safe(next), "failed": failed}
        )

    @app.post("/login")
    async def login(password: str = Form(...), next: str = Form("/")):
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
            return error("/welcome", f"Not created: {exc}")
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
            return error("/welcome", f"Not loaded: {exc}")
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
    def dashboard(request: Request, weeks: int = 4):
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
                    hints=next_goals(session, rules, today),
                    recent=recent,
                    waiting=queue_top_waiting_days(session, today),
                    nudge_days=QUEUE_NUDGE_DAYS,
                    stats=figures,
                    multiplier=streak_multiplier(max(figures["streak"], 1), rules),
                    summaries=weekly_summaries(
                        session, rules, today, weeks=min(max(weeks, 1), 104)
                    ),
                    weeks=weeks,
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
                return render(
                    request,
                    "recommendations.html",
                    session,
                    cfg,
                    batch=current,
                    batches=batches,
                    recs=recs,
                    rec_status=recommendation_status(session, recs),
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
            return error("/recommendations?topics=1#topics", f"Not saved: {exc}")
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

    @app.get("/library", response_class=HTMLResponse)
    def library(request: Request, q: str = ""):
        cfg, engine = open_session()
        try:
            with Session(engine) as session:
                results = []
                if q.strip():
                    words = q.lower().split()
                    for paper in session.exec(select(Paper)).all():
                        haystack = " ".join(
                            [
                                paper.title,
                                " ".join(paper.authors),
                                paper.bibtex_key or "",
                                paper.doi or "",
                                " ".join(paper.folders),
                            ]
                        ).lower()
                        if all(w in haystack for w in words):
                            results.append(paper)
                queued = set(session.exec(select(QueueItem.paper_id)).all())
                readings = {r.paper_id: r for r in session.exec(select(Reading)).all()}
                return render(
                    request,
                    "library.html",
                    session,
                    cfg,
                    q=q,
                    results=results[:100],
                    n_results=len(results),
                    queued=queued,
                    readings=readings,
                )
        finally:
            engine.dispose()

    @app.get("/papers/{paper_id}", response_class=HTMLResponse)
    def paper_page(request: Request, paper_id: int):
        cfg, engine = open_session()
        try:
            with Session(engine) as session:
                paper = session.get(Paper, paper_id)
                if paper is None:
                    return HTMLResponse("Paper not found", status_code=404)
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
                return render(
                    request,
                    "paper.html",
                    session,
                    cfg,
                    paper=paper,
                    reading=session.get(Reading, paper_id),
                    recs=recs,
                    xp_events=xp_events,
                    queue_item=item,
                    drop_cost=drop_would_cost(session, cfg, paper) if item else 0,
                    papers_by_id=lambda pid: session.get(Paper, pid),
                )
        finally:
            engine.dispose()

    def ledger_context(session: Session, kind: str, page: int) -> dict:
        page = max(page, 1)
        rows, total = ledger(session, kind or None, LEDGER_PAGE, (page - 1) * LEDGER_PAGE)
        all_rows, _ = ledger(session, limit=10**9)
        totals: dict[str, list[int]] = {}
        for row in all_rows:
            entry = totals.setdefault(row["event"].kind, [0, 0])
            entry[0] += 1
            entry[1] += row["event"].points
        return {
            "rows": rows,
            "total": total,
            "kind": kind,
            "page": page,
            "pages": max(1, -(-total // LEDGER_PAGE)),
            "totals": dict(sorted(totals.items(), key=lambda kv: -kv[1][1])),
            "earned": sum(r["event"].points for r in all_rows if r["event"].points > 0),
            "lost": sum(r["event"].points for r in all_rows if r["event"].points < 0),
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
                badges = badge_progress(session, today, rules)
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
                    bests=personal_bests(session, rules, today),
                    calendar=reading_calendar(session, today, weeks=26),
                    rif=reading_impact_factor(session, today),
                    weeks=list(reversed(summaries)),
                    multiplier=streak_multiplier(max(s["streak"], 1), rules),
                    hints=next_goals(session, rules, today),
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
    def weekly_page(weeks: int = 8):
        return RedirectResponse(f"/?weeks={min(max(weeks, 1), 104)}#weekly", status_code=307)

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

    # --- Paperpile library ------------------------------------------------------------

    def library_rows(session: Session, q: str, folder: str, status: str):
        papers = session.exec(select(Paper).where(Paper.in_library).order_by(Paper.title)).all()
        readings = {r.paper_id: r for r in session.exec(select(Reading)).all()}
        queued = set(session.exec(select(QueueItem.paper_id)).all())
        rows = []
        words = q.lower().split()
        for paper in papers:
            reading = readings.get(paper.id)
            level = depth(reading.status) if reading else 0
            if folder and not any(f == folder or f.startswith(folder + "/") for f in paper.folders):
                continue
            if status == "unread" and level >= 2:
                continue
            if status == "read" and level < 2:
                continue
            if words:
                haystack = " ".join(
                    [
                        paper.title,
                        " ".join(paper.authors),
                        paper.bibtex_key or "",
                        paper.doi or "",
                        paper.venue or "",
                    ]
                ).lower()
                if not all(w in haystack for w in words):
                    continue
            rows.append({"paper": paper, "reading": reading, "queued": paper.id in queued})
        return rows

    def paperpile_context(session: Session, cfg, q="", folder="", status="", page=1):
        papers = session.exec(select(Paper).where(Paper.in_library)).all()
        folders: dict[str, int] = {}
        for paper in papers:
            for name in paper.folders:
                folders[name] = folders.get(name, 0) + 1
        rows = library_rows(session, q, folder, status)
        page = max(page, 1)
        bib = cfg.paths.bibtex
        modified = (
            datetime.fromtimestamp(bib.stat().st_mtime).replace(microsecond=0)
            if bib.exists()
            else None
        )
        runs = session.exec(select(IngestRun).order_by(IngestRun.id.desc()).limit(10)).all()
        return {
            "rows": rows[(page - 1) * LIBRARY_PAGE : page * LIBRARY_PAGE],
            "n_rows": len(rows),
            "page": page,
            "pages": max(1, -(-len(rows) // LIBRARY_PAGE)),
            "q": q,
            "folder": folder,
            "status": status,
            "folders": sorted(folders.items()),
            "n_library": len(papers),
            "n_read": sum(1 for r in library_rows(session, "", "", "read")),
            "bib_path": bib,
            "bib_modified": modified,
            "runs": runs,
            "last_run": runs[0] if runs else None,
            "instructions": BIB_INSTRUCTIONS,
            "field_rules": BIB_FIELDS,
            "stale_days": STALE_LIBRARY_DAYS,
        }

    @app.get("/paperpile", response_class=HTMLResponse)
    def paperpile_page(
        request: Request, q: str = "", folder: str = "", status: str = "", page: int = 1
    ):
        cfg, engine = open_session()
        try:
            with Session(engine) as session:
                return render(
                    request,
                    "paperpile.html",
                    session,
                    cfg,
                    **paperpile_context(session, cfg, q, folder, status, page),
                )
        finally:
            engine.dispose()

    def run_ingest(cfg, source: str):
        from litresearch.ingest.pipeline import ingest_library

        engine = init_db(cfg.paths.database)
        http = make_http(cfg)
        try:
            with Session(engine) as session:
                return ingest_library(cfg, session, http, source=source)
        finally:
            http.close()
            engine.dispose()

    def ingest_payload(report) -> dict:
        warnings = len(report.errors)
        return {
            "kind": "success" if not warnings else "warning",
            "events": [],
            "points": 0,
            "sparkle": True,
            "message": f"Library updated: {report.entries} entries ({report.added} new, "
            f"{report.removed} removed), {report.openalex_enriched} with metrics, "
            f"{report.embeddings} with embeddings"
            + (f"; {warnings} warning(s), see the import history" if warnings else ""),
        }

    @app.post("/paperpile/reimport")
    def paperpile_reimport():
        cfg = current_cfg()
        if not cfg.paths.bibtex.exists():
            return error("/paperpile", f"No BibTeX file at {cfg.paths.bibtex}; upload one.")
        check = check_bibtex(cfg.paths.bibtex, cfg.library.ignore_labels)
        if not check.ok:
            return error("/paperpile", "Not imported: " + check.findings[0].message)
        return back("/paperpile", ingest_payload(run_ingest(cfg, "file")))

    @app.post("/paperpile/upload", response_class=HTMLResponse)
    async def paperpile_upload(request: Request):
        form = await request.form()
        cfg = current_cfg()
        upload = form.get("file")
        if not hasattr(upload, "read") or not upload.filename:
            return error("/paperpile", "Choose a .bib file to upload")
        try:
            raw = await upload.read()
        finally:
            await upload.close()
        if len(raw) > MAX_UPLOAD:
            return error("/paperpile", f"The file is larger than {MAX_UPLOAD // 2**20} MB")
        target = cfg.paths.bibtex
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=".upload.", suffix=".bib")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(raw)
            check = check_bibtex(tmp, cfg.library.ignore_labels)
            if form.get("action") != "import" or not check.ok:
                engine = init_db(cfg.paths.database)
                try:
                    with Session(engine) as session:
                        return render(
                            request,
                            "paperpile.html",
                            session,
                            cfg,
                            upload_check=check,
                            upload_name=upload.filename,
                            **paperpile_context(session, cfg),
                        )
                finally:
                    engine.dispose()
            if target.exists():
                shutil.copy2(target, target.with_name(target.name + ".bak"))
            os.replace(tmp, target)
        finally:
            tmp.unlink(missing_ok=True)
        return back("/paperpile", ingest_payload(run_ingest(cfg, "upload")))

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
                raw = await upload.read()
            finally:
                await upload.close()  # release the temporary file right away
            if len(raw) > MAX_UPLOAD:
                raise BackupError(f"{upload.filename} is larger than {MAX_UPLOAD // 2**20} MB")
            try:
                result.append({"name": upload.filename, "data": json.loads(raw)})
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise BackupError(f"{upload.filename} is not a JSON file") from exc
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
                rows = [summarize(export_progress(session), cfg, "current")]
                problems = []
                for entry in entries:
                    try:
                        rows.append(summarize(entry["data"], cfg, entry["name"]))
                    except BackupError as exc:
                        problems.append(f"{entry['name']}: {exc}")
                saves = [path.name for path in saved_files(cfg.backup.dir)][:30]
                return render(
                    request, "compare.html", session, cfg, rows=rows, problems=problems, saves=saves
                )
        finally:
            engine.dispose()

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
            return error("/settings", f"Not saved: {exc}")
        return back(
            "/settings",
            {
                "kind": "success",
                "message": "Settings saved. They apply to the next recommendation batch.",
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
                            paper = fetch_by_doi(cfg, session, normalize_doi(ref))
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

    def fetch_by_doi(cfg, session: Session, doi: str | None) -> Paper:
        if not doi:
            raise ReadingError("Not found. Enter a DOI, r1–r5, a citation key or title words.")
        http = make_http(cfg)
        try:
            works = OpenAlex(http, cfg.api.openalex_key, cfg.api.contact_email).works_by_doi([doi])
        except HttpError as exc:
            raise ReadingError(f"Could not fetch {doi}: {exc}") from exc
        finally:
            http.close()
        if doi not in works:
            raise ReadingError(f"OpenAlex does not know {doi}; add it by title instead")
        return upsert_work(session, works[doi])

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

    @app.post("/papers/{paper_id}/note")
    def note_action(paper_id: int, text: str = Form(...)):
        cfg, engine = open_session()
        url = f"/papers/{paper_id}"
        try:
            with Session(engine) as session:
                paper = session.get(Paper, paper_id)
                try:
                    if paper is None:
                        raise ReadingError("Unknown paper")
                    if not text.strip():
                        raise ReadingError("The note is empty")
                    award = add_note(session, cfg, paper, text)
                except ReadingError as exc:
                    return error(url, str(exc))
                return back(url, award_payload(award, "Note saved", session))
        finally:
            engine.dispose()

    def safe(url: str) -> str:
        """Only allow local redirect targets."""
        return url if url.startswith("/") and not url.startswith("//") else "/"

    app.state.celebrations = celebrations
    env.globals.update(Category=Category, depth=depth)
    return app


def make_http(cfg):
    # imported lazily so that tests can patch the CLI's factory
    from litresearch import cli

    return cli.make_http(cfg)
