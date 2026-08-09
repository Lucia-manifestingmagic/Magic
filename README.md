# Noble Key Supply — paid media dashboard

A client-facing dashboard for Noble Key Supply's two paid channels: **Meta**
(Facebook/Instagram) and **YouTube** (video campaigns in Google Ads).

It is built around one question:

> **Is each channel acquiring customers below $550, and should I put more money
> into it?**

$550 is the client's proven all-in cost to acquire a new locksmith account
through human cold sales. Everything above the fold is judged against it.

---

## Quick start

```bash
make dev
```

That creates a virtualenv, installs dependencies, seeds fixture data, and starts
the dashboard on **http://localhost:8000**.

With no credentials configured it runs in **mock mode** — the full UI rendered
from deterministic fixture data, labelled as mock on every screen. Build and demo
the interface without touching a live ad account.

```bash
make test       # metric calculation tests
make seed       # reload fixtures (safe to re-run)
make demo       # export a sanitised static demo to docs/
make backfill   # first 90-day pull from the live APIs      (stage 2/3)
make sync       # incremental 28-day refresh                (stage 2/3)
```

Requires Python 3.9+. No Node, no build step, no bundler.

## The shareable demo

`make demo` renders the dashboard to static HTML in `docs/` — one page per date
range, plus the stylesheet and chart script. Charts, tooltips, table views,
sorting and the channel toggle are all client-side, so the exported pages are
fully interactive with no server behind them. Open `docs/index.html` directly,
or host the folder anywhere static.

**The export is sanitised.** It runs with `DEMO_MODE=1`, which replaces the
client's name and real cost structure with a fictional distributor and round
placeholder economics, and labels the page as a portfolio demo. This is
deliberate: **a hosted page is readable by anyone with the link even when the
repository holding it is private**, so the sanitising happens at the source
rather than relying on repo permissions.

`DEMO_MODE=0 python -m app.export` produces the real figures for local review
and prints a warning telling you not to host them.

### Hosting it on GitHub Pages

GitHub Pages requires a **public** repository on the free plan, and Pages sites
are publicly reachable regardless. Since this repo holds real client data, the
demo belongs in a separate public repo:

1. Create a new public repo, e.g. `ads-dashboard-demo`.
2. Copy the contents of `docs/` into it and push.
3. Settings → Pages → Source: *Deploy from a branch*, branch `main`, folder `/`
   (or `/docs` if you keep the folder structure).

Re-run `make demo` and re-copy whenever you want the demo refreshed.

---

## Build status

| Stage | | |
|---|---|---|
| 1 | Data model, normalized schema, fixtures, full UI | ✅ done |
| 2 | Meta Marketing API — backfill + incremental sync | next |
| 3 | Google Ads API — YouTube video campaigns | |
| 4 | Derived metrics layer | ✅ done in stage 1 |
| 5 | UI polish, tooltips, mobile | ✅ done in stage 1 |
| 6 | Sync scheduling, error surfacing | |

Stage 4 landed early because the metrics layer is what the UI is built on;
fixtures exercise every formula, and the connectors in stages 2–3 only have to
fill the same normalized columns.

---

## Architecture

```
Meta Marketing API ─┐
                    ├─► connector ─► raw_snapshots ─► daily_metrics ─► metrics.py ─► UI
Google Ads API ─────┘                (verbatim)      (normalized)     (pure funcs)
```

| Path | What it is |
|---|---|
| `app/constants.py` | Every business number, env-overridable. No magic numbers downstream. |
| `app/db.py` | SQLite schema, idempotent upserts, reads |
| `app/metrics.py` | All formulas. Pure, no I/O, fully tested. |
| `app/view.py` | Assembles the view model. The template renders; it does not calculate. |
| `app/ranges.py` | Date windows — all end on the last complete day |
| `app/fixtures.py` | Deterministic mock data |
| `app/connectors/` | One module per platform + shared plumbing |
| `app/main.py` | FastAPI routes and number formatting |
| `app/templates/`, `app/static/` | Server-rendered page, hand-written SVG charts |
| `METRICS.md` | **Every formula on the dashboard** |

### Why the data layer looks like this

**`daily_metrics` is platform-neutral.** One row per
`(date, channel, level, campaign_id, adset_id, ad_id)` with unified column
names. The UI never sees a Meta-shaped or Google-shaped payload. Adding Google
Search, Shopping, or TikTok later is a new connector module plus one line in
`app/connectors/__init__.py` — not a rewrite.

**Counts are stored; rates are derived.** CTR, CPM, ROAS and CAC are never
written to the database. Storing a rate and later averaging it across days is
the most common way an ads dashboard ends up quietly wrong. See METRICS.md.

**Raw responses are kept.** Every API response is stored verbatim in
`raw_snapshots` before parsing, so metrics can be re-derived without re-hitting
the APIs — which matters when you change your mind about which `action_type`
counts as a conversion.

**Upserts are idempotent.** Both platforms restate recent conversions for days
afterwards, so the sync re-fetches a rolling 28-day window and overwrites by
primary key rather than appending.

**Reach lives in its own table.** Reach is not additive across days, so it is
keyed by the exact window it was fetched for. Frequency renders only where a
matching window exists, and shows `—` otherwise rather than being computed from
a sum of daily reach, which would be wrong.

---

## Configuration

```bash
cp .env.example .env
```

Fill in the credentials below, then set `LIVE_DATA=1`. Business constants can
also be overridden in `.env` — never edit them in code.

`.env` is gitignored. Tokens are never logged.

### Meta — Marketing API

This is **ad account** data from the Marketing API, not the Graph API page
endpoints.

