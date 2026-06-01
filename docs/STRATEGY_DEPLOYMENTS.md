# Strategy Deployments

Updated: 2026-05-29

This document defines how a backtested strategy moves into forward testing, with a manual approval gate before any paper trade or recommendation is acted on.

## Status

Implemented (slices 1, 2, 3, 4, 5, 8, 9, 11):

- Persisted Strategy Deployment objects in `strategy_deployments`.
- Source validation: only saved Presets or saved Backtest Runs.
- 1m_close evaluator with scheduler, time-of-day blocks, expiry-day cutoff, drift detection.
- Approval UI (Approve / Skip / Mark Blocked) with auto-paper trade creation on approval.
- Pre-flight data realism check at deployment creation.
- Quality warnings with required acknowledgment.
- Paper trade auto square-off at 15:00 IST every market day, with `allow_overnight` opt-out.
- Idempotency: unique partial index `(deployment_id, candle_ts)`.
- Strategy source SHA pinning + drift auto-pause.

Not implemented yet (slices 10, 12):

- Forward metrics aggregation per deployment (win-rate, avg P&L, profit factor, annotated with session completeness).
- Per-deployment kill switches (`max_consecutive_losses`, `daily_loss_cutoff_pct`, `max_open_paper_trades`).

Out of scope (Phase 5+):

- Probability engine (Kaplan–Meier survival).
- Per-tick evaluation mode.
- Automatic broker order placement.

## Core Principle

A strategy must pass through an explicit deployment contract before it can produce live recommendations or paper trades.

The flow is:

1. Backtest a strategy in Backtest Lab.
2. Save or choose an approved Preset / Backtest Run.
3. Create a Strategy Deployment from that audited artifact.
4. Run the deployment in forward testing (`shadow`, `paper`, or `recommendation`).
5. Journal clean signals (CONFIRMED) and blocked signals (AUDITED) separately.
6. Manually approve every actionable signal.
7. Review forward profitability before trusting the strategy.

Direct deployment from a raw strategy plugin file is blocked.

## User Decisions (Locked)

- First confirmation mode is `1m_close`. Per-tick mode is a later manual switch only.
- Every generated signal requires manual approval before paper deployment or recommendation action.
- Default option moneyness: `ATM`. Configurable: `ATM`, `OTM1`, `ITM1`.
- Default DTE filter: `[0, 1, 2, 3, 4, 5, 6]`.
- Auto square-off at 15:00 IST every market day. `risk.allow_overnight=true` opts out per deployment.
- Time-of-day blocks: 09:15–09:25 (first 10 min) and 14:50–15:30 (last 30 min) IST.
- Expiry-day cutoff at 15:00 IST blocks new signals on the deployment instrument's expiry day. Looked up from `option_contracts.expiry_date`. Never weekday-hardcoded.
- Lot size always sourced from `option_contracts.lot_size` (Upstox-supplied).
- Walk-forward divergence warns; the user makes a conscious choice via the ack checkbox.
- Blocked signals are stored and clearly identifiable. Fewer cleaner signals are preferred over recording every weak setup.

## Deployment Document

`strategy_deployments` collection. Key fields:

| Field | Purpose |
|---|---|
| `id` | Stable deployment id |
| `name` | User-facing deployment name |
| `source_type` | `preset` or `backtest_run` |
| `source_id` | Preset name or backtest run id |
| `strategy_id` | Strategy plugin id |
| `strategy_version` | Plugin version at deployment time |
| `strategy_hash` | Optional hash of strategy code/config |
| `strategy_source_sha` | SHA-256 of plugin .py file (16 hex truncated). Pinned for drift detection |
| `params` | Frozen strategy parameters |
| `instrument` | `NIFTY`, `BANKNIFTY`, or `SENSEX` |
| `timeframe` | `1m` for first version |
| `confirmation_mode` | `1m_close` for first version |
| `option_policy` | `{ moneyness, dte_filter }` — moneyness in `atm/otm1/itm1`, dte_filter default `[0..6]` |
| `pretrade_profile` | Conservative / Balanced / Aggressive or custom |
| `mode` | `shadow`, `paper`, or `recommendation` |
| `risk` | `{ default_lots, allow_overnight, ... }` — default_lots default 1, allow_overnight default false |
| `status` | `ACTIVE`, `PAUSED`, or `ARCHIVED` |
| `quality_at_creation` | Snapshot of quality warnings at creation time |
| `acknowledged_warnings` | True if user accepted quality warnings |
| `last_evaluated_ts` | Idempotency cursor for the evaluator |
| `drift_reason`, `drift_pinned_sha`, `drift_current_sha`, `drift_detected_at` | Set on auto-pause when source SHA drifts |
| `created_at`, `updated_at` | Audit timestamps |

## Modes

### Shadow

Generates and journals signals but never creates paper trades. Use this first for every new deployment to confirm clean firing without hindsight.

### Paper

Allowed to create paper trades on approval. Approval is still manual.

### Recommendation

Shows a trade recommendation with full context. The user clicks Take or Skip. This is not broker order execution.

## Signal Lifecycle

States: `WATCHING → FORMING → CONFIRMED → TRIGGERED → ACTIVE → EXITED → AUDITED`. Plus side states: `SKIPPED`, `BLOCKED`.

The 1m_close evaluator produces:

