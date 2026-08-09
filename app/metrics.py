"""Derived metrics.

Two rules hold this file together, and both exist because the dashboard is
supposed to settle budget arguments:

1.  **Nothing is ever guessed.** Every metric is either a real number derived
    from stored raw values, or `None` with a plain-English reason attached. A
    `Metric` never carries a zero that means "we don't know", and never carries
    `inf` or `nan`.

2.  **Ratios are derived, never averaged.** A period's CAC is
    `sum(spend) / sum(conversions)` over the whole period — not the mean of the
    daily CACs. Averaging pre-computed ratios weights a $40 day the same as a
    $4,000 day and is the classic way an ads dashboard ends up wrong. The same
    applies to rolling averages: `rolling_totals()` rolls the *inputs*, and the
    ratio is computed from the rolled sums.

Everything here is pure — no database, no network — so it is directly testable.
"""

from __future__ import annotations

import calendar
import dataclasses
import datetime as dt
from typing import Any, Dict, Iterable, List, NamedTuple, Optional, Sequence, Set, Tuple

from . import constants as C


class Metric(NamedTuple):
    """A number, or an explicit absence with a reason the UI can show on hover."""

    value: Optional[float]
    reason: Optional[str] = None

    @property
    def known(self) -> bool:
        return self.value is not None


KNOWN = None  # readability: Metric(x) with no reason means the value is known


# Fields that can legitimately be added across rows and across days.
# `reach` is deliberately absent — see rolling_totals() and frequency().
ADDITIVE_FIELDS: Tuple[str, ...] = (
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
)


@dataclasses.dataclass
class Totals:
    """Summed raw counts for some slice of rows.

    `present` records which fields had at least one non-null value in the
    source rows. A field that no channel in the slice reports stays out of
    `present`, so downstream metrics resolve to "not reported by this channel"
    rather than to a zero that looks like real data.
    """

    spend: float = 0.0
    impressions: float = 0.0
    clicks: float = 0.0
    link_clicks: float = 0.0
    landing_page_views: float = 0.0
    conversions: float = 0.0
    conversion_value: float = 0.0
    purchases: float = 0.0
    video_views: float = 0.0
    video_p25: float = 0.0
    video_p50: float = 0.0
    video_p75: float = 0.0
    video_p100: float = 0.0
    thruplays: float = 0.0
    three_sec_views: float = 0.0
    present: Set[str] = dataclasses.field(default_factory=set)
    row_count: int = 0

    def has(self, field: str) -> bool:
        return field in self.present

    def add(self, other: "Totals") -> "Totals":
        out = Totals()
        for field in ADDITIVE_FIELDS:
            setattr(out, field, getattr(self, field) + getattr(other, field))
        out.present = self.present | other.present
        out.row_count = self.row_count + other.row_count
        return out


def sum_rows(rows: Iterable[Any]) -> Totals:
    """Sum any iterable of mappings (sqlite3.Row works) into a Totals."""
    totals = Totals()
    for row in rows:
        totals.row_count += 1
        for field in ADDITIVE_FIELDS:
            try:
                raw = row[field]
            except (KeyError, IndexError):
                continue
            if raw is None:
                continue
            totals.present.add(field)
            setattr(totals, field, getattr(totals, field) + float(raw))
    return totals


def _div(
    numerator: Optional[float],
    denominator: Optional[float],
    *,
    scale: float = 1.0,
    zero_reason: str,
    missing_reason: Optional[str] = None,
) -> Metric:
    """Divide, or explain why we can't. Never returns inf, nan or a fake zero."""
    if numerator is None or denominator is None:
        return Metric(None, missing_reason or zero_reason)
    if denominator == 0:
        return Metric(None, zero_reason)
    return Metric((numerator / denominator) * scale)


def _field(totals: Totals, name: str, channel_label: str = "this channel") -> Optional[float]:
    if not totals.has(name):
        return None
    return getattr(totals, name)


