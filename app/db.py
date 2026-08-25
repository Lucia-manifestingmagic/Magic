"""SQLite storage.

Shape of the thing:

    raw_snapshots   every API response, stored verbatim before parsing, so any
                    metric can be re-derived later without re-hitting the APIs
                    (and without the vendor's retention limits mattering)
          |
          v
    daily_metrics   one normalized row per (date, channel, level, ids). The UI
                    reads only from here and never sees a platform-shaped
                    payload, which is what makes adding a third channel a new
                    connector file rather than a rewrite.

    bio_link_daily  link-in-bio traffic. Deliberately NOT a row in daily_metrics:
                    it has no spend, so folding it into the paid tables would
                    silently drag blended CAC toward zero.

    reach_periods   reach is NOT additive across days, so it lives in its own
                    table keyed by the exact window it was fetched for.
    creatives       ad names, thumbnails, permalinks
    sync_runs       what ran, when, and what broke — surfaced in the UI footer
"""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import sqlite3
from typing import Any, Dict, Iterable, List, Optional, Sequence

DB_PATH = os.environ.get("DB_PATH", "data/dashboard.db")

LEVELS = ("account", "campaign", "adset", "ad")

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    channel       TEXT NOT NULL,
    endpoint      TEXT NOT NULL,
    params_json   TEXT NOT NULL,
    response_json TEXT NOT NULL,
    fetched_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_metrics (
    date               TEXT NOT NULL,
    channel            TEXT NOT NULL,
    level              TEXT NOT NULL,
    account_id         TEXT NOT NULL DEFAULT '',
    campaign_id        TEXT NOT NULL DEFAULT '',
    campaign_name      TEXT,
    adset_id           TEXT NOT NULL DEFAULT '',
    adset_name         TEXT,
    ad_id              TEXT NOT NULL DEFAULT '',
    ad_name            TEXT,
    currency           TEXT NOT NULL DEFAULT 'USD',

    spend              REAL,
    impressions        INTEGER,
    clicks             INTEGER,
    link_clicks        INTEGER,
    landing_page_views INTEGER,
    conversions        REAL,
    conversion_value   REAL,
    purchases          REAL,

    video_views        INTEGER,
    video_p25          INTEGER,
    video_p50          INTEGER,
    video_p75          INTEGER,
    video_p100         INTEGER,
    thruplays          INTEGER,
    three_sec_views    INTEGER,

    attribution_window TEXT,
    conversion_source  TEXT NOT NULL DEFAULT 'purchase_proxy',
    conversion_action  TEXT,
    raw_snapshot_id    INTEGER REFERENCES raw_snapshots(id),
    synced_at          TEXT NOT NULL,

    PRIMARY KEY (date, channel, level, campaign_id, adset_id, ad_id)
);

CREATE INDEX IF NOT EXISTS idx_daily_date    ON daily_metrics (date);
CREATE INDEX IF NOT EXISTS idx_daily_channel ON daily_metrics (channel, date);

CREATE TABLE IF NOT EXISTS reach_periods (
    channel     TEXT NOT NULL,
    level       TEXT NOT NULL,
    entity_id   TEXT NOT NULL DEFAULT '',
    date_start  TEXT NOT NULL,
    date_end    TEXT NOT NULL,
    reach       INTEGER,
    synced_at   TEXT NOT NULL,
    PRIMARY KEY (channel, level, entity_id, date_start, date_end)
);

CREATE TABLE IF NOT EXISTS bio_link_daily (
    date         TEXT NOT NULL,
    source       TEXT NOT NULL DEFAULT 'instagram',
    link_clicks  INTEGER,
    sessions     INTEGER,
    new_sessions INTEGER,
    orders       REAL,
    revenue      REAL,
    provider     TEXT,
    synced_at    TEXT NOT NULL,
    PRIMARY KEY (date, source)
);

CREATE TABLE IF NOT EXISTS creatives (
    channel       TEXT NOT NULL,
    ad_id         TEXT NOT NULL,
    ad_name       TEXT,
    thumbnail_url TEXT,
    permalink     TEXT,
    asset_type    TEXT,
    updated_at    TEXT NOT NULL,
    PRIMARY KEY (channel, ad_id)
);

CREATE TABLE IF NOT EXISTS sync_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    channel       TEXT NOT NULL,
    kind          TEXT NOT NULL,
    window_start  TEXT,
    window_end    TEXT,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    status        TEXT NOT NULL,
    rows_upserted INTEGER NOT NULL DEFAULT 0,
    error         TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

