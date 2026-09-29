"""What time it is for trading — ONE server-side answer for every countdown. PURE.

The operator learned about the 15:00 entry cutoff only afterwards, and every
clock on the page was the browser's own: holiday-blind (the cockpit pill read
"MARKET OPEN" on NSE holidays), never corrected for a skewed PC clock, and
re-deriving 15:00 from yet another literal (there are eight in the codebase).

``describe_session`` derives each boundary from the thing that ENFORCES it:

  * the entry cutoff — ``mode.entry_cutoff_today_ist``, the gate's own function;
  * the EOD square — the guard's configured time, passed in by the caller (it is
    a separate constant from the cutoff and equal to it only by coincidence);
  * the trading day — the holiday-aware ``nse_calendar`` (special sessions too);
  * the open / derivatives close / closing auction — ``session_spec``.

Every boundary is also an absolute epoch-ms, so a browser counts down against the
server's instants, never its own wall clock. Anything unknowable is None.
"""
from __future__ import annotations

from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Any, Dict, Optional

IST = timezone(timedelta(hours=5, minutes=30))

#: How far ahead to look for the next trading day before answering "unknown".
_NEXT_DAY_SCAN = 10


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _at(d: date, t: dtime) -> datetime:
    return datetime.combine(d, t, tzinfo=IST)


def _hhmm(t: Optional[dtime]) -> Optional[str]:
    return t.strftime("%H:%M") if t is not None else None


def describe_session(now_utc: datetime, *,
                     eod_square_ist: Optional[dtime]) -> Dict[str, Any]:
    """The trading clock at ``now_utc``. Never raises for a well-formed datetime.

    ``phase`` (checked in this order): ``closed_day`` (not a trading day),
    ``pre_open``, ``after_eod``, ``after_cutoff``, ``session``. ``after_eod`` is
    checked before ``after_cutoff`` because today they are the same instant.

    ``next_event`` is ``{kind, at_ms}`` — market_open / entry_cutoff / eod_square /
    next_session_open — or None when it cannot be known (EOD time not supplied;
    the next session falls outside the verified calendar).
    """
    from app.live.mode import entry_cutoff_today_ist
    from app.nse_calendar import HOLIDAY_LABELS, YEAR_LAST_VERIFIED, is_trading_day
    from app.session_spec import (OPTIONS, SESSION_OPEN_MIN, cas_start_time,
                                  segment_close_time)

    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    now_ist = now_utc.astimezone(IST)
    today = now_ist.date()
    iso = today.isoformat()
    trading = is_trading_day(iso)
    open_t = dtime(SESSION_OPEN_MIN // 60, SESSION_OPEN_MIN % 60)
    open_dt = _at(today, open_t)
    cutoff_dt = datetime.fromisoformat(entry_cutoff_today_ist(now_utc))
    eod_dt = _at(today, eod_square_ist) if eod_square_ist is not None else None
    deriv_close = segment_close_time(iso, OPTIONS)

    if not trading:
        day_kind = "weekend" if today.weekday() >= 5 else "holiday"
    else:
        day_kind = "trading"

    if not trading:
        phase = "closed_day"
    elif now_ist < open_dt:
        phase = "pre_open"
    elif eod_dt is not None and now_ist >= eod_dt:
        phase = "after_eod"
    elif now_ist >= cutoff_dt:
        phase = "after_cutoff"
    else:
        phase = "session"

    next_event: Optional[Dict[str, Any]] = None
    if phase == "pre_open":
        next_event = {"kind": "market_open", "at_ms": _ms(open_dt)}
    elif phase == "session":
        next_event = {"kind": "entry_cutoff", "at_ms": _ms(cutoff_dt)}
    elif phase == "after_cutoff":
        next_event = ({"kind": "eod_square", "at_ms": _ms(eod_dt)}
                      if eod_dt is not None else None)
    else:  # after_eod / closed_day → the next session's open, if it is knowable
        d = today
        for _ in range(_NEXT_DAY_SCAN):
            d = d + timedelta(days=1)
            if d.year > YEAR_LAST_VERIFIED:
                break                       # outside the verified calendar: unknown
            if is_trading_day(d.isoformat()):
                next_event = {"kind": "next_session_open", "at_ms": _ms(_at(d, open_t)),
                              "date": d.isoformat()}
                break

    return {
        "server_now_ms": _ms(now_utc),
        "now_ist": now_ist.isoformat(),
        "ist_date": iso,
        "is_trading_day": trading,
        "day_kind": day_kind,
        "holiday_label": HOLIDAY_LABELS.get(iso) if day_kind == "holiday" else None,
        "calendar_verified": today.year <= YEAR_LAST_VERIFIED,
        "phase": phase,
        "market_open_ist": _hhmm(open_t),
        "market_open_ms": _ms(open_dt),
        "entry_cutoff_ist": cutoff_dt.astimezone(IST).strftime("%H:%M"),
        "entry_cutoff_ms": _ms(cutoff_dt),
        "eod_square_ist": _hhmm(eod_square_ist),
        "eod_square_ms": _ms(eod_dt) if eod_dt is not None else None,
        "derivatives_close_ist": _hhmm(deriv_close),
        "cas_start_ist": _hhmm(cas_start_time(iso)),
        "next_event": next_event,
    }
