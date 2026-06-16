"""Shared scaffolding for the adaptive strategy slate: time-gate + mode-aware
Speed confirm + ATR-relative exits. Concrete strategies override `_core_signal`
and set `extra_params`. Trusted core infra (versioned with the app), like
indicators.py / context_signals.py."""
from __future__ import annotations
from typing import Any, Dict, List, Tuple
import pandas as pd
from app.strategies.base import StrategyBase, Signal

BASE_PARAMS: Dict[str, Any] = {
    "k_acc": {"type": "float", "min": 0.0, "max": 2.0, "default": 0.5},
    "k_acc_fade": {"type": "float", "min": 0.0, "max": 2.0, "default": 0.5},
    "t_atr": {"type": "float", "min": 0.5, "max": 6.0, "default": 1.5},
    "s_atr": {"type": "float", "min": 0.3, "max": 3.0, "default": 0.8},
    "time_stop_min": {"type": "int", "min": 2, "max": 60, "default": 12},
    "signal_threshold": {"type": "int", "min": 30, "max": 90, "default": 55},
    "cooldown_bars": {"type": "int", "min": 1, "max": 30, "default": 5},
    "entry_cutoff_hhmm": {"type": "str", "default": "14:00"},
    "use_time_gate": {"type": "bool", "default": True},
}


class AdaptiveStrategyBase(StrategyBase):
    supported_instruments = ["NIFTY", "SENSEX"]
    supported_modes = ["SCALP", "INTRADAY"]
    supported_timeframes = ["1m", "3m", "5m"]
    extra_params: Dict[str, Any] = {}

    def __init_subclass__(cls, **kw):
        super().__init_subclass__(**kw)
        cls.parameter_schema = {**BASE_PARAMS, **getattr(cls, "extra_params", {})}

    def _core_signal(self, row, prev, params, ctx) -> Tuple[str, int, List[str], List[str], str]:
        """Return (direction, score, reasons, blockers, mode∈{momentum,reversion})."""
        raise NotImplementedError

    def _time_ok(self, row: pd.Series, params: Dict[str, Any]) -> bool:
        if not params.get("use_time_gate", True):
            return True
        t = str(row.get("ist_time") or "")
        if t and t >= str(params.get("entry_cutoff_hhmm", "14:00")):
            return False
        tg = row.get("tod_tradeable")
        return True if tg is None else bool(tg)

    def _speed_ok(self, direction: str, mode: str, row: pd.Series, params: Dict[str, Any]) -> bool:
        az = row.get("accel_z")
        if az is None or pd.isna(az):
            return False
        az = float(az)
        if mode == "momentum":
            k = float(params.get("k_acc", 0.5))
            return az >= k if direction == "CE" else az <= -k
        kf = float(params.get("k_acc_fade", 0.5))
        return az >= -kf if direction == "CE" else az <= kf

    def evaluate(self, row, prev, params, ctx) -> Signal:
        if pd.isna(row.get("atr")) or pd.isna(row.get("accel_z")):
            return Signal(direction="NONE", blockers=["warming up"])
        if not self._time_ok(row, params):
            return Signal(direction="NONE", blockers=["time gate"])
        direction, score, reasons, blockers, mode = self._core_signal(row, prev, params, ctx)
        if direction not in ("CE", "PE"):
            return Signal(direction="NONE", score=int(score or 0), reasons=reasons or [], blockers=blockers or [])
        if not self._speed_ok(direction, mode, row, params):
            return Signal(direction="NONE", score=int(score), reasons=reasons or [],
                          blockers=list(blockers or []) + ["speed gate"])
        atr = float(row["atr"])
        return Signal(
            direction=direction, score=int(score), reasons=reasons or [], blockers=list(blockers or []),
            spot_target_pts=round(float(params["t_atr"]) * atr, 2),
            spot_stop_pts=round(float(params["s_atr"]) * atr, 2),
            time_stop_minutes=int(params["time_stop_min"]),
        )