UPSERT_COLUMNS: Sequence[str] = (
    "date",
    "channel",
    "level",
    "account_id",
    "campaign_id",
    "campaign_name",
    "adset_id",
    "adset_name",
    "ad_id",
    "ad_name",
    "currency",
    "spend",
    "impressions",
    "clicks",
    "link_clicks",
    "landing_page_views",
    "conversions",
    "conversion_value",
    "purchases",
    "video_views",
    "video_p25",
    "video_p50",
    "video_p75",
    "video_p100",
    "thruplays",
    "three_sec_views",
    "attribution_window",
    "conversion_source",
    "conversion_action",
    "raw_snapshot_id",
    "synced_at",
)

_KEY_COLUMNS = {"date", "channel", "level", "campaign_id", "adset_id", "ad_id"}


def connect(path: Optional[str] = None) -> sqlite3.Connection:
    target = path or DB_PATH
    if target != ":memory:":
        pathlib.Path(target).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def _upsert_sql() -> str:
    columns = ", ".join(UPSERT_COLUMNS)
    placeholders = ", ".join(":" + c for c in UPSERT_COLUMNS)
    updates = ", ".join(
        "{0}=excluded.{0}".format(c) for c in UPSERT_COLUMNS if c not in _KEY_COLUMNS
    )
    return (
        "INSERT INTO daily_metrics ({columns}) VALUES ({placeholders}) "
        "ON CONFLICT (date, channel, level, campaign_id, adset_id, ad_id) "
        "DO UPDATE SET {updates}"
    ).format(columns=columns, placeholders=placeholders, updates=updates)


def upsert_daily(conn: sqlite3.Connection, rows: Iterable[Dict[str, Any]]) -> int:
    """Insert-or-replace normalized rows.

    Idempotent by primary key: re-syncing the last 28 days overwrites in place
    rather than duplicating, which matters because both platforms restate
    recent conversions for days after the fact.
    """
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    payload: List[Dict[str, Any]] = []
    for row in rows:
        record = {column: row.get(column) for column in UPSERT_COLUMNS}
        record["synced_at"] = record["synced_at"] or now
        record["currency"] = record["currency"] or "USD"
        record["conversion_source"] = record["conversion_source"] or "purchase_proxy"
        for key in ("account_id", "campaign_id", "adset_id", "ad_id"):
            record[key] = record[key] or ""
        if record["level"] not in LEVELS:
            raise ValueError("unknown level: %r" % (record["level"],))
        payload.append(record)
    if not payload:
        return 0
    conn.executemany(_upsert_sql(), payload)
    conn.commit()
    return len(payload)


def store_raw(
    conn: sqlite3.Connection, channel: str, endpoint: str, params: Any, response: Any
) -> int:
    cursor = conn.execute(
        "INSERT INTO raw_snapshots (channel, endpoint, params_json, response_json, fetched_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (
            channel,
            endpoint,
            json.dumps(params, default=str),
            json.dumps(response, default=str),
            dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        ),
    )
    conn.commit()
    return int(cursor.lastrowid)


def upsert_reach(conn: sqlite3.Connection, rows: Iterable[Dict[str, Any]]) -> int:
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    payload = [
        (
            r["channel"],
            r.get("level", "account"),
            r.get("entity_id", ""),
            r["date_start"],
            r["date_end"],
            r.get("reach"),
            now,
        )
        for r in rows
    ]
    if not payload:
        return 0
    conn.executemany(
        "INSERT INTO reach_periods (channel, level, entity_id, date_start, date_end, reach, synced_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT (channel, level, entity_id, date_start, date_end)"
        " DO UPDATE SET reach=excluded.reach, synced_at=excluded.synced_at",
        payload,
    )
    conn.commit()
    return len(payload)


def upsert_bio_link(conn: sqlite3.Connection, rows: Iterable[Dict[str, Any]]) -> int:
    """Insert-or-replace daily link-in-bio traffic. Idempotent by (date, source)."""
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    payload = [
        (
            r["date"],
            r.get("source", "instagram"),
            r.get("link_clicks"),
            r.get("sessions"),
            r.get("new_sessions"),
            r.get("orders"),
            r.get("revenue"),
            r.get("provider"),
            now,
        )
        for r in rows
    ]
    if not payload:
        return 0
    conn.executemany(
        "INSERT INTO bio_link_daily (date, source, link_clicks, sessions, new_sessions,"
        " orders, revenue, provider, synced_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT (date, source) DO UPDATE SET"
        " link_clicks=excluded.link_clicks, sessions=excluded.sessions,"
        " new_sessions=excluded.new_sessions, orders=excluded.orders,"
        " revenue=excluded.revenue, provider=excluded.provider,"
        " synced_at=excluded.synced_at",
        payload,
    )
    conn.commit()
    return len(payload)


