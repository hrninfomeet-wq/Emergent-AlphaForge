"""Absolute-price (level) exit target, resolved by the SAME intrabar rule as
delta targets so spot/option fills never drift. Used by VOLATILE_FADE
(fade back to the session OPEN)."""
from __future__ import annotations
from typing import Optional, Tuple
from app.exit_engine import intrabar_exit


def level_exit_decision(*, high: float, low: float, stop: Optional[float],
                        level_target: Optional[float], is_long: bool) -> Tuple[Optional[float], Optional[str]]:
    return intrabar_exit(high=high, low=low, stop=stop, target=level_target, is_long=is_long, stop_first=True)
