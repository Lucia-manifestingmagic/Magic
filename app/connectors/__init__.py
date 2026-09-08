"""Channel connectors.

Each connector turns one platform's API into normalized `daily_metrics` rows
and nothing else. The registry below is the only place the rest of the app
learns that a channel exists — adding Google Search or TikTok later means a new
module plus one line here.

Stage 2 fills in `meta`, stage 3 fills in `google_ads`.
"""

from __future__ import annotations

from typing import Callable, Dict

# key -> module. The sync CLI walks this in order; each module exposes
# LABEL, is_configured() and sync(conn, start, end) -> rows upserted.
REGISTRY: Dict[str, str] = {
    # paid
    "meta": "app.connectors.meta",
    "youtube": "app.connectors.google_ads",
    # organic
    "meta_organic": "app.connectors.meta_organic",
    "youtube_organic": "app.connectors.youtube_organic",
    "tiktok_organic": "app.connectors.tiktok_organic",
}
