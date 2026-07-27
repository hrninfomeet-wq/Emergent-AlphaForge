"""Load-time DATA columns: warehouse-backed series joined onto the bar frame.

Sibling to `app.features`, with one decisive difference. A `FeatureGroup` is
PURE PANDAS over the frame it is handed (`compute(df, params)` — no I/O, by
contract, so the module stays host-importable with no motor dependency). A
`DataColumn` is backed by a SEPARATE warehouse series that must be fetched and
joined BEFORE indicator enrichment. That is precisely why India VIX can never
be a FeatureGroup: it lives in `candles_1m` under an AUX instrument, and no
amount of pandas over the spot frame can conjure it.

Three contracts this module holds:

* **Opt-in.** A strategy gets these columns only by declaring
  `StrategyBase.required_data`. An empty declaration means this module never
  runs and the frame is byte-identical to before — the same guarantee
  `app.features` gives for structural features.

* **Causal by construction.** Every value is joined AS-OF the bar's own
  timestamp: at-or-before, never after, bounded by `max_staleness_ms`. A bar at
  time T can only ever see a print stamped <= T. This is not a convention that
  callers must remember — it is the only join this module implements.

* **Honest about absence.** Where no print reaches a bar within the staleness
  bound the value is NaN, never a filled default. `attach_data_columns` also
  returns per-column coverage so a caller can surface "this column was present
  for 68% of your window" instead of letting a strategy silently score zero on
  the missing third. (That failure mode is not hypothetical — it is exactly what
  the dead `vix_boost_threshold` knob did, and what the VIX backfill to
  2024-11-25 was done to prevent.)

Pure: no motor, no I/O. The caller fetches the source rows and passes them in —
see `app.warehouse.attach_required_data`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from app.vix import VIX_INSTRUMENT, build_asof_index

_MS_PER_DAY = 24 * 60 * 60 * 1000


@dataclass(frozen=True)
class DataColumn:
    """A warehouse-backed column joined as-of the bar timestamp.

    `instrument` is the `candles_1m.instrument` the source series lives under.
    `max_staleness_ms` bounds how far back the as-of lookup may reach; beyond it
    the bar gets NaN rather than a stale print.
    """
    name: str
    column: str
    instrument: str
    max_staleness_ms: int
    description: str = ""
    source_field: str = "close"
    causal: bool = True


# name -> DataColumn. Deliberately tiny: this is a seam, not a framework.
DATA_COLUMN_REGISTRY: Dict[str, DataColumn] = {
    "vix": DataColumn(
        name="vix",
        column="vix",
        instrument=VIX_INSTRUMENT,
        # India VIX prints every minute alongside spot, so a same-session print
        # is the norm. Four calendar days lets the first bar of a session reach
        # the previous session's close across a long weekend or holiday cluster,
        # without ever reaching back far enough to pass off a stale regime as
        # current.
        max_staleness_ms=4 * _MS_PER_DAY,
        description=(
            "India VIX (INDIAVIX) close, as-of this bar's timestamp. The "
            "implied-volatility regime an options buyer is paying into. NaN "
            "where no print reaches the bar within the staleness bound."
        ),
    ),
}


class DataColumnError(Exception):
    """Raised when a strategy declares a data column the engine cannot provide.

    Mirrors `app.features.registry.FeatureError` so the authoring/feasibility
    layer can convert it into a REJECT-with-explanation rather than a crash.
    """

    def __init__(self, code: str, *, name: str = "", available: Optional[List[str]] = None):
        self.code = code
        self.name = name
        self.available = available or []
        super().__init__(
            f"{code}: data column {name!r} is not registered. "
            f"available={sorted(self.available)}"
        )


def resolve_data_columns(required: Sequence[str]) -> List[DataColumn]:
    """Map declared names to their specs, preserving declaration order and
    dropping duplicates. Raises DataColumnError on any unknown name."""
    out: List[DataColumn] = []
    seen: set = set()
    for name in required or ():
        if name in seen:
            continue
        spec = DATA_COLUMN_REGISTRY.get(name)
        if spec is None:
            raise DataColumnError("unknown_data_column", name=name,
                                  available=list(DATA_COLUMN_REGISTRY))
        seen.add(name)
        out.append(spec)
    return out


def data_column_names(required: Sequence[str]) -> List[str]:
    """The frame columns a declaration materializes (for allow-listing)."""
    return [spec.column for spec in resolve_data_columns(required)]


def asof_series(
    bar_ts: "pd.Series | Iterable[int]",
    source_rows: Sequence[Dict[str, Any]],
    max_staleness_ms: Optional[int],
    *,
    source_field: str = "close",
) -> pd.Series:
    """As-of (at-or-before) join of `source_rows` onto `bar_ts`.

    Vectorized via `pd.merge_asof`, but deliberately routed through
    `app.vix.build_asof_index` first so the sort/parse/dedup semantics are the
    SAME single implementation the scalar `vix_asof` uses. Equivalence with that
    scalar path is pinned by a property test over randomized frames
    (tests/test_data_columns.py) — the two must never drift.

    Returns a float Series aligned to `bar_ts`'s index; NaN where no print
    at-or-before the bar falls inside `max_staleness_ms`.
    """
    ts = bar_ts if isinstance(bar_ts, pd.Series) else pd.Series(list(bar_ts))
    index = build_asof_index(list(source_rows or []), value_field=source_field)
    src_ts = index["ts"]
    if len(ts) == 0:
        return pd.Series([], index=ts.index, dtype="float64")
    if not src_ts:
        return pd.Series(np.nan, index=ts.index, dtype="float64")

    left = pd.DataFrame({
        "_ts": pd.to_numeric(ts, errors="coerce").astype("Int64"),
        "_pos": np.arange(len(ts)),
    })
    # merge_asof requires a sorted key and rejects NaN keys; hold bad rows out
    # and reinstate them as NaN afterwards so a malformed ts can never silently
    # borrow a neighbour's value.
    good = left.dropna(subset=["_ts"]).copy()
    good["_ts"] = good["_ts"].astype("int64")
    good = good.sort_values("_ts", kind="mergesort")

    right = pd.DataFrame({
        "_ts": np.asarray(src_ts, dtype="int64"),
        "_val": np.asarray(index["close"], dtype="float64"),
    })

    merged = pd.merge_asof(
        good, right, on="_ts", direction="backward",
        tolerance=(None if max_staleness_ms is None else int(max_staleness_ms)),
    )
    out = pd.Series(np.nan, index=ts.index, dtype="float64")
    # Round with PYTHON's round(), element-wise — NOT np.round / Series.round.
    # app.vix.vix_asof (the scalar path the live premium gate uses) rounds with
    # builtin round(), and the two disagree on exact-half values: numpy scales by
    # 100 and rints, accumulating float error, while CPython uses a correctly
    # rounded decimal conversion. A randomized property test caught the split at
    # 16.715 -> 16.72 (numpy) vs 16.71 (python). Backtest and live must agree to
    # the paisa, so the vectorized path conforms to the scalar one.
    vals = merged["_val"].to_numpy()
    out.iloc[merged["_pos"].to_numpy()] = [
        np.nan if v != v else round(float(v), 2) for v in vals
    ]
    return out


def attach_data_columns(
    df: pd.DataFrame,
    specs: Sequence[DataColumn],
    sources: Dict[str, Sequence[Dict[str, Any]]],
) -> Tuple[pd.DataFrame, Dict[str, Dict[str, Any]]]:
    """Append every spec's column to a COPY of `df` (never mutates the caller's
    frame, which may be an optimizer cache frame) and report coverage.

    Coverage per column: {"bars": N, "present": M, "coverage_pct": ...,
    "sessions_missing": [...]} — enough for a caller to refuse or warn rather
    than quietly backtest a window the data does not cover.
    """
    if not specs:
        return df, {}
    if df is None or df.empty or "ts" not in df.columns:
        return df, {}

    out = df.copy()
    coverage: Dict[str, Dict[str, Any]] = {}
    for spec in specs:
        series = asof_series(out["ts"], sources.get(spec.name) or (),
                             spec.max_staleness_ms, source_field=spec.source_field)
        out[spec.column] = series
        present = int(series.notna().sum())
        bars = int(len(series))
        info: Dict[str, Any] = {
            "bars": bars,
            "present": present,
            "coverage_pct": round(100.0 * present / bars, 2) if bars else 0.0,
        }
        if "session_date" in out.columns and present < bars:
            missing = sorted(
                str(s) for s in out.loc[series.isna(), "session_date"].dropna().unique()
            )
            info["sessions_missing"] = missing[:50]
            info["sessions_missing_count"] = len(missing)
        coverage[spec.column] = info
    return out, coverage
