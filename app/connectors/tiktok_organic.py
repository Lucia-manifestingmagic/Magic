"""TikTok organic — TikTok Business API.

The awkward one. TikTok has no agency delegation comparable to Meta's Business
Manager: an app is authorised against an account directly, so whoever holds
posting access performs the OAuth once and the resulting token is what this
uses. It also needs a registered TikTok developer app whose scopes have been
approved, which is a review process rather than a setting.

Required scopes: `user.info.basic`, `user.info.profile`, `user.info.stats`,
`video.list`.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Any, Dict, List

from . import base

LABEL = "TikTok organic"

API = "https://business-api.tiktok.com/open_api/v1.3"

VIDEO_FIELDS = [
    "item_id", "create_time", "thumbnail_url", "share_url", "caption",
    "video_views", "likes", "comments", "shares", "reach",
    "video_duration", "full_video_watched_rate", "total_time_watched",
    "average_time_watched",
]
ACCOUNT_FIELDS = [
    "followers_count", "profile_views", "video_views", "likes", "comments",
    "shares", "reach",
]


def is_configured() -> bool:
    return bool(base.env("TIKTOK_ACCESS_TOKEN") and base.env("TIKTOK_BUSINESS_ID"))


def _headers() -> Dict[str, str]:
    return {"Access-Token": base.require("TIKTOK_ACCESS_TOKEN")}


def _unwrap(payload: Dict[str, Any]) -> Dict[str, Any]:
    """TikTok returns HTTP 200 with an error code in the body."""
    if payload.get("code") not in (0, None):
        raise base.ConnectorError(
            "TikTok API error %s: %s" % (payload.get("code"), payload.get("message"))
        )
    return payload.get("data") or {}


def sync(conn: sqlite3.Connection, start: dt.date, end: dt.date) -> int:
    from .. import db

    business_id = base.require("TIKTOK_BUSINESS_ID")
    headers = _headers()
    rows: List[Dict[str, Any]] = []

    # --- account level, per day ---
    account = _unwrap(base.get_json("%s/business/get/" % API, headers=headers, params={
        "business_id": business_id,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "fields": '["%s"]' % '","'.join(ACCOUNT_FIELDS),
    }))
    db.store_raw(conn, "tiktok_organic", "business/get", {"start": start.isoformat()}, account)

    for entry in account.get("metrics", []) or []:
        day = entry.get("date") or entry.get("stat_time_day", "")[:10]
        if not day:
            continue
        rows.append({
            "date": day, "platform": "tiktok", "account_id": business_id,
            "entity_type": "account", "entity_id": business_id,
            "views": base.as_int(entry.get("video_views")),
            "reach": base.as_int(entry.get("reach")),
            "likes": base.as_int(entry.get("likes")),
            "comments": base.as_int(entry.get("comments")),
            "shares": base.as_int(entry.get("shares")),
            "profile_views": base.as_int(entry.get("profile_views")),
            "engagements": (base.as_int(entry.get("likes")) or 0)
                           + (base.as_int(entry.get("comments")) or 0)
                           + (base.as_int(entry.get("shares")) or 0),
            "view_definition": "an immediate play, with no minimum duration",
            "provider": "tiktok business api",
        })

    # --- per video ---
    cursor, pages = None, 0
    while pages < 20:
        params = {
            "business_id": business_id,
            "fields": '["%s"]' % '","'.join(VIDEO_FIELDS),
            "max_count": 50,
        }
        if cursor:
            params["cursor"] = cursor
        payload = _unwrap(base.get_json("%s/business/video/list/" % API, headers=headers, params=params))
        db.store_raw(conn, "tiktok_organic", "business/video/list", {"cursor": cursor}, payload)

        for video in payload.get("videos", []) or []:
            created = video.get("create_time")
            day = _to_date(created)
            if not day or not (start.isoformat() <= day <= end.isoformat()):
                continue
            watched = base.as_float(video.get("total_time_watched"))
            rows.append({
                "date": day, "platform": "tiktok", "account_id": business_id,
                "entity_type": "post", "entity_id": video.get("item_id"),
                "post_caption": (video.get("caption") or "")[:300],
                "post_url": video.get("share_url"),
                "post_type": "video",
                "published_at": day,
                "views": base.as_int(video.get("video_views")),
                "reach": base.as_int(video.get("reach")),
                "likes": base.as_int(video.get("likes")),
                "comments": base.as_int(video.get("comments")),
                "shares": base.as_int(video.get("shares")),
                "engagements": (base.as_int(video.get("likes")) or 0)
                               + (base.as_int(video.get("comments")) or 0)
                               + (base.as_int(video.get("shares")) or 0),
                "watch_seconds": watched,
                "avg_view_seconds": base.as_float(video.get("average_time_watched")),
                "view_definition": "an immediate play, with no minimum duration",
                "provider": "tiktok business api",
            })

        cursor = payload.get("cursor")
        if not payload.get("has_more") or not cursor:
            break
        pages += 1

    written = db.upsert_organic(conn, rows)

    followers = base.as_int((account.get("metrics") or [{}])[-1].get("followers_count")) \
        if account.get("metrics") else None
    if followers is not None:
        db.upsert_followers(conn, [{
            "date": dt.date.today().isoformat(), "platform": "tiktok",
            "account_id": business_id, "followers": followers,
        }])
    return written


def _to_date(value: Any) -> str:
    """create_time arrives as either a unix timestamp or an ISO string."""
    if value is None:
        return ""
    try:
        return dt.datetime.utcfromtimestamp(int(value)).date().isoformat()
    except (TypeError, ValueError, OSError):
        return str(value)[:10]
