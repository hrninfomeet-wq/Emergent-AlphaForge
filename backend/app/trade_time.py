"""Compare trade timestamps as INSTANTS, never as strings.

``closed_at`` is written in two formats. The paper square-off sweep stamps an IST
wall-clock string (``2026-09-28T15:00:00+05:30``); the marker, the manual close and
every live close stamp UTC (``2026-09-28T09:30:00+00:00``). Both are valid ISO-8601
and they name the same instant, yet they do NOT sort or compare alike as text — the
IST form of 15:00 sorts BEFORE a UTC form of 11:00 that is really four hours
earlier. Two readers did exactly that: the kill switch fed a Mongo string sort into
its "trailing consecutive losses" run, and the deployment overview compared
``closed_at`` to a UTC day-start as a string. This module is the single place that
parses, so every comparison is between instants whatever format was written.

Pure and dependency-free — safe to import from anywhere.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional


def parse_instant(value: Any) -> Optional[datetime]:
    """An aware UTC ``datetime`` for an ISO string / datetime, or None.

    A naive value is read as UTC — the convention every writer here follows for a
    bare ``created_at``. Unparseable and blank input is None, never a guess.
    """
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def instant_sort_key(value: Any) -> float:
    """Epoch-seconds sort key. A missing / unparseable time sorts FIRST (oldest),
    the same place an empty string sorted under the old string comparison."""
    dt = parse_instant(value)
    return dt.timestamp() if dt is not None else float("-inf")


def in_window(value: Any, start: Any, end: Any = None) -> bool:
    """True iff ``value`` parses and lies in ``[start, end)`` (``end`` optional).

    An unparseable time is NOT inside any window — an unknown close is not
    "today's" P&L. Fails closed on an unparseable bound too.
    """
    dt = parse_instant(value)
    lo = parse_instant(start)
    if dt is None or lo is None or dt < lo:
        return False
    if end is None:
        return True
    hi = parse_instant(end)
    return hi is not None and dt < hi


def realized_in_window(rows: Any, start: Any, end: Any = None) -> "dict[str, float]":
    """Sum ``realized_pnl`` per ``deployment_id`` over CLOSED rows closed in
    ``[start, end)``, comparing INSTANTS (see the module docstring).

    A row flagged ``exit_day_unknown`` is skipped — the live reconcile closed it on
    a day nobody recorded, so its ``closed_at`` is only when it was noticed. A row
    whose ``closed_at`` does not parse, or whose ``realized_pnl`` is not a finite
    number, contributes nothing: an unknown is never booked as today's P&L.
    """
    out: "dict[str, float]" = {}
    for r in rows or ():
        if r.get("exit_day_unknown") or not in_window(r.get("closed_at"), start, end):
            continue
        try:
            pnl = float(r.get("realized_pnl") or 0.0)
        except (TypeError, ValueError):
            continue
        if pnl != pnl or pnl in (float("inf"), float("-inf")):
            continue
        dep = str(r.get("deployment_id") or "")
        out[dep] = out.get(dep, 0.0) + pnl
    return out
