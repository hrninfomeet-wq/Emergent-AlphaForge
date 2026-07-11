"""Per-session-date precompute helpers.

Some strategies need constants that are fixed for a whole trading session --
the opening range, the day's opening gap, a session VWAP anchor, etc. Deriving
those INSIDE the per-bar signal loop (e.g. ``hist.iloc[:i+1]`` + a boolean mask
every bar) is O(N) per bar -> O(N^2) per backtest, which makes an option-rerank
"analyzing" pass grind for hours on a full year of 1-minute data.

These helpers compute each constant ONCE per ``session_date`` so the strategy can
look it up O(1) per bar. They generalize the original ``_compute_orb_for_session``
special-case in ``backtest.py``; a strategy exposes whichever it needs via
``StrategyBase.session_precompute`` and ``run_backtest`` merges the result into the
per-bar ctx.

Assumptions (already guaranteed by ``run_backtest``): rows are time-ordered, each
session occupies a contiguous block, and the frame carries a clean 0..N-1
RangeIndex so a group's positional index aligns with the per-bar loop counter
``i``. All helpers no-op gracefully (empty maps) when ``session_date`` is absent,
matching the original code's "return None" guard.
"""
from __future__ import annotations
from typing import Any, Dict
import pandas as pd


def _has_session_col(df: pd.DataFrame) -> bool:
    return "session_date" in getattr(df, "columns", [])


def orb_range_by_session(df: pd.DataFrame, range_minutes: int = 15) -> Dict[str, Dict]:
    """Opening range (first ``range_minutes`` bars) high/low per session_date.

    The range is exposed for the WHOLE session (no forming gate) -- this is the
    long-standing Opening-Range-Breakout behavior, kept byte-identical to the
    former ``_compute_orb_for_session``.
    """
    orb_hi: Dict[str, float] = {}
    orb_lo: Dict[str, float] = {}
    if not _has_session_col(df):
        return {"orb_hi": orb_hi, "orb_lo": orb_lo}
    for date, grp in df.groupby("session_date"):
        first_bars = grp.head(range_minutes)
        if len(first_bars) > 0:
            orb_hi[date] = float(first_bars["high"].max())
            orb_lo[date] = float(first_bars["low"].min())
    return {"orb_hi": orb_hi, "orb_lo": orb_lo}


def opening_range_by_session(df: pd.DataFrame, or_minutes: int) -> Dict[str, Dict]:
    """Gated opening range (first ``or_minutes`` bars) high/low per session_date.

    Unlike ``orb_range_by_session`` the range is only valid once the session has
    accumulated MORE than ``or_minutes`` bars ("still forming" before that).
    ``or_ready_idx[session]`` is the global RangeIndex position of the first bar
    at which the range is ready, so a strategy gates with
    ``i >= or_ready_idx[session]``. Sessions with <= or_minutes bars never form a
    range and are omitted entirely. Its shipped consumer (OpeningRangeAdaptive)
    was deliberately deleted; the helper stays for custom strategies.
    """
    or_hi: Dict[str, float] = {}
    or_lo: Dict[str, float] = {}
    or_ready_idx: Dict[str, int] = {}
    if not _has_session_col(df):
        return {"or_hi": or_hi, "or_lo": or_lo, "or_ready_idx": or_ready_idx}
    for date, grp in df.groupby("session_date"):
        if len(grp) <= or_minutes:
            continue  # OR never becomes ready this session -> no entry
        or_bars = grp.iloc[:or_minutes]
        or_hi[date] = float(or_bars["high"].max())
        or_lo[date] = float(or_bars["low"].min())
        # first "ready" bar = the (or_minutes+1)-th bar of the session, i.e. the
        # bar at which len(session bars so far) first exceeds or_minutes.
        or_ready_idx[date] = int(grp.index[or_minutes])
    return {"or_hi": or_hi, "or_lo": or_lo, "or_ready_idx": or_ready_idx}


def gap_by_session(df: pd.DataFrame) -> Dict[str, Dict]:
    """Day-open and prior-session close per session_date.

    Mirrors ``GapFade._gap``: ``day_open[session]`` is the session's first bar
    open; ``prev_close[session]`` is the last close of the immediately-preceding
    session. The first session has no prior close and is omitted from
    ``prev_close`` (the original returned None there).
    """
    day_open: Dict[str, float] = {}
    prev_close: Dict[str, float] = {}
    if not _has_session_col(df):
        return {"day_open": day_open, "prev_close": prev_close}
    prev_session_last_close = None
    for date, grp in df.groupby("session_date"):
        day_open[date] = float(grp["open"].iloc[0])
        if prev_session_last_close is not None:
            prev_close[date] = prev_session_last_close
        prev_session_last_close = float(grp["close"].iloc[-1])
    return {"day_open": day_open, "prev_close": prev_close}
