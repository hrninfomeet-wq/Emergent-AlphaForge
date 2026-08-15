"""Close today's candle gaps from a HISTORICAL API, before the live feed is trusted.

The scenario this exists for: the backend starts after the session has begun. The
tick->1m roller (`live_candle_roller`) only aggregates ticks it personally
witnesses, so everything before boot is simply absent. On 2026-08-14 that left
NIFTY with 340 of 375 bars, and the warehouse was not repaired until 09:13 IST the
FOLLOWING day — long after every trading decision had been made.

**WebSockets cannot supply history.** The only same-day sources are REST.

Broker capabilities, established from the code and the decoded vendor spec:

* **Upstox V3 intraday** (`upstox_client.fetch_intraday_1m`, primary) — the correct
  same-day endpoint. It takes **NO date parameters**: it returns "today" wholesale,
  so a sub-range fetch is impossible and gap-only fetching is achieved by relying on
  the idempotent `(instrument, ts)` upsert rather than by narrowing the request. One
  call per instrument covers any gap, however fragmented. It serves ONLY the three
  index keys in `instruments.INSTRUMENT_KEYS`; INDIAVIX and option contracts raise.
* **Upstox V3 historical** — returns EMPTY for the in-progress day. Unusable here;
  this is the documented limitation that made the roller necessary in the first place.
* **Flattrade TPSeries** (fallback, decoded spec endpoint #29) — serves 1-minute
  candles and DOES accept explicit `st`/`et` epoch bounds. Wired through the
  injectable `fallback_fetch` seam rather than hard-coded, because it needs a Noren
  numeric token rather than an Upstox instrument key.

Two invariants are load-bearing:

1. **Never write the in-progress minute.** `persist_candles_df` is an unconditional
   `$set` upsert — last writer wins, with no merge. A fetch of "today so far"
   includes a partial current minute; writing it could replace a complete
   roller-built bar with a partial one. Bars are therefore filtered to minutes that
   have CLOSED, which is also exactly what `candle_gap.expected_minute_ts` demands.
2. **Recovery is idempotent and restart-safe by construction.** The gap assessment
   IS the state — it is recomputed from the warehouse on every run, so a process
   that dies mid-backfill simply finds a smaller gap next time. There is no progress
   marker to corrupt and no half-written day that can look complete.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence

from app.candle_gap import (
    MINUTE_MS,
    assess_completeness,
    expected_minute_ts,
    format_ranges_ist,
)
from app.session_spec import SPOT

log = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

#: Spacing between per-instrument fetches. Upstox documents 50 req/sec and the
#: existing client already sleeps 0.15s between calls; recovery touches at most a
#: handful of instruments once, so this is deliberately generous rather than tuned.
FETCH_SPACING_SEC = 0.25

#: A recovery pass never fetches an instrument more than this many times. Upstox
#: intraday returns the whole day in one call, so >1 attempt only ever means a
#: transport retry — an unbounded loop here would hammer a shared rate budget.
MAX_ATTEMPTS_PER_INSTRUMENT = 2


def _today_ist(now_ms: Optional[int] = None) -> str:
    ts = now_ms if now_ms is not None else int(datetime.now(timezone.utc).timestamp() * 1000)
    return datetime.fromtimestamp(ts / 1000, IST).strftime("%Y-%m-%d")


def _last_closed_minute_ms(now_ms: int) -> int:
    return (int(now_ms) // MINUTE_MS) * MINUTE_MS - MINUTE_MS


async def _present_ts(db: Any, instrument: str, iso_date: str) -> List[int]:
    """Every stored bar timestamp for one instrument-day.

    Reads `ts` only — never the `datetime` string. `candles_1m` holds TWO datetime
    formats (roller `2026-08-14 09:50:00` vs ingest `2026-08-14T09:15:00+05:30`)
    which sort INVERTED against each other, so `ts` is the only safe key. It also
    carries the collection's unique index.
    """
    start = int(datetime.fromisoformat(f"{iso_date}T00:00:00+05:30").timestamp() * 1000)
    end = start + 24 * 60 * 60 * 1000
    rows = await db.candles_1m.find(
        {"instrument": instrument.upper(), "ts": {"$gte": start, "$lt": end}},
        {"_id": 0, "ts": 1},
    ).to_list(length=3000)
    return [int(r["ts"]) for r in rows if r.get("ts") is not None]


class RecoveryReport(dict):
    """Per-instrument outcome plus the aggregate. A plain dict for JSON transport."""


async def recover_instrument(
    db: Any,
    instrument: str,
    *,
    iso_date: str,
    now_ms: int,
    primary_fetch: Callable[[str], Awaitable[Any]],
    persist: Callable[[str, Any], Awaitable[Dict[str, int]]],
    fallback_fetch: Optional[Callable[[str, int, int], Awaitable[Any]]] = None,
    segment: str = SPOT,
) -> Dict[str, Any]:
    """Assess -> fetch -> persist -> RE-assess, for one instrument-day.

    The closing re-assessment is the point: it reports what is true AFTER the
    attempt, from the warehouse itself, rather than what the fetch claimed. A
    broker that returns a truncated day is therefore reported as still-incomplete
    rather than as a success.

    `primary_fetch(instrument)` -> DataFrame (Upstox intraday; no date args).
    `fallback_fetch(instrument, start_ms, end_ms)` -> DataFrame, tried only when the
    primary yields nothing usable. `persist(instrument, df)` -> counts.
    """
    before = assess_completeness(
        iso_date, await _present_ts(db, instrument, iso_date),
        segment=segment, now_ms=now_ms)

    out: Dict[str, Any] = {
        "instrument": instrument, "date": iso_date, "segment": segment,
        "before": before, "source": None, "attempts": 0,
        "fetched": 0, "written": 0, "errors": [],
    }
    if before["complete"]:
        out["after"] = before
        out["status"] = "already_complete"
        return out

    cutoff = _last_closed_minute_ms(now_ms)
    sources: List[tuple] = [("upstox_intraday", lambda: primary_fetch(instrument))]
    if fallback_fetch is not None:
        lo = before["first_missing"] or expected_minute_ts(
            iso_date, segment=segment, now_ms=now_ms)[0]
        hi = min(before["last_missing"] or cutoff, cutoff)
        sources.append(("flattrade_tpseries", lambda: fallback_fetch(instrument, lo, hi)))

    for name, call in sources:
        if out["attempts"] >= MAX_ATTEMPTS_PER_INSTRUMENT:
            break
        out["attempts"] += 1
        try:
            df = await call()
        except Exception as exc:                       # noqa: BLE001
            out["errors"].append(f"{name}: {str(exc)[:200]}")
            log.warning("candle recovery: %s fetch failed for %s: %s",
                        name, instrument, exc)
            continue
        if df is None or getattr(df, "empty", True):
            out["errors"].append(f"{name}: empty")
            continue

        # INVARIANT 1 — never write the in-progress minute. persist_candles_df is
        # an unconditional $set; a partial current bar would otherwise overwrite a
        # complete roller-built one.
        try:
            usable = df[df["ts"] <= cutoff]
        except Exception:                              # noqa: BLE001
            usable = df
        if getattr(usable, "empty", True):
            out["errors"].append(f"{name}: nothing closed yet")
            continue

        out["fetched"] = int(len(usable))
        try:
            saved = await persist(instrument, usable)
        except Exception as exc:                       # noqa: BLE001
            out["errors"].append(f"{name}: persist failed: {str(exc)[:200]}")
            log.exception("candle recovery: persist failed for %s", instrument)
            continue
        out["written"] = int((saved or {}).get("candles_added") or 0) + \
            int((saved or {}).get("candles_updated") or 0)
        out["source"] = name
        break

    after = assess_completeness(
        iso_date, await _present_ts(db, instrument, iso_date),
        segment=segment, now_ms=now_ms)
    out["after"] = after
    out["status"] = ("recovered" if after["complete"]
                     else ("improved" if after["missing"] < before["missing"]
                           else "unresolved"))
    log.info("candle recovery %s %s: %s  %d/%d -> %d/%d  source=%s  gaps=%s",
             instrument, iso_date, out["status"],
             before["present"], before["expected"],
             after["present"], after["expected"], out["source"],
             format_ranges_ist(after["ranges"]))
    return out


async def recover_today(
    db: Any,
    instruments: Sequence[str],
    *,
    primary_fetch: Callable[[str], Awaitable[Any]],
    persist: Callable[[str, Any], Awaitable[Dict[str, int]]],
    fallback_fetch: Optional[Callable[[str, int, int], Awaitable[Any]]] = None,
    now_ms: Optional[int] = None,
    segment: str = SPOT,
) -> Dict[str, Any]:
    """Recover every instrument for the current IST trading day, sequentially.

    Sequential and spaced ON PURPOSE: the Upstox rate budget is shared with the
    user's live account, and recovery is a rare, small job. Parallelism would buy
    nothing and risks a 429 during a live session.

    Never raises. This runs on the startup path and inside the feed supervisor;
    a recovery failure must degrade to "reported unresolved", never take the
    backend down.
    """
    now = now_ms if now_ms is not None else int(datetime.now(timezone.utc).timestamp() * 1000)
    iso = _today_ist(now)
    results: List[Dict[str, Any]] = []

    for i, inst in enumerate(instruments or ()):
        if i:
            await asyncio.sleep(FETCH_SPACING_SEC)
        try:
            results.append(await recover_instrument(
                db, str(inst).upper(), iso_date=iso, now_ms=now,
                primary_fetch=primary_fetch, persist=persist,
                fallback_fetch=fallback_fetch, segment=segment))
        except Exception as exc:                       # noqa: BLE001
            log.exception("candle recovery: unhandled failure for %s", inst)
            results.append({"instrument": str(inst).upper(), "date": iso,
                            "status": "error", "errors": [str(exc)[:200]],
                            "before": None, "after": None})

    unresolved = [r for r in results
                  if r.get("status") in ("unresolved", "improved", "error")]
    return {
        "date": iso,
        "ran_at": datetime.fromtimestamp(now / 1000, timezone.utc).isoformat(),
        "instruments": results,
        "complete": not unresolved,
        "unresolved": [r["instrument"] for r in unresolved],
    }


# ---------------------------------------------------------------------------
# Any-day recovery. The two Upstox endpoints are NOT interchangeable, and picking
# the wrong one silently returns nothing:
#
#   today      -> /v3/historical-candle/intraday/{key}/minutes/1   (no date args)
#   prior day  -> /v3/historical-candle/{key}/minutes/1/{to}/{from}
#
# Verified live on 2026-08-15: the historical endpoint returned status=success
# with 375 candles for 2026-08-14 (09:15..15:29), while the intraday endpoint
# returned status=success with 0 candles because that day was a holiday. The
# historical endpoint is EMPTY for the in-progress day — the documented limitation
# that made the tick roller necessary in the first place.
#
# Without this, a gap on a prior day could never be closed: 2026-08-13 still shows
# 368/375 on all three instruments (a feed-level dropout at 09:15-09:18, 09:54 and
# 10:01-10:02), and today-only recovery would never have touched it.
# ---------------------------------------------------------------------------

async def recover_day(
    db: Any,
    instrument: str,
    *,
    iso_date: str,
    now_ms: int,
    persist: Callable[[str, Any], Awaitable[Dict[str, int]]],
    today_iso: str,
    intraday_fetch: Optional[Callable[[str], Awaitable[Any]]] = None,
    historical_fetch: Optional[Callable[[str, str], Awaitable[Any]]] = None,
    fallback_fetch: Optional[Callable[[str, int, int], Awaitable[Any]]] = None,
    segment: str = SPOT,
) -> Dict[str, Any]:
    """Recover one instrument-day, choosing the source by WHICH day it is.

    A COMPLETED day is assessed against the whole session: ``now_ms`` must not
    truncate it, or a gap at 15:20 yesterday would be invisible this morning.
    Only today is bounded by the clock, and only to exclude the minute still
    forming.
    """
    is_today = str(iso_date) == str(today_iso)
    assess_now = now_ms if is_today else None

    before = assess_completeness(
        iso_date, await _present_ts(db, instrument, iso_date),
        segment=segment, now_ms=assess_now)

    out: Dict[str, Any] = {
        "instrument": instrument, "date": iso_date, "segment": segment,
        "is_today": is_today, "before": before, "source": None, "attempts": 0,
        "fetched": 0, "written": 0, "errors": [],
    }
    if before["complete"]:
        out["after"] = before
        out["status"] = "already_complete"
        return out

    # Only today's in-progress minute needs excluding; a completed day has none.
    cutoff = _last_closed_minute_ms(now_ms) if is_today else None

    sources: List[tuple] = []
    if is_today and intraday_fetch is not None:
        sources.append(("upstox_intraday", lambda: intraday_fetch(instrument)))
    if (not is_today) and historical_fetch is not None:
        sources.append(("upstox_historical",
                        lambda: historical_fetch(instrument, iso_date)))
    if fallback_fetch is not None:
        lo = before["first_missing"]
        hi = before["last_missing"]
        if lo is not None and hi is not None:
            if cutoff is not None:
                hi = min(hi, cutoff)
            sources.append(("flattrade_tpseries",
                            lambda: fallback_fetch(instrument, lo, hi)))

    for name, call in sources:
        if out["attempts"] >= MAX_ATTEMPTS_PER_INSTRUMENT:
            break
        out["attempts"] += 1
        try:
            df = await call()
        except Exception as exc:                       # noqa: BLE001
            out["errors"].append(f"{name}: {str(exc)[:200]}")
            log.warning("candle recovery: %s failed for %s %s: %s",
                        name, instrument, iso_date, exc)
            continue
        if df is None or getattr(df, "empty", True):
            out["errors"].append(f"{name}: empty")
            continue
        usable = df
        if cutoff is not None:
            try:
                usable = df[df["ts"] <= cutoff]
            except Exception:                          # noqa: BLE001
                usable = df
        if getattr(usable, "empty", True):
            out["errors"].append(f"{name}: nothing closed yet")
            continue
        out["fetched"] = int(len(usable))
        try:
            saved = await persist(instrument, usable)
        except Exception as exc:                       # noqa: BLE001
            out["errors"].append(f"{name}: persist failed: {str(exc)[:200]}")
            log.exception("candle recovery: persist failed for %s %s",
                          instrument, iso_date)
            continue
        out["written"] = int((saved or {}).get("candles_added") or 0) + \
            int((saved or {}).get("candles_updated") or 0)
        out["source"] = name
        break

    after = assess_completeness(
        iso_date, await _present_ts(db, instrument, iso_date),
        segment=segment, now_ms=assess_now)
    out["after"] = after
    out["status"] = ("recovered" if after["complete"]
                     else ("improved" if after["missing"] < before["missing"]
                           else "unresolved"))
    log.info("candle recovery %s %s: %s  %d/%d -> %d/%d  source=%s  gaps=%s",
             instrument, iso_date, out["status"],
             before["present"], before["expected"],
             after["present"], after["expected"], out["source"],
             format_ranges_ist(after["ranges"]))
    return out


async def recover_recent_days(
    db: Any,
    instruments: Sequence[str],
    *,
    days: int = 3,
    persist: Callable[[str, Any], Awaitable[Dict[str, int]]],
    intraday_fetch: Optional[Callable[[str], Awaitable[Any]]] = None,
    historical_fetch: Optional[Callable[[str, str], Awaitable[Any]]] = None,
    fallback_fetch: Optional[Callable[[str, int, int], Awaitable[Any]]] = None,
    now_ms: Optional[int] = None,
    segment: str = SPOT,
) -> Dict[str, Any]:
    """Sweep the last ``days`` TRADING days, newest first, for every instrument.

    Today is repaired first because it is the one the live path is reading. Only
    trading days are visited: requesting a holiday would spend a call to be told
    the market was shut, and the completeness model already expects zero candles
    there.

    Never raises. A sweep that took the supervisor down would be a worse failure
    than the gaps it repairs.
    """
    from app.nse_calendar import is_trading_day

    now = now_ms if now_ms is not None else int(datetime.now(timezone.utc).timestamp() * 1000)
    today_iso = _today_ist(now)

    wanted: List[str] = []
    probe = datetime.fromtimestamp(now / 1000, IST).date()
    while len(wanted) < max(1, int(days)):
        iso = probe.isoformat()
        if is_trading_day(iso):
            wanted.append(iso)
        probe -= timedelta(days=1)

    day_results: List[Dict[str, Any]] = []
    unresolved: List[str] = []
    first = True
    for iso in wanted:
        for inst in instruments or ():
            if not first:
                await asyncio.sleep(FETCH_SPACING_SEC)
            first = False
            try:
                r = await recover_day(
                    db, str(inst).upper(), iso_date=iso, now_ms=now, persist=persist,
                    today_iso=today_iso, intraday_fetch=intraday_fetch,
                    historical_fetch=historical_fetch, fallback_fetch=fallback_fetch,
                    segment=segment)
            except Exception as exc:                   # noqa: BLE001
                log.exception("candle recovery: unhandled failure for %s %s", inst, iso)
                r = {"instrument": str(inst).upper(), "date": iso, "status": "error",
                     "errors": [str(exc)[:200]], "before": None, "after": None}
            day_results.append(r)
            if r.get("status") in ("unresolved", "improved", "error"):
                unresolved.append(f"{r['instrument']}@{iso}")

    return {
        "today": today_iso,
        "days": wanted,
        "ran_at": datetime.fromtimestamp(now / 1000, timezone.utc).isoformat(),
        "results": day_results,
        "complete": not unresolved,
        "unresolved": unresolved,
    }
