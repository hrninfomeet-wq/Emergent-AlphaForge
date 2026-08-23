"""Reusable option-buying screen — the gate that runs BEFORE a plugin is written.

[`docs/OPTION_BUYING_MICROSTRUCTURE_2026-08.md`](../../docs/OPTION_BUYING_MICROSTRUCTURE_2026-08.md)
measured the ATM buyer's payoff on this warehouse and found MFE/MAE of 0.90-0.95 —
negative before costs. It closes by saying the five screening scripts behind it were
"throwaway analysis, not shipped code" and that the screen "is cheap to rebuild and
should be re-run before any new campaign — it kills candidates before a plugin is
written."

Rebuilding a measurement from scratch each campaign is how two campaigns end up
disagreeing about what the same data says. This module is that screen, shipped: pure,
tested, and callable from `backend/scripts/screen_option_buying.py`.

Everything here is PURE (pandas/numpy only, no motor, no DB). The script does the I/O.

Three things this module exists to enforce, each one a lesson already paid for:

1. **Session-level statistics, never pooled bar counts.** Overlapping 1-minute windows
   are ~30x redundant; a pooled mean over 34k of them is roughly 1,100 independent
   observations. Hypothesis #5 in the microstructure register looked positive pooled
   (+1.78%) and collapsed to a per-session median of -5.49% with 1.4% of sessions
   positive. :func:`session_level_stats` reports the per-session median and a
   session-level t-stat, and nothing else is a result.

2. **Causal conditioning.** Any threshold a condition uses must be built from PRIOR
   sessions only. :func:`causal_session_stat` does that; a condition that peeks at its
   own session's distribution will manufacture an edge that does not exist forward.

3. **Net, not gross.** The 0DTE trap in that register was a gross-move-versus-friction
   comparison that ignored theta. :func:`net_hold_return_pct` charges the real friction
   the app models (`app.live_friction.fill_premium` semantics + statutory charges) so a
   cell cannot look good for the reason 0DTE looked good.

The screen answers one question — *is there anything to find?* — and its verdict is
advisory input to a human decision, not an automated promotion.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd


#: Minimum independent sessions before a per-session statistic is reported at all.
#: Below this the t-stat is noise and the cell is returned with ``sufficient=False``.
MIN_SESSIONS_FOR_STAT = 20

#: Session-level t-stat a positive cell must clear to be worth a plugin. Set by
#: `OPTION_BUYING_MICROSTRUCTURE_2026-08.md` §7 ("a session-level t-stat > 2 on an
#: untouched holdout before it means anything"). Applied here to TRAIN as a
#: pre-filter — clearing it on train earns the right to be tested, nothing more.
SCREEN_T_STAT_GATE = 2.0

#: An MFE/MAE ratio below this is the unconditioned base rate (measured 0.90-0.95).
#: A conditioned cell that does not clear it has not conditioned on anything.
BASE_RATE_MFE_MAE = 0.95


# ---------------------------------------------------------------------------
# Excursion measurement
# ---------------------------------------------------------------------------

def forward_excursions(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    horizon: int,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-bar forward MFE / MAE / net move over ``horizon`` bars, for a LONG.

    Entry is ``close[i]``; the excursion window is bars ``i+1 .. i+horizon``
    inclusive, so the entry bar's own extremes never count (a bar cannot be
    entered at its close and also claim its own high).

    Returns ``(mfe, mae, net)`` in premium POINTS, each NaN where the full
    horizon does not fit inside the array. MFE and MAE are clipped at 0: a
    window that never traded above entry has MFE 0, not a negative number.
    """
    n = len(close)
    mfe = np.full(n, np.nan, dtype=float)
    mae = np.full(n, np.nan, dtype=float)
    net = np.full(n, np.nan, dtype=float)
    h = int(horizon)
    if h < 1 or n == 0:
        return mfe, mae, net

    # Rolling forward max/min over the (i+1 .. i+h) window. Done with a strided
    # cumulative pass rather than a python loop so a 400-session sweep stays cheap.
    for i in range(n - h):
        window_hi = high[i + 1: i + 1 + h]
        window_lo = low[i + 1: i + 1 + h]
        entry = close[i]
        if not math.isfinite(entry) or entry <= 0:
            continue
        mfe[i] = max(0.0, float(np.nanmax(window_hi)) - entry)
        mae[i] = max(0.0, entry - float(np.nanmin(window_lo)))
        net[i] = float(close[i + h]) - entry
    return mfe, mae, net


