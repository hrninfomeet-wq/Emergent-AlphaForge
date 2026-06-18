"""Opening-Range Regime Router (ORR) — the proof strategy for the scenario-adaptive
framework. Routes on opening-range width (classified by the base):
  NARROW open  -> TREND_CONTINUATION: follow the opening drive (close vs session open)
  WIDE open    -> VOLATILE_FADE: enter OPPOSITE the drive (fade back toward the open)
Exit magnitudes (let-run target / fade stop) come from scenarios.exit_plan and are
optimizable via extra_params. This is the minimal end-to-end exerciser of the
classifier + routing base + level-exit; its option-rupee survival is the P4 gate."""
from __future__ import annotations
import pandas as pd
from app.strategies.scenario_routing_base import ScenarioRoutedStrategyBase


class OpeningRangeRegimeRouter(ScenarioRoutedStrategyBase):
    id = "opening_range_regime_router"
    name = "Opening-Range Regime Router"
    version = "1.0.0"
    description = ("Routes on opening-range width: narrow open -> trend-follow the "
                   "opening drive; wide open -> fade back toward the session open.")
    scenarios_traded = ("TREND_CONTINUATION", "VOLATILE_FADE")
    extra_params = {
        "trend_target_atr": {"type": "float", "min": 2.0, "max": 8.0, "default": 4.0},
        "trend_stop_atr":   {"type": "float", "min": 0.5, "max": 2.0, "default": 1.2},
        "fade_stop_atr":    {"type": "float", "min": 0.5, "max": 3.0, "default": 1.5},
    }

    def _route(self, row, prev, params, ctx, scenario):
        # Warm-up / data guards: need a known opening-range width, a close, and a regime.
        for k in ("orb_width_pct_partial", "close", "regime"):
            if pd.isna(row.get(k)):
                return ("NONE", 0, [], ["warming up"])
        open_px = self._session_open(row, ctx)
        if open_px is None:
            return ("NONE", 0, [], ["no session open"])
        drive_up = float(row["close"]) >= float(open_px)
        if scenario == "TREND_CONTINUATION":
            return ("CE" if drive_up else "PE", 60, ["narrow-open trend-follow"], [])
        if scenario == "VOLATILE_FADE":
            return ("PE" if drive_up else "CE", 60, ["wide-open fade-to-open"], [])
        return ("NONE", 0, [], [f"scenario {scenario} not routed"])
