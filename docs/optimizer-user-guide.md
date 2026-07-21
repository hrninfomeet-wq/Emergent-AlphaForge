# AlphaForge Auto-Optimizer — User Guide

*Beginner guide for `/optimizer`. Updated 2026-07-20.*

## What the optimizer can and cannot do

The optimizer searches a strategy's parameter bounds and ranks settings on historical
results. It is valuable for rejecting weak hypotheses, studying sensitivity, and
nominating a frozen one-lot paper candidate.

It does **not** prove profitability. Historical option bars are currently research-
only, a Single run is in-sample, and the Single-run Survivability panel is a stress
screen rather than the annual promotion model. The durable setting-by-setting audit
is in [`optimizer-decision-guide.md`](optimizer-decision-guide.md); the paper-to-live
contract is in [`forward-validation-policy.md`](forward-validation-policy.md).

## Recommended first run

1. Click **Apply ₹2L evidence profile**.
2. Select the instrument and the strategy hypothesis you actually intend to test.
3. Keep the intended pre-trade profile and date range fixed before running.
4. Keep ATM, one lot, option costs on, workers at one, and the 09:25–14:50 entry
   window unless the deployment contract deliberately differs.
5. Run the walk-forward job. It uses 60 training sessions, 20 unseen test sessions,
   20-session non-overlapping steps, 40 Bayesian trials per window, and at most six
   windows.
6. Treat the option-OOS result as a **rejection screen**. Negative option ₹ rejects
   the hypothesis. Positive option ₹ only nominates a research preset.
7. Save a qualifying result as a **Research Preset**, freeze its strategy hash, and
   collect a new one-lot paper cohort. Do not deploy it live from the optimizer.

## The two run types

- **Single optimization** searches and scores on one historical window. Use it for
  cheap exploration and parameter plots, never as promotion evidence.
- **Walk-forward** repeatedly fits on an earlier train window and scores the winner
  on the next unseen window. It is the correct research screen. Its window search is
  still spot-based; Option-aware OOS then models the stitched trades on historical
  option minute bars and reports the result without re-ranking the windows.

Walk-forward converts Grid to Bayesian. The saved `final_params` come from the most
recent training window, so parameter stability matters as much as the stitched total.

## Reading results

Check in this order:

1. **Data-integrity gate:** `research_only` means the result cannot justify promotion.
2. **Option OOS net ₹:** a spot winner that loses after option behaviour and costs is
   rejected.
3. **Per-window consistency:** require at least four of six option-positive windows
   in the later forward cohort; do not trust one lucky block.
4. **Pairing coverage:** missing candles can change the result. Research promotion
   later requires at least 95% point-in-time execution coverage.
5. **Parameter stability:** large window-to-window movement is evidence of regime
   fit or noise.
6. **Trade count:** the optimizer minimum of 30 is only a few-trade screen. Forward
   promotion requires 60 complete sessions and 120 closed trades.

## Single-run advanced controls

- **Option re-rank** re-scores a spot-selected top-K on historical option bars. Use
  25–50 candidates for research. It is a heuristic, not an exhaustive option-native
  optimization.
- **Analyzing budget** should be 0 for a complete comparison. A timed partial result
  is exploration-only.
- **Survivability stress screen** can filter obvious modelled account blow-ups. For
  this account use ₹2,00,000 capital, ₹1,00,000 impairment floor, 25% maximum
  drawdown, and 30% maximum stress RoR. The panel's IID observed-horizon RoR is not
  the 252-session block-bootstrap promotion statistic.
- **Exit-control search** reuses the same history and adds selection bias. Keep it
  off until a base exit policy has independent forward evidence; then test the new
  exit policy as a new frozen cohort.
- **Indicator-period search** substantially expands the search space. Keep it off
  for the primary run and name any later experiment separately.
- **Parallel workers >1** make Bayesian results nondeterministic. Keep one for any
  decision record.

## Current decision

No current optimizer result is promotion-ready. The best available VWAP-pullback WFO
record is spot-positive but loses ₹1,03,157 in modelled option OOS with only one of
six option-positive windows. SMC loses in both spot and option OOS. More trials on the
same evidence would increase selection pressure, not fix the missing edge.
