"""Static export of the dashboard, for hosting a shareable demo.

Renders the mock-mode dashboard to plain HTML files in `docs/` — one per date
range, plus a copy of the stylesheet and chart script. The charts, tooltips,
table views, sorting and channel toggles are all client-side, so the exported
pages are fully interactive with no server behind them.

**It runs in demo mode by default** (`DEMO_MODE=1`), which swaps the client's
name and real cost structure for a fictional distributor and round placeholder
economics. A hosted page is public to anyone with the link even when the repo
holding it is private, so the export is sanitised at the source rather than
relying on the repo being locked down.

    make demo                       # sanitised, safe to host publicly
    DEMO_MODE=0 python -m app.export  # real figures, for local review only

Run with:  python -m app.export
"""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import shutil
import sys

# Demo mode has to be set before app.constants is imported, since the constants
# read their defaults at import time.
os.environ.setdefault("DEMO_MODE", "1")
os.environ.setdefault("DB_PATH", "data/demo.db")

from jinja2 import Environment, FileSystemLoader, select_autoescape  # noqa: E402

from . import constants as C  # noqa: E402
from . import db, fixtures, main, ranges, view  # noqa: E402

BASE_DIR = pathlib.Path(__file__).resolve().parent
OUT_DIR = pathlib.Path(os.environ.get("DEMO_OUT", "docs"))

# The 28-day view is the one the client lands on, so it becomes index.html.
INDEX_RANGE = "28d"


def _page_name(range_key: str) -> str:
    return "index.html" if range_key == INDEX_RANGE else range_key + ".html"


def build() -> pathlib.Path:
    conn = db.connect()
    db.init(conn)
    if not conn.execute("SELECT 1 FROM daily_metrics LIMIT 1").fetchone():
        fixtures.load(conn)
    db.set_setting(conn, "data_mode", "mock")

    env = Environment(
        loader=FileSystemLoader(str(BASE_DIR / "templates")),
        autoescape=select_autoescape(["html"]),
    )
    env.filters.update(
        money=main.money,
        money2=main.money2,
        number=main.number,
        compact=main.compact,
        percent=main.percent,
        signed_percent=main.signed_percent,
        ratio=main.ratio,
        pretty_date=main.pretty_date,
    )
    template = env.get_template("dashboard.html")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    static_out = OUT_DIR / "static"
    if static_out.exists():
        shutil.rmtree(static_out)
    shutil.copytree(str(BASE_DIR / "static"), str(static_out))

    # Tells GitHub Pages to serve the files as-is rather than running Jekyll.
    (OUT_DIR / ".nojekyll").write_text("")

    written = []
    for range_key in ranges.RANGES:
        model = view.build(conn, range_key)
        html = template.render(
            v=model,
            view_json=json.dumps(model, default=str),
            constants=C,
            asset=lambda name: "static/" + name,
            range_href=_page_name,
        )
        target = OUT_DIR / _page_name(range_key)
        target.write_text(html)
        written.append(target)

    conn.close()

    if not C.DEMO_MODE:
        print(
            "WARNING: exported with DEMO_MODE=0 — these pages contain the real\n"
            "         client name and cost structure. Do not host them publicly.",
            file=sys.stderr,
        )

    print("Exported %d pages to %s/ as %s" % (len(written), OUT_DIR, C.CLIENT_NAME))
    for path in written:
        print("  " + str(path))
    return OUT_DIR


if __name__ == "__main__":
    build()
