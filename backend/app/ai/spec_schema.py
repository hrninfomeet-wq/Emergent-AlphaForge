"""Constrained Strategy Spec DSL (Pydantic v2 models).

A StrategySpec is a *data-only* description of an option-buying strategy. It is
deliberately small and total: a deterministic compiler (app.ai.compiler) turns a
VALIDATED spec into a safe StrategyBase plugin. Nothing here executes; nothing
here references the engine — it is pure host-safe data.
"""
from __future__ import annotations

from typing import List, Optional, Union, Literal

from pydantic import BaseModel, Field

# Comparison operators a Condition may use. Kept as a module constant so both the
# schema and the compiler/validator share one source of truth.
CMP_OPS = (">", ">=", "<", "<=", "==", "!=", "cross_above", "cross_below")


class Condition(BaseModel):
    left: str                       # an indicator/OHLCV column name
    op: str                         # one of CMP_OPS
    right: Union[float, int, str]   # a number, "param:NAME", or another column name
    label: Optional[str] = None     # human-readable reason text (optional)


class ParamSpec(BaseModel):
    name: str
    type: Literal["int", "float", "bool"]
    min: Optional[float] = None
    max: Optional[float] = None
    default: Union[int, float, bool]


class ExitSpec(BaseModel):
    spot_target_pts: Optional[float] = None
    spot_stop_pts: Optional[float] = None
    target_pct: Optional[float] = None
    stop_pct: Optional[float] = None
    time_stop_minutes: Optional[int] = None


class StrategySpec(BaseModel):
    id: str
    name: str
    version: str = "1.0.0"
    description: str = ""
    supported_instruments: List[str] = Field(default_factory=lambda: ["NIFTY", "BANKNIFTY", "SENSEX"])
    supported_modes: List[str] = Field(default_factory=lambda: ["SCALP", "INTRADAY"])
    supported_timeframes: List[str] = Field(default_factory=lambda: ["1m", "3m", "5m"])
    params: List[ParamSpec] = Field(default_factory=list)
    entry_ce: List[Condition] = Field(default_factory=list)   # ALL must hold to fire CE
    entry_pe: List[Condition] = Field(default_factory=list)   # ALL must hold to fire PE
    gate_skip_regimes: List[str] = Field(default_factory=list)  # e.g. ["CHOP","VOLATILE_CHOP"]
    cooldown_bars: int = 0
    exits: ExitSpec = Field(default_factory=ExitSpec)
    required_features: List[str] = Field(default_factory=list)  # opt-in structural features
