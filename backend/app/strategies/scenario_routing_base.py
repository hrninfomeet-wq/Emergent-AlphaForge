"""Scenario-routing strategy base: mirrors adaptive_base but routes on the
discovered opening-range regime. The base classifies the market scenario ONCE
(via scenario_classifier), admits only scenarios the concrete strategy declares
it trades, asks the concrete `_route` for a direction, then attaches the
per-scenario exit plan (via scenarios.exit_plan). Trusted core infra."""
from __future__ import annotations
from typing import Any, Dict, List, Tuple
import pandas as pd
from app.strategies.base import StrategyBase, Signal
from app.scenario_classifier import classify_scenario, SCENARIOS
from app.scenarios import exit_plan

ROUTING_BASE_PARAMS: Dict[str, Any] = {
    "or_minutes": {"type": "int", "min": 10, "max": 45, "default": 30},
    "narrow_thr": {"type": "float", "min": 0.1, "max": 0.6, "default": 0.30},
    "wide_thr": {"type": "float", "min": 0.4, "max": 1.5, "default": 0.60},
    "entry_cutoff_hhmm": {"type": "str", "default": "14:00"},
}


class ScenarioRoutedStrategyBase(StrategyBase):
    supported_instruments = ["NIFTY", "SENSEX"]
    supported_modes = ["SCALP", "INTRADAY"]
    supported_timeframes = ["1m"]
    extra_params: Dict[str, Any] = {}
    scenarios_traded: Tuple[str, ...] = ()

    def __init_subclass__(cls, **kw):
        super().__init_subclass__(**kw)
        cls.parameter_schema = {**ROUTING_BASE_PARAMS, **getattr(cls, "extra_params", {})}
        bad = [s for s in getattr(cls, "scenarios_traded", ()) if s not in SCENARIOS]
        if bad:
            raise ValueError(f"{cls.__name__}.scenarios_traded has unknown scenarios: {bad}")

    # --- hooks/helpers ---
    def _route(self, row, prev, params, ctx, scenario) -> Tuple[str, int, List[str], List[str]]:
        """Return (direction, score, reasons, blockers). `scenario` is already
        classified+admitted by the base. Concrete strategies override."""
        raise NotImplementedError

    @staticmethod
    def _atr_ratio(row) -> float:
        atr, atr_avg = row.get("atr"), row.get("atr_avg")
        try:
            a, av = float(atr), float(atr_avg)
            if not pd.isna(a) and not pd.isna(av) and av != 0.0:
                return a / av
        except (TypeError, ValueError):
            pass
        return 1.0

    @staticmethod
    def _session_open(row, ctx):
        """Session OPEN price (first bar's open of the current session), derived
        causally from history up to the current bar -- same pattern as gap_fade."""
        hist = ctx.get("history_df") if ctx else None
        i = ctx.get("i") if ctx else None
        if hist is None or i is None or "session_date" not in getattr(hist, "columns", []):
            return None
        sess = row.get("session_date")
        cur = hist.iloc[: int(i) + 1]
        cur = cur[cur["session_date"] == sess]
        return float(cur["open"].iloc[0]) if len(cur) else None

    def evaluate(self, row, prev, params, ctx) -> Signal:
        t = str(row.get("ist_time") or "")
        if t and t >= str(params.get("entry_cutoff_hhmm", "14:00")):
            return Signal(direction="NONE", blockers=["time gate"])
        scenario = classify_scenario(
            regime=row.get("regime"), orb_width_pct=row.get("orb_width_pct_partial"),
            day_type=row.get("day_type"), nr7=row.get("nr7"), atr_ratio=self._atr_ratio(row),
            narrow_thr=float(params.get("narrow_thr", 0.30)),
            wide_thr=float(params.get("wide_thr", 0.60)))
        if scenario not in self.scenarios_traded:
            return Signal(direction="NONE", scenario=scenario,
                          blockers=[f"scenario {scenario} not traded"])
        direction, score, reasons, blockers = self._route(row, prev, params, ctx, scenario)
        if direction not in ("CE", "PE"):
            return Signal(direction="NONE", score=int(score or 0), scenario=scenario,
                          reasons=reasons or [], blockers=blockers or [])
        plan = exit_plan(scenario, {"atr": row.get("atr"), "open": self._session_open(row, ctx)},
                         params=params)
        if plan is None:
            return Signal(direction="NONE", scenario=scenario, blockers=["no exit plan"])
        return Signal(direction=direction, score=int(score), scenario=scenario,
                      reasons=reasons or [], blockers=list(blockers or []),
                      spot_target_pts=plan["spot_target_pts"], spot_stop_pts=plan["spot_stop_pts"],
                      spot_target_level=plan["spot_target_level"], exit_mode=plan["exit_mode"])
