# Chart math checkpoint

Scope: presentation calculations and semantic drawing normalization only. Owned files: `frontend/src/lib/chartModel.js`, `frontend/src/lib/chartIndicators.js`, `tests/js/chartModel.test.mjs`, `tests/js/chartIndicators.test.mjs`.

Contract confirmed with the UI agent: indicator IDs `volume`, `vwap`, `ema`, `sma`, `bb`, `rsi`, `macd`, `supertrend`, `atr`, `adx`. Parameters are `period`, `multiplier` for BB/Supertrend, and `fast`/`slow`/`signal` for MACD. Composite series use colon-delimited stable IDs; panes are `price` or their indicator ID. Warmup has no points; interior unavailable values use `{time}` chart whitespace, never fake numeric values.

Confirmed: implementation and focused validation are complete. `node --test tests/js/chartModel.test.mjs tests/js/chartIndicators.test.mjs` reported **18 passed, 0 failed** on Node `v24.6.0`. The first run found a fixture bug: passing undefined into a test factory invoked its default volume; the fixture now explicitly overwrites volume. The implementation did not change to accommodate that test error. `git diff --check` on the owned paths exited zero. These files are newly added/untracked, so the diff command alone does not certify their content; the Node suite imports and executes them directly.

Conventions: SMA-seeded EMA; arithmetic-seeded Wilder smoothing; ATR includes first-bar high-low; RSI and directional movement require the first actual price change. ADX first output is after two complete smoothing stages. Completely flat RSI is neutral 50; zero-range directional movement is zero. Bollinger uses population variance. These explicit left-edge conventions are not a claim of bit-for-bit parity with every chart provider.

VWAP uses HLC3 weighted by volume and resets by IST date. Index/unknown volume suppresses both volume and VWAP with warnings. Missing volume invalidates the remainder of that session's VWAP. Loaded history can begin after session open, so the UI always receives a warning that full-session VWAP requires session-start intraday history.

Critical risk: a mathematically correct value can still be mistaken for a full-session or provider-identical value. The loaded-history warning and explicit seed conventions must remain; omitting them could mislead a trading decision. No strategy calculations or order paths are changed.

Primary formula references inspected this session: [Supertrend](https://www.tradingview.com/support/solutions/43000634738-supertrend/), [ADX](https://www.tradingview.com/support/solutions/43000589099-average-directional-index-adx/), [RSI](https://www.tradingview.com/support/solutions/43000502338-relative-strength-index-rsi/). Independent checks use hand-derived fractions, with MACD/ADX cross-calculated through Python `Fraction`, not production helper copies.

Core lesson: warmup alignment and missing volume must be specified as part of the data contract. Confirmed approach: preserve application-owned drawing anchors/contract identity and compute chart-only indicators without strategy coupling. Dead end to avoid: substituting an arithmetic mean when volume is absent.

Review handoff: `chartModel.js` was already present from the interrupted agent and was preserved unchanged after the new tests exercised its contract. The UI agent has been told that interior missing indicator data uses Lightweight Charts whitespace and that volume availability requires boolean true. Root owns final integration, build/browser checks and `learning_log.md`/TODO updates. This math suite does not certify live prices, broker freshness, chart rendering, or trading efficacy. Node's module-type warning is informational; no package configuration was changed.
