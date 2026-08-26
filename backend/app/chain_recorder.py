"""Records point-in-time option-chain snapshots into ``chain_snapshots``.

This is the writer the collection never had. ``db.py`` has created an index on
``chain_snapshots`` since the collection was introduced and nothing has ever
inserted a row, so every trading session up to now is permanently unobservable:
unlike spot candles, option candles or expired contracts — all of which
backfill — an option chain is a point-in-time observation with no historical
endpoint behind it. A minute not recorded is a minute that never existed.

**Why the REST chain and not the live tick stream.** The obvious source is the
WebSocket universe the paper/live path already subscribes to, and it is the
wrong one. That universe is sized from what ACTIVE DEPLOYMENTS need
(``radius_for_deployments``, floored at 3), it is hard-clamped to ATM +/- 5 and
60 instrument keys, and changing it RESTARTS the stream. Recording from it would
mean either accepting an ATM +/- 3 window — too narrow for max-pain or OI-wall
work, and the width cannot be revised retroactively — or perturbing the feed the
live trading path depends on, to serve a research capture. Neither is
acceptable. ``/v2/option/chain`` is a separate read that returns the full chain
and touches nothing the trading path uses.

It also returns something the tick stream does not: **``bid_price`` and
``ask_price`` per strike**. The optimizer's own ``research_eligibility`` guard
blocks promotion today with ``no_point_in_time_execution_surface`` ("Forward
full-feed chain capture is required to calibrate executable spread and
liquidity"). This capture is the only thing that can ever clear it. The repo's
cost model currently assumes a flat 1% of premium per side, an assumption that
accounted for 54% of gross P&L in the 2026-08-23 SENSEX run — the least
validated number in the system, and one that decides whether any strategy here
is profitable.

Design rules, each one paid for by a defect already in this repo's history:

* **Never write an empty reading.** A failed or empty fetch inserts nothing. A
  gap is visible; a row of nulls looks like a measurement and silently poisons
  every later aggregate — the ``vix_boost_threshold`` failure mode.
* **Bucketed timestamps.** ``ts`` is floored to the capture interval so a
  restart mid-interval cannot double-record and later analysis gets evenly
  spaced samples. The true read time is kept alongside as ``captured_at_ms``.
* **No BSON date field, ever.** ``ticks`` carries a 30-day TTL and a TTL index
  requires a date field. Storing none makes an accidental TTL on this collection
  structurally impossible.
* **One instrument's failure is not the loop's failure.** Each capture is
  isolated; the loop keeps its cadence regardless.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, Optional, Sequence

from app.chain_snapshot import normalize_chain_response

log = logging.getLogger("alphaforge.chain_recorder")

IST = timezone(timedelta(hours=5, minutes=30))

#: Seconds between captures. 60s gives ~375 samples per instrument per session —
#: enough to characterise the intraday spread distribution (the point of §7.6)
#: without making the collection unwieldy. Open interest updates far more slowly
#: than this, so a finer cadence would mostly re-record the same OI.
CAPTURE_INTERVAL_SEC = 60

#: How long the loop sleeps while the market is shut. Long enough to be
#: invisible, short enough that the first capture after the open is prompt.
IDLE_SLEEP_SEC = 60

#: The option underlyings worth recording. Kept explicit rather than derived
#: from INSTRUMENT_KEYS so adding an index to the app does not silently add an
#: unattended API poll.
RECORDED_INSTRUMENTS: Sequence[str] = ("NIFTY", "BANKNIFTY", "SENSEX")

#: Phases in which OPTIONS are tradeable. `cas` (15:15-15:30) freezes the cash
#: index and `derivatives_only` (15:30-15:40) is after cash settles — in both,
#: options still trade, so both are exactly when a chain capture matters most.
TRADEABLE_PHASES = frozenset({"open", "cas", "derivatives_only"})


def bucket_ts_ms(now_ms: int, interval_sec: int = CAPTURE_INTERVAL_SEC) -> int:
    """Floor an epoch-ms timestamp to the capture interval."""
    step = max(1, int(interval_sec)) * 1000
    return (int(now_ms) // step) * step


def should_capture(market: Any) -> bool:
    """Whether to capture, given a ``nse_calendar.market_status`` payload.

    Fails CLOSED: anything that is not a dict carrying an explicit ``is_open``
    and a recognised tradeable ``phase`` returns False. A recorder that captures
    when it cannot establish the market is open would fill the collection with
    stale repeats of the last print — indistinguishable, months later, from real
    flat quotes.
    """
    if not isinstance(market, dict):
        return False
    if not market.get("is_open"):
        return False
    return str(market.get("phase") or "") in TRADEABLE_PHASES


async def _default_next_expiry(db: Any, instrument: str) -> Optional[str]:
    """Nearest upcoming expiry, from the warehouse's own contract master.

    Deliberately reuses ``live_option_universe._next_expiry`` rather than the
    vendor's ``current_week`` keyword: expiry weekday has rotated twice in this
    window (NIFTY Thu -> Tue, SENSEX Fri -> Tue -> Thu), and the contract master
    is the only source that has been right across all of it.
    """
    from app.live_option_universe import _next_expiry

    today = datetime.now(IST).date().isoformat()
    return await _next_expiry(db, instrument, today)


async def _default_fetch_chain(instrument_key: str, expiry_date: str) -> Dict[str, Any]:
    from app.upstox_client import fetch_option_chain

    return await fetch_option_chain(instrument_key, expiry_date)


async def capture_once(
    db: Any,
    instrument: str,
    *,
    now: Optional[datetime] = None,
    force: bool = False,
    fetch_chain: Optional[Callable[[str, str], Awaitable[Any]]] = None,
    next_expiry: Optional[Callable[[Any, str], Awaitable[Optional[str]]]] = None,
) -> Dict[str, Any]:
    """Fetch, normalize and store one snapshot. Never raises.

    Returns ``{ok, instrument, reason, strike_count, ts}`` — ``reason`` is one of
    ``written`` / ``market_closed`` / ``no_expiry`` / ``fetch_failed`` /
    ``empty_chain`` / ``duplicate``, so the caller can log a cause rather than a
    bare failure.

    The market gate is applied HERE as well as in the loop, deliberately. Off
    hours the endpoint still answers — with the previous session's closing book —
    and storing that under today's ``session_date`` records a chain on a date it
    never traded. That is the same class of poison as an empty reading, and this
    is the last line before permanent storage. A smoke test at 00:11 IST wrote
    exactly three such rows before this guard existed. ``force=True`` is the
    escape hatch for a deliberate off-hours read.
    """
    from app.instruments import INSTRUMENT_KEYS
    from app.nse_calendar import market_status

    instrument = str(instrument).upper()
    now = now or datetime.now(IST)
    true_ms = int(now.timestamp() * 1000)
    ts = bucket_ts_ms(true_ms)
    result: Dict[str, Any] = {"ok": False, "instrument": instrument,
                              "reason": "", "strike_count": 0, "ts": ts}

    if not force and not should_capture(market_status(now)):
        result["reason"] = "market_closed"
        return result

    underlying_key = INSTRUMENT_KEYS.get(instrument)
    if not underlying_key:
        result["reason"] = "unknown_instrument"
        return result

    expiry_fn = next_expiry or _default_next_expiry
    fetch_fn = fetch_chain or _default_fetch_chain

    try:
        expiry = await expiry_fn(db, instrument)
    except Exception as exc:  # contract-master read failed; not fatal to the loop
        result["reason"] = "no_expiry"
        result["error"] = str(exc)
        return result
    if not expiry:
        result["reason"] = "no_expiry"
        return result

    try:
        payload = await fetch_fn(underlying_key, expiry)
    except Exception as exc:
        result["reason"] = "fetch_failed"
        result["error"] = str(exc)
        return result

    doc = normalize_chain_response(
        payload,
        instrument=instrument,
        captured_ts_ms=ts,
        session_date=now.astimezone(IST).strftime("%Y-%m-%d"),
    )
    if not doc["strike_count"]:
        # Refuse to record "we looked and saw nothing" as if it were a chain.
        result["reason"] = "empty_chain"
        return result

    # Provenance the pure normalizer cannot know: when the read actually
    # happened, and which expiry we asked for (which may differ from the one the
    # response reports if the vendor rolled mid-capture — worth being able to see).
    doc["captured_at_ms"] = true_ms
    doc["requested_expiry_date"] = expiry
    doc["created_at"] = now.astimezone(timezone.utc).isoformat()  # ISO STRING, never a date

    try:
        await db.chain_snapshots.insert_one(doc)
    except Exception as exc:
        if type(exc).__name__ == "DuplicateKeyError":
            result["reason"] = "duplicate"
            return result
        result["reason"] = "write_failed"
        result["error"] = str(exc)
        return result

    result.update(ok=True, reason="written", strike_count=doc["strike_count"])
    return result


async def chain_recorder_loop() -> None:
    """Capture every instrument once per interval while options are tradeable.

    Runs for the life of the process. Repeated identical failures are logged
    once per transition rather than every cadence, so a day with no Upstox token
    produces one line per instrument instead of 375.
    """
    from app.db import get_db
    from app.nse_calendar import market_status

    last_reason: Dict[str, str] = {}
    log.info("Chain recorder started (interval %ss, instruments %s)",
             CAPTURE_INTERVAL_SEC, ", ".join(RECORDED_INSTRUMENTS))

    while True:
        try:
            now = datetime.now(IST)
            if not should_capture(market_status(now)):
                await asyncio.sleep(IDLE_SLEEP_SEC)
                continue

            db = get_db()
            for instrument in RECORDED_INSTRUMENTS:
                try:
                    res = await capture_once(db, instrument, now=datetime.now(IST))
                except Exception as exc:  # belt and braces: never kill the loop
                    res = {"ok": False, "instrument": instrument,
                           "reason": "unexpected", "error": str(exc)}
                reason = str(res.get("reason") or "")
                if res.get("ok"):
                    if last_reason.get(instrument) != "written":
                        log.info("Chain capture OK for %s (%s strikes)",
                                 instrument, res.get("strike_count"))
                elif last_reason.get(instrument) != reason:
                    log.warning("Chain capture skipped for %s: %s%s", instrument, reason,
                                f" — {res['error']}" if res.get("error") else "")
                last_reason[instrument] = reason if not res.get("ok") else "written"
        except asyncio.CancelledError:
            log.info("Chain recorder stopping")
            raise
        except Exception:
            log.exception("Chain recorder iteration failed; continuing")

        await asyncio.sleep(CAPTURE_INTERVAL_SEC)
