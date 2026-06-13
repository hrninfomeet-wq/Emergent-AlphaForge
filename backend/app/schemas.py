"""Pydantic request models for the AlphaForge API.

Moved verbatim from backend/server.py (quality-hardening Slice C).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field

from app.option_data_planner import DEFAULT_LEGS
from app.upstox_stream import DEFAULT_STREAM_MODE
from app.data_hygiene import DEFAULT_SAMPLE_INTERVAL_MIN as HYGIENE_DEFAULT_SAMPLE


# ---------------------------------------------------------------------------
# Data Warehouse
# ---------------------------------------------------------------------------

class IngestReq(BaseModel):
    instrument: str
    days: int = 7


class ProfileSave(BaseModel):
    name: str
    settings: Dict[str, Any]


# ---------------------------------------------------------------------------
# Backtest
# ---------------------------------------------------------------------------

class OptionBacktestReq(BaseModel):
    enabled: bool = False
    expiry_date: Optional[str] = None
    # ATM is the default: it matches the warehouse's auto-maintained data scope
    # (Data Hygiene keeps ATM CE/PE current) and the deployment default.
    moneyness: str = "atm"
    lots: int = 1
    entry_max_age_sec: int = 120
    exit_max_age_sec: int = 180
    auto_fetch: bool = True
    max_auto_fetch_contracts: int = 12
    slippage_config: Optional[Dict[str, Any]] = None
    # Option exit mode: "spot_exit" (option mirrors the spot trade's exit) or
    # "option_levels" (exit on the option's own premium target/stop).
    exit_mode: str = "spot_exit"
    option_target_pts: Optional[float] = None
    option_stop_pts: Optional[float] = None
    option_target_pct: Optional[float] = None
    option_stop_pct: Optional[float] = None
    # DTE filter: None/"all" = every weekly expiry; a single token ("dte0".."dte6"
    # or 0..6) or a list of tokens ([0, 1, 2]) = only sessions that many trading
    # days before the nearest expiry. Lets the user test a strategy on, e.g.,
    # expiry-day only (0) or the 0-2 DTE buying window ([0, 1, 2]).
    dte_filter: Optional[Union[str, int, List[Union[str, int]]]] = None
    # Rupee cost model (brokerage + STT + charges + % bid-ask spread). Opt-in;
    # when omitted/disabled the backtest reports gross premium P&L as before.
    cost_config: Optional[Dict[str, Any]] = None
    # Position sizing + capital (premium-at-risk or fixed lots). Opt-in; off keeps
    # the fixed `lots` count. Lot SIZE always comes from the contract metadata.
    sizing_config: Optional[Dict[str, Any]] = None


class BacktestReq(BaseModel):
    instrument: str = "NIFTY"
    mode: str = "SCALP"
    strategy_id: str
    timeframe: str = "1m"
    params: Dict[str, Any] = Field(default_factory=dict)
    start_ts: Optional[int] = None
    end_ts: Optional[int] = None
    costs_enabled: bool = True
    walkforward: bool = True
    train_pct: float = 0.6
    n_folds: int = 3
    pretrade_filters: Dict[str, Any] = Field(default_factory=dict)
    option_backtest: OptionBacktestReq = Field(default_factory=OptionBacktestReq)
    # Intraday trade window (IST HH:MM). Default 09:25-15:00 implements the
    # user's discipline rule: no entries in the first 10 min (09:15-09:25) or the
    # last 30 min (15:00-15:30). Configurable per run.
    trade_window_start: str = "09:25"
    trade_window_end: str = "15:00"
    name: str = "Untitled Run"


# ---------------------------------------------------------------------------
# Presets (named backtest configs)
# ---------------------------------------------------------------------------

class PresetSaveBody(BaseModel):
    name: str
    config: Dict[str, Any]


class VolatilityAuditReq(BaseModel):
    instrument: str = "NIFTY"
    from_date: str
    to_date: str
    spike_threshold: float = 2.5
    realized_window: int = 5
    baseline_lookback_bars: int = 11250


class SignalsPurgeReq(BaseModel):
    ids: Optional[List[str]] = None
    deployment_id: Optional[str] = None
    older_than_days: Optional[int] = None
    states: Optional[List[str]] = None


class TradesPurgeReq(BaseModel):
    ids: Optional[List[str]] = None
    deployment_id: Optional[str] = None
    older_than_days: Optional[int] = None


# ---------------------------------------------------------------------------
# Auto-Optimizer (Phase 3)
# ---------------------------------------------------------------------------

class OptimizerStartReq(BaseModel):
    instrument: str = "NIFTY"
    mode: str = "SCALP"
    strategy_id: str
    method: str = "bayesian"  # bayesian | grid | genetic
    objective: str = "risk_adjusted"  # sharpe | profit_factor | total_pnl_pts | net_pnl_inr | win_rate | neg_max_dd | risk_adjusted
    n_trials: int = 200
    costs_enabled: bool = True
    pretrade_filters: Dict[str, Any] = Field(default_factory=dict)
    pretrade_profile: Optional[str] = None  # stored for lossless clone/display; engine uses pretrade_filters
    param_overrides: Dict[str, Any] = Field(default_factory=dict)
    start_ts: Optional[int] = None
    end_ts: Optional[int] = None
    name: str = "Optimization run"
    # Guard rails against degenerate solutions (1-trade / all-PE etc.)
    min_trades: int = 10
    min_direction_share: float = 0.0  # 0 disables one-sided guard
    optimize_indicator_periods: bool = False
    # Evaluation mode: "spot" (default, original — score the index backtest) or
    # "option_rerank" (two-stage: spot search, then re-rank the top-K candidates
    # by REAL paired-option net rupee P&L). option_config mirrors OptionBacktestReq.
    evaluation_mode: str = "spot"
    rerank_top_k: int = 50
    # Broaden the re-rank shortlist with a diversity sample so an option-profitable
    # but spot-mediocre config can surface (default off = top-K by spot objective).
    rerank_diversity: bool = False
    option_config: Optional[Dict[str, Any]] = None


class WfoStartReq(BaseModel):
    """Walk-forward optimization: re-optimize on each train window, evaluate on
    the unseen test window, stitch OOS. Window sizes are in TRADING DAYS present
    in the data (holiday-aware by construction)."""
    instrument: str = "NIFTY"
    mode: str = "SCALP"
    strategy_id: str
    method: str = "bayesian"  # bayesian | genetic (grid is not supported per-window)
    objective: str = "risk_adjusted"
    costs_enabled: bool = True
    pretrade_filters: Dict[str, Any] = Field(default_factory=dict)
    pretrade_profile: Optional[str] = None
    param_overrides: Dict[str, Any] = Field(default_factory=dict)
    start_ts: Optional[int] = None
    end_ts: Optional[int] = None
    name: str = "Walk-forward optimization"
    min_trades: int = 10
    min_direction_share: float = 0.0
    optimize_indicator_periods: bool = False
    # Window configuration
    train_days: int = 60
    test_days: int = 20
    step_days: Optional[int] = None  # default = test_days (contiguous OOS)
    wf_mode: str = "rolling"  # rolling | anchored
    n_trials_per_window: int = 40
    max_windows: int = 12
    # Option-aware OOS (WFO v2): after stitching, pair the OOS spot trades with
    # real option candles ONCE and report net rupee + per-window rupee
    # consistency alongside the spot stitch. option_config mirrors the
    # optimizer re-rank's option_config shape.
    option_aware: bool = False
    option_config: Optional[Dict[str, Any]] = None


class UpstoxStreamStartReq(BaseModel):
    instrument_keys: Optional[List[str]] = None
    mode: str = DEFAULT_STREAM_MODE
    persist_ticks: bool = True


class UpstoxOptionStreamRestartReq(BaseModel):
    underlyings: Optional[List[str]] = None
    radius: int = Field(1, ge=0, le=5)
    max_option_keys: int = Field(60, ge=2, le=200)
    mode: str = DEFAULT_STREAM_MODE
    persist_ticks: bool = True


class UpstoxIngestReq(BaseModel):
    instrument: str  # NIFTY / BANKNIFTY / SENSEX
    from_date: str   # YYYY-MM-DD (IST)
    to_date: str     # YYYY-MM-DD (IST)
    chunk_days: Optional[int] = None


class UpstoxOptionCandleIngestReq(BaseModel):
    instrument_key: str
    from_date: str
    to_date: str
    underlying: Optional[str] = None
    expiry_date: Optional[str] = None
    strike: Optional[float] = None
    side: Optional[str] = None
    trading_symbol: Optional[str] = None
    chunk_days: int = 7


class OptionWarehousePlanReq(BaseModel):
    underlying: str = "NIFTY"
    from_date: str
    to_date: str
    moneyness: List[str] = Field(default_factory=lambda: ["atm"])
    legs: List[str] = Field(default_factory=lambda: list(DEFAULT_LEGS))
    expiry_policy: str = "next_available"
    fixed_expiry_date: Optional[str] = None
    sample_interval_minutes: int = 15
    chunk_days: Optional[int] = None
    fetch_missing_only: bool = True
    max_contracts: int = 250
    confirm_large_fetch: bool = False


class ExpiredOptionContractBackfillReq(BaseModel):
    from_date: str
    to_date: str
    max_expiries: int = 12
    confirm_large_fetch: bool = False


class PaperMarkReq(BaseModel):
    last_price: float
    auto_close_on_risk: bool = True
    # Bypass the option-premium sanity check (e.g. an intentional far value). The
    # UI sets this only after the operator confirms a flagged price.
    override_sanity: bool = False


class PaperCloseReq(BaseModel):
    exit_price: float
    reason: str = "manual"
    override_sanity: bool = False


class DeploymentCreateReq(BaseModel):
    name: str
    source_type: str
    source_id: str
    mode: str = "signal_only"  # signal_only | paper (legacy shadow/recommendation map to signal_only)
    confirmation_mode: str = "1m_close"
    option_moneyness: List[str] = Field(default_factory=lambda: ["atm"])
    pretrade_profile: str = "Balanced"
    risk: Dict[str, Any] = Field(default_factory=dict)
    dte_filter: List[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4, 5, 6])
    allow_overnight: bool = False
    default_lots: int = 1
    # Auto paper trading (2026-06-10): paper mode only. When true, every clean
    # CONFIRMED signal opens a paper trade immediately (no manual approval) so
    # the signal's outcome is auditable. Default ON for new deployments.
    auto_paper: bool = True
    # Optional deployment-level premium exits (long options): points (₹ of
    # premium) or % of entry premium. Points take precedence over percent,
    # matching the backtest's option_levels rule. The strategy's own risk
    # hints on the signal take precedence over both.
    auto_paper_target_pts: Optional[float] = None
    auto_paper_stop_pts: Optional[float] = None
    auto_paper_target_pct: Optional[float] = None
    auto_paper_stop_pct: Optional[float] = None
    # Per-deployment kill switches (Slice 12). Paper mode only. Omit/0/None to disable.
    max_consecutive_losses: Optional[int] = None
    daily_loss_cutoff_pct: Optional[float] = None
    max_open_paper_trades: Optional[int] = None
    # Live execution realism (app.live_friction). When enabled, auto-paper fills
    # are slipped (BUY entry / SELL exit) and charged with the SAME model the
    # backtest uses, so forward P&L mirrors it instead of overstating gross.
    # Shape: {"enabled": bool, "slippage": {...SlippageConfig}, "costs": {...CostConfig}}.
    # Default None → gross (legacy); the deploy wizard prefills it ON from the
    # preset's backtest execution policy and lets the user tune every knob.
    friction: Optional[Dict[str, Any]] = None
    acknowledged_warnings: bool = False


# ---------------------------------------------------------------------------
# Data Hygiene workflow (slice 6)
# ---------------------------------------------------------------------------


class DataHygieneScopeReq(BaseModel):
    # None -> rolling 9-month window (data_hygiene.default_scope_start);
    # pass an explicit date to audit a wider/narrower range.
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    instruments: Optional[List[str]] = None
    moneyness: Optional[List[str]] = None
    legs: Optional[List[str]] = None
    sample_interval_minutes: int = HYGIENE_DEFAULT_SAMPLE


class DataHygieneExecuteReq(BaseModel):
    plan: Dict[str, Any]
    chunk_days_spot: int = 30
    max_contracts_per_action: int = 2000


class DataHygieneCatchUpReq(BaseModel):
    instruments: Optional[List[str]] = None
    moneyness: Optional[List[str]] = None
    legs: Optional[List[str]] = None
    sample_interval_minutes: int = HYGIENE_DEFAULT_SAMPLE
    include_options: bool = True
    dry_run: bool = False
    chunk_days_spot: int = 30


class AutoUpdateToggleReq(BaseModel):
    enabled: bool


class VixIngestReq(BaseModel):
    from_date: str
    to_date: str
    chunk_days: int = 7
