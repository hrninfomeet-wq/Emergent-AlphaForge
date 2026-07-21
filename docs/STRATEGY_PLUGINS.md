# Writing a Custom Strategy Plugin

Drop a single Python file into `backend/app/strategies/plugins/`. It will be auto-discovered on backend restart and appear in the **Strategy Library** + **Backtest Lab** + **Optimizer** automatically.

Important: a successfully loaded, non-retired plugin that supports the current `1m` evaluator can be deployed directly from Strategy Library. AlphaForge freezes the selected instrument, timeframe, complete parameter set, strategy version, and source SHA into an immutable deployment snapshot. Saving a preset/backtest first is still the recommended evidence workflow but is no longer a technical prerequisite. An unregistered, failed-to-load, retired, or non-1m plugin is not compatible. Editing deployed plugin code later auto-pauses its deployments through drift detection. See [Strategy Deployments](STRATEGY_DEPLOYMENTS.md).

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
    is_builtin = False                 # REQUIRED for custom plugins — StrategyBase defaults this to True (built-in badge)
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

        row:    pd.Series of current bar with all pre-computed indicator columns
                (see "Available Indicators" below).
        prev:   pd.Series of previous bar (same columns).
        params: dict of merged defaults + user/optimizer overrides.
        ctx:    {"history_df": DataFrame of all bars up to now,
                 "i": current bar index,
                 "instrument": the instrument string e.g. "NIFTY" / "BANKNIFTY",
                 plus strategy-specific extras set by session_precompute(),
                 e.g. orb_hi/orb_lo for ORB strategies}

        Return Signal(
            direction="CE"/"PE"/"NONE",
            score=0..100,              # >= params["signal_threshold"] to fire
            reasons=[...],             # human-readable reasons (shown in UI)
            blockers=[...],            # non-empty kills the signal
            spot_target_pts=...,       # required if mode=SCALP/INTRADAY
            spot_stop_pts=...,
            # Optional advanced fields:
            scenario=...,              # scenario classification label (e.g. "breakout")
            spot_target_level=...,     # explicit spot price to target (absolute level)
            exit_mode=...,             # exit-mode override (e.g. "aggressive")
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

## Per-session precompute (perf)

If your strategy needs values anchored to the start of the session (e.g. opening range, first-bar VWAP, session high/low so far), override `session_precompute` instead of re-deriving them on every bar:

```python
def session_precompute(self, df: pd.DataFrame, params: dict) -> dict:
    """Called ONCE before the bar loop for this session's slice of data.

    Return a dict; its keys are merged into every per-bar `ctx` dict
    alongside history_df, i, and instrument.

    Example — pre-compute the opening range:
        orb_end = df["ist_time"].iloc[0] + pd.Timedelta(minutes=params["orb_minutes"])
        orb_bars = df[df["ist_time"] <= orb_end]
        return {"orb_hi": orb_bars["high"].max(), "orb_lo": orb_bars["low"].min()}
    """
    return {}
```

The returned dict is available in every `evaluate()` call as `ctx["orb_hi"]`, `ctx["orb_lo"]`, etc.

Helpers for common session-level calculations (session open, first-bar typical price, etc.) live in `app/strategies/session_features.py`. See `opening_range_breakout.py` for a worked example of the full pattern.

## Restart Backend

The plugins directory is volume-mounted into the backend container (`docker-compose.yml`), so a restart is enough — no image rebuild:

```bash
docker compose restart backend
docker compose logs --tail 20 backend
# look for: "Strategy registered: my_strategy_v1 (My Strategy v1)"
```

If you see `Failed to import strategy my_strategy` or `Failed to instantiate MyStrategy`, check the backend log for the full Python traceback. The Strategy Library page also surfaces failed plugins with the error message.

**No hot-reload at startup.** Plugin discovery runs once at process start; if you edit a file that is already loaded you need a full backend restart to pick up the changes. In-app reload (without a restart) will arrive with the strategy authoring tool.

## Available Indicators (pre-computed)

Every `row` provided to `evaluate()` already has these columns computed by `precompute_all_indicators()` — no per-bar work needed.

### Core indicators

| Column | Description |
|---|---|
| `ema9`, `ema21`, `ema50` | Exponential moving averages |
| `rsi` | RSI (Wilder smoothing) |
| `macd_line`, `macd_signal`, `macd_hist` | MACD (12/26/9 default) |
| `atr`, `atr_avg` | Average True Range (Wilder) + 100-bar rolling mean |
| `adx` | ADX trend strength |
| `chop` | Choppiness Index (>60 = ranging, <40 = trending) |
| `vwap` | Anchored session VWAP (falls back to typical-price MA for indices) |
| `fvg` | "UP" / "DOWN" / None (Fair Value Gap at this bar) |
| `is_swing_high`, `is_swing_low` | Boolean swing detection (5-bar default) |
| `session_date`, `ist_time` | For session-anchored logic |
| `gap_before` | Boolean: this bar is >1 min after the previous bar within the same IST session (a warehouse data gap). Rolling indicators reset their warm-up across it so they never compute over a time discontinuity |

### Regime

| Column | Description |
|---|---|
| `regime` | TREND / TREND_EXPANDING / CHOP / VOLATILE_CHOP / MIXED / UNKNOWN — added by `classify_regime_series()` **after** `precompute_all_indicators()`, not inside it |
| `regime_score` | Continuous regime strength score used by adaptive strategies |

### Adaptive toolkit

These columns power the adaptive and scenario-routed strategies and are available to plugins too:

| Column | Description |
|---|---|
| `vel_z` | Price velocity z-score (normalised rate of change) |
| `accel_z` | Price acceleration z-score (second derivative) |
| `vr` | Lo-MacKinlay variance ratio (>1 trending, <1 mean-reverting, ~1 random walk) |
| `squeeze_on` | True when Bollinger Bands are inside Keltner Channels (squeeze) |
| `squeeze_fire` | True on the bar the squeeze releases |
| `sqz_mom` | Squeeze momentum oscillator value |
| `supertrend` | Supertrend price level |
| `st_dir` | Supertrend direction: +1 (bullish) / -1 (bearish) |
| `vwap_sigma` | VWAP standard deviation used to build the bands below |
| `vwap_u1`, `vwap_u2` | VWAP + 1σ and + 2σ upper bands |
| `vwap_l1`, `vwap_l2` | VWAP − 1σ and − 2σ lower bands |
| `nr7` | True on Narrow Range 7 bars (lowest range in 7 bars) |

### Candle geometry (always-on)

Per-bar candle geometry, computed for every bar (no params, no carry-forward):

| Column | Description |
|---|---|
| `body_frac` | \|close − open\| as a fraction of the bar range (0 on a zero-range bar) |
| `upper_wick_frac` | Upper wick (high − max(open, close)) as a fraction of the bar range |
| `lower_wick_frac` | Lower wick (min(open, close) − low) as a fraction of the bar range |
| `inside_bar` | True when the bar is fully inside the prior bar (high < prev high AND low > prev low) |
| `close_z` | Trailing 60-bar rolling z-score of close (NaN during warm-up) |

### Pivot levels

| Column | Description |
|---|---|
| `cpr_p` | Central Pivot Range — pivot point |
| `cpr_tc` | CPR top central |
| `cpr_bc` | CPR bottom central |
| `cpr_width_pct` | CPR width as % of price (wide = trending day, narrow = sideways) |
| `day_type` | Day-type classification derived from CPR width |
| `R1`, `R2` | Standard pivot resistance levels |
| `S1`, `S2` | Standard pivot support levels |

### Opening range & time

| Column | Description |
|---|---|
| `orb_width_pct_partial` | Opening range width % computed on the partial (current) session |
| `orb_width_pct_prior` | Opening range width % from the previous session (known before open) |
| `tod_tradeable` | True during tradeable time-of-day windows |

If you need a column that isn't here, add it to `app/indicators.py:precompute_all_indicators()`.

## Signal Fields Reference

`Signal` is a dataclass — every field not listed here defaults to `None` (optional). The engine reads:

| Field | Type | Description |
|---|---|---|
| `direction` | `str` | `"CE"` / `"PE"` / `"NONE"` |
| `score` | `int` | 0–100 conviction; must reach `params["signal_threshold"]` to fire |
| `reasons` | `List[str]` | Human-readable reasons shown in the journal/UI |
| `blockers` | `List[str]` | Non-empty list vetoes the signal regardless of score |
| `spot_target_pts` | `float` | Exit when underlying moves this many index points in your favour |
| `spot_stop_pts` | `float` | Exit when underlying moves this many points against you |
| `target_pct` | `float` | Exit as % of option entry premium (alternative to spot pts) |
| `stop_pct` | `float` | Stop as % of option entry premium |
| `time_stop_minutes` | `int` | Maximum holding time in minutes |
| `scenario` | `str` | Scenario classification label for this signal (e.g. `"breakout"`, `"mean_revert"`) — stored in journal, used by scenario-routed strategies |
| `spot_target_level` | `float` | Explicit absolute spot price to target (overrides pts-based calculation when set) |
| `exit_mode` | `str` | Exit-mode override (e.g. `"aggressive"`, `"trail"`) passed through to the exit plan |

## Risk Hints Drive Live Exits

The exit fields you return on `Signal` are not just backtest inputs — in forward testing the deployment evaluator captures them as `risk_hints` on every journaled signal, and auto-created paper trades use them as live exit levels:

| Signal field | Meaning | Live behaviour (auto paper trade) |
|---|---|---|
| `spot_target_pts` / `spot_stop_pts` | Exit when the UNDERLYING moves this many index points | Spot-mirror exit: when the index hits the level, the option closes at its current premium (`spot_target_hit`/`spot_stop_hit`). Direction-aware (CE target above entry spot, PE below). This is the live equivalent of the backtest's `spot_exit` mode — all built-in strategies use it |
| `target_pct` / `stop_pct` | Exit as % of the option entry premium | Premium stop/target on the trade itself (`target_hit`/`stop_hit`) |
| `time_stop_minutes` | Maximum holding time | Captured AND enforced live (reason `time_stop`, backtest parity) |

Strategy hints take priority over the deployment's `auto_paper_target_pct`/`auto_paper_stop_pct` fallbacks. If your strategy returns no exit fields and the deployment sets no fallbacks, an auto trade only closes at the 15:00 IST square-off — so always return explicit exits for SCALP/INTRADAY modes.

## Optional Base Classes

For advanced patterns you can subclass beyond `StrategyBase`:

- **`AdaptiveStrategyBase`** — adds regime-aware parameter switching and score weighting. The adaptive toolkit columns (`vel_z`, `accel_z`, `vr`, `regime_score`, etc.) are the primary inputs. See `gap_fade.py` for a worked example.
- **`ScenarioRoutedStrategyBase`** — routes each bar to one of several named scenario sub-handlers based on market conditions. Set `scenario` on the returned `Signal` to label the regime; the router accumulates per-scenario P&L for analysis. See `opening_range_regime_router.py` for a worked example.

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

Look at the shipped strategies for working patterns — `confluence_scalper.py`
(the one permanent built-in) lives in `backend/app/strategies/builtin/`; the
other 11 ship as regular plugins in `backend/app/strategies/plugins/` so they
can be retired AND deleted like any custom strategy:
- `builtin/confluence_scalper.py` — multi-factor scoring with VWAP inhibit + regime gate
- `plugins/opening_range_breakout.py` — uses `session_precompute` to set `ctx["orb_hi"]` / `ctx["orb_lo"]`
- `plugins/smc_liquidity_sweep_fvg.py` — uses `ctx["history_df"]` for lookback
- `plugins/vwap_mean_reversion.py` — regime-conditional (only in chop)
- `plugins/gap_fade.py` — `AdaptiveStrategyBase` example with `session_precompute`
- `opening_range_regime_router.py` — `ScenarioRoutedStrategyBase` example