def mfe_mae_ratio(mfe: Sequence[float], mae: Sequence[float]) -> Optional[float]:
    """Median MFE divided by median MAE — the register's headline statistic.

    Uses the ratio OF MEDIANS (not the median of per-bar ratios), matching how
    `OPTION_BUYING_MICROSTRUCTURE_2026-08.md` §1 reports it, so a new number is
    comparable against the 0.90-0.95 already on record. Per-bar ratios are
    undefined whenever MAE is 0, which is common at short horizons, and dropping
    those bars would bias the statistic upward.

    Two degenerate cases are deliberately distinguished, because collapsing them
    is how a screen reports "no edge" for the best series it will ever see:

    * ``None``          — no usable observations. Nothing was measured.
    * ``math.inf``      — the median bar never traded against the entry at all
      (median MAE 0) while median MFE is positive. That is an unbounded-favourable
      cell, not a missing one. Real premium series over a multi-bar window
      essentially never do this; a synthetic or very thin sample can.

    A cell where BOTH medians are 0 (a frozen series) is ``None`` — no
    information either way.
    """
    a = np.asarray(mfe, dtype=float)
    b = np.asarray(mae, dtype=float)
    a = a[np.isfinite(a)]
    b = b[np.isfinite(b)]
    if a.size == 0 or b.size == 0:
        return None
    med_mfe = float(np.median(a))
    med_mae = float(np.median(b))
    if med_mae <= 0:
        return math.inf if med_mfe > 0 else None
    return med_mfe / med_mae


# ---------------------------------------------------------------------------
# Net-of-friction hold return
# ---------------------------------------------------------------------------

def net_hold_return_pct(
    *,
    entry_premium: float,
    exit_premium: float,
    spread_pct_per_side: float,
    charges_pct_round_trip: float = 0.0,
) -> Optional[float]:
    """Net return on premium for one buy->sell round trip, as a percentage.

    ``spread_pct_per_side`` is the %-of-premium bid-ask model the app uses
    (`app.option_costs.spread_pts_for_premium`): the buyer pays it on entry and
    gives it up on exit. ``charges_pct_round_trip`` folds the statutory rupee
    charges in as a percentage of entry turnover so the caller can pass the
    figure `app.option_costs.round_trip_charges` produced for the real quantity
    rather than re-deriving rates here.

    This is the NET measurement §3 of the register turns on. Gross move over
    spread is the metric that made 0DTE look best and it is the wrong one.
    """
    e = float(entry_premium)
    x = float(exit_premium)
    if not math.isfinite(e) or not math.isfinite(x) or e <= 0:
        return None
    s = max(0.0, float(spread_pct_per_side)) / 100.0
    fill_in = e * (1.0 + s)
    fill_out = x * (1.0 - s)
    gross = (fill_out - fill_in) / fill_in
    return float((gross - max(0.0, float(charges_pct_round_trip)) / 100.0) * 100.0)


# ---------------------------------------------------------------------------
# Session-level statistics — the only kind that count
# ---------------------------------------------------------------------------

@dataclass
class SessionStat:
    """A per-session-median statistic with its session-level significance."""
    n_sessions: int
    median_of_session_medians: Optional[float]
    mean_of_session_medians: Optional[float]
    t_stat: Optional[float]
    share_sessions_positive: Optional[float]
    sufficient: bool
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "n_sessions": self.n_sessions,
            "median": self.median_of_session_medians,
            "mean": self.mean_of_session_medians,
            "t_stat": self.t_stat,
            "share_sessions_positive": self.share_sessions_positive,
            "sufficient": self.sufficient,
            "note": self.note,
        }


