# `atr_sigma_router` — optimizer winners and their out-of-sample verdict (2026-08-16)

Saved so these exact configurations are not re-run and re-believed. **All four failed
out-of-sample.** Read [`OPTION_BUYING_MICROSTRUCTURE_2026-08.md`](OPTION_BUYING_MICROSTRUCTURE_2026-08.md)
for why that was the expected outcome.

## Results

> ⚠ **These metrics are STALE as of `fc424a1` (2026-08-19).** A deployment audit found the
> plugin was level-triggered, so it re-fired while a setup stayed active, and it did not
> enforce its own `signal_threshold`. Both are now handled inside `evaluate()`, which changes
> trade counts. The recorded PARAMETERS remain valid for reproduction; the metrics beside them
> no longer correspond to the current code. **The failed-holdout verdict is unaffected** — it
> was reproduced across four independent runs, and the separate live-VWAP defect fixed in the
> same commit is live-only (the backtest engine anchors VWAP over the full frame).


| Job | Index | Trials | Search window | In-sample | True holdout | Verdict |
|---|---|---|---|---|---|---|
| `72c2b408` | SENSEX | 800/800 | 2025-11-01 → 2026-08-14 | **+₹529,656** · PF 2.27 · Sharpe 5.56 · win 63.6% | **−₹305,326** · win 40.8% · 233 paired | ❌ |
| `7124d77b` | NIFTY | 800/800 | 2025-11-01 → 2026-08-14 | **+₹655,931** · PF 1.61 · Sharpe 3.31 · win 55.6% | **−₹518,145** · win 41.7% · 408 paired | ❌ |
| `6f9c1698` | NIFTY | 396/800 | 2026-01-01 → 2026-08-14 | **+₹384,966** · PF 1.68 · Sharpe 3.33 | **−₹445,917** · win 28.9% · 263 paired | ❌ |
| `19489eba` | NIFTY | 77/120 | 2024-11-25 → 2025-12-31 | **+₹17,935** · PF 1.38 · Sharpe 2.12 | **−₹10,270** · win 30.8% · 26 paired | ❌ |

Holdout for the first three = 2024-11-25 → 2025-10-31 (before their search window).
Holdout for `19489eba` = 2026-01-01 → 2026-08-14 (after its search window).


## Post-fix re-test (2026-08-19, `34d5b69` + `4d95135`)

After the eight defect fixes, all three families were re-run at DEFAULT parameters on
NIFTY, `dte_filter [1,2]`, ATM, costs on. The fixes cut trade counts sharply (the plugin is
now edge-triggered and enforces its own threshold): momentum 5,287 → 1,715, fade 6,110 →
1,859, vol_expansion 1,120 → 835.

`fade` flipped from −₹126,928 to **+₹1,267** on the full window — which is **₹1.6 per trade
over 784 trades**, i.e. noise. Split train/holdout and tested properly:

| Family | Train (2024-11→2025-12) | Holdout (2026) |
|---|---|---|
| fade | −₹752 · t = **−0.02** | +₹657 · t = **+0.03** |
| vol_expansion | −₹29,884 · t = −1.17 | −₹30,760 · t = **−1.80** |

Nothing clears \|t\| > 2. (Those are TRADE-level t-stats — the grouping key was not a date,
so each trade counted as its own session. Trade-level is the *more permissive* test, with
more observations and a tighter standard error, so the conclusion holds a fortiori.)

**Verdict unchanged: no edge at default parameters.** The plugin is now correct and
well-tested; it is a search space, not a strategy with a demonstrated edge. This is what
[`OPTION_BUYING_MICROSTRUCTURE_2026-08.md`](OPTION_BUYING_MICROSTRUCTURE_2026-08.md)
predicted — the ATM buyer's MFE/MAE is 0.90–0.95 before costs.

## What went wrong, precisely

**1. No holdout in the search protocol.** The three large runs optimized over the whole
window and reported the best trial. An in-sample Sharpe of 5.56 selected from 800 trials is
a ranking artifact, not an edge estimate.

**2. `optimize_indicator_periods` was ON**, adding 8 dimensions (`adx_length`, `atr_length`,
`ema_fast/slow`, `macd_fast/slow/signal`, `rsi_length`, `chop_length`, `swing_lookback`) to
the 11 strategy parameters. **~19 dimensions over 800 trials** is far too much freedom for
~200 sessions of data.

**3. `weekday_mask` is the overfitting canary.** The winners chose masks 21 (`0b10101` =
Mon/Wed/Fri), 13 (Mon/Wed/Thu), 31 (all) and 27 — mutually inconsistent across runs on the
same data. When a purely calendar knob swings like that between runs, the search is fitting
noise. Consider pinning it to 31.

**4. Spot-positive, option-negative — again.** `19489eba` was **+490 spot points, PF 1.299,
Sharpe 1.816** on its holdout and still **−₹10,270** once real option candles were paired.
A thin spot edge does not survive premium friction and theta. Judge on `option_rerank`
rupees only.

## The one durable finding

**`entry_family: 2` (volatility-expansion) won all four runs independently** — two indices,
three different windows, two different cost models. That consistency is real even though the
parameterisations around it were not. If this strategy is revisited, family 2 is the only
part worth carrying forward.

## Verification defect found while producing this table

The optimizer stores `option_config` **without an `enabled` key** (it is implied by
`evaluation_mode: "option_rerank"`). `OptionBacktestReq.enabled` defaults to **`False`**, so
replaying a stored optimizer config verbatim through `/backtest/run` silently runs with the
option overlay OFF and reports `paired: 0` — which reads as a data-coverage failure rather
than a misconfiguration. Force `enabled: True` when replaying a stored config.

## If you optimize this again

- Split the window: search on train, evaluate the winner **once** on an untouched holdout.
- Turn `optimize_indicator_periods` OFF, or pin most of it.
- Pin `entry_family` to 2 and `weekday_mask` to 31.
- Keep `evaluation_mode: "option_rerank"`; never promote on the spot objective.
- Require the holdout to clear a **session-level t-stat > 2**, not just a positive total —
  26 or 44 paired trades cannot support a conclusion in either direction. The
  forward-validation policy's ≥120 closed trades is the right bar.

**Winning parameter sets** (for reproduction, not for deployment):
`scratchpad/atr_sigma_router_winners.json` from the 2026-08-16 session; the four `job_id`s
above are also queryable via `GET /api/optimize/jobs/{id}`.
