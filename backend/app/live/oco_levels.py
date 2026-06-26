"""Catastrophe-band OCO levels — DERIVED strictly wider than the software guard's
stop so the broker-resting OCO is a pure PC-down last-resort that never races the
in-process guard (which always exits first while alive)."""
import math
from typing import Optional, Tuple
from app.live.order_builder import round_to_tick

DEFAULT_STOP_PCT = 50.0      # configured floor; widened past the guard below
DEFAULT_TARGET_PCT = 135.0
MIN_GAP_PP = 15.0            # catastrophe stop must be >= guard stop + this many points
MAX_STOP_PCT = 95.0         # never deeper than ~5% of premium remaining
MAX_TARGET_PCT = 1000.0     # cap an absurd/mistyped target so a bogus value never rests a nonsense TP
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
    or None if entry is not finite-positive OR there is no safe gap below the guard.
    eff_stop_pct = max(configured|default, guard_stop_pct + MIN_GAP_PP) so sl_trigger
    is always a STRICTLY LOWER premium than the guard's own stop level — preventing a
    same-premium-or-shallower double-fire.

    GRACEFUL DEGRADE: if (guard_stop_pct + MIN_GAP_PP) leaves no room below the
    ~5%-premium floor (MAX_STOP_PCT), return None rather than clamping the OCO up to
    the guard level (which would invert the design). The caller then leaves the
    position software-guard-only and raises the no_broker_backstop alert."""
    e = _finite_pos(entry)
    if e is None:
        return None
    gsp = float(guard_stop_pct or 0.0)
    # No safe gap below the guard within the premium floor → degrade (do NOT clamp
    # the OCO up to / above the guard's own stop level).
    if gsp + MIN_GAP_PP > MAX_STOP_PCT:
        return None
    cfg = DEFAULT_STOP_PCT if stop_pct is None else float(stop_pct)
    eff = max(cfg, gsp + MIN_GAP_PP)
    eff = min(eff, MAX_STOP_PCT)  # never deeper than ~5% of premium remaining
    # Bound the target: a non-finite / <= 0 value is invalid (fall back to default);
    # an absurd value (e.g. a mistyped 5000%) is capped so a bogus operator value
    # never rests a nonsensical broker TP.
    tpv = _finite_pos(target_pct)
    tp = DEFAULT_TARGET_PCT if tpv is None else min(tpv, MAX_TARGET_PCT)
    sl_trigger = round_to_tick(e * (1 - eff / 100.0), tick, mode="down")
    tp_trigger = round_to_tick(e * (1 + tp / 100.0), tick, mode="down")
    sl_limit = round_to_tick(sl_trigger * (1 - CROSS_PCT / 100.0), tick, mode="down")
    tp_limit = round_to_tick(tp_trigger * (1 - CROSS_PCT / 100.0), tick, mode="down")
    if min(sl_trigger, sl_limit, tp_trigger, tp_limit) <= 0:
        return None
    return sl_trigger, sl_limit, tp_trigger, tp_limit