def session_level_stats(
    values: Sequence[float],
    session_dates: Sequence[str],
    *,
    min_sessions: int = MIN_SESSIONS_FOR_STAT,
) -> SessionStat:
    """Collapse per-bar values to one median per session, then test across sessions.

    This is the method lesson from the register's hypothesis #5, enforced in code:
    the primary statistical unit is a SESSION, because intraday bars from the same
    day share conditions and overlapping forward windows re-use the same price path.

    The t-stat is a one-sample t against zero over the per-session medians —
    ``mean / (sd / sqrt(n))`` with the sample (ddof=1) standard deviation.
    """
    v = np.asarray(values, dtype=float)
    d = np.asarray(session_dates, dtype=object)
    if v.size != d.size:
        raise ValueError("values and session_dates must be the same length")

    finite = np.isfinite(v)
    v, d = v[finite], d[finite]
    if v.size == 0:
        return SessionStat(0, None, None, None, None, False, "no finite observations")

    frame = pd.DataFrame({"v": v, "d": d})
    per_session = frame.groupby("d", sort=True)["v"].median()
    n = int(per_session.size)
    if n == 0:
        return SessionStat(0, None, None, None, None, False, "no sessions")

    arr = per_session.to_numpy(dtype=float)
    mean = float(np.mean(arr))
    median = float(np.median(arr))
    share_pos = float(np.mean(arr > 0))

    t_stat: Optional[float] = None
    if n >= 2:
        sd = float(np.std(arr, ddof=1))
        if sd > 0:
            t_stat = mean / (sd / math.sqrt(n))

    sufficient = n >= int(min_sessions)
    note = "" if sufficient else f"only {n} sessions (< {min_sessions}); treat as unpowered"
    return SessionStat(n, median, mean, t_stat, share_pos, sufficient, note)


# ---------------------------------------------------------------------------
# Causal conditioning helpers
# ---------------------------------------------------------------------------

def causal_session_stat(
    session_dates: Sequence[str],
    per_session_value: Dict[str, float],
    *,
    lookback_sessions: int,
    min_sessions: int = 5,
) -> pd.Series:
    """Rolling statistic over the N sessions STRICTLY BEFORE each session.

    Returns a Series indexed like ``session_dates`` holding, for each row, the
    mean of ``per_session_value`` over the previous ``lookback_sessions``
    sessions — never including the row's own session.

    Any threshold a screen condition uses must come through here. A condition
    built from the same session's distribution is not a hypothesis, it is a
    look-ahead, and it will not survive forward.
    """
    dates = pd.Index(pd.unique(pd.Series(list(session_dates), dtype=object))).sort_values()
    ordered = [per_session_value.get(str(d), np.nan) for d in dates]
    s = pd.Series(ordered, index=dates, dtype=float)
    prior = s.shift(1).rolling(int(lookback_sessions), min_periods=int(min_sessions)).mean()
    return pd.Series([prior.get(str(d), np.nan) for d in session_dates], dtype=float)


# ---------------------------------------------------------------------------
# Chronological split with a protected holdout
# ---------------------------------------------------------------------------

class HoldoutProtectionError(RuntimeError):
    """Raised when code asks for holdout sessions without the explicit unlock."""


@dataclass
class Split:
    """A frozen chronological split. Holdout access is deliberately awkward.

    ``consumed`` holds sessions that lie after the validation boundary but have
    ALREADY been read by an earlier campaign. They are not train, not validation,
    and emphatically **not holdout** — a holdout is untouched by definition, and
    calling a spent window "protected" is the single most dangerous label this
    module could print. Only sessions after every consumed window are holdout.
    """
    train: List[str] = field(default_factory=list)
    validation: List[str] = field(default_factory=list)
    consumed: List[str] = field(default_factory=list)
    _holdout: List[str] = field(default_factory=list, repr=False)
    unlocked: bool = field(default=False, repr=False)

    @property
    def holdout(self) -> List[str]:
        if not self.unlocked:
            raise HoldoutProtectionError(
                "The final holdout is protected. It may be read ONCE, by a recorded "
                "list of finalists, after train+validation selection is frozen. "
                "Call unlock_holdout(reason=...) and write the reason into the "
                "campaign record before touching it."
            )
        return list(self._holdout)

    def unlock_holdout(self, *, reason: str) -> List[str]:
        if not str(reason or "").strip():
            raise ValueError("unlocking the holdout requires a written reason")
        self.unlocked = True
        return list(self._holdout)

    def counts(self) -> Dict[str, int]:
        return {
            "train": len(self.train),
            "validation": len(self.validation),
            "consumed": len(self.consumed),
            "holdout": len(self._holdout),
        }


