"""Market-context classification for trades and signals.

Context is the "when/where" of a trade: the regime it fired in, the time-of-day
bucket, the days-to-expiry, and the volatility regime (India VIX). The elite-desk
principle is that edge is conditional — a strategy that prints in a trending
morning can bleed in a choppy afternoon. Tagging every trade with its context
lets us answer "where does this strategy actually have edge?" and later route
strategies by regime.

This module is pure and dependency-light: it classifies scalar inputs into
buckets. The data plumbing (reading the enriched dataframe row, resolving DTE
from expiry metadata, joining VIX) lives in the callers.

Time-of-day buckets (IST), aligned with the user's discipline windows
(avoid 09:15-09:25 open and 15:00-15:30 close):
  OPEN       09:15-09:25   (first 10 min — high noise, user avoids)
  MORNING    09:25-11:00   (primary trend-development window)
  MIDDAY     11:00-13:30   (typically lower volatility / chop)
  AFTERNOON  13:30-15:00   (trend resumption / positioning)
  CLOSE      15:00-15:30   (last 30 min — user avoids)

VIX buckets (India VIX), aligned with the user's note that >15 is where
near-expiry option premiums can explode 2x-10x on sharp moves:
  LOW        < 12
  NORMAL     12-15
  ELEVATED   15-20
  HIGH       >= 20
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional


IST_OFFSET = timedelta(hours=5, minutes=30)


def _to_minutes(ist_time: str) -> Optional[int]:
    """Parse 'HH:MM' IST into minutes-since-midnight. None if unparseable."""
    try:
        hh, mm = str(ist_time).split(":")[:2]
        return int(hh) * 60 + int(mm)
    except Exception:
        return None


def time_of_day_bucket(ist_time: str) -> str:
    """Classify an IST 'HH:MM' string into a session bucket."""
    m = _to_minutes(ist_time)
    if m is None:
        return "UNKNOWN"
    if m < 9 * 60 + 15:
        return "PRE_OPEN"
    if m < 9 * 60 + 25:
        return "OPEN"
    if m < 11 * 60:
        return "MORNING"
    if m < 13 * 60 + 30:
        return "MIDDAY"
    if m < 15 * 60:
        return "AFTERNOON"
    if m < 15 * 60 + 30:
        return "CLOSE"
    return "POST_CLOSE"


def ist_time_from_ts(ts_ms: Any) -> Optional[str]:
    """Return 'HH:MM' IST for an epoch-ms timestamp, or None."""
    try:
        dt = datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc) + IST_OFFSET
        return dt.strftime("%H:%M")
    except Exception:
        return None


def vix_bucket(vix: Optional[float]) -> str:
    """Classify an India VIX level into a volatility regime bucket."""
    if vix is None:
        return "UNKNOWN"
    try:
        v = float(vix)
    except (TypeError, ValueError):
        return "UNKNOWN"
    if v < 12:
        return "LOW"
    if v < 15:
        return "NORMAL"
    if v < 20:
        return "ELEVATED"
    return "HIGH"


def build_trade_context(
    *,
    regime: Optional[str] = None,
    ist_time: Optional[str] = None,
    ts_ms: Optional[Any] = None,
    dte: Optional[int] = None,
    vix: Optional[float] = None,
) -> Dict[str, Any]:
    """Assemble a context snapshot for a trade/signal.

    Provide either `ist_time` ('HH:MM') or `ts_ms`; ist_time wins if both given.
    All fields are optional so callers can attach what they have.
    """
    tod_source = ist_time or (ist_time_from_ts(ts_ms) if ts_ms is not None else None)
    return {
        "regime": regime or "UNKNOWN",
        "ist_time": tod_source,
        "time_of_day": time_of_day_bucket(tod_source) if tod_source else "UNKNOWN",
        "dte": dte,
        "vix": round(float(vix), 2) if vix is not None else None,
        "vix_bucket": vix_bucket(vix),
    }
