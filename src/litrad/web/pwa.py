"""Installable phone app: web app manifest, service worker and offline page.

These routes (and the icons in /static) work without login and without a
chosen profile: the browser fetches them before anyone signs in. They contain
no personal data.
"""

import json

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from litrad import __version__

OPEN_PATHS = frozenset({"/manifest.webmanifest", "/sw.js", "/offline"})
THEME_COLOUR = "#f4f5fb"  # --bg of app.css (light)

MANIFEST = {
    "name": "litrad · The Literature Radar Game",
    "short_name": "litrad",
    "description": "The Literature Radar Game: paper recommendations, a reading queue and XP.",
    "id": "/",
    "start_url": "/",
    "scope": "/",
    "display": "standalone",
    "theme_color": THEME_COLOUR,
    "background_color": THEME_COLOUR,
    "icons": [
        {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
        {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"},
        {
            "src": "/static/icon-maskable-512.png",
            "sizes": "512x512",
            "type": "image/png",
            "purpose": "maskable",
        },
    ],
}


def register(app: FastAPI, templates: Jinja2Templates, directory) -> None:
    """Add /manifest.webmanifest, /sw.js and /offline to ``app``."""
    worker = (directory / "sw.js").read_text(encoding="utf-8").replace("__VERSION__", __version__)

    @app.get("/manifest.webmanifest")
    def manifest():
        return Response(json.dumps(MANIFEST), media_type="application/manifest+json")

    @app.get("/sw.js")
    def service_worker():
        return Response(
            worker,
            media_type="text/javascript",
            headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
        )

    @app.get("/offline", response_class=HTMLResponse)
    def offline(request: Request):
        return templates.TemplateResponse(request, "offline.html", {})