def chronological_split(
    sessions: Iterable[str],
    *,
    train_end: str,
    validation_end: str,
    consumed_until: Optional[str] = None,
) -> Split:
    """Split ascending ISO session dates into train / validation / consumed / holdout.

    Boundaries are INCLUSIVE of their slice: ``train`` is ``<= train_end``,
    ``validation`` is ``(train_end, validation_end]``. No shuffling, no
    interleaving — an intraday strategy tested on shuffled days leaks tomorrow
    into today through the volatility regime.

    ``consumed_until`` names the last session an EARLIER campaign already read.
    Sessions in ``(validation_end, consumed_until]`` are returned as ``consumed``
    and are excluded from the holdout.

    This parameter exists because omitting it produced a genuinely dangerous
    number. Run against this warehouse the split reported **158 protected holdout
    sessions**, while prior campaigns had already read 2026-01-01 → 2026-07-10
    (see `PREMIUM_MOMENTUM_EDGE_VERDICT_2026-07.md`); only ~30 sessions were
    actually untouched. A holdout is untouched by definition, so a spent window
    counted into it does not merely inflate a number — it destroys the one
    property the holdout exists to provide, while displaying the word PROTECTED.
    """
    ordered = sorted({str(s) for s in sessions})
    train = [s for s in ordered if s <= str(train_end)]
    validation = [s for s in ordered if str(train_end) < s <= str(validation_end)]

    consumed: List[str] = []
    boundary = str(validation_end)
    if consumed_until and str(consumed_until) > boundary:
        consumed = [s for s in ordered if boundary < s <= str(consumed_until)]
        boundary = str(consumed_until)

    holdout = [s for s in ordered if s > boundary]
    return Split(train=train, validation=validation, consumed=consumed,
                 _holdout=holdout)


# ---------------------------------------------------------------------------
# The screen itself
# ---------------------------------------------------------------------------

@dataclass
class ScreenCell:
    """One (condition x horizon) measurement."""
    label: str
    horizon: int
    n_bars: int
    mfe_mae: Optional[float]
    net_pct: SessionStat
    verdict: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "label": self.label,
            "horizon": self.horizon,
            "n_bars": self.n_bars,
            "mfe_mae": self.mfe_mae,
            "net_pct": self.net_pct.to_dict(),
            "verdict": self.verdict,
        }


def classify_cell(
    *,
    mfe_mae: Optional[float],
    stat: SessionStat,
    base_rate: float = BASE_RATE_MFE_MAE,
    t_gate: float = SCREEN_T_STAT_GATE,
) -> str:
    """Turn one measured cell into a verdict string.

    The bar is deliberately high and deliberately dull:

    * ``UNPOWERED``  — too few sessions to say anything.
    * ``NO_EDGE``    — MFE/MAE at or below the unconditioned base rate. The
      condition did not condition on anything.
    * ``WEAK``       — MFE/MAE clears the base rate but net per-session is not
      significantly positive. Interesting, not investable.
    * ``CANDIDATE``  — clears the base rate AND a positive session-level t-stat.
      Earns the right to have a plugin written. Nothing more than that.
    """
    if not stat.sufficient:
        return "UNPOWERED"
    if mfe_mae is None or mfe_mae <= float(base_rate):
        return "NO_EDGE"
    t = stat.t_stat
    median = stat.median_of_session_medians
    if t is None or median is None:
        return "WEAK"
    if t > float(t_gate) and median > 0:
        return "CANDIDATE"
    return "WEAK"


def _contiguous_blocks(keys: np.ndarray) -> List[Tuple[int, int]]:
    """Half-open ``[start, stop)`` spans over runs of an identical key.

    Runs, not groups: two separated stretches of the same key are two blocks.
    That is deliberate — a forward window may only look inside the stretch of
    bars it is actually contiguous with.
    """
    n = len(keys)
    if n == 0:
        return []
    cuts = [0]
    for i in range(1, n):
        if keys[i] != keys[i - 1]:
            cuts.append(i)
    cuts.append(n)
    return [(cuts[i], cuts[i + 1]) for i in range(len(cuts) - 1)]


