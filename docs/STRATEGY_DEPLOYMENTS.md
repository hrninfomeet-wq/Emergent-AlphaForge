# Strategy Deployments

Updated: 2026-05-26

This document defines the direction for deploying backtested strategies into forward testing, live recommendations, and paper trading.

Current implementation status:

- Implemented: persisted Strategy Deployment objects, backend CRUD/status routes, source validation from saved presets or saved backtest results, and a Live Signals management panel.
- Not implemented yet: the 1-minute close evaluator/runner that turns deployment output into clean or blocked signals.
- Not implemented yet: WebSocket/tick-driven evaluation, automatic paper fills, live mark-to-market, and broker order execution.

## Purpose

AlphaForge should let a trusted backtest or saved preset become a controlled forward-test deployment. The goal is to journal clean signals, blocked signals, paper trades, and profitability by strategy version and parameter set.

The app must not treat a good backtest as permission to generate live trade recommendations automatically. A strategy must pass through an explicit deployment contract first.

## Core Principle

Use this flow:

1. Backtest a strategy.
2. Save or choose an approved preset/backtest result.
3. Create a Strategy Deployment from that audited artifact.
4. Run the deployment in forward testing.
5. Journal clean signals and blocked signals.
6. Manually approve any paper trade or recommendation.
7. Review forward profitability before trusting the strategy.

Avoid this unsafe shortcut:

`strategy file -> direct live recommendation`

## User Decisions

The following decisions are locked for the first implementation:

- First forward mode uses `1m_close` confirmation.
- Per-tick evaluation is allowed later, but only by manual user switch after the strategy is trusted.
- Every generated signal requires manual approval before paper deployment or recommendation action.
- Default option selection is `ATM`.
- User can configure `ATM`, `OTM1`, and `ITM1`.
- Deployments can be created only from saved presets or saved backtest results.
- Blocked signals must be recorded and clearly identifiable.
- Fewer cleaner signals are preferred over recording every weak signal.

## Deployment Object

A Strategy Deployment is persisted in the `strategy_deployments` collection.

Current/planned fields:

| Field | Purpose |
|---|---|
| `id` | Stable deployment id |
| `name` | User-facing deployment name |
| `source_type` | `preset` or `backtest_run` |
| `source_id` | Preset name or backtest run id |
| `strategy_id` | Strategy plugin id |
| `strategy_version` | Plugin version at deployment time, when available |
| `strategy_hash` | Optional hash of strategy code/config for audit, when available |
| `params` | Frozen strategy parameters |
| `instrument` | NIFTY, BANKNIFTY, or SENSEX |
| `timeframe` | First implementation: `1m` |
| `confirmation_mode` | First implementation: `1m_close`; later `tick` |
| `option_policy` | Moneyness, expiry policy, lot settings |
| `pretrade_profile` | Conservative/Balanced/Aggressive or custom settings |
| `mode` | `shadow`, `paper`, or `recommendation` |
| `risk` | Stop, target, max trades/day, daily loss cutoff |
| `status` | `ACTIVE`, `PAUSED`, or `ARCHIVED` |
| `created_at` / `updated_at` | Audit timestamps |

## Modes

### Shadow Mode

Shadow mode generates and journals signals but does not create paper trades.

Use it first for every new deployment. This mode answers: would the strategy fire cleanly in real time without hindsight?

### Paper Mode

Paper mode can create paper trades from eligible signals, but the first implementation must require manual approval for each signal.

No auto-paper trading by default.

### Recommendation Mode

Recommendation mode shows a trade recommendation with full context. The user chooses Take or Skip.

This is not broker order execution.

## Signal Rules

Forward signals should be evaluated on completed 1-minute candles first.

Each signal record should include:

- deployment id
- source preset or backtest run id
- strategy id and version/hash
- frozen params
- instrument and timeframe
- confirmation mode
- current candle timestamp
- direction
- score/confidence
- reasons
- blockers
- selected option contract
- pre-trade profile result
- state transition history
- manual approval decision
- linked paper trade id, if any

Blocked signals should be stored with a distinct state or flag such as:

- `state: "BLOCKED"`
- `blocked: true`
- `blockers: [...]`

Blocked signals must be easy to filter out of normal actionable signal views but visible in audit/review views.

## Option Selection Policy

First version:

- Default moneyness: `ATM`.
- Configurable choices: `ATM`, `OTM1`, `ITM1`.
- Use stored option contract metadata.
- Do not hard-code expiry weekdays.
- Use current expiry policy from the deployment.

Later versions can support wider option chains, liquidity filters, and spread checks.

## Manual Approval

Every signal must require manual approval before becoming a paper trade or recommendation action.

Approval choices:

- Take to Paper
- Skip
- Watch
- Mark blocked/invalid

Each choice should be journaled with timestamp and optional note.

## Deployment Eligibility

A strategy should become deployable only from:

- saved preset
- saved backtest result

This preserves:

- parameter audit
- backtest metrics
- source strategy id/version
- source run context
- future comparison between backtest and forward performance

Direct deployment from a raw strategy plugin should be blocked.

## Current API Shape

Implemented routes:

- `GET /api/deployments`
- `POST /api/deployments`
- `GET /api/deployments/{id}`
- `POST /api/deployments/{id}/pause`
- `POST /api/deployments/{id}/resume`
- `POST /api/deployments/{id}/archive`
- `GET /api/deployments/{id}/signals`

Planned evaluator route:

- `POST /api/deployments/{id}/evaluate-on-close`

The existing signal lifecycle and paper trading APIs can be reused underneath these routes.

## Implementation Order

Recommended next implementation sequence:

1. Done: add `strategy_deployments` backend model/store and routes.
2. Done: create deployments only from saved preset/backtest result.
3. Done: add Deployment panel in Live Signals.
4. Next: add 1-minute close forward evaluator using stored/latest candles.
5. Next: journal clean and blocked signals from deployment evaluation.
6. Next: add manual approval actions for deployment-generated signals.
7. Next: link approved signals to paper trades.
8. Later: after WebSocket stream is stable, feed new 1-minute closes automatically.
9. Later: add manual switch from `1m_close` to `tick` only after the user explicitly enables it.

## Non-Goals For First Version

- No fully automated paper trading.
- No real broker order placement.
- No per-tick strategy evaluation by default.
- No signals from unsaved/unreviewed strategy files.
- No wide option-chain scanning until ATM/OTM1/ITM1 is reliable.

## Success Criteria

The first version is successful when:

- A saved preset or backtest result can become a deployment.
- A deployment can evaluate completed 1-minute candles.
- Clean signals and blocked signals are both journaled.
- Signals show reasons, blockers, strategy params, and source artifact.
- User approval is required before paper deployment.
- Paper results can be reviewed by deployment, strategy, instrument, and date.