def derive(
    totals: Totals,
    *,
    reach: Optional[float] = None,
    reach_reason: Optional[str] = None,
    conversion_source: str = "purchase_proxy",
) -> Dict[str, Metric]:
    """Compute every derived metric from summed raw counts.

    `reach` must be a period-level reach for exactly the period `totals` covers.
    Pass None when we don't have one; frequency then reports why rather than
    being computed from a sum of daily reach values, which would be wrong.
    """
    spend = totals.spend
    no_spend = "No spend recorded in this period."

    conversions = _field(totals, "conversions")
    conversion_value = _field(totals, "conversion_value")
    impressions = _field(totals, "impressions")
    clicks = _field(totals, "clicks")
    link_clicks = _field(totals, "link_clicks")
    video_views = _field(totals, "video_views")

    # Conversion-rate denominator: link clicks where the channel reports them,
    # otherwise all clicks. Recorded in METRICS.md; surfaced in the tooltip.
    cvr_denominator = link_clicks if link_clicks is not None else clicks

    out: Dict[str, Metric] = {}

    out["spend"] = Metric(spend)
    out["impressions"] = Metric(impressions, None if impressions is not None else "Not reported.")
    out["clicks"] = Metric(clicks, None if clicks is not None else "Not reported.")
    out["conversions"] = Metric(
        conversions, None if conversions is not None else "No conversion data for this channel."
    )
    out["revenue"] = Metric(
        conversion_value,
        None if conversion_value is not None else "No revenue reported for this channel.",
    )

    out["cac"] = _div(
        spend,
        conversions,
        zero_reason=(
            "No accounts acquired in this period, so cost per account cannot be "
            "calculated. Spend to date is shown instead."
        ),
        missing_reason="This channel reports no conversion data.",
    )

    out["roas"] = _div(
        conversion_value,
        spend,
        zero_reason=no_spend,
        missing_reason="No revenue reported for this channel.",
    )

    # What Sean actually keeps: revenue at 40% gross margin, per dollar spent.
    out["profit_roas"] = _div(
        conversion_value * C.GROSS_MARGIN if conversion_value is not None else None,
        spend,
        zero_reason=no_spend,
        missing_reason="No revenue reported for this channel.",
    )

    out["cpm"] = _div(spend, impressions, scale=1000.0, zero_reason="No impressions recorded.")
    out["cpc"] = _div(spend, clicks, zero_reason="No clicks recorded.")
    out["ctr"] = _div(clicks, impressions, zero_reason="No impressions recorded.")
    out["cvr"] = _div(
        conversions,
        cvr_denominator,
        zero_reason="No clicks recorded.",
        missing_reason="No click data for this channel.",
    )
    out["aov"] = _div(
        conversion_value,
        conversions,
        zero_reason="No conversions in this period.",
        missing_reason="No revenue reported for this channel.",
    )
    out["cost_per_lpv"] = _div(
        spend,
        _field(totals, "landing_page_views"),
        zero_reason="No landing page views recorded.",
        missing_reason="Landing page views are not reported by this channel.",
    )

    # --- Video ---
    out["cpv"] = _div(
        spend,
        video_views,
        zero_reason="No video views recorded.",
        missing_reason="This channel does not report video views.",
    )
    out["view_rate"] = _div(
        video_views,
        impressions,
        zero_reason="No impressions recorded.",
        missing_reason="This channel does not report video views.",
    )
    for quartile in (25, 50, 75, 100):
        out["vtr_%d" % quartile] = _div(
            _field(totals, "video_p%d" % quartile),
            impressions,
            zero_reason="No impressions recorded.",
            missing_reason="This channel does not report video completion.",
        )

    # --- Meta-specific ---
    out["thumbstop"] = _div(
        _field(totals, "three_sec_views"),
        impressions,
        zero_reason="No impressions recorded.",
        missing_reason="Thumbstop rate is a Meta metric; not reported here.",
    )

    if reach is None:
        out["frequency"] = Metric(None, reach_reason or "No period-level reach stored for this date range.")
    else:
        out["frequency"] = _div(impressions, reach, zero_reason="Reach recorded as zero.")

    # --- LTV view: how long until an acquired account pays back its own CAC ---
    cac = out["cac"].value
    if cac is None:
        out["payback_months"] = Metric(None, "Needs a cost per account first.")
        out["ltv_to_cac"] = Metric(None, "Needs a cost per account first.")
    else:
        out["payback_months"] = _div(
            cac, C.ACCOUNT_MONTHLY_GP, zero_reason="Monthly gross profit per account is zero."
        )
        out["ltv_to_cac"] = _div(
            C.ACCOUNT_LTV_GP, cac, zero_reason="Cost per account is zero."
        )

    out["_conversion_source"] = Metric(None, conversion_source)
    return out


