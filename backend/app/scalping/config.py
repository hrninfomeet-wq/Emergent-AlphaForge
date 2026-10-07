"""Per-index scalper parameter sets. Every threshold carries a unit and a provenance tag.

Provenance tags
---------------
measured     derived from a named measurement in docs/scalping/02-measurements.md (M1..M7)
provisional  a starting value for PAPER only; must be re-derived from forward data before any reliance
policy       a safety/risk rule chosen on purpose (not fitted), e.g. order-rate caps below SEBI's threshold
hypothesis   the entry rule under test; NOT evidence of edge (both specs are pre-registered falsification tests)

Monetary limits are PROVISIONAL-PAPER values. No monetary limit here is operator-approved for real
money; ``validate`` refuses any mode other than paper / replay.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Dict, FrozenSet, Optional, Tuple

ALLOWED_MODES = frozenset({"paper", "replay"})


@dataclass(frozen=True)
class ScalperConfig:
    # ---- identity
    strategy_id: str
    underlying: str                  # "NIFTY" | "SENSEX"
    exchange: str                    # "NFO" | "BFO"
    mode: str = "paper"              # "paper" | "replay"  (no live adapter exists)
    lot_size: int = 0                # resolved from contract data at runtime; 0 = must resolve
    tick_size: float = 0.05          # Rs

    # ---- entry signal (hypothesis)
    signal_kind: str = "synthetic_impulse"   # "synthetic_impulse" | "joint_impulse"
    window_s: int = 30               # s, impulse look-back
    z_threshold: float = 3.0         # sigma units
    sigma_lookback_s: int = 600      # s, trailing window for the 1-s change std
    sigma_min_samples: int = 300     # count of 1-s changes before z is defined
    confirm_underlying: Optional[str] = None  # joint_impulse: the index that must agree
    confirm_z: float = 0.0           # sigma units for the confirming index

    # ---- contract selection
    allowed_dte: FrozenSet[int] = frozenset({0, 1, 2})   # trading days to expiry
    expiry_rank: int = 0             # 0 = nearest listed weekly expiry
    strike_offset: int = 0           # strikes from ATM, + = ITM (0 = ATM)
    min_premium: float = 20.0        # Rs per unit
    max_premium: float = 600.0       # Rs per unit

    # ---- liquidity / quote filters (all must pass at the decision second)
    max_spread_ticks: int = 8        # ticks
    max_spread_pct: float = 0.35     # % of mid
    min_ask_touch_qty_x: float = 1.0 # best-ask qty >= x * order qty
    min_ask_depth5_qty_x: float = 3.0  # sum of 5 ask levels >= x * order qty
    max_quote_age_ms: int = 1500     # ms since the option quote's local ingest
    max_ref_age_ms: int = 2000       # ms since the reference (index / synthetic) update
    entry_windows_ist: Tuple[Tuple[str, str], ...] = (("09:20", "14:45"),)
    force_exit_ist: str = "15:00"    # forced flat (app-wide EOD square is also 15:00)

    # ---- entry execution
    entry_cross_ticks: int = 2       # BUY limit = best ask + n ticks
    entry_max_slip_pct: float = 0.5  # cap: limit <= ask * (1 + x/100)
    entry_timeout_ms: int = 2000     # unfilled remainder cancelled after this
    ack_timeout_ms: int = 3000       # no ack within -> status UNKNOWN -> reconcile
    cancel_confirm_timeout_ms: int = 3000  # cancel not confirmed within -> reconcile
    reconcile_interval_s: int = 10   # policy: routine order-book + position read while anything is live
                                     # (backstop for lost order updates; 2 REST reads per interval)

    # ---- exits
    max_hold_s: int = 30             # s, time exit
    stop_loss_pct: float = 5.0       # % of entry fill, on the BID (executable side)
    target_pct: Optional[float] = 6.0  # % of entry fill on the BID; None = no target
    trail_arm_pct: Optional[float] = 3.0   # % gain on the bid that arms the trail
    trail_giveback_pct: float = 50.0       # % of peak gain given back before exit
    exit_cross_ticks: Tuple[int, ...] = (2, 6, 20, 60)   # escalation ladder, ticks below bid
    exit_reprice_ms: int = 1500      # ms an exit may work before cancel -> re-price
    stale_feed_exit_ms: int = 5000   # in position and no quote for this long -> protective exit

    # ---- sizing / risk  (PROVISIONAL-PAPER)
    lots: int = 1
    max_concurrent_positions: int = 1
    max_trades_per_day: int = 20
    daily_loss_limit_inr: float = 2000.0   # realized net + open MTM at bid; PROVISIONAL-PAPER
    max_consecutive_losses: int = 4
    pause_after_losses_s: int = 1800
    cooldown_after_exit_s: int = 5
    max_orders_per_sec: int = 3      # policy: far below SEBI's 10 OPS registration threshold
    max_orders_per_min: int = 15     # policy: Flattrade order APIs allow 40/min PER KEY; NIFTY + SENSEX
                                     # engines (2 x 15) leave 10/min for the app's other paths + the MCP

    provenance: Dict[str, str] = field(default_factory=dict, compare=False, hash=False)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["allowed_dte"] = sorted(self.allowed_dte)
        d["entry_windows_ist"] = [list(w) for w in self.entry_windows_ist]
        d["exit_cross_ticks"] = list(self.exit_cross_ticks)
        return d


def _hhmm_ok(s: str) -> bool:
    try:
        h, m = s.split(":")
        return 0 <= int(h) < 24 and 0 <= int(m) < 60
    except (ValueError, AttributeError):
        return False


def validate(cfg: ScalperConfig) -> ScalperConfig:
    """Raise ValueError on any inconsistent or unsafe setting. Returns cfg for chaining."""
    errs = []
    if cfg.mode not in ALLOWED_MODES:
        errs.append(f"mode {cfg.mode!r} not allowed; the scalper has no real-money adapter "
                    f"(allowed: {sorted(ALLOWED_MODES)})")
    if cfg.underlying not in ("NIFTY", "SENSEX"):
        errs.append("underlying must be NIFTY or SENSEX")
    if (cfg.underlying, cfg.exchange) not in (("NIFTY", "NFO"), ("SENSEX", "BFO")):
        errs.append("exchange must be NFO for NIFTY and BFO for SENSEX")
    if cfg.signal_kind not in ("synthetic_impulse", "joint_impulse"):
        errs.append("unknown signal_kind")
    if cfg.signal_kind == "joint_impulse" and not cfg.confirm_underlying:
        errs.append("joint_impulse needs confirm_underlying")
    if not (1 <= cfg.window_s <= 300) or cfg.z_threshold <= 0:
        errs.append("window_s in [1,300] s and z_threshold > 0 required")
    if cfg.sigma_min_samples > cfg.sigma_lookback_s:
        errs.append("sigma_min_samples cannot exceed sigma_lookback_s")
    if cfg.lots < 1 or cfg.max_concurrent_positions != 1:
        errs.append("lots >= 1 and max_concurrent_positions == 1 (the engine holds one position)")
    if cfg.stop_loss_pct <= 0 or cfg.stop_loss_pct >= 100:
        errs.append("stop_loss_pct in (0, 100)")
    if cfg.target_pct is not None and cfg.target_pct <= 0:
        errs.append("target_pct > 0 or None")
    if cfg.trail_arm_pct is not None and not (0 < cfg.trail_giveback_pct < 100):
        errs.append("trail_giveback_pct in (0, 100)")
    if not cfg.exit_cross_ticks or list(cfg.exit_cross_ticks) != sorted(cfg.exit_cross_ticks):
        errs.append("exit_cross_ticks must be a non-empty ascending ladder")
    if cfg.max_orders_per_sec > 9:
        errs.append("max_orders_per_sec must stay below SEBI's 10 orders/s algo threshold")
    if cfg.max_orders_per_min > 18:
        errs.append("max_orders_per_min > 18 would let two engines exceed Flattrade's 40 order calls/min per key")
    if cfg.daily_loss_limit_inr <= 0:
        errs.append("daily_loss_limit_inr must be positive")
    if cfg.entry_timeout_ms <= 0 or cfg.max_hold_s <= 0:
        errs.append("entry_timeout_ms and max_hold_s must be positive")
    for a, b in cfg.entry_windows_ist:
        if not (_hhmm_ok(a) and _hhmm_ok(b) and a < b):
            errs.append(f"bad entry window {a}-{b}")
    if not _hhmm_ok(cfg.force_exit_ist):
        errs.append("bad force_exit_ist")
    elif any(b > cfg.force_exit_ist for _, b in cfg.entry_windows_ist):
        errs.append("entry windows must end before force_exit_ist")
    if errs:
        raise ValueError("; ".join(errs))
    return cfg


# --------------------------------------------------------------------------------------------
# Pre-registered specs (docs/scalping/03-strategy-specs.md). Provenance strings cite M-numbers.
# --------------------------------------------------------------------------------------------

NIFTY_N1 = validate(ScalperConfig(
    strategy_id="scalp_nifty_n1_synthetic_impulse",
    underlying="NIFTY", exchange="NFO",
    signal_kind="synthetic_impulse", window_s=30, z_threshold=3.0,
    allowed_dte=frozenset({0, 1, 2}), min_premium=20.0, max_premium=400.0,
    max_spread_ticks=8, max_spread_pct=0.35, min_ask_touch_qty_x=1.0, min_ask_depth5_qty_x=3.0,
    max_quote_age_ms=1500, max_ref_age_ms=2000,
    entry_windows_ist=(("09:20", "14:45"),), force_exit_ist="15:00",
    entry_cross_ticks=2, entry_max_slip_pct=0.5, entry_timeout_ms=2000,
    max_hold_s=30, stop_loss_pct=5.0, target_pct=6.0, trail_arm_pct=3.0, trail_giveback_pct=50.0,
    exit_cross_ticks=(2, 6, 20, 60), exit_reprice_ms=1500, stale_feed_exit_ms=5000,
    lots=1, max_trades_per_day=20, daily_loss_limit_inr=2000.0,
    max_consecutive_losses=4, pause_after_losses_s=1800,
    provenance={
        "signal": "hypothesis: IMP_30_3.0 cell, holdout net +1.34 %/trade t=1.50 (H30) but discovery "
                  "negative -> HARKed from the holdout, expected value ~0; forward falsification only",
        "reference": "measured M3: options-implied synthetic forward leads the index by ~1 s (lag-1 corr 0.17 vs 0.08)",
        "max_spread_pct": "measured M2: NIFTY ATM spread p90 0.323 % of mid, p99 0.413 %",
        "max_spread_ticks": "measured M1: ATM spread p90 0.15-0.45 pts = 3-9 ticks across DTE 0-4",
        "min_premium": "measured M1: 0DTE premium ~28 had spread p90 0.61 % of mid (2x normal)",
        "max_quote_age_ms": "measured M1: ATM quote inter-arrival p90 1.05 s, p99 1.2-2.9 s",
        "depth": "measured M1: ask touch p10 = 1 lot, ask 5-level p10 45-265 lots",
        "stop_loss_pct": "provisional, anchored to measured M2 30-s adverse excursion p90 -5.1 %",
        "target_pct": "provisional, anchored to measured M2 60-s favourable excursion p90 6.7 %",
        "max_hold_s": "hypothesis cell H=30 s",
        "money": "PROVISIONAL-PAPER; not approved for real money",
        "rates": "policy: 3 orders/s, 15/min per engine (Flattrade 40/min per key, shared)",
    }))

SENSEX_S1E = validate(ScalperConfig(
    strategy_id="scalp_sensex_s1e_expiry_joint_impulse",
    underlying="SENSEX", exchange="BFO",
    signal_kind="joint_impulse", window_s=20, z_threshold=2.5,
    confirm_underlying="NIFTY", confirm_z=2.5,
    allowed_dte=frozenset({0}), min_premium=20.0, max_premium=600.0,
    max_spread_ticks=20, max_spread_pct=0.30, min_ask_touch_qty_x=1.0, min_ask_depth5_qty_x=3.0,
    max_quote_age_ms=2000, max_ref_age_ms=2000,
    entry_windows_ist=(("09:20", "14:30"),), force_exit_ist="15:00",
    entry_cross_ticks=3, entry_max_slip_pct=0.5, entry_timeout_ms=2500,
    max_hold_s=30, stop_loss_pct=4.0, target_pct=5.0, trail_arm_pct=2.5, trail_giveback_pct=50.0,
    exit_cross_ticks=(3, 10, 30, 90), exit_reprice_ms=2000, stale_feed_exit_ms=6000,
    lots=1, max_trades_per_day=6, daily_loss_limit_inr=1500.0,
    max_consecutive_losses=3, pause_after_losses_s=1800,
    provenance={
        "signal": "hypothesis: joint NIFTY+SENSEX impulse. All-days version KILLED (M7, holdout net -0.50 %, "
                  "family 3/18). Restricted to SENSEX expiry day because no SENSEX 0DTE session has ever "
                  "been recorded with depth - the only untested SENSEX regime; forward falsification only",
        "max_spread_pct": "measured M2: SENSEX ATM spread p90 0.277 %, p99 0.306 % of mid",
        "max_spread_ticks": "measured M1: SENSEX ATM spread p50 11-18 ticks, p90 14-26 ticks",
        "max_quote_age_ms": "measured M1: SENSEX ATM quote inter-arrival p50 1.04 s, p99 1.9-5.2 s",
        "depth": "measured M1: SENSEX ask touch p50 2-3 lots, ask 5-level p10 13-30 lots -> 1 lot only",
        "stop_loss_pct": "provisional: SENSEX 30-s adverse excursion p90 -2.9 %, p95 -3.8 % (M2, non-expiry DTE)",
        "target_pct": "provisional: SENSEX 60-s favourable excursion p90 3.7 % (M2, non-expiry DTE)",
        "exit_cross_ticks": "provisional: SENSEX spreads are ~5x NIFTY in ticks, so the ladder is wider",
        "money": "PROVISIONAL-PAPER; not approved for real money",
        "rates": "policy: 3 orders/s, 15/min per engine (Flattrade 40/min per key, shared)",
    }))

PRESETS: Dict[str, ScalperConfig] = {NIFTY_N1.strategy_id: NIFTY_N1, SENSEX_S1E.strategy_id: SENSEX_S1E}


def with_overrides(cfg: ScalperConfig, **kw) -> ScalperConfig:
    return validate(replace(cfg, **kw))
