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
import time
from typing import Any, Optional

try:  # optional: the app runs fine without a .env in mock mode
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

import asyncio
import base64
import secrets
import threading

from fastapi import FastAPI, Query, Request
from fastapi.responses import Response
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import constants as C
from . import db, fixtures, ranges, view

BASE_DIR = pathlib.Path(__file__).resolve().parent
LIVE_DATA = os.environ.get("LIVE_DATA", "0").strip() in {"1", "true", "yes"}

DASHBOARD_USER = os.environ.get("DASHBOARD_USER", "").strip()
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "").strip()


class BasicAuth(BaseHTTPMiddleware):
    """Password-gate the whole dashboard when credentials are configured.

    The page carries the client's spend, revenue and margins, so it must not sit
    on an open URL. Locally no credentials are set and this does nothing, which
    keeps development friction-free; in deployment both are set and every route
    requires them. Comparison is constant-time so the password cannot be guessed
    by timing.
    """

    async def dispatch(self, request, call_next):
        if not (DASHBOARD_USER and DASHBOARD_PASSWORD):
            return await call_next(request)
        if request.url.path == "/healthz":
            return await call_next(request)

        header = request.headers.get("authorization", "")
        if header.startswith("Basic "):
            try:
                decoded = base64.b64decode(header[6:]).decode("utf-8")
                user, _, password = decoded.partition(":")
                if (secrets.compare_digest(user, DASHBOARD_USER)
                        and secrets.compare_digest(password, DASHBOARD_PASSWORD)):
                    return await call_next(request)
            except Exception:
                pass
        return Response(
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="Noble Key Supply dashboard"'},
        )


app = FastAPI(title="Noble Key Supply — paid media dashboard")
app.add_middleware(BasicAuth)
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
        mode = db.get_setting(conn, "data_mode", "mock")
        real = conn.execute(
            "SELECT 1 FROM organic_daily WHERE provider IS NOT 'fixture' LIMIT 1"
        ).fetchone() or conn.execute("SELECT 1 FROM daily_metrics LIMIT 1").fetchone()

        # Never seed fixtures over a database that has been synced for real.
        # LIVE_DATA being unset is a config oversight, not an instruction to
        # overwrite the client's data with invented rows.
        if mode != "live" and not real and not LIVE_DATA:
            fixtures.load(conn)
            db.set_setting(conn, "data_mode", "mock")
        elif LIVE_DATA or mode == "live":
            db.set_setting(conn, "data_mode", "live")
    finally:
        conn.close()


def _background_sync() -> None:
    """Keep the data fresh from inside the container.

    On a server this replaces the laptop scheduler entirely, which is the point:
    the client's link stays current whether or not anyone's Mac is awake.
    """
    from . import sync as sync_module

    hours = float(os.environ.get("SYNC_INTERVAL_HOURS", "6") or 6)
    while True:
        try:
            sync_module.run(days=28, only=[], backfill=False)
        except Exception as exc:  # noqa: BLE001 - never kill the web server
            print("scheduled sync failed: %s" % exc, flush=True)
        time.sleep(hours * 3600)


@app.on_event("startup")
def _start_scheduler() -> None:
    if os.environ.get("ENABLE_BACKGROUND_SYNC", "").strip() in {"1", "true", "yes"}:
        threading.Thread(target=_background_sync, daemon=True).start()
        print("background sync enabled", flush=True)


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
