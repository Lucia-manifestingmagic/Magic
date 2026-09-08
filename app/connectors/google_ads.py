"""Google Ads — YouTube video campaigns (paid).

YouTube *ads* live here, not in the YouTube APIs. Queried with GAQL over the
REST endpoint rather than the Python SDK, which pins hard and ages badly.

The developer token is the gate: a new one only returns data for test accounts
until Basic access is approved. Everything here works against a test token
first, so the pipeline can be verified while approval is pending.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Any, Dict, List

from . import base

LABEL = "YouTube ads"

TOKEN_URL = "https://oauth2.googleapis.com/token"
API_VERSION = "v18"

GAQL = """
SELECT
  segments.date,
  campaign.id, campaign.name,
  ad_group.id, ad_group.name,
  ad_group_ad.ad.id, ad_group_ad.ad.name,
  metrics.cost_micros, metrics.impressions, metrics.clicks,
  metrics.conversions, metrics.conversions_value,
  metrics.video_views, metrics.video_view_rate,
  metrics.video_quartile_p25_rate, metrics.video_quartile_p50_rate,
  metrics.video_quartile_p75_rate, metrics.video_quartile_p100_rate
FROM ad_group_ad
WHERE campaign.advertising_channel_type = 'VIDEO'
  AND segments.date BETWEEN '{start}' AND '{end}'
"""


def is_configured() -> bool:
    return bool(
        base.env("GOOGLE_ADS_DEVELOPER_TOKEN") and base.env("GOOGLE_ADS_CLIENT_ID")
        and base.env("GOOGLE_ADS_REFRESH_TOKEN") and base.env("GOOGLE_ADS_CUSTOMER_ID")
    )


def _access_token() -> str:
    payload = base.get_json(TOKEN_URL, method="POST", params={
        "client_id": base.require("GOOGLE_ADS_CLIENT_ID"),
        "client_secret": base.require("GOOGLE_ADS_CLIENT_SECRET"),
        "refresh_token": base.require("GOOGLE_ADS_REFRESH_TOKEN"),
        "grant_type": "refresh_token",
    })
    token = payload.get("access_token")
    if not token:
        raise base.ConnectorError("no access_token in the OAuth response")
    return token


def sync(conn: sqlite3.Connection, start: dt.date, end: dt.date) -> int:
    from .. import db

    customer = base.require("GOOGLE_ADS_CUSTOMER_ID").replace("-", "")
    headers = {
        "Authorization": "Bearer %s" % _access_token(),
        "developer-token": base.require("GOOGLE_ADS_DEVELOPER_TOKEN"),
        "Content-Type": "application/json",
    }
    manager = base.env("GOOGLE_ADS_LOGIN_CUSTOMER_ID").replace("-", "")
    if manager:
        headers["login-customer-id"] = manager

    url = "https://googleads.googleapis.com/%s/customers/%s/googleAds:search" % (API_VERSION, customer)
    query = GAQL.format(start=start.isoformat(), end=end.isoformat())

    rows: List[Dict[str, Any]] = []
    page_token, pages = None, 0
    while pages < 200:
        body: Dict[str, Any] = {"query": query, "pageSize": 10000}
        if page_token:
            body["pageToken"] = page_token
        payload = base.get_json(url, headers=headers, method="POST", json_body=body)
        snapshot = db.store_raw(conn, "youtube", "googleAds:search",
                                {"start": start.isoformat(), "end": end.isoformat()}, payload)

        for result in payload.get("results", []):
            rows.append(_normalize(result, customer, snapshot))

        page_token = payload.get("nextPageToken")
        pages += 1
        if not page_token:
            break

    return db.upsert_daily(conn, rows)


def _normalize(result: Dict[str, Any], customer: str, snapshot_id: int) -> Dict[str, Any]:
    metrics = result.get("metrics", {})
    campaign = result.get("campaign", {})
    ad_group = result.get("adGroup", {})
    ad = (result.get("adGroupAd", {}) or {}).get("ad", {})

    impressions = base.as_int(metrics.get("impressions"))

    def quartile(rate_key: str):
        """Google returns quartile *rates*; store counts so they stay additive."""
        rate = base.as_float(metrics.get(rate_key))
        if rate is None or impressions is None:
            return None
        return int(round(rate * impressions))

    return {
        "date": (result.get("segments") or {}).get("date"),
        "channel": "youtube",
        "level": "ad",
        "account_id": customer,
        "campaign_id": str(campaign.get("id") or ""),
        "campaign_name": campaign.get("name"),
        "adset_id": str(ad_group.get("id") or ""),
        "adset_name": ad_group.get("name"),
        "ad_id": str(ad.get("id") or ""),
        "ad_name": ad.get("name"),
        "currency": "USD",
        "spend": base.micros_to_currency(metrics.get("costMicros")),
        "impressions": impressions,
        "clicks": base.as_int(metrics.get("clicks")),
        "link_clicks": None,
        "landing_page_views": None,
        "conversions": base.as_float(metrics.get("conversions")),
        "conversion_value": base.as_float(metrics.get("conversionsValue")),
        "purchases": base.as_float(metrics.get("conversions")),
        "video_views": base.as_int(metrics.get("videoViews")),
        "video_p25": quartile("videoQuartileP25Rate"),
        "video_p50": quartile("videoQuartileP50Rate"),
        "video_p75": quartile("videoQuartileP75Rate"),
        "video_p100": quartile("videoQuartileP100Rate"),
        "thruplays": None,
        "three_sec_views": None,
        "attribution_window": "click-through, account model",
        "conversion_source": "purchase_proxy",
        "conversion_action": base.env("GOOGLE_ADS_CONVERSION_ACTIONS", "all conversions"),
        "raw_snapshot_id": snapshot_id,
        "synced_at": None,
    }
