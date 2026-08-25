# Metric definitions

Every number on the dashboard is defined here. If a figure on screen is not in
this file, that is a bug — the dashboard exists to settle budget arguments, and
a number nobody can trace is worse than no number.

All formulas live in [`app/metrics.py`](app/metrics.py) and are covered by
[`tests/test_metrics.py`](tests/test_metrics.py).

## Two rules that shape everything else

**1. Ratios are derived from period totals, never averaged across days.**

A period's cost per account is `sum(spend) / sum(accounts)` over the whole
period. It is *not* the mean of the daily figures. Averaging pre-computed
ratios weights a $40 Sunday the same as a $4,000 Tuesday.

| | $ spend | Accounts | CAC |
|---|---|---|---|
| Mon | 100 | 1 | $100 |
| Tue | 900 | 2 | $450 |
| **Period** | **1,000** | **3** | **$333** ✅ |
| Naive average of daily CACs | | | $275 ❌ |

The same applies to the 7-day rolling lines on the charts: `rolling_totals()`
sums spend and conversions across the trailing window, then divides once.

**2. A missing number is never a zero.**

Each metric is a `Metric(value, reason)`. When the value cannot be computed,
`value` is `None` and `reason` explains why in plain English — shown on the
dashboard as `—` with the reason on hover or tap. There is no code path that
turns absent data into `0`, `Infinity`, or `NaN`.

The distinction that matters: a field the channel *never reports* (Meta does not
report Google-style video views) resolves to "not reported by this channel". A
field that was reported as zero (1,000 impressions, 0 clicks) gives a real
`0.0%` CTR — but CPC still resolves to `—`, because dividing by zero clicks is
undefined, not free.

## Windows

**Every analysis window ends on the last complete day** (yesterday). Both
platforms are still writing to today's numbers while today is happening, and a
partial day drags CAC upward every morning. Reporting through yesterday means
the figure at 8am matches the figure at 8pm. The end date is stated in the data
health footer.

| Range | Window |
|---|---|
| Last 7 days | the 7 days ending yesterday |
| Last 28 days | the 28 days ending yesterday |
| Last 90 days | the 90 days ending yesterday |
| Month to date | 1st of the month through yesterday |

Deltas compare against the equal-length window immediately before.

## Core metrics

| Metric | Formula | Notes |
|---|---|---|
| Spend | `Σ spend` | Meta: `spend`. Google: `cost_micros ÷ 1,000,000`. |
| New accounts | `Σ conversions` | See *Conversions* below — currently proxied. |
| **Cost per new account (CAC)** | `Σ spend ÷ Σ conversions` | The headline. Judged against the **$550** cold-sales benchmark. `—` when there are no conversions. |
| Revenue | `Σ conversion_value` | First-order revenue attributed by the platform. |
| **ROAS** | `Σ revenue ÷ Σ spend` | Plotted against the break-even reference line. |
| **Profit-adjusted ROAS** | `(Σ revenue × 0.40) ÷ Σ spend` | Return on gross profit — what the business actually keeps. |
| Average order value | `Σ revenue ÷ Σ conversions` | |
| CPM | `(Σ spend ÷ Σ impressions) × 1000` | |
| CPC | `Σ spend ÷ Σ clicks` | |
| CTR | `Σ clicks ÷ Σ impressions` | |
| Conversion rate | `Σ conversions ÷ Σ link_clicks` | Falls back to all clicks where the channel does not report link clicks. |
| Cost per landing page view | `Σ spend ÷ Σ landing_page_views` | Meta only; `—` on YouTube. |

## Benchmarks and reference lines

| Constant | Value | Where it appears |
|---|---|---|
| `CAC_BENCHMARK` | $550 | Reference line on the CAC chart; the green/amber/red boundary |
| `CAC_TARGET` | $500 | The plan target; below it, the recommendation becomes "increase budget" |
| `CAC_WARN_MULTIPLIER` | 1.15 | Above $550 but within $632.50 is amber, not red |
| `GROSS_MARGIN` | 40% | Profit-adjusted ROAS |
| `ACCOUNT_LTV_GP` | $4,800 | LTV : CAC ratio |
| `ACCOUNT_MONTHLY_GP` | $240 | Payback months |
| `BREAKEVEN_ROAS` | 6.3 @ $10K · 5.3 @ $15K · 4.6 @ $20K | Reference line on the ROAS chart |

Break-even ROAS is **linearly interpolated** between the three supplied points
and **clamped** outside them — the client gave three points, not a curve, so we
do not extrapolate past what they gave us.

Payback months = `CAC ÷ $240`. LTV : CAC = `$4,800 ÷ CAC`.

## The verdict

Per channel, in this order:

| Condition | State | Recommendation |
|---|---|---|
| No spend | unknown | Nothing to decide |
| No conversions **and** spend < $550 | unknown | Too early — less than one benchmark CAC spent |
| No conversions **and** spend ≥ $550 | critical | Pause and diagnose; check tracking first |
| CAC ≤ $500 | good | Increase budget |
| $500 < CAC ≤ $550 | good | Hold and tighten before scaling |
| $550 < CAC ≤ $632.50 | warning | Hold flat; cut the weakest ads |
| CAC > $632.50 | critical | Cut budget and rebuild |

The "no conversions, but under one benchmark of spend" case exists because zero
conversions on $300 of spend is not evidence of failure — you would not expect
one yet.

## Video metrics

**These are not comparable between the two platforms** and the dashboard never
blends them. Meta counts a video view at 3 seconds; YouTube counts one at 30
seconds or full play on a skippable ad. Putting them in one "views" column
would be the single most misleading thing this dashboard could do.

