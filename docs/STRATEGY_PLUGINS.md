# Writing a Custom Strategy Plugin

Drop a single Python file into `backend/app/strategies/plugins/`. It will be auto-discovered on backend restart and appear in the **Strategy Library** + **Backtest Lab** + **Optimizer** automatically.

Important: a plugin becoming available does not make it deployable for forward testing. Future Strategy Deployments will be created only from saved presets or saved backtest results so the exact parameters, source run, and audit context are preserved. See [Strategy Deployments](STRATEGY_DEPLOYMENTS.md).

## Template

```python
# backend/app/strategies/plugins/my_strategy.py
import pandas as pd
from app.strategies.base import StrategyBase, Signal


class MyStrategy(StrategyBase):
    # ── Required class attributes ─────────────────────────────────
    id = "my_strategy_v1"             # unique; used in DB + API
    name = "My Strategy v1"            # human-readable
    version = "1.0.0"
    description = "One-line description that appears in the UI."
    supported_instruments = ["NIFTY", "BANKNIFTY", "SENSEX"]
    supported_modes = ["SCALP", "INTRADAY"]
    supported_timeframes = ["1m", "3m", "5m"]

    # Parameters: type/min/max/default. Used to build sliders + optimizer bounds.
    parameter_schema = {
        "ema_fast":     {"type": "int",   "min": 5,  "max": 50,  "default": 9},
        "ema_slow":     {"type": "int",   "min": 10, "max": 200, "default": 21},
        "rsi_thr_bull": {"type": "float", "min": 50, "max": 70,  "default": 55},
        "signal_threshold": {"type": "int", "min": 40, "max": 90, "default": 60},
        "cooldown_bars":    {"type": "int", "min": 1,  "max": 30, "default": 5},
        "spot_target_pts": {"type": "float", "min": 5, "max": 100, "default": 30},
        "spot_stop_pts":   {"type": "float", "min": 3, "max": 60,  "default": 15},
        "use_vwap_filter": {"type": "bool", "default": True},
    }

    # ── The only method you must implement ────────────────────────
    def evaluate(self, row, prev, params, ctx) -> Signal:
        """Called once per 1-minute bar by the backtest/live engine.

        row:    pd.Series of current bar with columns:
                  ts, datetime, open, high, low, close, volume, ist_time,
                  ema9, ema21, ema50, rsi, macd_line, macd_signal, macd_hist,
                  atr, adx, chop, vwap, atr_avg, regime, fvg, is_swing_high,
                  is_swing_low, session_date
        prev:   pd.Series of previous bar (same columns).
        params: dict of merged defaults + user/optimizer overrides.
        ctx:    {"history_df": DataFrame of all bars, "i": current index,
                 plus strategy-specific extras like orb_hi/orb_lo for ORB}

        Return Signal(
            direction="CE"/"PE"/"NONE",
            score=0..100,         # ≥ params["signal_threshold"] to fire
            reasons=[...],        # human-readable reasons (shown in UI)
            blockers=[...],       # non-empty kills the signal
            spot_target_pts=...,  # required if mode=SCALP/INTRADAY
            spot_stop_pts=...,
        )
        """
        # Warm-up guard
        required = ["close", "ema9", "ema21", "rsi", "vwap"]
        if any(pd.isna(row.get(k)) for k in required):
            return Signal(direction="NONE", blockers=["indicators warming up"])

        close = float(row["close"])
        ema_f = float(row["ema9"])
        ema_s = float(row["ema21"])
        rsi_v = float(row["rsi"])
        vwap  = float(row["vwap"])

        # Your strategy logic
        score = 0
        reasons = []
        direction = "NONE"

        if close > ema_f > ema_s and rsi_v > params["rsi_thr_bull"]:
            direction = "CE"
            score = 65
            reasons.append("EMA stack bull + RSI strong")
            if params["use_vwap_filter"] and close > vwap:
                score += 10
                reasons.append("above VWAP")
        elif close < ema_f < ema_s and rsi_v < (100 - params["rsi_thr_bull"]):
            direction = "PE"
            score = 65
            reasons.append("EMA stack bear + RSI weak")

        return Signal(
            direction=direction,
            score=score,
            reasons=reasons,
            spot_target_pts=params["spot_target_pts"],
            spot_stop_pts=params["spot_stop_pts"],
        )
```

## Restart Backend

```bash
sudo supervisorctl restart backend
tail -n 20 /var/log/supervisor/backend.out.log
# look for: "Strategy registered: my_strategy_v1 (My Strategy v1)"
```

If you see `Failed to import strategy my_strategy` or `Failed to instantiate MyStrategy`, check the backend log for the full Python traceback. The Strategy Library page also surfaces failed plugins with the error message.

## Available Indicators (pre-computed)

Every `row` provided to `evaluate()` already has these columns computed (no per-bar work needed):

| Column | Description |
|---|---|
| `ema9`, `ema21`, `ema50` | Exponential moving averages |
| `rsi` | RSI (Wilder smoothing) |
| `macd_line`, `macd_signal`, `macd_hist` | MACD (12/26/9 default) |
| `atr`, `atr_avg` | Average True Range (Wilder) + 100-bar rolling mean |
| `adx` | ADX trend strength |
| `chop` | Choppiness Index (>60 = ranging, <40 = trending) |
| `vwap` | Anchored session VWAP (falls back to typical-price MA for indices) |
| `regime` | TREND / TREND_EXPANDING / CHOP / VOLATILE_CHOP / MIXED / UNKNOWN |
| `fvg` | "UP" / "DOWN" / None (Fair Value Gap at this bar) |
| `is_swing_high`, `is_swing_low` | Boolean swing detection (5-bar default) |
| `session_date`, `ist_time` | For session-anchored logic |

If you need more, add them to `app/indicators.py:precompute_all_indicators()`.

## Test Your Plugin Quickly

```bash
curl -X POST http://localhost:8001/api/backtest/run \
  -H "Content-Type: application/json" \
  -d '{
    "instrument":"NIFTY",
    "mode":"SCALP",
    "strategy_id":"my_strategy_v1",
    "params":{},
    "costs_enabled":true,
    "walkforward":true,
    "name":"my plugin test"
  }' | python -m json.tool | head -30
```

Or open the UI → Backtest Lab → pick your strategy from the dropdown.

## Then Optimize It

Open the **Optimizer** page → pick your strategy → method=bayesian → objective=risk_adjusted → click Auto-Optimize. The system finds the best params automatically.

## Examples to Study

Look at the 6 built-in strategies in `backend/app/strategies/builtin/` for working patterns:
- `confluence_scalper.py` — multi-factor scoring with VWAP inhibit + regime gate
- `opening_range_breakout.py` — uses `ctx["orb_hi"]` / `ctx["orb_lo"]` (session-anchored)
- `smc_liquidity_sweep_fvg.py` — uses `ctx["history_df"]` for lookback
- `vwap_mean_reversion.py` — regime-conditional (only in chop)
