"""YouTube organic — YouTube Analytics API.

This is a different permission world from Google Ads. An MCC link grants
nothing here: access comes from a role on the *channel* itself, and the OAuth
user must be an Owner or Manager of that channel's Brand Account.

Needs a Google Cloud OAuth client with the YouTube Analytics API enabled and
the scope `https://www.googleapis.com/auth/yt-analytics.readonly`. No developer
token and no approval process, unlike Google Ads, so this can run today.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Any, Dict, List

from . import base

LABEL = "YouTube organic"

TOKEN_URL = "https://oauth2.googleapis.com/token"
REPORTS_URL = "https://youtubeanalytics.googleapis.com/v2/reports"
DATA_URL = "https://www.googleapis.com/youtube/v3/videos"

DAY_METRICS = [
    "views", "estimatedMinutesWatched", "averageViewDuration", "likes",
    "comments", "shares", "subscribersGained", "subscribersLost",
]
VIDEO_METRICS = ["views", "estimatedMinutesWatched", "averageViewDuration", "likes", "comments", "shares"]


def is_configured() -> bool:
    return bool(
        base.env("YT_CLIENT_ID") and base.env("YT_CLIENT_SECRET")
        and base.env("YT_REFRESH_TOKEN") and base.env("YT_CHANNEL_ID")
    )


def _access_token() -> str:
    payload = base.get_json(
        TOKEN_URL,
        method="POST",
        params={
            "client_id": base.require("YT_CLIENT_ID"),
            "client_secret": base.require("YT_CLIENT_SECRET"),
            "refresh_token": base.require("YT_REFRESH_TOKEN"),
            "grant_type": "refresh_token",
        },
    )
    token = payload.get("access_token")
    if not token:
        raise base.ConnectorError("no access_token in the OAuth response")
    return token


def sync(conn: sqlite3.Connection, start: dt.date, end: dt.date) -> int:
    from .. import db

    token = _access_token()
    channel = base.require("YT_CHANNEL_ID")
    headers = {"Authorization": "Bearer %s" % token}
    ids = "channel==%s" % channel

    rows: List[Dict[str, Any]] = []

    # --- per day, account level ---
    daily = base.get_json(REPORTS_URL, headers=headers, params={
        "ids": ids, "startDate": start.isoformat(), "endDate": end.isoformat(),
        "metrics": ",".join(DAY_METRICS), "dimensions": "day",
    })
    db.store_raw(conn, "youtube_organic", "reports/day", {"startDate": start.isoformat()}, daily)

    columns = [c["name"] for c in daily.get("columnHeaders", [])]
    for entry in daily.get("rows", []) or []:
        record = dict(zip(columns, entry))
        minutes = base.as_float(record.get("estimatedMinutesWatched")) or 0.0
        gained = base.as_int(record.get("subscribersGained")) or 0
        lost = base.as_int(record.get("subscribersLost")) or 0
        rows.append({
            "date": record.get("day"),
            "platform": "youtube",
            "account_id": channel,
            "entity_type": "account",
            "entity_id": channel,
            "views": base.as_int(record.get("views")),
            "likes": base.as_int(record.get("likes")),
            "comments": base.as_int(record.get("comments")),
            "shares": base.as_int(record.get("shares")),
            "engagements": (base.as_int(record.get("likes")) or 0)
                           + (base.as_int(record.get("comments")) or 0)
                           + (base.as_int(record.get("shares")) or 0),
            "follows": gained - lost,
            "watch_seconds": minutes * 60.0,
            "avg_view_seconds": base.as_float(record.get("averageViewDuration")),
            "view_definition": "about 30 seconds, or a click on a short",
            "provider": "youtube analytics api",
        })

    # --- top videos in the window ---
    videos = base.get_json(REPORTS_URL, headers=headers, params={
        "ids": ids, "startDate": start.isoformat(), "endDate": end.isoformat(),
        "metrics": ",".join(VIDEO_METRICS), "dimensions": "video",
        "sort": "-views", "maxResults": 50,
    })
    db.store_raw(conn, "youtube_organic", "reports/video", {"startDate": start.isoformat()}, videos)

    vcolumns = [c["name"] for c in videos.get("columnHeaders", [])]
    entries = [dict(zip(vcolumns, r)) for r in (videos.get("rows") or [])]
    titles = _titles(headers, [e.get("video") for e in entries if e.get("video")])

    for record in entries:
        video_id = record.get("video")
        minutes = base.as_float(record.get("estimatedMinutesWatched")) or 0.0
        rows.append({
            # Video totals cover the whole window, so they are dated to its end
            # rather than pretending to be a single day's activity.
            "date": end.isoformat(),
            "platform": "youtube",
            "account_id": channel,
            "entity_type": "post",
            "entity_id": video_id,
            "post_caption": titles.get(video_id, {}).get("title"),
            "post_url": "https://www.youtube.com/watch?v=%s" % video_id,
            "post_type": "video",
            "published_at": titles.get(video_id, {}).get("publishedAt"),
            "views": base.as_int(record.get("views")),
            "likes": base.as_int(record.get("likes")),
            "comments": base.as_int(record.get("comments")),
            "shares": base.as_int(record.get("shares")),
            "engagements": (base.as_int(record.get("likes")) or 0)
                           + (base.as_int(record.get("comments")) or 0)
                           + (base.as_int(record.get("shares")) or 0),
            "watch_seconds": minutes * 60.0,
            "avg_view_seconds": base.as_float(record.get("averageViewDuration")),
            "view_definition": "about 30 seconds, or a click on a short",
            "provider": "youtube analytics api",
        })

    written = db.upsert_organic(conn, rows)
    _subscribers(conn, headers, channel)
    return written


def _titles(headers: Dict[str, str], video_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """Analytics returns IDs only; titles come from the Data API."""
    out: Dict[str, Dict[str, Any]] = {}
    for chunk in [video_ids[i:i + 50] for i in range(0, len(video_ids), 50)]:
        if not chunk:
            continue
        try:
            payload = base.get_json(DATA_URL, headers=headers, params={
                "part": "snippet", "id": ",".join(chunk),
            })
        except base.ConnectorError:
            continue
        for item in payload.get("items", []):
            snippet = item.get("snippet", {})
            out[item.get("id")] = {
                "title": (snippet.get("title") or "")[:300],
                "publishedAt": snippet.get("publishedAt"),
            }
    return out


def _subscribers(conn, headers: Dict[str, str], channel: str) -> None:
    from .. import db
    try:
        payload = base.get_json(
            "https://www.googleapis.com/youtube/v3/channels",
            headers=headers, params={"part": "statistics", "id": channel},
        )
    except base.ConnectorError:
        return
    items = payload.get("items") or []
    if not items:
        return
    count = base.as_int(items[0].get("statistics", {}).get("subscriberCount"))
    if count is None:
        return
    db.upsert_followers(conn, [{
        "date": dt.date.today().isoformat(), "platform": "youtube",
        "account_id": channel, "followers": count,
    }])
