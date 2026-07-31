"""Shared schemas for indicator dimensions an optimizer may add to any strategy."""
from __future__ import annotations

from typing import Any, Dict


INDICATOR_PARAM_CATALOG: Dict[str, Dict[str, Any]] = {
    "ema_fast": {"type": "int", "min": 3, "max": 20, "default": 9},
    "ema_slow": {"type": "int", "min": 15, "max": 80, "default": 21},
    "rsi_length": {"type": "int", "min": 5, "max": 30, "default": 14},
    "macd_fast": {"type": "int", "min": 5, "max": 20, "default": 12},
    "macd_slow": {"type": "int", "min": 20, "max": 60, "default": 26},
    "macd_signal": {"type": "int", "min": 5, "max": 15, "default": 9},
    "atr_length": {"type": "int", "min": 7, "max": 30, "default": 14},
    "adx_length": {"type": "int", "min": 7, "max": 30, "default": 14},
    "chop_length": {"type": "int", "min": 7, "max": 30, "default": 14},
    "swing_lookback": {"type": "int", "min": 3, "max": 15, "default": 5},
}
