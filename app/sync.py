"""Sync CLI.

    python -m app.sync --days 28                 # incremental, all configured sources
    python -m app.sync --days 90 --backfill      # first pull
    python -m app.sync --days 7 --only meta      # one source

Every source runs independently. One failing connector records its error
against its own sync run and the others still complete, because a dashboard
that goes blank when TikTok's token expires is worse than one that says so.
Errors land in `sync_runs` and surface in the data-health footer, not just a log.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib
import sys
import traceback
from typing import Dict, List

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

from . import db, ranges
from .connectors import REGISTRY


def run(days: int, only: List[str], backfill: bool) -> int:
    today = dt.date.today()
    end = ranges.last_complete_day(today)
    start = end - dt.timedelta(days=days - 1)

    conn = db.connect()
    db.init(conn)

    failures = 0
    for key, module_path in REGISTRY.items():
        if only and key not in only:
            continue
        try:
            module = importlib.import_module(module_path)
        except ImportError as exc:
            print("  %-16s module missing (%s)" % (key, exc))
            continue

        if not getattr(module, "is_configured", lambda: False)():
            print("  %-16s skipped, not configured" % key)
            continue

        label = getattr(module, "LABEL", key)
        run_id = db.start_sync(conn, key, "backfill" if backfill else "incremental", start, end)
        try:
            rows = module.sync(conn, start, end)
        except Exception as exc:  # noqa: BLE001 - the whole point is not to crash
            failures += 1
            detail = "%s: %s" % (type(exc).__name__, exc)
            db.finish_sync(conn, run_id, "error", 0, detail[:500])
            print("  %-16s FAILED  %s" % (key, detail[:160]))
            if "--traceback" in sys.argv:
                traceback.print_exc()
        else:
            db.finish_sync(conn, run_id, "ok", rows)
            print("  %-16s ok, %d rows  (%s)" % (key, rows, label))

    db.set_setting(conn, "data_mode", "live")
    conn.close()
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync ad and organic data.")
    parser.add_argument("--days", type=int, default=28)
    parser.add_argument("--backfill", action="store_true")
    parser.add_argument("--only", default="", help="comma separated source keys")
    parser.add_argument("--traceback", action="store_true")
    args = parser.parse_args()

    only = [k.strip() for k in args.only.split(",") if k.strip()]
    print("Syncing %d days%s..." % (args.days, " (backfill)" if args.backfill else ""))
    failures = run(args.days, only, args.backfill)
    if failures:
        print("\n%d source(s) failed. The dashboard still renders from what synced;"
              " the footer shows which are stale." % failures)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
