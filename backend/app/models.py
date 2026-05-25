"""Pydantic models for API + persistence."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Literal
from pydantic import BaseModel, Field, ConfigDict
import uuid


class Candle(BaseModel):
    instrument: str
    ts: int  # ms epoch UTC
    datetime: str  # ISO format with timezone
    open: float
    high: float
    low: float
    close: float
    volume: float = 0


class StrategyMeta(BaseModel):
    id: str
    name: str
    version: str
    description: str
    supported_instruments: List[str]
    supported_modes: List[str]  # SCALP, INTRADAY, SWING
    supported_timeframes: List[str]
    parameter_schema: Dict[str, Any]
    is_builtin: bool = True
    is_loaded: bool = True
    error: Optional[str] = None


class BacktestConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    instrument: str = "NIFTY"
    mode: Literal["SCALP", "INTRADAY", "SWING"] = "SCALP"
    strategy_id: str
    timeframe: str = "1m"
    params: Dict[str, Any] = Field(default_factory=dict)
    start_date: Optional[str] = None  # ISO date
    end_date: Optional[str] = None
    costs_enabled: bool = True
    walkforward: bool = True
    train_pct: float = 0.6
    n_folds: int = 3
    pretrade_filters: Dict[str, Any] = Field(default_factory=dict)


class BacktestResult(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    name: str = "Untitled Run"
    config: Dict[str, Any]
    metrics: Dict[str, Any]
    trades: List[Dict[str, Any]]
    equity_curve: List[Dict[str, Any]]
    walkforward: Optional[Dict[str, Any]] = None
    significance: Dict[str, Any] = Field(default_factory=dict)
    candle_count: int = 0
    regime_distribution: Dict[str, int] = Field(default_factory=dict)
    signal_funnel: Dict[str, int] = Field(default_factory=dict)


class IngestRequest(BaseModel):
    instrument: str  # NIFTY, BANKNIFTY, SENSEX
    days: int = 7
    interval: str = "1m"


class PresetSave(BaseModel):
    name: str
    config: Dict[str, Any]


class PreTradeProfile(BaseModel):
    name: Literal["Conservative", "Balanced", "Aggressive"]
    settings: Dict[str, Any]
