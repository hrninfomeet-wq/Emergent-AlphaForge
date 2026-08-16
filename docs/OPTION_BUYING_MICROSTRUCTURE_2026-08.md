# Option-buying microstructure — measured register (2026-08-16)

**Read this before proposing any intraday option-BUYING strategy.** It records what
the warehouse actually says about the payoff an option buyer faces, so the next
campaign starts from measurement instead of from a hypothesis. Every number below
was measured on this repo's own data, not taken from literature.

Companion to [`BACKTEST_INTEGRITY_AUDIT.md`](BACKTEST_INTEGRITY_AUDIT.md) (can I trust
a number?) and [`PREMIUM_MOMENTUM_EDGE_VERDICT_2026-07.md`](PREMIUM_MOMENTUM_EDGE_VERDICT_2026-07.md)
(the third failed campaign). This one answers: *is there anything to find?*

**Data:** `candles_1m` + `options_1m`, 2024-11-25 → 2026-08-14 (428 NIFTY / 426 SENSEX
sessions), plus 769k NIFTY ticks (2026-05-27 → 2026-08-14).

---

## 1. The headline: the buyer's payoff is negative BEFORE costs

For an ATM option, measured over every eligible bar, favourable excursion (MFE) divided
by adverse excursion (MAE):

| Horizon | NIFTY | SENSEX |
|---|---|---|
| 5 min | 0.92 | 0.92 |
| 10 min | 0.95 | 0.94 |
| 15 min | 0.90 | 0.90 |

**Below 1.0 means the median long-premium position moves against you more than for you** —
before slippage, STT or brokerage. This is the structural fact behind three failed
campaigns. It is not a tuning problem.

Two corroborating measurements:

- **`share of bars with MFE ≥ 2×MAE` is 35–37% at EVERY horizon 3m → 30m.** Horizon-invariance
  is the signature of a random walk: there is no timeframe at which the index is naturally
  trendier. Any edge must come from *conditioning*, not from horizon choice.
- **Efficiency (daily range ÷ 5-min path length) = 0.26 on all three indices.** NIFTY walks
  792 points of 5-minute path to net a 207-point range. 74% of the path is noise.

## 2. Frequency is not free

Round-trip friction as a share of the median favourable move:

| Horizon | friction / median MFE |
|---|---|
| 3 min | 43–90% |
| 5 min | 32–67% |
| 30 min | 13–26% |

Every added round trip pays a fixed cost against a mostly-noise path. **"More trades" is the
wrong direction in this app regardless of signal quality.** Combined with §4 below, tight
stops are also exactly where the backtest stops being trustworthy.

## 3. DTE: 0DTE is the WORST day to buy, not the best

Net cost of holding an ATM option for 5 minutes, by days to expiry:

| | 0DTE | 1DTE | 2DTE | 3DTE |
|---|---|---|---|---|
| NIFTY | **−4.43%** | −1.48% | −1.22% | −0.95% |
| SENSEX | **−2.01%** | −0.61% | −0.53% | −0.45% |

⚠ **A trap this document exists to prevent.** On *gross-move-versus-friction* 0DTE looks
best: its premium is ~4× cheaper (₹36.6 vs ₹140 median on NIFTY) while the absolute
option-point move is nearly identical (5-min MFE 3.30 vs 3.90 pts), and 0DTE ATM carries
**8.4× the volume**. That ratio is real and it is the wrong metric — it ignores theta.
Measure **net**, and 0DTE bleeds ~3× faster than 1DTE. An earlier pass of this analysis
recommended 0DTE on exactly that error.

**Moneyness:** ITM1 is *worse* than ATM (NIFTY 1DTE: −2.20% vs −1.48%) — it carries 2× the
modelled per-side slippage (`SlippageConfig`: ITM1 1.0 pt vs ATM 0.5) and worse raw MFE/MAE.
Note that 2× is a model assumption, not a measurement; if real ITM1 spreads are tighter the
gap narrows. OTM1 has the best raw MFE/MAE (>1.0 at 30 min) but see §5.

## 4. 1-minute bars vs live ticks — the disparity is real, bounded, and avoidable

