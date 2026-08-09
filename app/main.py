"""FastAPI app.

Server-rendered on purpose: the page arrives with its numbers already in it, so
there is no loading skeleton and no layout jump, and the range switcher is a
plain link rather than a refetch. The only client-side JavaScript draws the
charts and handles tooltips.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
from typing import Any, Optional

try:  # optional: the app runs fine without a .env in mock mode
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import constants as C
from . import db, fixtures, ranges, view

BASE_DIR = pathlib.Path(__file__).resolve().parent
LIVE_DATA = os.environ.get("LIVE_DATA", "0").strip() in {"1", "true", "yes"}

app = FastAPI(title="Noble Key Supply — paid media dashboard")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# --- formatting ------------------------------------------------------------
# Formatting lives here rather than in the template so "—" is impossible to
# forget: a None never renders as 0, an empty string, or "NaN".

DASH = "—"


def money(value: Optional[float], places: int = 0) -> str:
    if value is None:
        return DASH
    return "${:,.{p}f}".format(value, p=places)


def money2(value: Optional[float]) -> str:
    return money(value, 2)


def number(value: Optional[float], places: int = 0) -> str:
    if value is None:
        return DASH
    return "{:,.{p}f}".format(value, p=places)


def compact(value: Optional[float]) -> str:
    if value is None:
        return DASH
    magnitude = abs(value)
    if magnitude >= 1_000_000:
        return "{:.1f}M".format(value / 1_000_000)
    if magnitude >= 10_000:
        return "{:.1f}K".format(value / 1_000)
    return "{:,.0f}".format(value)


def percent(value: Optional[float], places: int = 1) -> str:
    if value is None:
        return DASH
    return "{:.{p}f}%".format(value * 100, p=places)


def signed_percent(value: Optional[float], places: int = 0) -> str:
    if value is None:
        return DASH
    return "{:+.{p}f}%".format(value * 100, p=places)


def ratio(value: Optional[float], places: int = 2) -> str:
    if value is None:
        return DASH
    return "{:.{p}f}x".format(value, p=places)


def pretty_date(value: Optional[str]) -> str:
    if not value:
        return DASH
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%b %-d, %-I:%M %p")
    except ValueError:
        return value


templates.env.filters.update(
    money=money,
    money2=money2,
    number=number,
    compact=compact,
    percent=percent,
    signed_percent=signed_percent,
    ratio=ratio,
    pretty_date=pretty_date,
)


def _connection():
    conn = db.connect()
    db.init(conn)
    return conn


@app.on_event("startup")
def _startup() -> None:
    conn = _connection()
    try:
        has_rows = conn.execute("SELECT 1 FROM daily_metrics LIMIT 1").fetchone()
        if not has_rows and not LIVE_DATA:
            fixtures.load(conn)
        db.set_setting(conn, "data_mode", "live" if LIVE_DATA else "mock")
    finally:
        conn.close()


@app.get("/healthz")
def healthz() -> JSONResponse:
    return JSONResponse({"ok": True})


@app.get("/api/view")
def api_view(range: str = Query(default=ranges.DEFAULT_RANGE)) -> JSONResponse:
    conn = _connection()
    try:
        return JSONResponse(view.build(conn, range))
    finally:
        conn.close()


@app.get("/")
def dashboard(request: Request, range: str = Query(default=ranges.DEFAULT_RANGE)) -> Any:
    conn = _connection()
    try:
        model = view.build(conn, range)
    finally:
        conn.close()
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "v": model,
            "view_json": json.dumps(model, default=str),
            "constants": C,
            # The static export supplies its own versions of these two, which
            # is the whole difference between a served page and an exported one.
            "asset": lambda name: "/static/" + name,
            "range_href": lambda key: "/?range=" + key,
        },
    )
