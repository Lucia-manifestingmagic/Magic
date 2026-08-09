"""Business constants for Noble Key Supply.

Every number here came from the client and drives a decision on the dashboard.
Nothing downstream hardcodes a value: change it here (or in .env) and the whole
dashboard, including the reference lines drawn on the charts, moves with it.
"""

from __future__ import annotations

import datetime as dt
import os
from typing import List, Optional, Tuple


def _f(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


# --- Demo mode -------------------------------------------------------------
# The static export (`make demo`) sets DEMO_MODE=1, which swaps the client's
# identity and real economics for round placeholder values. The dashboard is
# identical in every other respect. This exists so a shareable link can show
# the work without publishing a client's cost structure.
DEMO_MODE: bool = os.environ.get("DEMO_MODE", "0").strip() in {"1", "true", "yes"}


def _demo(real, demo):
    return demo if DEMO_MODE else real


def _s(name: str, default: str) -> str:
    """An env var set to an empty string means 'unset', not 'blank'.

    `CLIENT_NAME= python -m app.export` must still resolve to the default —
    os.environ.get would return "" here, which would silently blank the
    client name on the page.
    """
    return os.environ.get(name, "").strip() or default


CLIENT_NAME: str = _s("CLIENT_NAME", _demo("Noble Key Supply", "Acme Wholesale Supply"))
CLIENT_SHORT: str = _s("CLIENT_SHORT", _demo("NKS", "ACME"))
CLIENT_TAGLINE: str = _s(
    "CLIENT_TAGLINE", _demo("", "Sample data — this is a portfolio demo, not a real account.")
)


# --- The pass/fail line ----------------------------------------------------
# Sean's proven all-in cost to acquire a new locksmith account through human
# cold sales. Paid media only earns more budget when it beats this.
CAC_BENCHMARK: float = _f("CAC_BENCHMARK", _demo(550.0, 500.0))

# What the media plan is modelled against — tighter than the benchmark, so
# there is headroom before the benchmark is threatened.
CAC_TARGET: float = _f("CAC_TARGET", _demo(500.0, 450.0))

# How far above the benchmark still counts as "watch it" rather than "cut it".
CAC_WARN_MULTIPLIER: float = _f("CAC_WARN_MULTIPLIER", 1.15)

# --- Unit economics --------------------------------------------------------
GROSS_MARGIN: float = _f("GROSS_MARGIN", _demo(0.40, 0.42))
BASELINE_MONTHLY_REV: float = _f("BASELINE_MONTHLY_REV", _demo(240_000.0, 250_000.0))
ACCOUNT_LTV_GP: float = _f("ACCOUNT_LTV_GP", _demo(4_800.0, 5_000.0))
ACCOUNT_MONTHLY_GP: float = _f("ACCOUNT_MONTHLY_GP", _demo(240.0, 250.0))

# --- Spend plan ------------------------------------------------------------
# (through month N inclusive, monthly budget). The last entry runs forever.
SPEND_PLAN: List[Tuple[Optional[int], float]] = [
    (3, 10_000.0),
    (6, 15_000.0),
    (None, 20_000.0),
]

# Client-modelled break-even ROAS at each monthly spend level. Drawn as the
# reference line on the ROAS chart, interpolated for spend in between.
BREAKEVEN_ROAS_BY_SPEND: List[Tuple[float, float]] = [
    (10_000.0, 6.3),
    (15_000.0, 5.3),
    (20_000.0, 4.6),
]

# --- Historical baselines, for context only --------------------------------
# These are prior-period Google Ads figures the client already knows. They are
# comparison context, never mixed into the paid-social/YouTube numbers.
HISTORICAL = {
    "google_blended_roas": 11.94,
    "branded_search_roas": 63.94,
    "pmax_roas": 7.89,
    "shopping_roas": 1.03,  # broken campaign, being rebuilt
    "site_aov": 160.0,
    "site_conversion_rate": 0.0253,
}

# --- Program timing --------------------------------------------------------
# YYYY-MM of the first month of the media program. Unset means "assume this
# month is month 1", and PROGRAM_START_ASSUMED tells the UI to say so out loud
# rather than silently pacing against a guessed budget.
_raw_start = os.environ.get("PROGRAM_START_MONTH", "").strip()
PROGRAM_START_ASSUMED: bool = not _raw_start


def _parse_start(raw: str) -> dt.date:
    if raw:
        try:
            year, month = raw.split("-")
            return dt.date(int(year), int(month), 1)
        except (ValueError, TypeError):
            pass
    today = dt.date.today()
    return dt.date(today.year, today.month, 1)


PROGRAM_START: dt.date = _parse_start(_raw_start)


def program_month_index(day: dt.date) -> int:
    """1-based month number of the media program that `day` falls in."""
    months = (day.year - PROGRAM_START.year) * 12 + (day.month - PROGRAM_START.month)
    return months + 1


def planned_spend(day: dt.date) -> float:
    """Monthly spend target for the month `day` falls in."""
    index = program_month_index(day)
    if index < 1:
        return 0.0
    for through, budget in SPEND_PLAN:
        if through is None or index <= through:
            return budget
    return SPEND_PLAN[-1][1]


def breakeven_roas(monthly_spend: float) -> float:
    """Break-even ROAS at a given monthly spend, linearly interpolated.

    Clamped at both ends: below $10K uses the $10K figure, above $20K the $20K
    figure. The client supplied three points, not a curve, so we do not
    extrapolate past what they gave us.
    """
    points = BREAKEVEN_ROAS_BY_SPEND
    if monthly_spend <= points[0][0]:
        return points[0][1]
    if monthly_spend >= points[-1][0]:
        return points[-1][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 <= monthly_spend <= x1:
            span = x1 - x0
            if span == 0:
                return y0
            return y0 + (y1 - y0) * (monthly_spend - x0) / span
    return points[-1][1]


# --- Channels --------------------------------------------------------------
# Adding a channel means adding an entry here and a connector module. The UI
# and the metrics layer read this list and never name a channel directly.
CHANNELS = {
    "meta": {"label": "Meta", "detail": "Facebook + Instagram"},
    "youtube": {"label": "YouTube", "detail": "Google Ads video campaigns"},
}

# Ads below this spend in the selected period are excluded from the creative
# leaderboard, so a $12 ad with one lucky conversion cannot top the table.
CREATIVE_SPEND_FLOOR: float = _f("CREATIVE_SPEND_FLOOR", 100.0)

# Meta's small addressable universe here (automotive locksmiths) makes fatigue
# a live risk; above this frequency the dashboard raises a flag.
FREQUENCY_WARN: float = _f("FREQUENCY_WARN", 3.0)