- Clean signals at state `CONFIRMED` when strategy fires and pretrade allows.
- Blocked signals at state `AUDITED` with `blockers[]` populated when filters or guards reject.

Approval transitions:

- Approve: `CONFIRMED → TRIGGERED → ACTIVE` and (in paper mode) auto-creates a paper trade.
- Skip: `CONFIRMED → SKIPPED → AUDITED`.
- Mark Blocked: any non-AUDITED → `AUDITED` with the supplied note as a blocker.

## Audit Trail Invariants

Every signal must carry:

- `bar_ts` — the candle minute the strategy evaluated against
- `decision_ts` — wall-clock when the evaluator decided
- `strategy_id`, `strategy_version`, `strategy_hash` (over id+version+params)
- `pretrade_profile_name` + full `pretrade_settings_snapshot` resolved at signal time
- `regime` at the time of evaluation
- `option_contract` chosen with strike + side + instrument_key + lot_size
- `tracked_for_pnl` flag — false when `option_no_data` or `concurrency_lower_score` or `manual_block`
- `next_expiry_iso` for the deployment instrument
- All blockers as a list of human-readable strings

## Idempotency

The unique partial index `signals_deployment_bar_unique` over `(deployment_id, candle_ts)` (partial: `{deployment_id: {$exists: true, $type: "string"}}`) prevents duplicate journaling.

The evaluator catches `E11000` errors and treats them as `outcome="skipped"`, `reason="already_journaled"`, then advances `last_evaluated_ts` to avoid retry loops. Manual research signals (no `deployment_id`) are unaffected.

## Concurrency Rule

If multiple deployments fire on the same `(instrument, candle_ts)`, only the highest-score signal is kept as actionable; the rest are journaled with `tracked_for_pnl=false` and reason `concurrency_lower_score`.

## Drift Detection

`strategy_source_sha` is pinned at deployment creation. On every evaluator tick, the pinned SHA is compared to the current SHA of the plugin's .py file. On mismatch:

- Deployment auto-pauses with `status="PAUSED"`.
- Audit fields populated: `drift_reason="strategy_source_drift"`, `drift_pinned_sha`, `drift_current_sha`, `drift_detected_at`.
- Pre-slice-8 deployments without a pinned SHA continue to operate (legacy compat).

## Pre-flight And Quality Gates

At deployment creation:

1. `GET /api/deployments/preflight?instrument=...` returns spot coverage (last 30 trading days), upcoming expiries, active vs expired contracts, and Upstox token state. Frontend `PreflightBadge` surfaces this.
2. `GET /api/deployments/quality?source_type=...&source_id=...` returns 5 checks (missing walk-forward, divergence, low trade count, weak Sharpe, large drawdown).
3. If any quality warnings, `POST /api/deployments` requires `acknowledged_warnings=true`. Otherwise `400 acknowledgment_required`.
4. Quality snapshot is stored on the deployment as `quality_at_creation` plus the ack flag.

## API

Implemented routes:

- `GET /api/deployments`
- `POST /api/deployments`
- `GET /api/deployments/{id}`
- `POST /api/deployments/{id}/pause`
- `POST /api/deployments/{id}/resume`
- `POST /api/deployments/{id}/archive`
- `GET /api/deployments/{id}/signals`
- `POST /api/deployments/{id}/evaluate-on-close`
- `POST /api/deployments/evaluate-active`
- `GET /api/deployments/preflight?instrument=...`
- `GET /api/deployments/quality?source_type=...&source_id=...`

Approval routes:

- `POST /api/signals/{id}/approve`
- `POST /api/signals/{id}/skip`
- `POST /api/signals/{id}/mark-blocked`

## Implementation Status

| Slice | Description | Status |
|---|---|---|
| 1 | 1m_close evaluator + scheduler | Done |
| 2 | Approval UI (Approve / Skip / Mark Blocked) | Done |
| 3 | Auto square-off + expiry-day cutoff + dte_filter | Done |
| 4 | Paper trade auto-creation on approval | Done |
| 5 | Pre-flight data realism panel + active-expiry contract picker fix | Done |
| 6 | Data Hygiene + NSE calendar | Done |
| 6.5 | Live tick → 1m OHLC roller | Done |
| 7 | Slippage + post-hoc volatility detector | Done |
| 8 | Strategy source SHA pinning + drift detection | Done |
| 9 | Quality warnings + ack checkbox | Done |
| 10 | Forward metrics aggregation per deployment | Done |
| 11 | Idempotency partial unique index | Done |
| 12 | Per-deployment kill switches | Pending |

## Non-Goals For Current Phase

- No fully automated paper trading.
- No real broker order placement.
- No per-tick strategy evaluation by default.
- No signals from unsaved/unreviewed strategy files.
- No wide option-chain scanning until ATM/OTM1/ITM1 is reliable.

## Success Criteria

The deployment system is considered solid when:

- A saved Preset or Backtest Run can become a deployment with quality warnings surfaced.
- Pre-flight check catches data realism issues before the user creates a deployment.
- The 1m_close evaluator produces clean and blocked signals reliably during NSE market hours.
- Drift detection auto-pauses deployments when plugin source changes.
- Approval auto-creates paper trades with correct lot size and source provenance.
- Auto square-off at 15:00 IST closes intraday positions while honoring `allow_overnight`.
- Forward metrics per deployment surface profitability honestly with session completeness annotation.
