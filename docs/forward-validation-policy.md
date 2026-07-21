# Forward-Validation Policy — ₹2,00,000 Account

## Decision

No AlphaForge strategy is currently promotion-ready. The optimizer may nominate a
hypothesis, but only a frozen, one-lot paper cohort can earn the app's
**forward-validated** label and the recommended path to a later live operational
trial. An operator may explicitly override failed or incomplete evidence at the
real-money confirmation step; that choice does not make the strategy validated.

This policy turns the user's limits into a pre-registered contract:

- starting capital: **₹2,00,000**;
- formal impairment/ruin boundary: **₹1,00,000**;
- maximum calendar-month drawdown: **25%**;
- maximum whole-record peak drawdown: **25%** (a deliberately stricter companion
  rule); and
- upper one-sided 95% confidence bound on 252-session ruin probability: **below
  30%**.

Thirty percent annual ruin risk is permissive, not conservative. Passing it means
“within the user's stated ceiling,” not “safe” or “highly profitable.” Profitability
also requires positive forward expectancy after costs and stability across time.

## Why 60 sessions and 120 trades

A beginner should not choose a sample by asking how many trades looks impressive.
Intraday trades from the same day share market conditions and are correlated, so the
primary statistical unit is a **complete trading session**.

For promotion, "complete" means at least **357 of the 375 one-minute bars** in
the 09:15-15:30 IST market window (≥95%). The older Strategy Library visibility
card uses a deliberately looser 10:00-15:00 / 70% threshold only to show
preliminary diagnostics; those days and trades do not enter promotion statistics.

- **Plumbing checkpoint:** 10 complete sessions and 20 closed trades. This proves
  collection, entry/exit, EOD, and accounting plumbing only.
- **Promotion cohort:** at least 60 complete sessions **and** 120 closed trades.
  Both are required. Sixty sessions spans roughly three trading months, and 120
  trades prevents an extremely sparse strategy from passing on a few outcomes.
- Zero-trade complete sessions remain in the daily P&L series. Silence is part of
  the strategy record and must not be discarded.

Sixty sessions is a minimum evidence gate, not a guarantee. A regime-specific or
rare-event strategy may need materially more history.

## Frozen cohort requirements

Before session 1, save the strategy source hash and full deployment configuration.
The cohort must use:

- exactly one option lot;
- the ₹2,00,000 fixed account-wide paper-capital gate;
- costs enabled and the same entry/exit window intended for live use;
- a fixed strategy version, parameters, pre-trade profile, moneyness, DTE filter,
  exit controls, and risk policy;
- no overnight carry; and
- point-in-time option capture coverage for at least 95% of eligible decisions,
  with attempted entries that had no contract/usable price retained as misses.

Within the tolerated uncovered 5%, a positive legacy/LTP P&L contributes **zero**
to promotion expectancy while a loss remains a loss. This prevents missing
surfaces from improving the result.

Any material change starts a new cohort. Bug fixes that can change signals, pairing,
fills, or P&L also start a new cohort; the old cohort remains as evidence for the old
hash.

Every promoted trade must also carry entry-time evidence that the exact fixed
₹2,00,000 **account-wide** gate was evaluated and allowed it. Merely enabling the
gate near the end of a cohort cannot rehabilitate earlier unconstrained trades.

The ordinary strategy hash covers signal parameters. Promotion uses the broader
`forward_config_hash`; changing any decision-bearing paper configuration makes
the cohort ineligible instead of silently combining two experiments.

## Promotion gates

All checks must pass to receive `promotion_allowed=true` and the
**forward-validated** label:

| Gate | Required result |
|---|---|
| Complete sessions | ≥ 60 |
| Closed eligible trades | ≥ 120 |
| Daily expectancy | lower bound of block-bootstrap 95% CI > ₹0 |
| Time consistency | ≥ 4 of 6 non-overlapping 10-session blocks profitable |
| Option/execution coverage | ≥ 95% |
| EOD integrity | 0 overnight violations when overnight is disabled |
| Position size | exactly 1 lot |
| Capital | fixed ₹2,00,000 gate enforced |
| Configuration | stored forward-config hash still matches strategy source, parameters, option policy, filters, sizing, friction and exit/risk controls |
| Calendar-month drawdown | ≤ 25% |
| Whole-record drawdown | ≤ 25% |
| Annual impairment risk | one-sided 95% upper bound < 30% |

The app computes uncertainty from daily P&L using a five-session circular moving-
block bootstrap. It resamples 2,000 synthetic 252-session paths. “Ruin” means equity
touching ₹1,00,000 or less. The displayed point estimate is not enough; the Wilson
upper confidence bound must pass.

## Explicit operator override

Forward validation is an evidence gate, not an irrevocable strategy-selection
veto. Every technically compatible saved preset or 1-minute Strategy Library
entry can be created immediately in signal-only or paper mode. When real-money
activation is requested:

- a passing cohort can be enabled after the normal typed confirmation;
- a failed, incomplete, or unavailable cohort is shown as **Unvalidated** and
  requires a separate explicit checkbox plus the typed `ENABLE` confirmation;
- the backend requires the strict boolean `accept_unvalidated_live=true` and
  stores the failed checks, validation snapshot, accepting user, and timestamp
  in `risk.live.evidence_consent`; and
- this override bypasses evidence only. It never bypasses technical compatibility,
  an invalid/expired broker session or missing static-IP configuration, a retired or source-drifted strategy, an
  engine halt, mandatory positive daily loss cap, account lot/position ceilings,
  exchange/order constraints, idempotency, or exit protection.

Changing the account-level lot or open-position ceiling is a separate deliberate
action on Live Trading. A deployment request must fit the ceiling in force when it
is enabled, and the executor re-applies the current ceiling before every order.

## Operational loss limits

The statistical ceiling is not a daily trading budget. For the first validated
cohort:

- planned loss per trade: ₹1,000–₹2,000;
- hard daily halt: ₹4,000;
- investigation freeze: cumulative drawdown of ₹20,000 (10%); and
- absolute monthly/peak ceiling: ₹50,000 (25%).

The 10% freeze is a review stop, not automatic abandonment. Resume only after data,
execution, strategy drift, and broker/accounting reconciliation are understood.

## After paper promotion

The recommended path after paper promotion is a staged live operational validation:

1. Manual one-lot round trip using `live-readback-checklist.md`.
2. One-lot automated live cohort for at least 20 complete sessions and 30 closed
   round trips, with no cap, OCO, reconciliation, or EOD failure.
3. Compare paper-modelled and live realized slippage, rejection rate, missed-signal
   rate, and P&L attribution.
4. Scaling requires a new 60-session/120-trade cohort at the proposed sizing. Do not
   infer ten-lot capacity from one-lot results or linear P&L multiplication.

## What “highly profitable” can honestly mean

For this project, use a two-part definition:

1. **Survival contract:** every gate above passes within the stated ₹2,00,000 risk
   budget.
2. **Economic value:** the lower confidence bound of after-cost daily expectancy is
   positive and the return remains worthwhile after measured live slippage, taxes,
   downtime, and the operator's time.

Do not set a promised monthly return. First collect the qualifying cohort; then use
its confidence interval to set a realistic return range. A large in-sample or
unbounded ten-lot paper profit is not evidence of achievable monthly income.
