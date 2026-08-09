"""Deterministic fixture data for mock mode.

This exists so the UI can be built, reviewed and demoed with no live
credentials. The numbers are plausible but invented, and mock mode is labelled
in the interface on every screen — a dashboard whose whole purpose is settling
budget arguments must never let invented numbers pass as measured ones.

The shape of what this writes is exactly what a connector writes: the same
normalized rows, through the same upsert. If the dashboard renders correctly
from fixtures, the only thing stage 2 and 3 have to get right is the mapping
from each API's payload into these columns.

Run with:  python -m app.fixtures
"""

from __future__ import annotations

import datetime as dt
import math
import random
from typing import Any, Dict, List, Optional, Sequence

from . import constants as C
from . import db, ranges

SEED = 20260809
DAYS = 120

# --- The account structure being simulated ---------------------------------

META_ACCOUNT = "act_508841100279316"
GOOGLE_ACCOUNT = "774-219-3086"

META_STRUCTURE = [
    {
        "campaign_id": "23861004417710",
        "campaign_name": "NKS | Prospecting | Locksmith Lookalike 1%",
        "start_day": 0,
        "daily_spend": 118.0,
        "adsets": [
            {
                "adset_id": "23861004417711",
                "adset_name": "LAL 1% | Auto Locksmith Buyers",
                "frequency": 1.9,
                "ads": [
                    {"ad_id": "23861004417712", "ad_name": "Cutting Machine Demo | 30s", "strength": 0.82, "video": True},
                    {"ad_id": "23861004417713", "ad_name": "Wholesale Price Sheet | Static", "strength": 1.04, "video": False},
                    {"ad_id": "23861004417714", "ad_name": "Shop Owner Testimonial | 45s", "strength": 1.28, "video": True},
                ],
            },
            {
                "adset_id": "23861004417715",
                "adset_name": "Interest | Automotive Locksmith Trade",
                "frequency": 2.2,
                "ads": [
                    {"ad_id": "23861004417716", "ad_name": "Same-Day Shipping | Static", "strength": 0.95, "video": False},
                    {"ad_id": "23861004417717", "ad_name": "Key Blank Range | Carousel", "strength": 1.42, "video": False},
                ],
            },
        ],
    },
    {
        "campaign_id": "23861004418220",
        "campaign_name": "NKS | Retargeting | Site Visitors 30d",
        "start_day": 12,
        "daily_spend": 41.0,
        "adsets": [
            {
                "adset_id": "23861004418221",
                "adset_name": "RT | Viewed Product, No Order",
                "frequency": 4.1,
                "ads": [
                    {"ad_id": "23861004418222", "ad_name": "Open a Wholesale Account | Static", "strength": 0.64, "video": False},
                    {"ad_id": "23861004418223", "ad_name": "Bulk Discount Tiers | 15s", "strength": 0.78, "video": True},
                ],
            }
        ],
    },
]

GOOGLE_STRUCTURE = [
    {
        "campaign_id": "21455930188",
        "campaign_name": "NKS | YT | In-Feed | Locksmith Intent",
        "start_day": 45,
        "daily_spend": 96.0,
        "adsets": [
            {
                "adset_id": "163842991055",
                "adset_name": "Custom Segment | Searches Key Programming",
                "frequency": 1.5,
                "ads": [
                    {"ad_id": "704829113355", "ad_name": "How We Stock 4,000 Blanks | 60s", "strength": 0.94, "video": True},
                    {"ad_id": "704829113356", "ad_name": "Locksmith Account Walkthrough | 30s", "strength": 1.18, "video": True},
                ],
            }
        ],
    },
    {
        "campaign_id": "21455930712",
        "campaign_name": "NKS | YT | In-Stream Skippable | Trade Audience",
        "start_day": 58,
        "daily_spend": 74.0,
        "adsets": [
            {
                "adset_id": "163842991812",
                "adset_name": "In-Market | Automotive Tools",
                "frequency": 1.2,
                "ads": [
                    {"ad_id": "704829118841", "ad_name": "Cut and Program in Under 5 Minutes | 30s", "strength": 1.05, "video": True},
                    {"ad_id": "704829118842", "ad_name": "Founder Cold Open | 20s", "strength": 1.46, "video": True},
                ],
            }
        ],
    },
]

# Cost per acquired account, before per-ad strength: where each channel started
# and where it has got to. Meta is beating the $550 benchmark; YouTube is not
# yet — the fixture deliberately shows one channel of each kind, so the verdict
# row demonstrates both states.
CAC_CURVE = {
    "meta": (640.0, 385.0),
    "youtube": (1010.0, 655.0),
}

# Revenue per acquired account on the first order (wholesale opening order,
# well above the $160 retail site AOV).
ORDER_VALUE = {
    "meta": (780.0, 240.0),
    "youtube": (700.0, 210.0),
}

