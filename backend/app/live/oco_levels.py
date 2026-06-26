"""Catastrophe-band OCO levels — DERIVED strictly wider than the software guard's
stop so the broker-resting OCO is a pure PC-down last-resort that never races the
in-process guard (which always exits first while alive)."""
import math
from typing import Optional, Tuple
from app.live.order_builder import round_to_tick

DEFAULT_STOP_PCT = 50.0      # configured floor; widened past the guard below
DEFAULT_TARGET_PCT = 135.0
MIN_GAP_PP = 15.0            # catastrophe stop must be >= guard stop + this many points
CROSS_PCT = 2.0             # marketable buffer: SELL leg limit sits below its trigger so it clears

def _finite_pos(x) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) and v > 0 else None

def compute_catastrophe_band(entry, *, guard_stop_pct, stop_pct=None, target_pct=None,
                             tick: float = 0.05) -> Optional[Tuple[float, float, float, float]]:
    """Return (sl_trigger, sl_limit, tp_trigger, tp_limit) for a long-option SELL OCO,
    or None if entry is not finite-positive. eff_stop_pct = max(configured|default,
    guard_stop_pct + MIN_GAP_PP) so sl_trigger is always a LOWER premium than the
    guard's own stop level — preventing a same-premium double-fire."""
    e = _finite_pos(entry)
    if e is None:
        return None
    cfg = DEFAULT_STOP_PCT if stop_pct is None else float(stop_pct)
    gsp = float(guard_stop_pct or 0.0)
    eff = max(cfg, gsp + MIN_GAP_PP)
    eff = min(eff, 95.0)  # never deeper than ~5% of premium remaining
    tp = DEFAULT_TARGET_PCT if target_pct is None else float(target_pct)
    sl_trigger = round_to_tick(e * (1 - eff / 100.0), tick, mode="down")
    tp_trigger = round_to_tick(e * (1 + tp / 100.0), tick, mode="down")
    sl_limit = round_to_tick(sl_trigger * (1 - CROSS_PCT / 100.0), tick, mode="down")
    tp_limit = round_to_tick(tp_trigger * (1 - CROSS_PCT / 100.0), tick, mode="down")
    if min(sl_trigger, sl_limit, tp_trigger, tp_limit) <= 0:
        return None
    return sl_trigger, sl_limit, tp_trigger, tp_limit