# ---------------------------------------------------------------------------
# Time series
# ---------------------------------------------------------------------------


def rolling_totals(
    series: Sequence[Tuple[dt.date, Totals]], window: int
) -> List[Tuple[dt.date, Totals]]:
    """Trailing-window sums of the raw inputs.

    Ratios are computed from these sums, never averaged across days. A 7-day
    rolling CAC is `sum(spend over 7d) / sum(conversions over 7d)`, which is the
    only version of that number that survives scrutiny.

    The first `window - 1` points are returned as partial windows, so an early
    campaign still plots from day one rather than starting with a gap. Callers
    that need full windows only can slice.
    """
    if window < 1:
        raise ValueError("window must be >= 1")
    out: List[Tuple[dt.date, Totals]] = []
    for index, (day, _) in enumerate(series):
        start = max(0, index - window + 1)
        bucket = Totals()
        for _, totals in series[start : index + 1]:
            bucket = bucket.add(totals)
        out.append((day, bucket))
    return out


def daily_series(rows: Iterable[Any]) -> List[Tuple[dt.date, Totals]]:
    """Group rows by date into one Totals per day, gaps included as empty days."""
    buckets: Dict[dt.date, List[Any]] = {}
    for row in rows:
        raw_date = row["date"]
        day = raw_date if isinstance(raw_date, dt.date) else dt.date.fromisoformat(str(raw_date))
        buckets.setdefault(day, []).append(row)
    if not buckets:
        return []
    ordered = sorted(buckets)
    first, last = ordered[0], ordered[-1]
    out: List[Tuple[dt.date, Totals]] = []
    day = first
    while day <= last:
        out.append((day, sum_rows(buckets.get(day, []))))
        day += dt.timedelta(days=1)
    return out


def delta(current: Optional[float], previous: Optional[float]) -> Metric:
    """Fractional change vs the preceding period of equal length."""
    if current is None or previous is None:
        return Metric(None, "No comparable prior period.")
    if previous == 0:
        return Metric(None, "Prior period was zero, so a percentage change is undefined.")
    return Metric((current - previous) / previous)


# ---------------------------------------------------------------------------
# Pacing
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class Pacing:
    month_start: dt.date
    days_in_month: int
    days_elapsed: int
    days_remaining: int
    mtd_spend: float
    planned_monthly: float
    projected_month_end: Metric
    pct_of_plan: Metric
    projected_vs_plan: Metric
    daily_budget_remaining: Metric


def pacing(mtd_spend: float, today: dt.date, planned_monthly: Optional[float] = None) -> Pacing:
    """Month-to-date spend against plan, and where the month lands at this rate.

    `days_elapsed` counts today as a full day only when it is the last day of
    the month; a partial today would otherwise inflate the projection every
    morning. Mid-month, the projection is built from completed days.
    """
    month_start = dt.date(today.year, today.month, 1)
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    is_last_day = today.day == days_in_month
    days_elapsed = today.day if is_last_day else today.day - 1
    days_remaining = days_in_month - days_elapsed
    plan = C.planned_spend(today) if planned_monthly is None else planned_monthly

    if days_elapsed <= 0:
        projected = Metric(None, "The month has no completed days yet.")
    else:
        projected = Metric(mtd_spend / days_elapsed * days_in_month)

    pct = _div(mtd_spend, plan, zero_reason="No monthly budget set for this month.")

    if projected.value is None or plan == 0:
        vs_plan = Metric(None, projected.reason or "No monthly budget set for this month.")
    else:
        vs_plan = Metric((projected.value - plan) / plan)

    if days_remaining <= 0:
        remaining_daily = Metric(None, "The month is over.")
    else:
        remaining_daily = Metric(max(plan - mtd_spend, 0.0) / days_remaining)

    return Pacing(
        month_start=month_start,
        days_in_month=days_in_month,
        days_elapsed=days_elapsed,
        days_remaining=days_remaining,
        mtd_spend=mtd_spend,
        planned_monthly=plan,
        projected_month_end=projected,
        pct_of_plan=pct,
        projected_vs_plan=vs_plan,
        daily_budget_remaining=remaining_daily,
    )


# ---------------------------------------------------------------------------
# The verdict — the one question the dashboard answers
# ---------------------------------------------------------------------------

