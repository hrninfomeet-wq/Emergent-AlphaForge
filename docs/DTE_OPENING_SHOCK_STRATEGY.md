# DTE Opening Shock Breakout

Status: **research and paper evaluation only**. This strategy has not demonstrated a
reliable after-cost edge and is not approved for live capital.

## Purpose

`dte_opening_shock_breakout` tests whether a directional opening shock followed by a
confirmed opening-range break can transfer to long DTE1/DTE2 NIFTY or SENSEX options.
It is an ordinary, underlying-led one-minute plugin. DTE, expiry and moneyness remain
execution-policy settings so the same signal logic can be compared without embedding
weekday assumptions in the strategy.

## Frozen signal contract

1. Build the opening range from the exact 30 bars labelled 09:15 through 09:44 IST.
2. Compare the 09:44 bar close with the previous session close:
   - above: only a CE setup is eligible;
   - below: only a PE setup is eligible;
   - equal or unavailable: no setup.
3. Emit only the first same-session close that crosses the range in the eligible
   direction. There is at most one signal per index-session.
4. The 14:48 bar is the final eligible signal bar. It closes at 14:49, before the
   hard 14:50 entry cutoff. The 14:49 bar is not eligible because its decision would
   occur at 14:50.
5. The spot stop distance anchors to the broken opening-range boundary. The default
   spot target is 40 index points. Score 65 is an operational ranking value, not a
   win-probability estimate.
6. Missing prior close, missing opening bars, non-finite values or missing session
   context fail closed.

The live evaluator retains 400 bars for this plugin so the prior close and exact
opening range remain available through 14:48. Other plugins retain their existing
200-bar default.

## Required Backtest and Optimizer configuration

Use separate NIFTY and SENSEX cohorts. Do not pool the result or select the better
index after viewing a protected holdout.

```json
{
  "mode": "INTRADAY",
  "strategy_id": "dte_opening_shock_breakout",
  "timeframe": "1m",
  "params": {
    "spot_target_pts": 40,
    "signal_threshold": 60
  },
  "trade_window_start": "09:45",
  "trade_window_end": "15:00",
  "costs_enabled": true,
  "option_backtest": {
    "enabled": true,
    "moneyness": "itm1",
    "lots": 1,
    "dte_filter": [1, 2],
    "auto_fetch": false,
    "cost_config": {
      "enabled": true,
      "exchange_txn_rate": 0.0003503,
      "spread_pct_of_premium": 1.0
    }
  }
}
```

The JSON above is the NIFTY/NFO example. For SENSEX/BFO use
`exchange_txn_rate: 0.000325`. In code, derive the complete base schedule with
`cost_config_for_exchange("NFO" | "BFO").to_dict()` and then add the 1% spread;
do not let SENSEX inherit the generic NFO default.

Important: the generic Optimizer UI defaults `trade_window_end` to 14:50, and the
backtest engine currently uses that field for both entry eligibility and forced exit.
For this plugin, set it explicitly to **15:00**. The plugin itself stops entry signals
at 14:48. Pin `signal_threshold` to 60; values through 65 are behaviorally identical
and values above 65 suppress every signal, so optimizing it would be meaningless.

The 1% premium spread is a stress sensitivity, not a measured historical spread.
Use date-appropriate statutory rates for historical studies and observed bid/ask
quotes for forward calibration.

## Development screen — not a profitability verdict

Selection window: 2025-01-06 through 2025-01-31. This is inside an already-used
training period; the protected 2026 holdout was not queried.

- Warehouse integrity: NIFTY and SENSEX each had 20/20 complete sessions and
  7,500/7,500 spot candles.
- Configuration: ITM1, DTE `[1, 2]`, one lot, costs enabled, 1% premium spread,
  spot-signal exit, no option-level target/stop.
- NIFTY: 16 all-DTE signals; 6 DTE1/2 option pairs; 100% pairing; net **-Rs 1,788.21**.
- SENSEX: 17 all-DTE signals; 6 DTE1/2 option pairs; 100% pairing; net **Rs 1,943.14**.
- The read-only in-memory Optimizer rerank reproduced NIFTY **-Rs 1,788.21** exactly
  for the same candidate and loaded 1,356 contracts plus 5,968 option candles.

Six paired trades per index are far too few to estimate expectancy. The positive
SENSEX result and negative NIFTY result are both provisional observations. They prove
that DTE filtering, option pairing, cost application and optimizer/backtest parity run;
they do not justify deployment or further tuning against the holdout.

## One-minute versus live-quote policy

- Signals are generated only from completed one-minute underlying bars.
- Live option ticks may protect exits and measure execution, but cannot be used to
  claim a sub-minute backtest edge.
- Historical option candles do not contain executable bid/ask depth, queue position,
  latency or intrabar path. The current screen is research triage only.
- Before promotion, collect a received-time-keyed one-second best-bid/ask tape,
  replay fills at next executable ask/bid with staleness and latency rules, and compare
  candle-model, quote-replay and observed paper fills. Current tick retention/storage
  is not a lossless long-horizon quote replay source.

## Promotion gates

1. Freeze the hypothesis and small parameter budget before validation.
2. Run chronological validation and then one untouched holdout only after finalists
   are recorded. Costs and spread sensitivities stay enabled.
3. Require project policy gates, including adequate point-in-time option coverage,
   stable time blocks and a positive 95% block-bootstrap lower bound.
4. Run a frozen one-lot paper-forward cohort and reconcile modeled versus observed
   fills. A green backtest is not live approval.
5. Live use still requires a separate user authorization and completion of the
   market-session validation plan. Do not weaken entry, square-off, concurrency,
   loss or broker-execution safeguards for this strategy.
