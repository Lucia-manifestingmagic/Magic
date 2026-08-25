"""Tests for the metric calculations.

These are the numbers the dashboard exists to defend, so the cases that matter
most are the awkward ones: zero conversions, a channel that does not report a
field at all, and the difference between a correctly-weighted period ratio and
a naive average of daily ratios.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app import constants as C
from app import db, metrics, ranges
from app.connectors import base


def row(**overrides):
    base_row = {
        "date": "2026-07-01",
        "channel": "meta",
        "spend": 0.0,
        "impressions": 0,
        "clicks": 0,
        "link_clicks": 0,
        "landing_page_views": 0,
        "conversions": 0.0,
        "conversion_value": 0.0,
        "purchases": 0.0,
        "video_views": None,
        "video_p25": None,
        "video_p50": None,
        "video_p75": None,
        "video_p100": None,
        "thruplays": None,
        "three_sec_views": None,
    }
    base_row.update(overrides)
    return base_row


# --- CAC -------------------------------------------------------------------


def test_cac_is_spend_over_conversions():
    totals = metrics.sum_rows([row(spend=4120.0, conversions=10.0)])
    assert metrics.derive(totals)["cac"].value == pytest.approx(412.0)


def test_cac_is_none_with_a_reason_when_there_are_no_conversions():
    totals = metrics.sum_rows([row(spend=900.0, conversions=0.0)])
    cac = metrics.derive(totals)["cac"]
    assert cac.value is None
    assert "No accounts acquired" in cac.reason


def test_cac_never_returns_infinity_or_nan():
    for spend, conversions in ((0.0, 0.0), (500.0, 0.0), (0.0, 3.0)):
        value = metrics.derive(metrics.sum_rows([row(spend=spend, conversions=conversions)]))["cac"].value
        assert value is None or (value == value and value not in (float("inf"), float("-inf")))


def test_cac_over_a_period_is_weighted_not_averaged():
    """The whole point of deriving ratios from summed inputs.

    Day one: $100 buys 1 account ($100 CAC). Day two: $900 buys 1 ($900 CAC).
    The true period CAC is $1000/2 = $500. Averaging the two daily figures
    gives $500 here only by coincidence of equal conversions, so use unequal
    ones: $100 -> 1 account and $900 -> 2 accounts is $333, while the naive
    average of $100 and $450 is $275.
    """
    rows = [row(spend=100.0, conversions=1.0), row(spend=900.0, conversions=2.0)]
    period_cac = metrics.derive(metrics.sum_rows(rows))["cac"].value
    naive_average = (100.0 / 1.0 + 900.0 / 2.0) / 2
    assert period_cac == pytest.approx(1000.0 / 3.0)
    assert period_cac != pytest.approx(naive_average)


# --- ROAS ------------------------------------------------------------------


def test_roas_and_profit_adjusted_roas():
    totals = metrics.sum_rows([row(spend=1000.0, conversion_value=6300.0)])
    derived = metrics.derive(totals)
    assert derived["roas"].value == pytest.approx(6.3)
    assert derived["profit_roas"].value == pytest.approx(6.3 * C.GROSS_MARGIN)


def test_roas_needs_spend():
    derived = metrics.derive(metrics.sum_rows([row(spend=0.0, conversion_value=500.0)]))
    assert derived["roas"].value is None
    assert "No spend" in derived["roas"].reason


# --- missing vs zero -------------------------------------------------------


def test_a_field_no_channel_reports_is_unavailable_not_zero():
    """Meta reports no video_views, so CPV must say so rather than show $0."""
    derived = metrics.derive(metrics.sum_rows([row(spend=500.0, video_views=None)]))
    assert derived["cpv"].value is None
    assert "does not report" in derived["cpv"].reason


def test_a_reported_zero_is_still_a_zero():
    derived = metrics.derive(metrics.sum_rows([row(spend=500.0, impressions=1000, clicks=0)]))
    assert derived["ctr"].value == 0.0
    assert derived["cpc"].value is None  # dividing by zero clicks, not a zero CPC


# --- frequency -------------------------------------------------------------


def test_frequency_requires_a_period_level_reach():
    totals = metrics.sum_rows([row(impressions=30000)])
    assert metrics.derive(totals)["frequency"].value is None
    assert metrics.derive(totals, reach=10000)["frequency"].value == pytest.approx(3.0)


# --- rolling windows -------------------------------------------------------


def test_rolling_totals_sums_inputs_then_divides():
    days = []
    start = dt.date(2026, 7, 1)
    for index in range(7):
        totals = metrics.Totals()
        totals.spend = 100.0
        totals.conversions = 1.0 if index == 6 else 0.0
        totals.present = {"spend", "conversions"}
        days.append((start + dt.timedelta(days=index), totals))

    rolled = metrics.rolling_totals(days, 7)
    last_window = rolled[-1][1]
    assert last_window.spend == pytest.approx(700.0)
    assert metrics.derive(last_window)["cac"].value == pytest.approx(700.0)
    # Six of the seven days had no conversions at all; a mean of daily CACs
    # would have been undefined for six of them.
    assert metrics.derive(days[0][1])["cac"].value is None


def test_daily_series_fills_gaps_as_empty_days():
    rows = [row(date="2026-07-01", spend=10.0), row(date="2026-07-04", spend=20.0)]
    series = metrics.daily_series(rows)
    assert [day.isoformat() for day, _ in series] == [
        "2026-07-01", "2026-07-02", "2026-07-03", "2026-07-04"
    ]
    assert series[1][1].spend == 0.0


# --- pacing ----------------------------------------------------------------


def test_pacing_projects_from_completed_days_only():
    pace = metrics.pacing(5000.0, dt.date(2026, 8, 11), planned_monthly=10000.0)
    assert pace.days_elapsed == 10          # today is not complete
    assert pace.days_in_month == 31
    assert pace.projected_month_end.value == pytest.approx(5000.0 / 10 * 31)
    assert pace.pct_of_plan.value == pytest.approx(0.5)


def test_pacing_on_the_first_of_the_month_has_nothing_to_project():
    pace = metrics.pacing(0.0, dt.date(2026, 8, 1), planned_monthly=10000.0)
    assert pace.projected_month_end.value is None
    assert "no completed days" in pace.projected_month_end.reason


# --- constants -------------------------------------------------------------


def test_breakeven_roas_interpolates_and_clamps():
    assert C.breakeven_roas(10000) == pytest.approx(6.3)
    assert C.breakeven_roas(20000) == pytest.approx(4.6)
    assert C.breakeven_roas(12500) == pytest.approx(5.8)
    assert C.breakeven_roas(4000) == pytest.approx(6.3)
    assert C.breakeven_roas(50000) == pytest.approx(4.6)


def test_spend_plan_steps_up_with_the_program_month():
    start = C.PROGRAM_START
    assert C.planned_spend(start) == 10000.0
    assert C.planned_spend(_add_months(start, 3)) == 15000.0
    assert C.planned_spend(_add_months(start, 6)) == 20000.0
    assert C.planned_spend(_add_months(start, 24)) == 20000.0


def _add_months(day: dt.date, count: int) -> dt.date:
    month_index = day.month - 1 + count
    return dt.date(day.year + month_index // 12, month_index % 12 + 1, 1)


# --- micros ----------------------------------------------------------------


def test_google_micros_convert_to_currency():
    assert base.micros_to_currency(12_345_678) == pytest.approx(12.345678)
    assert base.micros_to_currency("9600000") == pytest.approx(9.6)
    assert base.micros_to_currency(0) == 0.0


def test_missing_micros_are_none_not_zero():
    assert base.micros_to_currency(None) is None
    assert base.micros_to_currency("") is None
    assert base.micros_to_currency("not a number") is None


def test_meta_actions_are_summed_only_for_the_chosen_action_type():
    actions = [
        {"action_type": "landing_page_view", "value": "412"},
        {"action_type": "offsite_conversion.fb_pixel_purchase", "value": "9"},
        {"action_type": "omni_purchase", "value": "11"},
    ]
    assert base.sum_actions(actions, ["offsite_conversion.fb_pixel_purchase"]) == 9.0
    assert base.sum_actions(actions, ["nonexistent"]) == 0.0
    assert base.sum_actions(None, ["anything"]) is None


# --- verdict ---------------------------------------------------------------


def _verdict_for(spend, conversions, revenue=0.0):
    totals = metrics.sum_rows(
        [row(spend=spend, conversions=conversions, conversion_value=revenue)]
    )
    return metrics.verdict("Meta", metrics.derive(totals), breakeven=6.3)


def test_verdict_recommends_scaling_below_the_target():
    result = _verdict_for(4120.0, 10.0)
    assert result.state == metrics.STATE_GOOD
    assert "increasing budget" in result.recommendation


def test_verdict_is_critical_well_above_the_benchmark():
    result = _verdict_for(9000.0, 10.0)
    assert result.state == metrics.STATE_CRITICAL
    assert "cutting budget" in result.recommendation


def test_verdict_holds_just_above_the_benchmark():
    result = _verdict_for(6000.0, 10.0)  # $600 CAC, inside the 1.15x band
    assert result.state == metrics.STATE_WARNING


def test_verdict_says_too_early_below_one_benchmark_of_spend():
    result = _verdict_for(300.0, 0.0)
    assert result.state == metrics.STATE_UNKNOWN
    assert "too early" in result.detail.lower()


def test_verdict_is_critical_when_spend_passes_a_benchmark_with_nothing_to_show():
    result = _verdict_for(2200.0, 0.0)
    assert result.state == metrics.STATE_CRITICAL


# --- ranges ----------------------------------------------------------------


def test_windows_end_on_the_last_complete_day():
    window = ranges.resolve("28d", dt.date(2026, 8, 9))
    assert window.end == dt.date(2026, 8, 8)
    assert window.start == dt.date(2026, 7, 12)
    assert window.days == 28


def test_previous_window_is_equal_length_and_immediately_prior():
    window = ranges.resolve("7d", dt.date(2026, 8, 9))
    prior = window.previous()
    assert prior.end == window.start - dt.timedelta(days=1)
    assert prior.days == window.days


# --- storage ---------------------------------------------------------------


def test_upserts_are_idempotent():
    conn = db.connect(":memory:")
    db.init(conn)
    record = {
        "date": "2026-07-01", "channel": "meta", "level": "ad",
        "campaign_id": "1", "adset_id": "2", "ad_id": "3",
        "spend": 100.0, "conversions": 1.0,
    }
    db.upsert_daily(conn, [record])
    db.upsert_daily(conn, [dict(record, spend=125.0, conversions=2.0)])

    rows = list(conn.execute("SELECT spend, conversions FROM daily_metrics"))
    assert len(rows) == 1                       # restated, not duplicated
    assert rows[0]["spend"] == pytest.approx(125.0)


def test_reach_is_only_returned_for_an_exact_window():
    conn = db.connect(":memory:")
    db.init(conn)
    db.upsert_reach(conn, [{
        "channel": "meta", "level": "account", "entity_id": "",
        "date_start": "2026-07-12", "date_end": "2026-08-08", "reach": 40000,
    }])
    assert db.fetch_reach(conn, "meta", dt.date(2026, 7, 12), dt.date(2026, 8, 8)) == 40000
    assert db.fetch_reach(conn, "meta", dt.date(2026, 7, 13), dt.date(2026, 8, 8)) is None


# --- link in bio -----------------------------------------------------------


def bio_row(**overrides):
    base_row = {
        "date": "2026-08-01",
        "source": "instagram",
        "link_clicks": 0,
        "sessions": 0,
        "new_sessions": 0,
        "orders": 0.0,
        "revenue": 0.0,
    }
    base_row.update(overrides)
    return base_row


def test_click_to_visit_is_sessions_over_clicks():
    totals = metrics.sum_bio([bio_row(link_clicks=1000, sessions=770)])
    derived = metrics.derive_bio(totals)
    assert derived["click_to_visit"].value == pytest.approx(0.77)
    assert derived["lost_clicks"].value == pytest.approx(230)


def test_bio_rate_is_weighted_across_days_not_averaged():
    """Same rule as CAC: sum both sides, then divide once."""
    rows = [bio_row(link_clicks=100, sessions=90), bio_row(date="2026-08-02", link_clicks=900, sessions=450)]
    period = metrics.derive_bio(metrics.sum_bio(rows))["click_to_visit"].value
    naive = (0.90 + 0.50) / 2
    assert period == pytest.approx(540 / 1000.0)
    assert period != pytest.approx(naive)


def test_bio_needs_both_sides_to_compare():
    """Clicks without sessions cannot produce a rate or a loss figure."""
    totals = metrics.sum_bio([{"date": "2026-08-01", "link_clicks": 500}])
    derived = metrics.derive_bio(totals)
    assert derived["click_to_visit"].value is None
    assert "sessions" in derived["click_to_visit"].reason
    assert derived["lost_clicks"].value is None


def test_bio_lost_clicks_never_goes_negative():
    """Analytics can out-count the link tool; that is not negative loss."""
    derived = metrics.derive_bio(metrics.sum_bio([bio_row(link_clicks=100, sessions=140)]))
    assert derived["lost_clicks"].value == 0.0


def test_bio_traffic_is_not_in_the_paid_tables():
    """Bio traffic carries no spend, so it must never reach blended CAC."""
    conn = db.connect(":memory:")
    db.init(conn)
    db.upsert_bio_link(conn, [bio_row(link_clicks=900, sessions=700, orders=6.0)])
    paid = db.fetch_rows(conn, dt.date(2026, 8, 1), dt.date(2026, 8, 1))
    assert paid == []
    assert metrics.sum_rows(paid).conversions == 0.0


def test_bio_upserts_are_idempotent():
    conn = db.connect(":memory:")
    db.init(conn)
    db.upsert_bio_link(conn, [bio_row(link_clicks=100)])
    db.upsert_bio_link(conn, [bio_row(link_clicks=250)])
    rows = db.fetch_bio_link(conn, dt.date(2026, 8, 1), dt.date(2026, 8, 1))
    assert len(rows) == 1
    assert rows[0]["link_clicks"] == 250