STATE_GOOD = "good"
STATE_WARNING = "warning"
STATE_CRITICAL = "critical"
STATE_UNKNOWN = "unknown"


@dataclasses.dataclass
class Verdict:
    state: str
    headline: str
    detail: str
    recommendation: str


def _money(value: float) -> str:
    return "${:,.0f}".format(value)


def verdict(
    label: str,
    metrics: Dict[str, Metric],
    *,
    breakeven: Optional[float] = None,
    conversion_source: str = "purchase_proxy",
) -> Verdict:
    """Plain-language read on one channel: is it beating the $550 benchmark?

    Written for someone who is a numbers guy but not a marketer: it states the
    number, states the line it is being judged against, and names the action.
    """
    spend = metrics["spend"].value or 0.0
    conversions = metrics["conversions"].value
    cac = metrics["cac"].value
    roas = metrics["roas"].value
    noun = "accounts" if conversion_source == "verified_account" else "purchases"

    if spend <= 0:
        return Verdict(
            STATE_UNKNOWN,
            "%s is not running." % label,
            "No spend recorded in this period.",
            "Nothing to decide until the channel is live.",
        )

    if not conversions:
        # Below one benchmark CAC of spend you would not expect a conversion
        # yet, so "zero so far" is not evidence of failure.
        if spend < C.CAC_BENCHMARK:
            return Verdict(
                STATE_UNKNOWN,
                "%s has spent %s with no %s yet." % (label, _money(spend), noun),
                "That is less than one %s benchmark, so it is too early to judge."
                % _money(C.CAC_BENCHMARK),
                "Let it run until spend passes %s before reading anything into it."
                % _money(C.CAC_BENCHMARK),
            )
        return Verdict(
            STATE_CRITICAL,
            "%s has spent %s with no %s." % (label, _money(spend), noun),
            "That is %.1f times your %s benchmark with nothing to show."
            % (spend / C.CAC_BENCHMARK, _money(C.CAC_BENCHMARK)),
            "Pause and diagnose before spending more — check tracking first, then targeting.",
        )

    roas_clause = ""
    if roas is not None and breakeven is not None:
        if roas >= breakeven:
            roas_clause = " ROAS of %.1fx is above the %.1fx break-even." % (roas, breakeven)
        else:
            roas_clause = (
                " First-order ROAS of %.1fx is below the %.1fx break-even, so the return"
                " is coming from repeat orders rather than the first sale." % (roas, breakeven)
            )

    if cac is None:  # defensive; conversions > 0 means cac is computable
        return Verdict(
            STATE_UNKNOWN,
            "%s cost per account is unavailable." % label,
            metrics["cac"].reason or "",
            "No recommendation until the number is available.",
        )

    if cac <= C.CAC_TARGET:
        return Verdict(
            STATE_GOOD,
            "%s is acquiring accounts at %s." % (label, _money(cac)),
            "Below your %s benchmark, and below the %s plan target.%s"
            % (_money(C.CAC_BENCHMARK), _money(C.CAC_TARGET), roas_clause),
            "Recommend increasing budget.",
        )

    if cac <= C.CAC_BENCHMARK:
        return Verdict(
            STATE_GOOD,
            "%s is acquiring accounts at %s." % (label, _money(cac)),
            "Below your %s benchmark, above the %s plan target.%s"
            % (_money(C.CAC_BENCHMARK), _money(C.CAC_TARGET), roas_clause),
            "Recommend holding budget and tightening before scaling.",
        )

    if cac <= C.CAC_BENCHMARK * C.CAC_WARN_MULTIPLIER:
        return Verdict(
            STATE_WARNING,
            "%s is acquiring accounts at %s." % (label, _money(cac)),
            "Above your %s benchmark by %s.%s"
            % (_money(C.CAC_BENCHMARK), _money(cac - C.CAC_BENCHMARK), roas_clause),
            "Recommend holding budget flat — cut the weakest ads before adding spend.",
        )

    return Verdict(
        STATE_CRITICAL,
        "%s is acquiring accounts at %s." % (label, _money(cac)),
        "That is %.0f%% above your %s benchmark, so cold sales is currently cheaper.%s"
        % ((cac / C.CAC_BENCHMARK - 1) * 100, _money(C.CAC_BENCHMARK), roas_clause),
        "Recommend cutting budget here and rebuilding the campaign.",
    )
