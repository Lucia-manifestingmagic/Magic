"""Meta organic — Instagram and Facebook Page content.

Separate from the ads connector on purpose: this reads *Page* and *Instagram
account* assets, which are granted independently of the ad account. A token
that can read ad insights very often cannot read Page insights, and the failure
is a permissions error rather than an empty result.

Scopes needed on the System User token:
    instagram_basic, instagram_manage_insights,
    pages_read_engagement, pages_show_list, read_insights

A note on volatility: Meta renames and deprecates insight metrics between API
versions more often than any other part of the platform. This asks for a
candidate set of metrics and keeps whatever comes back, rather than failing the
whole sync because one metric was retired. Raw responses are stored, so if a
metric disappears we can see exactly when and re-derive.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Any, Dict, List, Optional

from . import base

LABEL = "Meta organic (IG + FB)"

# Requested per media object. Anything the API rejects is dropped and retried
# without it, so one retired metric cannot take the sync down.
IG_MEDIA_METRICS = ["reach", "likes", "comments", "shares", "saved", "total_interactions", "views"]
IG_ACCOUNT_METRICS = ["reach", "profile_views", "website_clicks", "accounts_engaged"]


def _token() -> str:
    """Prefer the Page token: it does not expire, unlike the 60-day user token."""
    return base.env("META_PAGE_TOKEN") or base.require("META_ACCESS_TOKEN")


def is_configured() -> bool:
    return bool(
        (base.env("META_PAGE_TOKEN") or base.env("META_ACCESS_TOKEN"))
        and base.env("IG_USER_ID")
    )


def _api() -> str:
    return "https://graph.facebook.com/%s" % base.env("META_API_VERSION", "v21.0")


def sync(conn: sqlite3.Connection, start: dt.date, end: dt.date) -> int:
    from .. import db

    token = _token()
    ig_user = base.require("IG_USER_ID")

    rows: List[Dict[str, Any]] = []
    rows.extend(_instagram_posts(conn, ig_user, token, start, end))
    rows.extend(_instagram_account(conn, ig_user, token, start, end))

    page_id = base.env("FB_PAGE_ID")
    if page_id:
        rows.extend(_facebook_page(conn, page_id, token, start, end))

    written = db.upsert_organic(conn, rows)
    _followers(conn, ig_user, token)
    return written


def _get_with_metric_fallback(url: str, params: Dict[str, Any], metrics: List[str]) -> Optional[Dict]:
    """Request the metric set, dropping any the API rejects, then retry once."""
    attempt = list(metrics)
    for _ in range(len(metrics)):
        try:
            params["metric"] = ",".join(attempt)
            return base.get_json(url, params=params)
        except base.ConnectorError as exc:
            message = str(exc)
            dropped = [m for m in attempt if m in message]
            if not dropped or len(attempt) == 1:
                return None
            attempt = [m for m in attempt if m not in dropped]
    return None


def _instagram_posts(conn, ig_user: str, token: str, start: dt.date, end: dt.date) -> List[Dict]:
    from .. import db

    url = "%s/%s/media" % (_api(), ig_user)
    params = {
        # like_count and comments_count come with instagram_basic. The richer
        # numbers (reach, views, saves) need instagram_manage_insights, which a
        # non-Business app cannot hold, so they are fetched separately and are
        # allowed to come back empty.
        "fields": ("id,caption,media_type,media_product_type,permalink,timestamp,"
                   "like_count,comments_count"),
        "since": start.isoformat(),
        "until": end.isoformat(),
        "limit": 100,
        "access_token": token,
    }

    out: List[Dict[str, Any]] = []
    pages = 0
    while url and pages < 50:
        payload = base.get_json(url, params=params)
        db.store_raw(conn, "meta_organic", "ig/media", {"since": start.isoformat()}, payload)

        for media in payload.get("data", []):
            published = (media.get("timestamp") or "")[:10]
            if not published or not (start.isoformat() <= published <= end.isoformat()):
                continue

            stats = _get_with_metric_fallback(
                "%s/%s/insights" % (_api(), media["id"]),
                {"access_token": token},
                IG_MEDIA_METRICS,
            )
            values = _flatten_insights(stats)

            out.append({
                "date": published,
                "platform": "instagram",
                "account_id": ig_user,
                "entity_type": "post",
                "entity_id": media["id"],
                "post_caption": (media.get("caption") or "")[:300],
                "post_url": media.get("permalink"),
                "post_type": (media.get("media_product_type") or media.get("media_type") or "").lower(),
                "published_at": media.get("timestamp"),
                "reach": values.get("reach"),
                "views": values.get("views"),
                # Prefer the insight where we have it, fall back to the field.
                "likes": values.get("likes", media.get("like_count")),
                "comments": values.get("comments", media.get("comments_count")),
                "shares": values.get("shares"),
                "saves": values.get("saved"),
                "engagements": values.get("total_interactions") or _basic_engagements(media),
                "view_definition": "plays of 1 second or more",
                "provider": "instagram graph api",
            })
        pages += 1
        url = (payload.get("paging") or {}).get("next")
        params = None
    return out


def _instagram_account(conn, ig_user: str, token: str, start: dt.date, end: dt.date) -> List[Dict]:
    from .. import db
    payload = _get_with_metric_fallback(
        "%s/%s/insights" % (_api(), ig_user),
        {"period": "day", "since": start.isoformat(), "until": end.isoformat(),
         "metric_type": "total_value", "access_token": token},
        IG_ACCOUNT_METRICS,
    )
    if not payload:
        return []
    db.store_raw(conn, "meta_organic", "ig/account-insights", {"since": start.isoformat()}, payload)

    by_day: Dict[str, Dict[str, Any]] = {}
    for metric in payload.get("data", []):
        name = metric.get("name")
        for point in metric.get("values", []):
            day = (point.get("end_time") or "")[:10]
            if not day:
                continue
            by_day.setdefault(day, {})[name] = point.get("value")

    return [{
        "date": day, "platform": "instagram", "account_id": ig_user,
        "entity_type": "account", "entity_id": ig_user,
        "reach": values.get("reach"),
        "profile_views": values.get("profile_views"),
        "link_clicks": values.get("website_clicks"),
        "engagements": values.get("accounts_engaged"),
        "view_definition": "plays of 1 second or more",
        "provider": "instagram graph api",
    } for day, values in by_day.items()]


def _facebook_page(conn, page_id: str, token: str, start: dt.date, end: dt.date) -> List[Dict]:
    from .. import db
    payload = _get_with_metric_fallback(
        "%s/%s/insights" % (_api(), page_id),
        {"period": "day", "since": start.isoformat(), "until": end.isoformat(),
         "access_token": token},
        # page_impressions and page_impressions_unique were removed in v19+.
        # Asking for a retired metric fails the whole call with "must be a valid
        # insights metric", taking the valid ones down with it, so this list is
        # only metrics confirmed to exist on v21.
        ["page_post_engagements", "page_views_total", "page_daily_follows",
         "page_follows", "page_total_actions"],
    )
    if not payload:
        return []
    db.store_raw(conn, "meta_organic", "fb/page-insights", {"since": start.isoformat()}, payload)

    by_day: Dict[str, Dict[str, Any]] = {}
    for metric in payload.get("data", []):
        for point in metric.get("values", []):
            day = (point.get("end_time") or "")[:10]
            if day:
                by_day.setdefault(day, {})[metric.get("name")] = point.get("value")

    return [{
        "date": day, "platform": "facebook", "account_id": page_id,
        "entity_type": "account", "entity_id": page_id,
        "reach": None,   # no reach metric survives on v21 for Pages
        "engagements": values.get("page_post_engagements"),
        "profile_views": values.get("page_views_total"),
        "follows": values.get("page_daily_follows"),
        "view_definition": "1 second or more",
        "provider": "facebook graph api",
    } for day, values in by_day.items()]


def _followers(conn, ig_user: str, token: str) -> None:
    """Today's follower count. A level, so it is snapshotted, never summed."""
    from .. import db
    try:
        payload = base.get_json(
            "%s/%s" % (_api(), ig_user),
            params={"fields": "followers_count", "access_token": token},
        )
    except base.ConnectorError:
        return
    count = base.as_int(payload.get("followers_count"))
    if count is None:
        return
    db.upsert_followers(conn, [{
        "date": dt.date.today().isoformat(), "platform": "instagram",
        "account_id": ig_user, "followers": count,
    }])


def _basic_engagements(media: Dict[str, Any]) -> Optional[int]:
    """Likes plus comments, when insights are unavailable."""
    likes = base.as_int(media.get("like_count"))
    comments = base.as_int(media.get("comments_count"))
    if likes is None and comments is None:
        return None
    return (likes or 0) + (comments or 0)


def _flatten_insights(payload: Optional[Dict]) -> Dict[str, Any]:
    if not payload:
        return {}
    out: Dict[str, Any] = {}
    for metric in payload.get("data", []):
        values = metric.get("values") or []
        if values:
            out[metric.get("name")] = values[0].get("value")
        elif metric.get("total_value"):
            out[metric.get("name")] = metric["total_value"].get("value")
    return out
