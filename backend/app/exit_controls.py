# backend/app/exit_controls.py
"""Pure execution-overlay deciders: premium trailing/breakeven stop + per-day
governor + validation. THE single source both the sim (option_backtest) and the
live mark (paper_auto / deployment_kill_switch) call, so they can never drift.

No motor/optuna imports -> host-testable like app/survival.py. Never raises on
bad config (the router validates too); silently ignores out-of-range values.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# --- Exit / skip reason taxonomy (single source; schemas re-exports the names) ---
EXIT_TRAIL_STOP = "OPTION_TRAIL_STOP"
EXIT_BREAKEVEN_STOP = "OPTION_BREAKEVEN_STOP"
SKIPPED_STATUS = "SKIPPED_DAILY_CAP"
SKIP_DAILY_LOSS = "DAILY_LOSS_HALT"
SKIP_DAILY_TARGET = "DAILY_TARGET_HALT"
SKIP_MAX_TRADES = "MAX_TRADES_HALT"


# Used by DailyCapsConfig.from_dict (added in the next task); not dead code.
def _pos(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        f = float(value)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


@dataclass
class ExitControlsConfig:
    enabled: bool = False
    unit: str = "pct"              # "pct" (of entry premium) | "pts" (absolute premium)
    be_trigger: float = 0.0        # breakeven: > 0 enables
    be_lock: float = 0.0
    trail_activation: float = 0.0  # trailing: trail_distance > 0 enables
    trail_distance: float = 0.0

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "ExitControlsConfig":
        if not data:
            return cls()
        cfg = cls()
        cfg.enabled = bool(data.get("enabled"))
        if str(data.get("unit") or "pct").lower() == "pts":
            cfg.unit = "pts"
        be = data.get("breakeven") or {}
        tr = data.get("trailing") or {}
        for attr, raw in (("be_trigger", be.get("trigger")), ("be_lock", be.get("lock")),
                          ("trail_activation", tr.get("activation")), ("trail_distance", tr.get("distance"))):
            try:
                if raw is not None and raw != "":
                    setattr(cfg, attr, float(raw))
            except (TypeError, ValueError):
                pass
        return cfg


def effective_premium_stop(*, entry: float, running_max: float,
                           base_stop: Optional[float], cfg: ExitControlsConfig) -> Optional[float]:
    """The ratcheted LONG-option stop = max(base, breakeven?, trailing?). Monotonic
    non-decreasing in running_max *when the caller supplies running_max as a true
    running peak*; this function is stateless and does not enforce that invariant.
    Disabled cfg => base_stop unchanged."""
    candidates: List[float] = []
    if base_stop is not None:
        candidates.append(float(base_stop))
    if cfg.enabled:
        e = float(entry)
        rm = float(running_max)
        if cfg.be_trigger and cfg.be_trigger > 0:
            if cfg.unit == "pts":
                trigger_level = e + cfg.be_trigger
                lock_level = e + (cfg.be_lock or 0.0)
            else:
                trigger_level = e * (1.0 + cfg.be_trigger)
                lock_level = e * (1.0 + (cfg.be_lock or 0.0))
            if rm >= trigger_level:
                candidates.append(lock_level)
        if cfg.trail_distance and cfg.trail_distance > 0:
            if cfg.unit == "pts":
                activation_level = e + (cfg.trail_activation or 0.0)
                trail_level = rm - cfg.trail_distance
            else:
                activation_level = e * (1.0 + (cfg.trail_activation or 0.0))
                trail_level = rm * (1.0 - cfg.trail_distance)
            if rm >= activation_level:
                candidates.append(trail_level)
    return max(candidates) if candidates else None


def stop_fill_price(level: float, reason: str, bar_open: Optional[float]) -> float:
    """Gap-fill honesty (overlay path only): a LONG stop that gaps below fills at the
    bar OPEN, not the (higher) stop level. Non-stop reasons fill at the level."""
    if reason == "STOP" and bar_open is not None:
        try:
            o = float(bar_open)
            if o < float(level):
                return o
        except (TypeError, ValueError):
            pass
    return float(level)
