"""Fail closed on UNVERIFIABLE DATA — and on nothing else.

The operator asked for this explicitly: "block new live strategy deployments when
required data cannot be verified, while clearly reporting the affected instruments,
intervals, and recovery status."

## Why this is not the gate that was removed twice

Two kinds of gate were deleted from this project on standing instruction, and both
were about *permission to trade*: the per-deployment ARM ceremony, and the
research-qualification gate that refused to go live until forward evidence passed.
Those judged the STRATEGY, and the operator's position is that arming is their call.

This gate judges the DATA, not the strategy. It answers "do we actually possess the
bars this deployment's signals will be computed from?" — a question with a factual
answer that the operator cannot override by deciding to accept risk, because a
strategy evaluated over a hole is not accepting known risk, it is computing on
fiction. On 2026-08-14 exactly that happened: NIFTY was missing 09:15-09:49 and the
evaluator's 200-bar lookback silently held 199 previous-session bars and one of
today's.

## Scope — deliberately the narrowest thing that works

BLOCKS: the `mode -> "live"` transition for a deployment (a NEW live activation).

NEVER BLOCKS, and must never be wired to:
  * exits of any kind — `live_position_guard`, `live_sl_monitor`, the OCO backstop
  * the kill switch or `square_off_open_paper_trades`
  * the 15:00 EOD square
  * an already-live deployment continuing to manage what it holds

Blocking an EXIT because data looks stale would strand a real position — strictly
worse than the disease. A position already open must always be closeable; this gate
only refuses to open a NEW front while the data is unverifiable.

Missing data is not the same as a bad day: a holiday, a pre-open moment, and a
market that has not opened yet all expect zero candles and therefore PASS.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.candle_gap import (
    assess_completeness,
    expected_minute_ts,
    format_ranges_ist,
)
from app.session_spec import SPOT

log = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

#: A live deployment must be able to see at least this fraction of the session so
#: far. It is 1.0 — any missing CLOSED minute blocks. There is no "mostly complete"
#: that is safe here: an indicator series is either continuous or it is not, and a
#: partial threshold would only invite tuning it downward after a false alarm.
REQUIRED_COVERAGE = 1.0


def evaluate_live_data_gate(
    completeness_by_instrument: Dict[str, Dict[str, Any]],
    *,
    recovery: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """PURE verdict from already-computed completeness reports.

    Pure so the policy is testable without a DB. Returns
    ``{ok, reason, blocked_instruments, detail, report}``; ``detail`` is the
    operator-facing sentence naming instrument, interval and recovery status.
    """
    blocked: List[str] = []
    lines: List[str] = []
    for inst, c in sorted((completeness_by_instrument or {}).items()):
        if not isinstance(c, dict):
            continue
        if c.get("complete"):
            continue
        blocked.append(inst)
        spans = format_ranges_ist(c.get("ranges") or [])
        more = " …" if c.get("ranges_truncated") else ""
        lines.append(
            f"{inst} 1m {c.get('present')}/{c.get('expected')} bars "
            f"({c.get('coverage_pct')}%), missing {', '.join(spans)}{more}")

    if not blocked:
        return {"ok": True, "reason": "data_verified", "blocked_instruments": [],
                "detail": "", "report": completeness_by_instrument or {}}

    status = "not attempted"
    if isinstance(recovery, dict):
        per = {r.get("instrument"): r.get("status")
               for r in (recovery.get("instruments") or []) if isinstance(r, dict)}
        bits = [f"{i}: {per.get(i, 'not attempted')}" for i in blocked]
        status = "; ".join(bits)

    return {
        "ok": False,
        "reason": "incomplete_market_data",
        "blocked_instruments": blocked,
        "detail": ("Today's 1-minute data is incomplete, so signals would be "
                   "computed over a hole. " + " · ".join(lines) +
                   f". Recovery — {status}."),
        "report": completeness_by_instrument or {},
    }


async def check_live_data_gate(
    db: Any,
    instruments: List[str],
    *,
    now_ms: Optional[int] = None,
    segment: str = SPOT,
    recovery: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Assess today's completeness for ``instruments`` and apply the gate.

    Fails CLOSED on its own failure: if the warehouse cannot be read, the answer is
    "cannot verify", which is precisely the condition this gate exists for. That is
    safe here only because the gate's blast radius is a single new activation —
    it is never consulted by an exit path.
    """
    now = now_ms if now_ms is not None else int(datetime.now(timezone.utc).timestamp() * 1000)
    iso = datetime.fromtimestamp(now / 1000, IST).strftime("%Y-%m-%d")

    # Nothing expected -> nothing to verify. A holiday, a weekend and a pre-open
    # moment all take this path, and it must not touch the warehouse at all: a
    # read error on a day with no candles would otherwise fail CLOSED on a
    # question that has no content. Checked once here rather than per instrument
    # because the session calendar is instrument-independent.
    if not expected_minute_ts(iso, segment=segment, now_ms=now):
        return {"ok": True, "reason": "no_candles_expected", "blocked_instruments": [],
                "detail": "", "report": {}}

    start = int(datetime.fromisoformat(f"{iso}T00:00:00+05:30").timestamp() * 1000)

    reports: Dict[str, Dict[str, Any]] = {}
    for inst in instruments or ():
        name = str(inst).upper()
        try:
            rows = await db.candles_1m.find(
                {"instrument": name, "ts": {"$gte": start, "$lt": start + 86_400_000}},
                {"_id": 0, "ts": 1},
            ).to_list(length=3000)
            reports[name] = assess_completeness(
                iso, [r.get("ts") for r in rows], segment=segment, now_ms=now)
        except Exception as exc:                       # noqa: BLE001
            log.warning("live data gate: cannot read candles for %s: %s", name, exc)
            reports[name] = {
                "date": iso, "segment": segment, "complete": False,
                "reason": "unreadable", "expected": None, "present": None,
                "missing": None, "coverage_pct": 0.0, "ranges": [],
                "ranges_truncated": False, "first_missing": None,
                "last_missing": None,
            }
    return evaluate_live_data_gate(reports, recovery=recovery)
