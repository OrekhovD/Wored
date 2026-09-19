"""Isolated browser fixture for the trading-day interaction prototype."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from webui.paper_api import router

ROOT = Path(__file__).resolve().parents[1]
WEBUI = ROOT / "webui"
templates = Jinja2Templates(directory=str(WEBUI / "templates"))
templates.env.autoescape = True

app = FastAPI(title="WORED Trading Day Browser Fixture")
app.add_middleware(SessionMiddleware, secret_key="paper-browser-fixture-secret")
app.mount("/static", StaticFiles(directory=str(WEBUI / "static")), name="static")
app.include_router(router)


@app.get("/trading-day", response_class=HTMLResponse)
async def trading_day(request: Request):
    return templates.TemplateResponse(request, "trading_day.html", {
        "page_title": "Сегодня",
        "current_path": "/trading-day",
        "csrf_token": "fixture-csrf",
        "auth_enabled": False,
        "authenticated": True,
        "username": "fixture",
        "health": {"redis": True, "postgres": True, "collector_feed": True},
    })


@app.get("/__qa__/health")
async def health():
    return {"ok": True, "fixture": "trading-day"}