def screen_condition(
    frame: pd.DataFrame,
    *,
    label: str,
    horizons: Sequence[int],
    condition: Optional[pd.Series] = None,
    spread_pct_per_side: float = 1.0,
    charges_pct_round_trip: float = 0.0,
    min_sessions: int = MIN_SESSIONS_FOR_STAT,
    group_by: Optional[Sequence] = None,
) -> List[ScreenCell]:
    """Measure one entry condition across horizons on an option premium series.

    ``frame`` must carry columns ``session_date``, ``high``, ``low``, ``close``
    and be sorted ascending in time within each contiguous block. ``condition``
    is an optional boolean mask selecting eligible ENTRY bars; None screens every
    bar (the unconditioned base rate).

    **A forward window never crosses a block boundary.** ``group_by`` labels the
    blocks — pass one entry per row, e.g. ``session_date + "|" + side`` when a
    frame stacks a CE leg and a PE leg. It defaults to ``session_date``, so the
    safe behaviour is the default rather than something the caller must remember.

    This used to be the caller's problem, documented and not enforced. The one
    caller then concatenated every session and both option legs into a single
    frame, so each block boundary produced ``horizon`` bars whose "forward"
    excursion was measured against a different contract. A contract a docstring
    states and nothing checks is a contract that gets broken.
    """
    required = {"session_date", "high", "low", "close"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"frame is missing required columns: {sorted(missing)}")

    cells: List[ScreenCell] = []
    high = frame["high"].to_numpy(dtype=float)
    low = frame["low"].to_numpy(dtype=float)
    close = frame["close"].to_numpy(dtype=float)
    sessions = frame["session_date"].astype(str).to_numpy()

    if group_by is None:
        block_keys = sessions
    else:
        block_keys = np.asarray([str(k) for k in group_by], dtype=object)
        if block_keys.size != len(frame):
            raise ValueError("group_by length does not match frame length")
    blocks = _contiguous_blocks(block_keys)

    mask = (
        np.ones(len(frame), dtype=bool) if condition is None
        else np.asarray(condition, dtype=bool)
    )
    if mask.size != len(frame):
        raise ValueError("condition mask length does not match frame length")

    for h in horizons:
        # Excursions are computed INSIDE each block and stitched back, so the
        # last `h` bars of every block are correctly ineligible rather than
        # borrowing the next block's prices.
        mfe = np.full(len(frame), np.nan, dtype=float)
        mae = np.full(len(frame), np.nan, dtype=float)
        for start, stop in blocks:
            b_mfe, b_mae, _ = forward_excursions(
                high[start:stop], low[start:stop], close[start:stop], int(h))
            mfe[start:stop] = b_mfe
            mae[start:stop] = b_mae
        # A bar is eligible if the condition fires AND the full horizon fits.
        eligible = mask & np.isfinite(mfe) & np.isfinite(mae)
        if not eligible.any():
            cells.append(ScreenCell(label, int(h), 0, None,
                                    SessionStat(0, None, None, None, None, False,
                                                "no eligible bars"),
                                    "UNPOWERED"))
            continue

        idx = np.flatnonzero(eligible)
        ratio = mfe_mae_ratio(mfe[idx], mae[idx])

        nets: List[float] = []
        for i in idx:
            r = net_hold_return_pct(
                entry_premium=float(close[i]),
                exit_premium=float(close[i + int(h)]),
                spread_pct_per_side=spread_pct_per_side,
                charges_pct_round_trip=charges_pct_round_trip,
            )
            nets.append(np.nan if r is None else r)

        stat = session_level_stats(nets, sessions[idx], min_sessions=min_sessions)
        cells.append(ScreenCell(label, int(h), int(idx.size), ratio, stat,
                                classify_cell(mfe_mae=ratio, stat=stat)))
    return cells


def summarize_screen(cells: Sequence[ScreenCell]) -> Dict[str, Any]:
    """Roll a set of cells into one campaign-level verdict.

    A condition survives only if at least one horizon reaches ``CANDIDATE``. The
    summary keeps every cell so a reader can see the shape, not just the winner —
    a single CANDIDATE cell surrounded by NO_EDGE cells at neighbouring horizons
    is a multiple-comparisons artefact, and the caller should treat it as one.
    """
    by_verdict: Dict[str, int] = {}
    for c in cells:
        by_verdict[c.verdict] = by_verdict.get(c.verdict, 0) + 1
    candidates = [c for c in cells if c.verdict == "CANDIDATE"]
    survives = len(candidates) > 0
    fragile = survives and len(candidates) == 1 and len(cells) > 2
    return {
        "survives": survives,
        "fragile_single_horizon": fragile,
        "verdict_counts": by_verdict,
        "candidate_horizons": sorted(c.horizon for c in candidates),
        "cells": [c.to_dict() for c in cells],
    }
