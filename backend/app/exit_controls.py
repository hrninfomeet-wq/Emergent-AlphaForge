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


@dataclass
class DailyCapsConfig:
    loss: Optional[float] = None        # ₹ (positive); halt when session cum-realized <= -loss
    target: Optional[float] = None      # ₹ (positive); halt when session cum-realized >= target
    max_trades: Optional[int] = None    # entries per IST session

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "DailyCapsConfig":
        if not data:
            return cls()
        cfg = cls()
        cfg.loss = _pos(data.get("loss"))
        cfg.target = _pos(data.get("target"))
        mt = data.get("max_trades")
        try:
            cfg.max_trades = int(mt) if mt not in (None, "") and int(mt) > 0 else None
        except (TypeError, ValueError):
            cfg.max_trades = None
        return cfg

    @property
    def active(self) -> bool:
        return self.loss is not None or self.target is not None or self.max_trades is not None


def daily_governor_decision(*, realized_cum_min: float, realized_cum_max: float,
                            entry_count: int, cfg: DailyCapsConfig) -> Dict[str, Any]:
    """Soft per-session halt from the session's cumulative-realized EXTREMA (sticky)
    + the entry count. Loss is surfaced before target before max-trades.

    `entry_count` is the count of trades ALREADY admitted this session (pre-this-trade);
    halting on >= max_trades therefore admits exactly max_trades entries. The caller
    must feed the same already-admitted convention live and in the sim (parity)."""
    if cfg.loss is not None and float(realized_cum_min) <= -abs(cfg.loss):
        return {"halt": True, "reason": SKIP_DAILY_LOSS}
    if cfg.target is not None and float(realized_cum_max) >= abs(cfg.target):
        return {"halt": True, "reason": SKIP_DAILY_TARGET}
    if cfg.max_trades is not None and int(entry_count) >= cfg.max_trades:
        return {"halt": True, "reason": SKIP_MAX_TRADES}
    return {"halt": False, "reason": None}


def validate_exit_risk_config(exit_controls: Optional[Dict[str, Any]],
                              daily_caps: Optional[Dict[str, Any]],
                              *, costs_on: bool, option_exec_on: bool) -> List[str]:
    """Pure validation; returns a list of error strings (empty = valid). The
    corpus-visible routers call this and raise 400 on any error."""
    errs: List[str] = []
    ec = ExitControlsConfig.from_dict(exit_controls)
    dc = DailyCapsConfig.from_dict(daily_caps)

    if ec.enabled and not option_exec_on:
        errs.append("exit_controls require option execution (option_levels / option re-rank); "
                    "premium trailing is impossible spot-only.")
    if (dc.loss is not None or dc.target is not None) and not costs_on:
        errs.append("daily ₹ caps (loss/target) require costs enabled (else the cap acts on gross P&L).")

    if ec.enabled:
        unit = ec.unit
        if ec.trail_distance and ec.trail_distance > 0:
            if unit == "pct" and not (0.0 < ec.trail_distance < 1.0):
                errs.append("trailing.distance must be in (0, 1) for unit=pct.")
            if unit == "pts" and ec.trail_distance <= 0:
                errs.append("trailing.distance must be > 0 for unit=pts.")
        if ec.be_trigger and ec.be_trigger > 0 and ec.be_lock and ec.be_lock >= ec.be_trigger:
            errs.append("breakeven.lock must be < breakeven.trigger.")
    if dc.loss is not None and dc.loss <= 0:
        errs.append("daily_caps.loss must be > 0.")
    if dc.target is not None and dc.target <= 0:
        errs.append("daily_caps.target must be > 0.")
    if daily_caps and daily_caps.get("max_trades") is not None and dc.max_trades is None:
        errs.append("daily_caps.max_trades must be an integer >= 1.")
    return errs