1. **Business Manager → Business settings → Users → System Users.** Create a
   system user (or use an existing one) and give it access to the ad account
   with the **Manage campaigns** or **View performance** role.
2. **Assign assets** → add the Noble Key Supply ad account to that system user.
3. **Generate new token.** Select your app, and tick the **`ads_read`** scope.
   `ads_management` is not needed — this dashboard only reads.
   - Choose a **60-day** or **never-expiring** token. A short token will expire
     mid-month and the sync will start failing silently otherwise — the data
     health footer surfaces the error either way.
4. Copy it into `META_ACCESS_TOKEN`.
5. **Ad account ID:** in Ads Manager the URL contains `act_XXXXXXXXXX`. Include
   the `act_` prefix in `META_AD_ACCOUNT_ID`.
6. **Pick the conversion action.** Run one sync, then look at a stored raw
   snapshot:

   ```bash
   sqlite3 data/dashboard.db \
     "SELECT response_json FROM raw_snapshots WHERE channel='meta' LIMIT 1;" \
     | python3 -m json.tool | grep action_type | sort -u
   ```

   Set `META_CONVERSION_ACTION` deliberately — `omni_purchase` and
   `offsite_conversion.fb_pixel_purchase` count different things, and this drives
   CAC. See METRICS.md § Conversions.

Verify the token works:

```bash
curl -G "https://graph.facebook.com/v21.0/act_XXXXXXXXXX/insights" \
  -d "fields=spend,impressions" -d "date_preset=last_7d" \
  -d "access_token=$META_ACCESS_TOKEN"
```

### Google Ads — YouTube video campaigns

YouTube ads are video campaigns **inside Google Ads**. The YouTube Data API is
not involved and cannot report ad spend.

1. **Developer token.** In the **manager (MCC)** account: Tools & Settings →
   Setup → **API Center**. Apply for a token.
   - A new token starts at **Test account access**, which only returns data for
     test accounts. Production access needs a review that typically takes a few
     business days.
   - **This dashboard works with a basic/test token first** — build and verify
     the pipeline, then swap the token when approval lands. Nothing else changes.
2. **OAuth client.** Google Cloud Console → APIs & Services → Credentials →
   Create credentials → **OAuth client ID** → *Desktop app*. Save the client ID
   and secret. Enable the **Google Ads API** for the project.
3. **Refresh token.** Authorize once with the
   `https://www.googleapis.com/auth/adwords` scope and keep the refresh token.
   Google's `generate_user_credentials.py` from the Ads API samples does this, or
   run the OAuth device flow manually.
4. **Customer IDs**, digits only, no dashes:
   - `GOOGLE_ADS_CUSTOMER_ID` — the account holding the campaigns
   - `GOOGLE_ADS_LOGIN_CUSTOMER_ID` — the manager account above it (omit if none)
5. **Conversion actions.** List them with:

   ```sql
   SELECT conversion_action.name, conversion_action.category
   FROM conversion_action
   WHERE conversion_action.status = 'ENABLED'
   ```

   Put the ones that count as an acquired account in
   `GOOGLE_ADS_CONVERSION_ACTIONS`. Leave blank to use all conversions — the UI
   says so when you do.

Only `advertising_channel_type = 'VIDEO'` campaigns are pulled. Search, Shopping
and Performance Max are deliberately excluded; the channel registry is where
they would be added later.

### Backfill and scheduling

```bash
make backfill   # 90 days, once
make sync       # rolling 28 days, incremental
```

Schedule the incremental sync every 6 hours:

```cron
0 */6 * * * cd /path/to/dashboard && make sync >> data/sync.log 2>&1
```

The dashboard always renders instantly from SQLite and never blocks on a live
API call. Last sync time per channel — and any sync error — is shown in the data
health footer on the page, not buried in a log.

---

## Reading the dashboard

1. **Verdict row.** Blended cost per account as the headline, then a per-channel
   card: current CAC, the state, and one plain-language recommendation.
2. **Spend pacing.** Month-to-date against plan, projected month end, and what is
   left per day.
3. **Trends.** Cost per account, ROAS, and daily spend. The $550 benchmark and
   the break-even ROAS are drawn on as reference lines. Toggle between compare,
   blended, and each channel. Each chart has a **Table** button — every value is
   readable as text, not only as a hovered tooltip.
4. **Channel comparison.** Meta vs YouTube on every metric, including the ones
   that only apply to one of them.
5. **Campaigns.** Sortable, with spend sparklines.
6. **Creative leaderboard.** What is working and what is not, above a $100 spend
   floor.
7. **Data health.** Last sync, attribution window, whether CAC is proxied, and
   the reporting cut-off date.

**A dash (`—`) always means the number is genuinely unavailable.** Hover or tap
it for the reason. Nothing on this page is estimated.

Any metric with a `?` next to it has a plain-English explanation on hover or
tap — the dashboard is written for a reader who runs the business, not for a
marketer.

---

## Open questions

Answering these makes the dashboard exact rather than approximate:

1. **Meta ad account ID**, and which `action_type` counts as an acquired
   account.
2. **Google Ads customer ID** and manager ID, and which conversion actions count.
3. **`PROGRAM_START_MONTH`** — which calendar month is month 1 of the $10K plan.
   Until this is set, pacing assumes the current month and labels the assumption.
4. **New-account definition.** Right now CAC is proxied from purchases, which
   includes repeat orders from existing accounts and therefore *understates* CAC.
   A Shopify feed of first-orders-from-new-accounts makes it exact — the seam is
   documented in METRICS.md.
5. **Ad account time zone**, if it differs from the business's — it determines
   what "a day" means on both platforms.