CPM = {"meta": (14.5, 2.2), "youtube": (9.2, 1.6)}
CTR = {"meta": (0.0122, 0.0028), "youtube": (0.0071, 0.0018)}

# Days each channel went dark, as an offset from the first day. Exercises the
# empty-state paths: zero-spend days must not render as "$0 CAC".
BLACKOUTS = {"meta": set(), "youtube": {77, 78}}


def _poisson(rng: random.Random, mean: float) -> int:
    """Whole conversions from a fractional expectation, without numpy."""
    if mean <= 0:
        return 0
    if mean > 30:
        return max(0, int(round(rng.gauss(mean, math.sqrt(mean)))))
    limit = math.exp(-mean)
    count, product = 0, 1.0
    while True:
        count += 1
        product *= rng.random()
        if product <= limit:
            return count - 1


def _clamped_gauss(rng: random.Random, mean: float, sigma: float, floor: float) -> float:
    return max(floor, rng.gauss(mean, sigma))


def _ramp(day_index: int, start_day: int, total_days: int) -> float:
    """Spend ramps in over the first two weeks rather than switching on flat."""
    live_days = day_index - start_day
    if live_days < 0:
        return 0.0
    return min(1.0, 0.35 + 0.65 * live_days / 14.0)


def build_rows(today: Optional[dt.date] = None) -> Dict[str, List[Dict[str, Any]]]:
    """Generate every fixture row. Deterministic for a given `today`."""
    today = today or dt.date.today()
    rng = random.Random(SEED)
    first_day = today - dt.timedelta(days=DAYS - 1)

    daily: List[Dict[str, Any]] = []
    creatives: List[Dict[str, Any]] = []
    reach_rows: List[Dict[str, Any]] = []

    channel_impressions: Dict[str, Dict[dt.date, float]] = {"meta": {}, "youtube": {}}
    channel_frequency: Dict[str, float] = {}

    for channel, structure, account in (
        ("meta", META_STRUCTURE, META_ACCOUNT),
        ("youtube", GOOGLE_STRUCTURE, GOOGLE_ACCOUNT),
    ):
        cac_start, cac_end = CAC_CURVE[channel]
        value_mean, value_sigma = ORDER_VALUE[channel]
        cpm_mean, cpm_sigma = CPM[channel]
        ctr_mean, ctr_sigma = CTR[channel]
        frequency_weighted: List[float] = []
        frequency_weights: List[float] = []

        for campaign in structure:
            for adset in campaign["adsets"]:
                for ad in adset["ads"]:
                    creatives.append(
                        {
                            "channel": channel,
                            "ad_id": ad["ad_id"],
                            "ad_name": ad["ad_name"],
                            # Live connectors fill these in; mock mode leaves
                            # them empty and the UI shows a labelled placeholder
                            # rather than inventing an image.
                            "thumbnail_url": None,
                            "permalink": None,
                            "asset_type": "video" if ad["video"] else "image",
                        }
                    )

        for day_index in range(DAYS):
            day = first_day + dt.timedelta(days=day_index)
            if day_index in BLACKOUTS[channel]:
                continue
            progress = day_index / float(DAYS - 1)
            base_cac = cac_start + (cac_end - cac_start) * progress

            for campaign in structure:
                if day_index < campaign["start_day"]:
                    continue
                ramp = _ramp(day_index, campaign["start_day"], DAYS)
                ad_count = sum(len(a["ads"]) for a in campaign["adsets"])

                for adset in campaign["adsets"]:
                    for ad in adset["ads"]:
                        spend = (
                            campaign["daily_spend"]
                            / ad_count
                            * ramp
                            * _clamped_gauss(rng, 1.0, 0.18, 0.25)
                        )
                        spend = round(spend, 2)
                        if spend <= 0:
                            continue

                        cpm = _clamped_gauss(rng, cpm_mean, cpm_sigma, 3.0)
                        impressions = int(spend / cpm * 1000)
                        ctr = _clamped_gauss(rng, ctr_mean, ctr_sigma, 0.001)
                        clicks = int(impressions * ctr)
                        link_clicks = int(clicks * 0.86)
                        landing_page_views = int(link_clicks * 0.79)

                        effective_cac = base_cac * ad["strength"] * _clamped_gauss(
                            rng, 1.0, 0.12, 0.4
                        )
                        conversions = _poisson(rng, spend / effective_cac)
                        conversion_value = sum(
                            round(_clamped_gauss(rng, value_mean, value_sigma, 95.0), 2)
                            for _ in range(conversions)
                        )

                        row: Dict[str, Any] = {
                            "date": day.isoformat(),
                            "channel": channel,
                            "level": "ad",
                            "account_id": account,
                            "campaign_id": campaign["campaign_id"],
                            # The account-name prefix follows CLIENT_SHORT, so
                            # the demo export carries no client identifiers.
                            "campaign_name": campaign["campaign_name"].replace(
                                "NKS", C.CLIENT_SHORT
                            ),
                            "adset_id": adset["adset_id"],
                            "adset_name": adset["adset_name"],
                            "ad_id": ad["ad_id"],
                            "ad_name": ad["ad_name"],
                            "currency": "USD",
                            "spend": spend,
                            "impressions": impressions,
                            "clicks": clicks,
                            "link_clicks": link_clicks,
                            "landing_page_views": landing_page_views,
                            "conversions": float(conversions),
                            "conversion_value": round(conversion_value, 2),
                            "purchases": float(conversions),
                            "attribution_window": (
                                "7d_click,1d_view" if channel == "meta" else "click-through, 30d"
                            ),
                            "conversion_source": "purchase_proxy",
                            "conversion_action": (
                                "offsite_conversion.fb_pixel_purchase"
                                if channel == "meta"
                                else "Wholesale account signup"
                            ),
                            "synced_at": None,
                        }

                        if channel == "meta":
                            # Meta does not report a Google-style "video view",
                            # so that column stays NULL and the UI reports it as
                            # not available rather than as zero.
                            row["three_sec_views"] = int(impressions * _clamped_gauss(rng, 0.29, 0.05, 0.05))
                            if ad["video"]:
                                row["thruplays"] = int(row["three_sec_views"] * _clamped_gauss(rng, 0.31, 0.06, 0.02))
                                row["video_p25"] = int(impressions * _clamped_gauss(rng, 0.135, 0.03, 0.01))
                                row["video_p50"] = int(row["video_p25"] * 0.58)
                                row["video_p75"] = int(row["video_p25"] * 0.37)
                                row["video_p100"] = int(row["video_p25"] * 0.24)
                        else:
                            row["video_views"] = int(impressions * _clamped_gauss(rng, 0.221, 0.035, 0.02))
                            row["video_p25"] = int(impressions * _clamped_gauss(rng, 0.318, 0.04, 0.02))
                            row["video_p50"] = int(row["video_p25"] * 0.62)
                            row["video_p75"] = int(row["video_p25"] * 0.44)
                            row["video_p100"] = int(row["video_p25"] * 0.29)

                        daily.append(row)
                        channel_impressions[channel][day] = (
                            channel_impressions[channel].get(day, 0.0) + impressions
                        )
                        frequency_weighted.append(adset["frequency"] * impressions)
                        frequency_weights.append(impressions)

        total_weight = sum(frequency_weights) or 1.0
        channel_frequency[channel] = sum(frequency_weighted) / total_weight

    # Reach is fetched per window, never summed from days. Generate it for
    # exactly the windows the UI offers, which is also exactly what the live
    # connectors will request.
    for window in ranges.all_windows(today):
        for channel in ("meta", "youtube"):
            impressions = sum(
                value
                for day, value in channel_impressions[channel].items()
                if window.start <= day <= window.end
            )
            if impressions <= 0:
                continue
            # Unique reach grows sublinearly with window length in a small
            # addressable universe, which is what pushes Meta's frequency up.
            frequency = channel_frequency[channel] * (1.0 + math.log(max(window.days, 1)) * 0.16)
            reach_rows.append(
                {
                    "channel": channel,
                    "level": "account",
                    "entity_id": "",
                    "date_start": window.start.isoformat(),
                    "date_end": window.end.isoformat(),
                    "reach": int(impressions / max(frequency, 0.1)),
                }
            )

    return {"daily": daily, "creatives": creatives, "reach": reach_rows}


def load(conn=None, today: Optional[dt.date] = None) -> Dict[str, int]:
    """Write fixtures into the database. Safe to re-run — upserts by key."""
    own_connection = conn is None
    conn = conn or db.connect()
    db.init(conn)
    data = build_rows(today)

    counts = {
        "daily": db.upsert_daily(conn, data["daily"]),
        "creatives": db.upsert_creatives(conn, data["creatives"]),
        "reach": db.upsert_reach(conn, data["reach"]),
    }

    today = today or dt.date.today()
    end = ranges.last_complete_day(today)
    for channel in ("meta", "youtube"):
        run_id = db.start_sync(conn, channel, "fixture", end - dt.timedelta(days=DAYS - 1), end)
        db.finish_sync(conn, run_id, "mock", counts["daily"])
    db.set_setting(conn, "data_mode", "mock")

    if own_connection:
        conn.close()
    return counts


if __name__ == "__main__":
    result = load()
    print(
        "Seeded {daily} daily rows, {creatives} creatives, {reach} reach windows.".format(**result)
    )