| Metric | Formula | Channel |
|---|---|---|
| Cost per view (CPV) | `Σ spend ÷ Σ video_views` | YouTube |
| View rate | `Σ video_views ÷ Σ impressions` | YouTube |
| Watched 25/50/75/100% | `Σ video_pNN ÷ Σ impressions` | Both, but counted differently |
| Thumbstop rate | `Σ three_sec_views ÷ Σ impressions` | Meta |

Quartile counts are stored as counts and divided by impressions here. Google's
API returns quartile *rates* directly; the connector multiplies them back into
counts before storing, so they stay additive across days.

## Link in bio

Traffic from the link in the Instagram bio, stored in its own `bio_link_daily`
table and **never** mixed into the paid tables.

That separation is deliberate. This traffic carries no ad spend, so folding it
into `daily_metrics` would add conversions to the denominator of blended CAC
with no dollars on top, dragging the headline number toward zero and flattering
Meta and YouTube with results they did not buy. A test asserts it stays out.

| Metric | Formula | Notes |
|---|---|---|
| Link taps | `Σ link_clicks` | Clicks on the bio link, from the link tool |
| Visits that landed | `Σ sessions` | Sessions analytics actually recorded |
| **Taps that reach the page** | `Σ sessions ÷ Σ link_clicks` | Summed both sides, then divided once — same rule as CAC |
| Taps lost | `Σ link_clicks − Σ sessions`, floored at 0 | Floored because analytics can out-count the link tool |
| New visitor share | `Σ new_sessions ÷ Σ sessions` | |
| Visit to order rate | `Σ orders ÷ Σ sessions` | |
| Revenue per visit | `Σ revenue ÷ Σ sessions` | |

**A tap and a visit are different events measured by different tools**, and the
gap between them is real traffic lost to slow loads, redirect chains and the
in-app browser closing before the page registers. Reporting only one of the two
hides it. Below `BIO_CLICK_TO_VISIT_WARN` (80%) the section raises a flag.

Both sides must be present for a rate: clicks with no session data resolves to
`—` with a reason, never to a rate computed off one number.

**Where the real numbers come from.** The click side needs the bio link tool
(Bitly, Linktree, Beacons — anything with an analytics API). The session side
needs GA4 or Shopify, filtered to the campaign tag on the bio URL. Tag the link
`?utm_source=instagram&utm_medium=bio` so sessions can be isolated; without a
tag, the session side cannot be separated from other traffic.

## Frequency — and why it is often a dash

`frequency = Σ impressions ÷ reach`

**Reach is not additive.** The number of unique people reached over 28 days is
not the sum of 28 daily reach figures — most of those people are the same
people. So reach lives in its own table (`reach_periods`) keyed by the exact
window it was fetched for, and frequency is computed **only** when a reach row
exists for exactly the window on screen. Otherwise it shows `—` with that
reason.

Blended frequency across Meta and YouTube is **never** shown: the same locksmith
may see both, and neither API reports the overlap.

Above `FREQUENCY_WARN` (3.0) the channel card raises a fatigue flag. The
addressable universe here — automotive locksmiths in the US — is small, so
frequency climbs fast.

## Pacing

```
days_elapsed        = completed days this month (today counts only on the last day of the month)
projected_month_end = MTD spend ÷ days_elapsed × days_in_month
pct_of_plan         = MTD spend ÷ monthly plan
daily_budget_left   = max(plan − MTD spend, 0) ÷ days_remaining
```

Today is excluded from `days_elapsed` because a partial day would inflate the
projection every morning. On the 1st of the month there is nothing to project
and the figure shows `—`.

Monthly plan: **$10K** months 1–3, **$15K** months 4–6, **$20K** month 7+, from
`PROGRAM_START_MONTH`. If that is unset the current month is assumed to be month
1 and the dashboard labels the assumption rather than hiding it.

## Conversions — what counts as an account

**Currently proxied.** `conversions` maps to the configured purchase conversion
action on each platform:

- Meta — the `action_type` named in `META_CONVERSION_ACTION`, pulled out of the
  `actions` array (default `offsite_conversion.fb_pixel_purchase`).
- Google Ads — the conversion actions named in `GOOGLE_ADS_CONVERSION_ACTIONS`,
  or all conversions if left blank.

A purchase is not the same thing as a **new locksmith account**: repeat orders
from existing accounts inflate the count and therefore understate CAC. Every row
carries a `conversion_source` column (`purchase_proxy` today), and the data
health footer shows a **"proxied from purchases"** tag whenever any row in view
is a proxy.

**The seam for making it exact:** feed rows with `conversion_source =
'verified_account'` and `conversions` set to first-orders-from-new-accounts,
sourced from Shopify. Nothing else in the metrics layer changes — CAC already
reads whatever is in that column, and the proxy tag disappears on its own once
no proxied rows remain in the window.

## Attribution

Meta requests `7d_click,1d_view` by default and the window used is stored on
**every row**, so the numbers can be defended later and a window change is
visible rather than silent. Google Ads attributes by its own model on the
account. The windows in use are printed in the data health footer.

Both platforms restate recent conversions for days after the fact, which is why
the sync re-fetches a rolling 28-day window and upserts by primary key rather
than appending.

## Creative leaderboard

Ads with less than `CREATIVE_SPEND_FLOOR` ($100) of spend in the window are
excluded, so a $12 ad with one lucky conversion cannot top the table. The count
of excluded ads is shown.

Ranking is by CAC ascending. An ad that spent real money and acquired nobody
sorts **last** — it is the worst performer, not an unrankable one — with its CAC
shown as `—` and its spend visible.

## What the dashboard deliberately does not do

- **No dual-axis charts.** Spend and CAC on shared axes would invent a
  correlation that is not in the data. Each metric gets its own chart.
- **No blended video metrics**, for the reason above.
- **No estimated or modelled conversions.** If the platform did not report it,
  it is not on the page.
