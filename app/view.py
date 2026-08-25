"""Assembles the dashboard view model.

Everything the page shows is computed here, in one pass, from the database.
The template renders; it does not calculate. That keeps every number on screen
traceable to a stored raw value and a formula in METRICS.md.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import constants as C
from . import db, metrics, ranges
from .metrics import Metric, Totals

HEADLINE_METRICS = ("cac", "roas", "spend", "conversions")


def _metric_json(metric: Metric) -> Dict[str, Any]:
    return {"value": metric.value, "reason": metric.reason}


def _metrics_json(bundle: Dict[str, Metric]) -> Dict[str, Any]:
    return {name: _metric_json(metric) for name, metric in bundle.items()}


def _rows_for(rows: Sequence[sqlite3.Row], channel: Optional[str]) -> List[sqlite3.Row]:
    if channel is None:
        return list(rows)
    return [row for row in rows if row["channel"] == channel]


def _series_points(
    rows: Sequence[sqlite3.Row], window: ranges.Window, roll: int = 7
) -> List[Dict[str, Any]]:
    """Daily spend plus rolling CAC/ROAS.

    The rolling values come from `rolling_totals`, which sums spend and
    conversions across the window and divides once. Averaging seven daily CACs
    would weight a $40 Sunday the same as a $400 Tuesday.
    """
    daily = metrics.daily_series(rows)
    if not daily:
        return []
    by_day = {day: totals for day, totals in daily}
    filled: List[Tuple[dt.date, Totals]] = []
    day = window.start
    while day <= window.end:
        filled.append((day, by_day.get(day, Totals())))
        day += dt.timedelta(days=1)

    rolled = metrics.rolling_totals(filled, roll)
    points: List[Dict[str, Any]] = []
    for (day, totals), (_, window_totals) in zip(filled, rolled):
        derived = metrics.derive(totals)
        rolling = metrics.derive(window_totals)
        points.append(
            {
                "date": day.isoformat(),
                "spend": totals.spend,
                "conversions": totals.conversions,
                "revenue": totals.conversion_value,
                "cac": _metric_json(rolling["cac"]),
                "roas": _metric_json(rolling["roas"]),
                "cac_daily": _metric_json(derived["cac"]),
            }
        )
    return points


def _channel_block(
    conn: sqlite3.Connection,
    rows: Sequence[sqlite3.Row],
    prior_rows: Sequence[sqlite3.Row],
    window: ranges.Window,
    channel: Optional[str],
    label: str,
    detail: str,
    breakeven: float,
) -> Dict[str, Any]:
    current = _rows_for(rows, channel)
    prior = _rows_for(prior_rows, channel)
    totals = metrics.sum_rows(current)
    prior_totals = metrics.sum_rows(prior)

    if channel is None:
        reach: Optional[int] = None
        reach_reason = (
            "Reach cannot be added across platforms — the same locksmith may see "
            "both, and neither API reports the overlap. Frequency is shown per "
            "channel instead."
        )
    else:
        reach = db.fetch_reach(conn, channel, window.start, window.end)
        reach_reason = (
            "No period-level reach stored for this window. Reach is not additive "
            "across days, so it is never summed from daily values."
        )

    derived = metrics.derive(totals, reach=reach, reach_reason=reach_reason)
    prior_derived = metrics.derive(prior_totals)

    deltas = {
        name: _metric_json(metrics.delta(derived[name].value, prior_derived[name].value))
        for name in HEADLINE_METRICS
    }

    verdict = metrics.verdict(label, derived, breakeven=breakeven)
    frequency = derived["frequency"].value

    return {
        "key": channel or "blended",
        "label": label,
        "detail": detail,
        "is_blended": channel is None,
        "metrics": _metrics_json(derived),
        "deltas": deltas,
        "verdict": {
            "state": verdict.state,
            "headline": verdict.headline,
            "detail": verdict.detail,
            "recommendation": verdict.recommendation,
        },
        "frequency_flag": frequency is not None and frequency > C.FREQUENCY_WARN,
        "series": _series_points(current, window),
        "row_count": totals.row_count,
    }


def _group(
    rows: Sequence[sqlite3.Row], key_fields: Sequence[str], name_field: str
) -> List[Dict[str, Any]]:
    buckets: Dict[Tuple[Any, ...], List[sqlite3.Row]] = {}
    for row in rows:
        key = tuple(row[field] for field in key_fields)
        buckets.setdefault(key, []).append(row)

    out: List[Dict[str, Any]] = []
    for key, group_rows in buckets.items():
        totals = metrics.sum_rows(group_rows)
        derived = metrics.derive(totals)
        daily = metrics.daily_series(group_rows)
        out.append(
            {
                "key": "|".join(str(part) for part in key),
                "name": group_rows[0][name_field],
                "channel": group_rows[0]["channel"],
                "campaign_name": group_rows[0]["campaign_name"],
                "metrics": _metrics_json(derived),
                "spend": totals.spend,
                "conversions": totals.conversions,
                "cac": derived["cac"].value,
                "roas": derived["roas"].value,
                "sparkline": [totals_for_day.spend for _, totals_for_day in daily],
            }
        )
    return out


def _creative_leaderboard(
    rows: Sequence[sqlite3.Row], creatives: Dict[str, sqlite3.Row]
) -> Dict[str, Any]:
    ads = _group(rows, ("channel", "ad_id"), "ad_name")
    qualified = [ad for ad in ads if ad["spend"] >= C.CREATIVE_SPEND_FLOOR]
    excluded = len(ads) - len(qualified)

    for ad in qualified:
        creative = creatives.get(ad["key"].replace("|", ":"))
        ad["thumbnail_url"] = creative["thumbnail_url"] if creative else None
        ad["permalink"] = creative["permalink"] if creative else None
        ad["asset_type"] = creative["asset_type"] if creative else None

    # An ad that spent real money and acquired nobody is the worst performer,
    # not an unrankable one. It sorts last with its CAC shown as unavailable.
    def rank(ad: Dict[str, Any]) -> Tuple[int, float]:
        if ad["cac"] is None:
            return (1, -ad["spend"])
        return (0, ad["cac"])

    ordered = sorted(qualified, key=rank)
    return {
        "best": ordered[:5],
        "worst": list(reversed(ordered[-5:])) if len(ordered) > 5 else [],
        "spend_floor": C.CREATIVE_SPEND_FLOOR,
        "excluded_count": excluded,
        "total_count": len(ads),
    }


def _bio_block(conn: sqlite3.Connection, window: ranges.Window, prior: ranges.Window) -> Dict[str, Any]:
    """Link-in-bio traffic: taps on the link, and visits that actually landed."""
    rows = db.fetch_bio_link(conn, window.start, window.end)
    prior_rows = db.fetch_bio_link(conn, prior.start, prior.end)

    totals = metrics.sum_bio(rows)
    derived = metrics.derive_bio(totals)
    prior_derived = metrics.derive_bio(metrics.sum_bio(prior_rows))

    series = []
    for day, day_totals in metrics.bio_series(rows):
        series.append({
            "date": day.isoformat(),
            "clicks": day_totals.link_clicks,
            "sessions": day_totals.sessions,
        })

    rate = derived["click_to_visit"].value
    providers = sorted({r["provider"] for r in rows if r["provider"]})

    return {
        "has_data": totals.row_count > 0,
        "source": C.BIO_LINK_SOURCE,
        "metrics": _metrics_json(derived),
        "deltas": {
            name: _metric_json(metrics.delta(derived[name].value, prior_derived[name].value))
            for name in ("link_clicks", "sessions", "orders")
        },
        "series": series,
        "warn": rate is not None and rate < C.BIO_CLICK_TO_VISIT_WARN,
        "warn_threshold": C.BIO_CLICK_TO_VISIT_WARN,
        "providers": providers,
    }


def _health(conn: sqlite3.Connection, rows: Sequence[sqlite3.Row]) -> Dict[str, Any]:
    syncs = []
    for row in db.latest_sync(conn):
        syncs.append(
            {
                "channel": row["channel"],
                "label": C.CHANNELS.get(row["channel"], {}).get("label", row["channel"]),
                "kind": row["kind"],
                "status": row["status"],
                "finished_at": row["finished_at"],
                "rows_upserted": row["rows_upserted"],
                "error": row["error"],
            }
        )

    windows = sorted({row["attribution_window"] for row in rows if row["attribution_window"]})
    actions = sorted({row["conversion_action"] for row in rows if row["conversion_action"]})
    sources = sorted({row["conversion_source"] for row in rows if row["conversion_source"]})
    return {
        "syncs": syncs,
        "attribution_windows": windows,
        "conversion_actions": actions,
        "conversion_proxied": "verified_account" not in sources,
    }


def build(
    conn: sqlite3.Connection, range_key: str = ranges.DEFAULT_RANGE, today: Optional[dt.date] = None
) -> Dict[str, Any]:
    today = today or dt.date.today()
    window = ranges.resolve(range_key, today)
    prior = window.previous()

    rows = db.fetch_rows(conn, window.start, window.end)
    prior_rows = db.fetch_rows(conn, prior.start, prior.end)
    creatives = db.fetch_creatives(conn)

    month_start, _ = ranges.month_bounds(today)
    mtd_rows = db.fetch_rows(conn, month_start, ranges.last_complete_day(today))
    mtd_spend = metrics.sum_rows(mtd_rows).spend
    pace = metrics.pacing(mtd_spend, today)
    breakeven = C.breakeven_roas(pace.planned_monthly)

    blocks = [
        _channel_block(
            conn, rows, prior_rows, window, key, meta["label"], meta["detail"], breakeven
        )
        for key, meta in C.CHANNELS.items()
    ]
    blended = _channel_block(
        conn, rows, prior_rows, window, None, "Both channels", "Meta + YouTube", breakeven
    )

    return {
        "generated_at": dt.datetime.now().strftime("%b %-d, %Y at %-I:%M %p"),
        "data_mode": db.get_setting(conn, "data_mode", "mock"),
        "client": {
            "name": C.CLIENT_NAME,
            "short": C.CLIENT_SHORT,
            "tagline": C.CLIENT_TAGLINE,
            "demo": C.DEMO_MODE,
        },
        "range_key": window.key,
        "ranges": [
            {"key": key, "label": label, "selected": key == window.key}
            for key, label in ranges.RANGES.items()
        ],
        "window": {
            "start": window.start.isoformat(),
            "end": window.end.isoformat(),
            "label": window.label,
            "days": window.days,
            "pretty": "%s – %s"
            % (window.start.strftime("%b %-d"), window.end.strftime("%b %-d, %Y")),
        },
        "prior_window": {"pretty": "%s – %s" % (prior.start.strftime("%b %-d"), prior.end.strftime("%b %-d"))},
        "channels": blocks,
        "blended": blended,
        "pacing": {
            "mtd_spend": pace.mtd_spend,
            "planned_monthly": pace.planned_monthly,
            "days_elapsed": pace.days_elapsed,
            "days_remaining": pace.days_remaining,
            "days_in_month": pace.days_in_month,
            "projected": _metric_json(pace.projected_month_end),
            "pct_of_plan": _metric_json(pace.pct_of_plan),
            "projected_vs_plan": _metric_json(pace.projected_vs_plan),
            "daily_budget_remaining": _metric_json(pace.daily_budget_remaining),
            "month_label": today.strftime("%B %Y"),
            "program_month": C.program_month_index(today),
            "start_assumed": C.PROGRAM_START_ASSUMED,
        },
        "benchmarks": {
            "cac": C.CAC_BENCHMARK,
            "cac_target": C.CAC_TARGET,
            "breakeven_roas": breakeven,
            "gross_margin": C.GROSS_MARGIN,
            "frequency_warn": C.FREQUENCY_WARN,
            "bio_click_to_visit_warn": C.BIO_CLICK_TO_VISIT_WARN,
            "account_ltv_gp": C.ACCOUNT_LTV_GP,
            "account_monthly_gp": C.ACCOUNT_MONTHLY_GP,
        },
        "campaigns": sorted(
            _group(rows, ("channel", "campaign_id"), "campaign_name"),
            key=lambda item: -item["spend"],
        ),
        "bio": _bio_block(conn, window, prior),
        "creatives": _creative_leaderboard(rows, creatives),
        "health": _health(conn, rows),
    }
