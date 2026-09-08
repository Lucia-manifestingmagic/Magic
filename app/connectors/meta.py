"""Meta Marketing API — paid insights.

Ad-account data, not Page data. Rows land in `daily_metrics` at ad level; the
UI aggregates upward, so campaign and ad-set figures are always consistent with
the ads beneath them.

Attribution is click-through only (`7d_click`, no view window), per the client's
decision. That reports fewer conversions than Meta's own UI, which defaults to
including 1-day view-through, so the CAC here reads higher than Ads Manager.
That is intentional and the window is stored on every row so the difference can
always be explained.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Any, Dict, List

from . import base

LABEL = "Meta ads"

FIELDS = [
    "date_start", "campaign_id", "campaign_name", "adset_id", "adset_name",
    "ad_id", "ad_name", "spend", "impressions", "reach", "clicks", "ctr", "cpc",
    "cpm", "actions", "action_values", "cost_per_action_type",
    "video_thruplay_watched_actions", "video_p25_watched_actions",
    "video_p50_watched_actions", "video_p75_watched_actions",
    "video_p100_watched_actions",
]


def is_configured() -> bool:
    return bool(base.env("META_ACCESS_TOKEN") and base.env("META_AD_ACCOUNT_ID"))


def _api_base() -> str:
    return "https://graph.facebook.com/%s" % base.env("META_API_VERSION", "v21.0")


def _windows() -> List[str]:
    raw = base.env("META_ATTRIBUTION_WINDOWS", "7d_click")
    return [w.strip() for w in raw.split(",") if w.strip()]


def sync(conn: sqlite3.Connection, start: dt.date, end: dt.date) -> int:
    token = base.require("META_ACCESS_TOKEN")
    account = base.require("META_AD_ACCOUNT_ID")
    conversion_action = base.env(
        "META_CONVERSION_ACTION", "offsite_conversion.fb_pixel_purchase"
    )
    windows = _windows()

    params = {
        "level": "ad",
        "time_increment": 1,
        "time_range": '{"since":"%s","until":"%s"}' % (start.isoformat(), end.isoformat()),
        "fields": ",".join(FIELDS),
        "action_attribution_windows": ",".join(windows),
        "limit": 500,
        "access_token": token,
    }

    url = "%s/%s/insights" % (_api_base(), account)
    rows: List[Dict[str, Any]] = []
    pages = 0

    while url and pages < 200:
        payload = base.get_json(url, params=params)
        snapshot = db_store_raw(conn, "meta", url, {k: v for k, v in (params or {}).items()
                                                    if k != "access_token"}, payload)
        for record in payload.get("data", []):
            rows.append(_normalize(record, account, conversion_action, windows, snapshot))
        pages += 1
        url = (payload.get("paging") or {}).get("next")
        params = None  # the `next` URL already carries every parameter

    from .. import db
    written = db.upsert_daily(conn, rows)
    _sync_reach(conn, account, token, start, end)
    return written


def db_store_raw(conn, channel, endpoint, params, payload) -> int:
    from .. import db
    return db.store_raw(conn, channel, endpoint, params, payload)


def _normalize(
    record: Dict[str, Any], account: str, conversion_action: str,
    windows: List[str], snapshot_id: int,
) -> Dict[str, Any]:
    actions = record.get("actions")
    values = record.get("action_values")

    return {
        "date": record.get("date_start"),
        "channel": "meta",
        "level": "ad",
        "account_id": account,
        "campaign_id": record.get("campaign_id") or "",
        "campaign_name": record.get("campaign_name"),
        "adset_id": record.get("adset_id") or "",
        "adset_name": record.get("adset_name"),
        "ad_id": record.get("ad_id") or "",
        "ad_name": record.get("ad_name"),
        "currency": "USD",
        "spend": base.as_float(record.get("spend")),
        "impressions": base.as_int(record.get("impressions")),
        "clicks": base.as_int(record.get("clicks")),
        "link_clicks": base.as_int(base.sum_actions(actions, ["link_click"])),
        "landing_page_views": base.as_int(base.sum_actions(actions, ["landing_page_view"])),
        "conversions": base.sum_actions(actions, [conversion_action]),
        "conversion_value": base.sum_actions(values, [conversion_action]),
        "purchases": base.sum_actions(actions, [conversion_action]),
        # Meta has no equivalent of a Google "video view", so the column stays
        # null rather than being filled with a near-enough number.
        "video_views": None,
        "video_p25": base.as_int(_first(record.get("video_p25_watched_actions"))),
        "video_p50": base.as_int(_first(record.get("video_p50_watched_actions"))),
        "video_p75": base.as_int(_first(record.get("video_p75_watched_actions"))),
        "video_p100": base.as_int(_first(record.get("video_p100_watched_actions"))),
        "thruplays": base.as_int(_first(record.get("video_thruplay_watched_actions"))),
        "three_sec_views": base.as_int(base.sum_actions(actions, ["video_view"])),
        "attribution_window": ",".join(windows),
        "conversion_source": "purchase_proxy",
        "conversion_action": conversion_action,
        "raw_snapshot_id": snapshot_id,
        "synced_at": None,
    }


def _first(entries):
    """Meta wraps several video metrics in a one-item list of {action_type,value}."""
    if not entries:
        return None
    if isinstance(entries, list) and entries:
        return entries[0].get("value")
    return entries


def _sync_reach(conn, account: str, token: str, start: dt.date, end: dt.date) -> None:
    """Fetch reach for the exact window, because it cannot be summed from days."""
    from .. import db
    payload = base.get_json(
        "%s/%s/insights" % (_api_base(), account),
        params={
            "level": "account",
            "time_range": '{"since":"%s","until":"%s"}' % (start.isoformat(), end.isoformat()),
            "fields": "reach,impressions",
            "access_token": token,
        },
    )
    data = payload.get("data") or []
    if not data:
        return
    db.upsert_reach(conn, [{
        "channel": "meta", "level": "account", "entity_id": "",
        "date_start": start.isoformat(), "date_end": end.isoformat(),
        "reach": base.as_int(data[0].get("reach")),
    }])
