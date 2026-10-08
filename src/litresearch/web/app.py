"""Local web UI (FastAPI + Jinja2, no external assets).

Single-user and meant for 127.0.0.1. POST requests from other origins are
rejected so that other websites cannot trigger actions in the background.
"""

import asyncio
import csv
import io
import json
import secrets
from collections import OrderedDict
from datetime import date
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from litresearch import __version__
from litresearch.backup import BackupError, export_progress, latest_backup, run_backup
from litresearch.config import load_config
from litresearch.db import (
    Category,
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
    BADGES,
    BASE_XP,
    KIND_LABELS,
    LEVELS,
    Award,
    badge_progress,
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
from litresearch.papers import upsert_work
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
from litresearch.recommend.engine import batch_items, build_recommendations, latest_batch
from litresearch.sources.http import HttpError
from litresearch.sources.openalex import OpenAlex
from litresearch.web.auth import (
    COOKIE,
    SESSION_DAYS,
    check_session,
    load_secret,
    make_session,
    verify_password,
)
from litresearch.web.settings import GROUPS, SettingsError, parse_form, save_settings

HERE = Path(__file__).parent
QUEUE_NUDGE_DAYS = 14
LEDGER_PAGE = 50

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
BADGE_ICONS = {
    "first_read": "🌱",
    "bookworm": "🐛",
    "centurion": "💯",
    "deep_diver": "🤿",
    "frog_eater": "🐸",
    "classicist": "🏛️",
    "early_bird": "🐦",
    "polymath": "🧭",
    "scribe": "✍️",
    "critic": "⭐",
    "streak_4": "🔥",
    "streak_12": "🏃",
    "streak_26": "🦸",
    "quest_master": "🗺️",
    "inbox_zero": "📭",
}
LEVEL_ICONS = ["🎓", "🧪", "📚", "☕", "🔬", "🧬", "👩‍🔬", "🎩", "🖋️", "🏆"]
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
        {"name": b.name, "description": b.description, "icon": BADGE_ICONS.get(b.code, "🏅")}
        for b in award.badges
    ]
    if award.level_before and award.level_after.number > award.level_before.number:
        payload["level_up"] = {
            "number": award.level_after.number,
            "title": award.level_after.title,
            "icon": LEVEL_ICONS[award.level_after.number - 1],
        }
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
        badge_icons=BADGE_ICONS,
        level_icons=LEVEL_ICONS,
        status_icons=STATUS_ICONS,
        base_xp=BASE_XP,
        levels=LEVELS,
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

    def open_session():
        cfg = load_config(config_path)
        engine = init_db(cfg.paths.database)
        return cfg, engine

    def secret() -> bytes:
        cfg = load_config(config_path)
        return load_secret(cfg.paths.database.parent / "web_secret")

    @app.middleware("http")
    async def require_login(request: Request, call_next):
        """With web.password_hash set, every page needs a valid session cookie."""
        password_hash = load_config(config_path).web.password_hash
        path = request.url.path
        if not password_hash or path == "/login" or path.startswith("/static/"):
            return await call_next(request)
        if check_session(request.cookies.get(COOKIE), secret(), password_hash):
            return await call_next(request)
        if request.method == "GET":
            target = path + (f"?{request.url.query}" if request.url.query else "")
            return RedirectResponse(f"/login?{urlencode({'next': target})}", status_code=303)
        return PlainTextResponse("Login required", status_code=401)

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
        s = stats(session, today)
        level = level_for(s["xp"])
        context.update(
            request=request,
            cfg=cfg,
            today=today,
            header={
                "xp": s["xp"],
                "level": level,
                "level_icon": LEVEL_ICONS[level.number - 1],
                "progress": level.progress(s["xp"]),
                "streak": s["streak"],
                "queue": len(queue_items(session)),
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

    # --- pages -------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        cfg, engine = open_session()
        try:
            with Session(engine) as session:
                today = date.today()
                items = queue_items(session)
                batch = latest_batch(session)
                recs = batch_items(session, batch) if batch else []
                progress = week_progress(session, cfg, today)
                recent, _ = ledger(session, limit=6)
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
                    hints=next_goals(session, cfg, today),
                    recent=recent,
                    waiting=queue_top_waiting_days(session, today),
                    nudge_days=QUEUE_NUDGE_DAYS,
                    stats=stats(session, today),
                    multiplier=streak_multiplier(max(stats(session, today)["streak"], 1)),
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
    def recommendations(request: Request, batch: int | None = None):
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
                    this_week_has_batch=latest_batch(session, date.today()) is not None,
                )
        finally:
            engine.dispose()

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

    @app.get("/progress", response_class=HTMLResponse)
    def progress_page(request: Request):
        cfg, engine = open_session()
        try:
            with Session(engine) as session:
                today = date.today()
                s = stats(session, today)
                summaries = weekly_summaries(session, cfg, today, weeks=12)
                return render(
                    request,
                    "progress.html",
                    session,
                    cfg,
                    stats=s,
                    level=level_for(s["xp"]),
                    badges=badge_progress(session, today),
                    bests=personal_bests(session, cfg, today),
                    calendar=reading_calendar(session, today, weeks=26),
                    rif=reading_impact_factor(session, today),
                    weeks=list(reversed(summaries)),
                    multiplier=streak_multiplier(max(s["streak"], 1)),
                    hints=next_goals(session, cfg, today),
                )
        finally:
            engine.dispose()

    @app.get("/weekly", response_class=HTMLResponse)
    def weekly_page(request: Request, weeks: int = 8):
        cfg, engine = open_session()
        try:
            with Session(engine) as session:
                summaries = weekly_summaries(session, cfg, date.today(), weeks=min(weeks, 104))
                return render(
                    request, "weekly.html", session, cfg, summaries=summaries, weeks=weeks
                )
        finally:
            engine.dispose()

    @app.get("/ledger", response_class=HTMLResponse)
    def ledger_page(request: Request, kind: str = "", page: int = 1):
        cfg, engine = open_session()
        try:
            with Session(engine) as session:
                page = max(page, 1)
                rows, total = ledger(session, kind or None, LEDGER_PAGE, (page - 1) * LEDGER_PAGE)
                all_rows, _ = ledger(session, limit=10**9)
                totals: dict[str, list[int]] = {}
                for row in all_rows:
                    entry = totals.setdefault(row["event"].kind, [0, 0])
                    entry[0] += 1
                    entry[1] += row["event"].points
                return render(
                    request,
                    "ledger.html",
                    session,
                    cfg,
                    rows=rows,
                    total=total,
                    kind=kind,
                    page=page,
                    pages=max(1, -(-total // LEDGER_PAGE)),
                    totals=dict(sorted(totals.items(), key=lambda kv: -kv[1][1])),
                    earned=sum(r["event"].points for r in all_rows if r["event"].points > 0),
                    lost=sum(r["event"].points for r in all_rows if r["event"].points < 0),
                )
        finally:
            engine.dispose()

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
                    result = run_backup(cfg, session)
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
                data = export_progress(session)
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
            save_settings(config_path, parse_form(data))
        except SettingsError as exc:
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

    @app.get("/how", response_class=HTMLResponse)
    def how_page(request: Request):
        cfg, engine = open_session()
        try:
            with Session(engine) as session:
                batch = latest_batch(session)
                return render(request, "how.html", session, cfg, groups=GROUPS, batch=batch)
        finally:
            engine.dispose()

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
    env.globals.update(Category=Category, depth=depth, badges=BADGES)
    return app


def make_http(cfg):
    # imported lazily so that tests can patch the CLI's factory
    from litresearch import cli

    return cli.make_http(cfg)
