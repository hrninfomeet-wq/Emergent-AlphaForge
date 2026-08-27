"""ATM option-side flow: CE/PE volume and open interest, per spot bar.

Sibling to `app.data_columns`, and deliberately the same shape of module: PURE
pandas/python over rows the caller has already fetched. No motor, no I/O, so it
stays host-importable and testable. The fetch lives in
`app.warehouse.attach_required_data`.

Three things decide this module's design, and all three are load-bearing.

**1. The baseline cannot live in the strategy.** `deployment_evaluator` clamps
the live window to `LIVE_LOOKBACK_MAX = 1000` bars — under three sessions. A
`session_precompute` deriving a 20-session baseline would see full history in a
backtest and under three sessions live, computing a *different number* in each
path while both looked healthy. That is the `live-window-anchors-session-
indicators` failure (a session-VWAP anchor error of 2.12 ATR silently inverted
nine shipped strategies) and it is invisible to a backtest by construction. So
the z-scores are computed HERE, against a window the fetch chooses independently
of what the strategy holds, and the strategy reads a ready-made number.

**2. Missing is NaN, never 0.** `app.vix.build_asof_index` — which
`app.data_columns.asof_series` routes through — falls back to `0.0` for a row
that LACKS the field it is asked for, and raises on `None`. A z-score of `0.0`
reads as "perfectly typical", so an omitted field would leave a strategy inert
while every log line looked healthy. Every row this module emits therefore
carries every field in :data:`OPTION_FLOW_FIELDS` explicitly, as a float, NaN
where unknown. That invariant is pinned by test, not by convention.

**3. Contracts join by IDENTITY, never by token.** Callers must select bars by
``underlying`` + ``expiry_date`` + ``strike`` + ``side`` + ``ts``. A token join
returns zero rows and looks exactly like an empty warehouse — it already cost
this project one wrong "the warehouse is empty" verdict (deliverable §11.1).
This module never sees a token; it groups the rows it is handed by identity.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

IST = timezone(timedelta(hours=5, minutes=30))

#: How many prior sessions the causal baseline reaches back over.
BASELINE_SESSIONS = 20

#: Below this many usable prior sessions in a time-of-day bucket the z-score is
#: NaN. Requiring all 20 would be brittle (holidays, ingestion gaps); requiring
#: one would standardize against noise. Half is the compromise, and it is a real
#: guard: below it the value is UNKNOWN, which is not the same fact as 0.
MIN_BASELINE_SESSIONS = 10

#: The two legs of the ATM straddle this module measures.
SIDES = ("CE", "PE")

#: Every field every emitted row carries. See docstring note 2 — an omitted
#: field would be read as 0.0 downstream, so this tuple is the schema and the
#: builder fills all of it or NaN.
OPTION_FLOW_FIELDS: Tuple[str, ...] = (
    # raw, as printed on the ATM contract's own bar
    "ce_volume", "pe_volume", "ce_oi", "pe_oi",
    # within-session change in open interest
    "ce_oi_delta", "pe_oi_delta",
    # causal time-of-day z-scores of the four flow quantities
    "ce_volume_z", "pe_volume_z", "ce_oi_delta_z", "pe_oi_delta_z",
    # causal 20-session median of ATM bar volume (both legs), for a liquidity floor
    "atm_volume_median_20d",
)

NAN = float("nan")


def atm_strike(spot: float, step: int) -> int:
    """The at-the-money strike for `spot` on a `step`-spaced ladder."""
    step = int(step)
    if step <= 0:
        raise ValueError(f"strike_step must be positive, got {step!r}")
    return int(round(float(spot) / step) * step)


def session_date_of(ts_ms: Any) -> Optional[str]:
    """IST session date (YYYY-MM-DD) for an epoch-ms timestamp."""
    try:
        return (datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc)
                .astimezone(IST).strftime("%Y-%m-%d"))
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def bucket_of(ts_ms: Any) -> Optional[str]:
    """IST time-of-day bucket (HH:MM) — the unit the z-score baseline groups by.

    Option volume has a pronounced intraday shape (open and close spikes, a
    midday trough), so a z-score against an all-day distribution would flag
    every 09:20 bar as extreme. Comparing a bar only against the same minute on
    prior sessions is what makes the score mean "unusual FOR THIS TIME".
    """
    try:
        return (datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc)
                .astimezone(IST).strftime("%H:%M"))
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _first_close_by_session(spot_rows: Iterable[Dict[str, Any]]) -> Dict[str, float]:
    """Each session's FIRST spot close, by ts.

    Deliberately the session's first bar rather than the first bar inside an
    entry window. The data layer must be policy-free and identical in backtest
    and live: an entry-window anchor would be undefined before the window opens,
    so the ATM strike would appear mid-session and could differ between a live
    evaluation at 09:20 and a backtest of the same session.
    """
    best: Dict[str, Tuple[int, float]] = {}
    for r in spot_rows or ():
        ts = r.get("ts")
        date = session_date_of(ts)
        if date is None:
            continue
        try:
            close = float(r.get("close"))
        except (TypeError, ValueError):
            continue
        if close != close:  # NaN
            continue
        prev = best.get(date)
        if prev is None or int(ts) < prev[0]:
            best[date] = (int(ts), close)
    return {d: v[1] for d, v in best.items()}


def nearest_upcoming_expiry(session: str, expiries: Sequence[str]) -> Optional[str]:
    """The nearest expiry at or after `session`. None if the session is past
    every known expiry — which is a real state (the contract master stops before
    the frame does) and must degrade, not raise."""
    candidates = sorted(str(e) for e in (expiries or ()) if e)
    return next((e for e in candidates if e >= session), None)


def _atm_series_by_session(
    *,
    spot_rows: Sequence[Dict[str, Any]],
    option_rows: Sequence[Dict[str, Any]],
    expiries: Sequence[str],
    strike_step: int,
) -> Tuple[Dict[str, Dict[int, Dict[str, float]]], Dict[str, Any]]:
    """Per session, the ATM CE/PE bars keyed by ts.

    Returns ``({session: {ts: {ce_volume, pe_volume, ce_oi, pe_oi,
    ce_oi_delta, pe_oi_delta}}}, diagnostics)``.

    The ATM strike is fixed once per session from that session's first spot
    close; re-selecting it intrabar would let a later price decide which
    contract an earlier bar "should" have watched, which is lookahead.
    """
    first_close = _first_close_by_session(spot_rows)
    diag: Dict[str, Any] = defaultdict(int)

    # Resolve each session's target contract identity up front.
    target: Dict[str, Tuple[float, str]] = {}
    for session, close in first_close.items():
        expiry = nearest_upcoming_expiry(session, expiries)
        if expiry is None:
            diag["sessions_no_expiry"] += 1
            continue
        target[session] = (float(atm_strike(close, strike_step)), expiry)
    diag["sessions_with_target"] = len(target)

    # Bucket the option bars that match a session's ATM identity. Rows are
    # matched by (expiry_date, strike, side) — identity, never a token.
    by_session: Dict[str, Dict[int, Dict[str, float]]] = defaultdict(dict)
    for r in option_rows or ():
        ts = r.get("ts")
        session = session_date_of(ts)
        if session is None:
            diag["rows_unparseable_ts"] += 1
            continue
        want = target.get(session)
        if want is None:
            continue
        side = str(r.get("side") or "").upper()
        if side not in SIDES:
            diag["rows_unknown_side"] += 1
            continue
        try:
            strike = float(r.get("strike"))
        except (TypeError, ValueError):
            diag["rows_unparseable_strike"] += 1
            continue
        if strike != want[0] or str(r.get("expiry_date") or "") != want[1]:
            continue

        slot = by_session[session].setdefault(int(ts), {})
        prefix = side.lower()
        for src, dst in (("volume", f"{prefix}_volume"), ("oi", f"{prefix}_oi")):
            try:
                slot[dst] = float(r.get(src))
            except (TypeError, ValueError):
                slot[dst] = NAN
        diag["rows_matched"] += 1

    # Within-session OI deltas. A cross-session delta would straddle an expiry
    # roll and an ATM strike change, so the first bar of every session is NaN —
    # unknown, which is a different fact from "no change".
    for session, per_ts in by_session.items():
        for side in SIDES:
            prefix = side.lower()
            prev: Optional[float] = None
            for ts in sorted(per_ts):
                cur = per_ts[ts].get(f"{prefix}_oi", NAN)
                if prev is None or prev != prev or cur != cur:
                    per_ts[ts][f"{prefix}_oi_delta"] = NAN
                else:
                    per_ts[ts][f"{prefix}_oi_delta"] = cur - prev
                prev = cur if cur == cur else prev
    return by_session, dict(diag)


#: Which raw quantity each z-score standardizes.
Z_SOURCES: Dict[str, str] = {
    "ce_volume_z": "ce_volume",
    "pe_volume_z": "pe_volume",
    "ce_oi_delta_z": "ce_oi_delta",
    "pe_oi_delta_z": "pe_oi_delta",
}


def _rolling_stats(values, window: int, min_count: int):
    """Causal (mean, std) of the up-to-`window` values BEFORE each position.

    Position i sees ``values[max(0, i-window):i]`` — never ``values[i]`` itself,
    which is what makes the score causal. Below `min_count` samples both are NaN.

    Deliberately a two-pass numpy std rather than the cumsum identity
    E[x**2] - E[x]**2. Option volume runs to 1e5, so that identity differences
    two ~1e11 quantities and loses the low bits: a genuinely FLAT baseline comes
    back with std ~1e-3 instead of 0, the "std <= 0 => NaN" guard below never
    fires, and the column emits enormous z-scores manufactured from float noise.
    """
    import numpy as np

    if min_count < 2:
        # ddof=1 needs two samples; one would divide by zero and yield a z built
        # from a single observation, which is not a distribution.
        raise ValueError(f"min_count must be >= 2, got {min_count!r}")

    k = len(values)
    means = np.full(k, np.nan, dtype="float64")
    stds = np.full(k, np.nan, dtype="float64")
    # Growing windows — only positions [min_count, window). Position i has
    # exactly i prior samples, so starting at min_count IS the minimum-sample
    # guard; there is deliberately no second, redundant early return, because a
    # guard that another guard always covers can never be pinned by a test.
    for i in range(min_count, min(k, window)):
        w = values[:i]
        means[i] = w.mean()
        stds[i] = w.std(ddof=1)
    if k > window:
        from numpy.lib.stride_tricks import sliding_window_view
        win = sliding_window_view(values, window)
        idx = np.arange(window, k)
        sub = win[idx - window]
        means[idx] = sub.mean(axis=1)
        stds[idx] = sub.std(axis=1, ddof=1)
    return means, stds


def _causal_z_stats(
    by_session: Dict[str, Dict[int, Dict[str, float]]],
    *,
    window: int,
    min_count: int,
) -> Dict[str, Dict[Tuple[str, str], Tuple[float, float]]]:
    """``{z_field: {(session, bucket): (mean, std)}}`` over PRIOR sessions only.

    A session with no value at a bucket contributes nothing and is skipped
    rather than counted, so the sample size stays at `window` across holidays
    and ingestion gaps instead of silently shrinking.
    """
    import numpy as np

    series: Dict[str, Dict[str, List[Tuple[str, float]]]] = {
        f: defaultdict(list) for f in Z_SOURCES.values()
    }
    for session in sorted(by_session):
        per_ts = by_session[session]
        for ts in sorted(per_ts):
            bucket = bucket_of(ts)
            if bucket is None:
                continue
            for field in Z_SOURCES.values():
                val = per_ts[ts].get(field, NAN)
                if val is None or val != val:
                    continue
                series[field][bucket].append((session, float(val)))

    out: Dict[str, Dict[Tuple[str, str], Tuple[float, float]]] = {}
    for zfield, field in Z_SOURCES.items():
        stats: Dict[Tuple[str, str], Tuple[float, float]] = {}
        for bucket, pairs in series[field].items():
            vals = np.asarray([v for _, v in pairs], dtype="float64")
            means, stds = _rolling_stats(vals, window, min_count)
            for i, (session, _) in enumerate(pairs):
                stats[(session, bucket)] = (float(means[i]), float(stds[i]))
        out[zfield] = stats
    return out


def _causal_volume_median(
    by_session: Dict[str, Dict[int, Dict[str, float]]],
    *,
    window: int,
    min_count: int,
) -> Dict[str, float]:
    """``{session: median ATM bar volume over the prior <= window sessions}``.

    Deliberately NOT bucketed by time-of-day, unlike the z-scores. §4.1 of the
    deliverable specifies the liquidity floor as a plain "20-session causal
    median"; it exists to exclude a dead contract, not to normalize away the
    intraday volume shape, and the frozen spec is implemented literally.

    A bar counts only when BOTH legs printed: the straddle volume of a bar whose
    put leg is missing is unknown, not the call leg's volume.
    """
    import numpy as np

    per_session: Dict[str, List[float]] = {}
    for session in sorted(by_session):
        vals: List[float] = []
        for slot in by_session[session].values():
            ce = slot.get("ce_volume", NAN)
            pe = slot.get("pe_volume", NAN)
            if ce is None or pe is None or ce != ce or pe != pe:
                continue
            vals.append(float(ce) + float(pe))
        if vals:
            per_session[session] = vals

    ordered = sorted(per_session)
    out: Dict[str, float] = {}
    for i, session in enumerate(ordered):
        lo = max(0, i - window)
        if (i - lo) < min_count:
            out[session] = NAN
            continue
        pool: List[float] = []
        for prior in ordered[lo:i]:
            pool.extend(per_session[prior])
        out[session] = (float(np.median(np.asarray(pool, dtype="float64")))
                        if pool else NAN)
    return out


def build_option_flow_rows(
    *,
    spot_rows: Sequence[Dict[str, Any]],
    option_rows: Sequence[Dict[str, Any]],
    expiries: Sequence[str],
    strike_step: int,
    frame_ts: Sequence[int],
    baseline_sessions: int = BASELINE_SESSIONS,
    min_baseline_sessions: int = MIN_BASELINE_SESSIONS,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Per-bar ATM flow rows for `frame_ts`, in the shape `asof_series` consumes.

    `spot_rows` and `option_rows` must span the BASELINE window as well as the
    frame — the CALLER's query window, not the strategy's. `frame_ts` is what
    gets a row; the extra sessions exist only so the causal baseline is the same
    size in a backtest and live. That is the whole point of the module: see note
    1 in the module docstring.

    Every returned row carries every field in :data:`OPTION_FLOW_FIELDS` as a
    float, NaN where unknown (note 2).
    """
    by_session, diag = _atm_series_by_session(
        spot_rows=spot_rows, option_rows=option_rows,
        expiries=expiries, strike_step=strike_step,
    )
    window = int(baseline_sessions)
    min_count = int(min_baseline_sessions)
    zstats = _causal_z_stats(by_session, window=window, min_count=min_count)
    medians = _causal_volume_median(by_session, window=window, min_count=min_count)

    rows: List[Dict[str, Any]] = []
    for ts in sorted({int(t) for t in (frame_ts or ())}):
        session = session_date_of(ts)
        bucket = bucket_of(ts)
        slot = (by_session.get(session) or {}).get(ts) or {}

        row: Dict[str, Any] = {"ts": ts}
        for field in OPTION_FLOW_FIELDS:
            # No try/except here, deliberately. `_atm_series_by_session` already
            # coerces every raw value it stores, so a TypeError at this point
            # could only mean a NEW field was added without coercion — and
            # swallowing that into NaN would hide the bug behind the same
            # "missing data" face that a real warehouse gap wears. Let it raise.
            row[field] = float(slot.get(field, NAN))

        for zfield, field in Z_SOURCES.items():
            x = row.get(field, NAN)
            mean, std = zstats.get(zfield, {}).get((session, bucket), (NAN, NAN))
            # std == 0 is NOT a z of 0. A degenerate baseline cannot say whether
            # this bar is typical, and claiming it can is exactly how a dead
            # feature comes to read as a healthy one.
            if x != x or mean != mean or std != std or std <= 0.0:
                row[zfield] = NAN
            else:
                row[zfield] = (float(x) - mean) / std

        row["atm_volume_median_20d"] = float(medians.get(session, NAN))
        rows.append(row)

    diag["rows_emitted"] = len(rows)
    diag["sessions_seen"] = len(by_session)
    return rows, diag
