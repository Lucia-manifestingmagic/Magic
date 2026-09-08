"""Shared connector plumbing.

Small, boring, and tested — because these are the places where ad-platform data
quietly goes wrong: Google reports money in millionths, Meta buries conversions
in a nested array of mixed action types, and both will hand you a string where
you expected a number.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Dict, Iterable, List, Optional, Sequence

MICROS = 1_000_000


def micros_to_currency(micros: Optional[Any]) -> Optional[float]:
    """Google Ads reports money in millionths of the account currency.

    `cost_micros: 12345678` is $12.345678. Returns None for a missing value
    rather than 0.0, so "no data" never renders as "spent nothing".
    """
    if micros is None or micros == "":
        return None
    try:
        return float(micros) / MICROS
    except (TypeError, ValueError):
        return None


def as_float(value: Optional[Any]) -> Optional[float]:
    """Both APIs return numbers as strings in places. None stays None."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_int(value: Optional[Any]) -> Optional[int]:
    number = as_float(value)
    return None if number is None else int(round(number))


def sum_actions(
    actions: Optional[Iterable[Dict[str, Any]]],
    wanted: Sequence[str],
    field: str = "value",
) -> Optional[float]:
    """Pull one or more action types out of Meta's `actions`/`action_values`.

    Meta returns conversions as a list of `{action_type, value}` objects mixing
    everything from `landing_page_view` to `omni_purchase`. Which one counts as
    an acquired account is a decision, not a default, so the caller passes it in
    explicitly. Returns None when the array is absent (no data) as distinct from
    0.0 (the array was there and the action did not occur).
    """
    if actions is None:
        return None
    wanted_set = set(wanted)
    total = 0.0
    for action in actions:
        if action.get("action_type") in wanted_set:
            value = as_float(action.get(field))
            if value is not None:
                total += value
    return total


def date_chunks(
    start: dt.date, end: dt.date, size_days: int
) -> List[Sequence[dt.date]]:
    """Split a range into chunks, to stay under per-request row limits."""
    out: List[Sequence[dt.date]] = []
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + dt.timedelta(days=size_days - 1), end)
        out.append((cursor, chunk_end))
        cursor = chunk_end + dt.timedelta(days=1)
    return out


# --- HTTP -------------------------------------------------------------------

import time as _time
from typing import Callable

import httpx


class ConnectorError(RuntimeError):
    """Raised when a connector cannot complete. Surfaced in the UI footer."""


def get_json(
    url: str,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    *,
    method: str = "GET",
    json_body: Optional[Dict[str, Any]] = None,
    attempts: int = 5,
    timeout: float = 60.0,
) -> Dict[str, Any]:
    """One HTTP call with backoff on the failures these APIs actually produce.

    Retries rate limits and 5xx with exponential backoff. Does not retry 4xx
    other than 429, because a bad token or a bad field list will fail exactly
    the same way the second time.
    """
    delay = 2.0
    last: Optional[str] = None
    for attempt in range(attempts):
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.request(
                    method, url, params=params, headers=headers, json=json_body
                )
        except httpx.RequestError as exc:
            last = "network error: %s" % exc
        else:
            if response.status_code < 400:
                return response.json()
            last = "HTTP %d: %s" % (response.status_code, response.text[:400])
            if response.status_code not in (429, 500, 502, 503, 504):
                raise ConnectorError(last)
        if attempt < attempts - 1:
            _time.sleep(delay)
            delay *= 2
    raise ConnectorError(last or "request failed")


def env(name: str, default: str = "") -> str:
    import os
    return os.environ.get(name, "").strip() or default


def require(name: str) -> str:
    value = env(name)
    if not value:
        raise ConnectorError(
            "%s is not set. See .env.example and README for how to obtain it." % name
        )
    return value