def fetch_bio_link(
    conn: sqlite3.Connection, start: dt.date, end: dt.date
) -> List[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT * FROM bio_link_daily WHERE date BETWEEN ? AND ? ORDER BY date",
            (start.isoformat(), end.isoformat()),
        )
    )


def upsert_creatives(conn: sqlite3.Connection, rows: Iterable[Dict[str, Any]]) -> int:
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    payload = [
        (
            r["channel"],
            r["ad_id"],
            r.get("ad_name"),
            r.get("thumbnail_url"),
            r.get("permalink"),
            r.get("asset_type"),
            now,
        )
        for r in rows
    ]
    if not payload:
        return 0
    conn.executemany(
        "INSERT INTO creatives (channel, ad_id, ad_name, thumbnail_url, permalink, asset_type, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT (channel, ad_id) DO UPDATE SET"
        " ad_name=excluded.ad_name, thumbnail_url=excluded.thumbnail_url,"
        " permalink=excluded.permalink, asset_type=excluded.asset_type,"
        " updated_at=excluded.updated_at",
        payload,
    )
    conn.commit()
    return len(payload)


# --- reads -----------------------------------------------------------------


def fetch_rows(
    conn: sqlite3.Connection,
    start: dt.date,
    end: dt.date,
    channels: Optional[Sequence[str]] = None,
    level: str = "ad",
) -> List[sqlite3.Row]:
    sql = "SELECT * FROM daily_metrics WHERE level = ? AND date BETWEEN ? AND ?"
    params: List[Any] = [level, start.isoformat(), end.isoformat()]
    if channels:
        sql += " AND channel IN (%s)" % ",".join("?" for _ in channels)
        params.extend(channels)
    sql += " ORDER BY date"
    return list(conn.execute(sql, params))


def fetch_reach(
    conn: sqlite3.Connection,
    channel: str,
    start: dt.date,
    end: dt.date,
    level: str = "account",
    entity_id: str = "",
) -> Optional[int]:
    """Reach for exactly this window, or None.

    Deliberately an exact-window lookup. There is no correct way to derive the
    reach of a 28-day window from daily reach values, so if we did not fetch
    the window we say we do not have it.
    """
    row = conn.execute(
        "SELECT reach FROM reach_periods WHERE channel = ? AND level = ? AND entity_id = ?"
        " AND date_start = ? AND date_end = ?",
        (channel, level, entity_id, start.isoformat(), end.isoformat()),
    ).fetchone()
    if row is None or row["reach"] is None:
        return None
    return int(row["reach"])


def fetch_creatives(conn: sqlite3.Connection) -> Dict[str, sqlite3.Row]:
    return {
        "%s:%s" % (row["channel"], row["ad_id"]): row
        for row in conn.execute("SELECT * FROM creatives")
    }


def latest_sync(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT channel, kind, status, started_at, finished_at, rows_upserted, error,"
            " window_start, window_end FROM sync_runs"
            " WHERE id IN (SELECT MAX(id) FROM sync_runs GROUP BY channel)"
            " ORDER BY channel"
        )
    )


def start_sync(
    conn: sqlite3.Connection, channel: str, kind: str, start: dt.date, end: dt.date
) -> int:
    cursor = conn.execute(
        "INSERT INTO sync_runs (channel, kind, window_start, window_end, started_at, status)"
        " VALUES (?, ?, ?, ?, ?, 'running')",
        (
            channel,
            kind,
            start.isoformat(),
            end.isoformat(),
            dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        ),
    )
    conn.commit()
    return int(cursor.lastrowid)


def finish_sync(
    conn: sqlite3.Connection,
    run_id: int,
    status: str,
    rows_upserted: int = 0,
    error: Optional[str] = None,
) -> None:
    conn.execute(
        "UPDATE sync_runs SET finished_at = ?, status = ?, rows_upserted = ?, error = ?"
        " WHERE id = ?",
        (
            dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            status,
            rows_upserted,
            error,
            run_id,
        ),
    )
    conn.commit()


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?)"
        " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def get_setting(conn: sqlite3.Connection, key: str, default: Optional[str] = None) -> Optional[str]:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default
