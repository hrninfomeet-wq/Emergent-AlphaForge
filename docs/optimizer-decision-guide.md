# Optimizer Decision Guide

## Practical verdict

The optimizer is useful for **hypothesis triage and parameter sensitivity**, not as
an automatic trading-engine oracle. Its best use is to reject fragile ideas and
nominate one frozen paper candidate. It cannot manufacture edge, repair biased data,
or replace forward validation.

Current important limitations:

- A Single run is in-sample.
- Walk-forward re-optimizes on spot and then reports option-bar OOS P&L; the option
  result does not re-rank each window.
- Single-run option re-rank considers only a top-K shortlist selected on spot.
- The promoted WFO `final_params` are the last training window's winner, not one
  universal parameter set validated across all windows.
- Grid is silently converted to Bayesian in walk-forward.
- Parallel Bayesian workers are nondeterministic.
- Cost/spread settings are model assumptions, not calibrated executable history.
- The Survivability panel is an observed-history IID stress screen, not the annual
  block-bootstrap promotion test.
- Exit-control search reuses the same history and adds another selection layer.

## Beginner-safe starting profile

Click **Apply ₹2L evidence profile**:

| Control | Setting |
|---|---|
| Run type / method | Walk-forward / Bayesian |
| Windows | 60 train, 20 test, 20 step, rolling |
| Search | 40 trials per window, maximum 6 windows |
| Workers | 1 |
| Objective | Risk-adjusted |
| Option OOS | On, ATM, one lot, costs on |
| Guard rails | On, minimum 30 trades |
| Indicator-period search | Off |
| Exit-control search | Off |
| Analysis budget | 0 (finish all planned analysis) |

This is deliberately a stable comparison profile. It is not a promise that 40
trials or six windows are universally optimal.

## Control-by-control value audit

| Setting | Value | Decision rule |
|---|---|---|
| Run name | High for traceability | Use a hypothesis/cohort name; never overwrite meaning with “best.” |
| Instrument | Essential | Optimize only the index to be traded; lot size/liquidity do not transfer. |
| Strategy | Essential | One hypothesis family per run. |
| Method | High | Bayesian default. Grid is useful only for a genuinely small discrete Single-run space. Genetic is exploratory. |
| Objective | High | Risk-adjusted for shortlist. Raw P&L invites unstable fits; Min DD can select economically trivial strategies. |
| Pre-trade profile | Essential | Must match the intended paper/live profile. |
| Run type | Essential | Single = exploration; walk-forward = evidence screen. |
| Train days | High | 60 is a starting compromise; compare 40/60/90 only as a robustness study. |
| Test days | High | 20 creates interpretable monthly blocks. Avoid tiny folds. |
| Step | High | Equal to test days for non-overlapping OOS evidence. Smaller overlaps inflate apparent sample. |
| Rolling/anchored | Medium | Rolling adapts to regime; anchored tests whether old data still helps. Predeclare one as primary. |
| Trials per window | High | 40 initially. More trials increase selection bias as well as search depth. |
| Max windows | High | Six gives the minimum 120 OOS-session design if data permits. Do not cherry-pick windows. |
| Parallel workers | Low/negative for decisions | Keep 1. Use >1 only for explicitly exploratory speed tests. |
| Option-aware OOS | High rejection value | Keep on. A negative result rejects a spot winner; a positive result remains research-only. |
| Evaluation mode | High in Single runs | Option re-rank for triage; spot-only only for cheap early exploration. WFO ignores this selector for window fitting. |
| Re-rank top-K | Medium | 25–50. It is a heuristic; raising K is not proof of an option-native optimum. |
| Diversity shortlist | Medium exploratory | Can rescue spot-mediocre candidates, but expands researcher degrees of freedom. Predeclare it. |
| Analysis budget | High | Use 0 for evidence. A timed partial result is exploration-only. |
| Moneyness | Essential | ATM default. Treat ATM/ITM/OTM comparisons as separate hypotheses. |
| DTE filter | Essential | Use the intended deployment DTE set. Testing many subsets is hidden multiple testing. |
| Lots | Essential for ₹ realism | One for this account. It scales modelled rupees but does not prove capacity. |
| Option exit | Essential | Mirror spot unless the strategy is explicitly premium-native. |
| Premium target/stop | Conditional | Valuable only in premium-level mode; predeclare units and values. |
| Option costs/spread | Essential | Keep on. Replace fixed assumptions with measured forward slippage when available. |
| Trading capital | High in Single stress screen | ₹2,00,000. It does not alter the WFO promotion policy. |
| Equity/ruin floor | High | ₹1,00,000 is the formal impairment boundary. |
| Max drawdown | High | 25% ceiling; operational freeze earlier at 10%. |
| Max stress RoR | Medium diagnostic | 30% user ceiling, but current panel is not annual. Do not confuse it with promotion. |
| Survival objective | Medium | Calmar is preferable to raw ₹ for triage. |
| Exit-control search | Low before edge exists | Off. Use only after a base candidate passes independent evidence, then revalidate in a new cohort. |
| Trial budget / early stop | High for Single exploration | Start 100–200 with early stop. Thousands of trials increase overfit risk. |
| Spot costs | High | Keep on for a consistent shortlist, while remembering option costs are separate. |
| Indicator-period search | Low initially | Off. Turn on only as a separately named, larger-budget experiment. |
| Guard rails / min trades | High | On / 30 for optimizer triage. This is not statistical significance. |
| Min CE/PE share | Conditional | 0 unless the thesis requires balanced direction. Do not force symmetry onto a directional edge. |
| Date window | Essential | Freeze start/end before running. Never choose it after seeing results. |
| Entry window | Essential | Match live-effective 09:25–14:50 unless the deployment rules change. |
| Parameter bounds | Essential, high risk | Use strategy-plausible ranges fixed before the run. Narrowing after seeing winners is curve fitting. |

## How to read the result

1. Reject any run with option-integrity `research_only` if the question is live
   promotion. It may still rank hypotheses.
2. Require positive option OOS P&L in at least four of six windows, not only a
   positive stitched total.
3. Inspect parameter stability. Wildly changing winners mean the model is fitting
   regimes or noise; the final window's params are not a durable answer.
4. Inspect paired coverage and trade count. Missing option bars can change both who
   trades and who wins.
5. Save the winner as a **research preset**, freeze its hash, and start a new one-lot
   forward cohort. Do not keep optimizing the same holdout after disappointment.

## Current evidence

The strongest available WFO records do not survive option modelling:

| Strategy | OOS windows | Spot OOS | Option OOS | Option-positive windows |
|---|---:|---:|---:|---:|
| VWAP pullback | 6 | +874.51 points / 526 trades | −₹1,03,157 | 1 of 6 |
| SMC | 6 | −1,090.74 points / 313 trades | −₹1,14,304 | 0 of 6 |

The correct optimizer decision today is therefore **reject both for promotion**,
not spend more trials trying to turn the same evidence green.