Rebuilt 1-min bars from the raw tick stream and compared against `candles_1m`:

- **Stored bars reproduce the tick extremes exactly — 0.0% of bars had ticks outside the
  stored high/low.** Stops and targets trigger faithfully in backtest.
- Feed lag (received − exchange ts): **p50 1026 ms, p90 1135 ms**. That is the real
  decision-to-fill staleness.
- A 1-min bar hides **~5× its range** in intrabar oscillation (p50 4.97×, p90 7.55×).
- Bars where a stop AND a target would both sit inside the range — where the backtest must
  guess the order and live would not:

| stop distance | ambiguous bars |
|---|---|
| ±5 NIFTY pts | 2.51% |
| **±10 pts** | **0.16%** |
| ±20 pts | 0.02% |

**Design rule: keep stops ≥ ~4 bps of spot (≈10 NIFTY / 32 SENSEX points)** and
backtest/live agree to within a fifth of a percent of bars. `atr_sigma_router`'s
`min_stop_bps` enforces this.

⚠ Caveat: the feed is **1 tick/second** (exactly 60/min), not every trade. Backtest and live
therefore see the same 1 Hz resolution — which is *why* they agree — but neither observes
true per-trade extremes.

## 5. Hypotheses tested and killed

All causal (σ built from prior sessions only), both indices, 5/10/15-minute horizons.

| # | Hypothesis | Result |
|---|---|---|
| 1 | **Noise-band breakout** (Zarattini/Aziz, translated to NSE) | Monotonically WORSE as the band widens: NIFTY 5m MFE/MAE 0.923 → 0.741 at k=2.0 |
| 2 | **Mean-reversion fade** (the inverse) | Also < 1.0, and inconsistent across indices |
| 3 | **Volatility compression → expansion** (best-powered: 6,000+ bars/bucket) | Straddle MFE/MAE 0.87–0.96 in EVERY quartile, and *rose* with activity rather than with coiling — the coiled-spring thesis is backwards |
| 4 | **ITM1 on the day before expiry** | Worse than ATM on both raw MFE/MAE and net (§3) |
| 5 | **OTM1 convexity at 30-min holds** | Pooled mean looked positive (+1.78% SENSEX) then **collapsed**: NIFTY median −5.49%, **1.4% of sessions positive**, t = −20.7; SENSEX 7.6%, t = −13.2 |

**Method lesson from #5, worth more than the result:** pooled means over overlapping 1-minute
windows are ~30× redundant and skew-driven. **Always report per-session medians and a
session-level t-stat before believing a cell.** A positive pooled mean on 34k overlapping
observations is roughly 1,100 independent ones.

## 6. Expiry weekday ROTATED TWICE — never hardcode it

Derived from stored `expiry_date` values:

| | 2024Q4 | 2025Q1–Q2 | 2025Q3 | 2025Q4 → 2026 |
|---|---|---|---|---|
| NIFTY | Thu | Thu | Thu → **Tue** | **Tue** |
| SENSEX | Fri | **Tue** | Tue → **Thu** | **Thu** |

A weekday-based DTE rule reproduces the real expiry on only **233/424 NIFTY** and
**238/426 SENSEX** sessions. Any "trade NIFTY on Mondays for 1DTE" rule is correct for the
2026 regime and silently wrong across 2024–2025.

**DTE targeting belongs to the run's `dte_filter` / the deployment's option policy**, which
read real expiry metadata (`runtime.py`, `app/dte.py`). Strategies must not re-derive it.

## 7. What this means for a strategy

- Frequency is a cost, not a feature (§1, §2).
- Prefer 1–3 DTE over 0DTE, ATM over ITM1 (§3).
- Keep stops above the ambiguity floor (§4) — which also happens to be where friction stops
  dominating.
- The only payoff shape that survives is small-loss/large-win. A high win rate is not
  available: 35–37% of bars are the whole opportunity set (§1).
- Anything that reports positive must clear a **session-level t-stat > 2** on an untouched
  holdout before it means anything.

**Reproduction:** the five screening scripts are throwaway analysis, not shipped code. The
MFE/MAE screen is cheap to rebuild and should be re-run before any new campaign — it kills
candidates before a plugin is written.
