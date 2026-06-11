# Strategy Deployments

Updated: 2026-06-12

This document defines how a backtested strategy moves into forward testing. Paper-mode deployments can auto-trade every clean signal (default for new deployments) so signal quality is auditable; shadow and recommendation modes keep a manual approval gate; nothing places broker orders.

## Status

All 12 Phase 4b slices are implemented, plus the auto-paper-trading extension (2026-06-11):

- Persisted Strategy Deployment objects in `strategy_deployments`.
- Source validation: only saved Presets or saved Backtest Runs.
- 1m_close evaluator with scheduler, time-of-day blocks, expiry-day cutoff, drift detection.
- Approval UI (Approve / Skip / Mark Blocked) with paper trade creation on approval.
- **Auto paper trading** on clean signals (`risk.auto_paper`) with option-premium entries, strategy-defined exits, and a per-minute live marker.
- Pre-flight data realism check at deployment creation.
- Quality warnings with required acknowledgment.
- Paper trade auto square-off at 15:00 IST every market day, with `allow_overnight` opt-out.
- Forward metrics aggregation per deployment (win-rate, avg P&L, profit factor, session-completeness-gated; low-sample results badged in Strategy Library).
- Per-deployment kill switches (`max_consecutive_losses`, `daily_loss_cutoff_pct`, `max_open_paper_trades`).
- Idempotency: unique partial index `(deployment_id, candle_ts)`.
- Strategy source SHA pinning + drift auto-pause.

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
6. Paper deployments with `auto_paper` on trade every clean signal automatically; everything else is manually approved.
7. Review forward profitability before trusting the strategy.

Direct deployment from a raw strategy plugin file is blocked.

## User Decisions (Locked)

- First confirmation mode is `1m_close`. Per-tick mode is a later manual switch only.
- **Amended 2026-06-10:** paper-mode deployments may auto-open a paper trade per clean signal (`risk.auto_paper`, default ON for new deployments) so signal outcomes are auditable without the user being present. Recommendation actions and anything beyond paper still require manual approval; broker orders are never placed.
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
| `risk` | `{ default_lots, allow_overnight, auto_paper, auto_paper_target_pts, auto_paper_stop_pts, auto_paper_target_pct, auto_paper_stop_pct, max_consecutive_losses, daily_loss_cutoff_pct, max_open_paper_trades }` — default_lots default 1, allow_overnight default false, auto_paper default true on new deployments (absent on pre-2026-06-11 deployments → old behavior), the `auto_paper_*` exit fields are optional premium fallbacks (points take precedence over percent), the last three are the kill switches (null = off) |
| `kill_switch_reason`, `kill_switch_inputs` | Stamped when a kill switch auto-pauses the deployment |
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

Allowed to create paper trades. With `risk.auto_paper` on (default for new deployments), every clean signal opens a paper trade automatically; with it off, trades are created on manual approval only.

### Recommendation

Shows a trade recommendation with full context. The user clicks Take or Skip. This is not broker order execution.

## Signal Lifecycle

States: `WATCHING → FORMING → CONFIRMED → TRIGGERED → ACTIVE → EXITED → AUDITED`. Plus side states: `SKIPPED`, `BLOCKED`.

The 1m_close evaluator produces:

- Clean signals at state `CONFIRMED` when strategy fires and pretrade allows.
- Blocked signals at state `AUDITED` with `blockers[]` populated when filters or guards reject.

Auto-paper transitions (paper mode, `auto_paper` on):

- The hook runs after the concurrency rule, re-reads the signal state, atomically claims it, opens the trade at the resolved option premium, and advances `CONFIRMED → TRIGGERED → ACTIVE` with `paper_trade_id` linked.
- No resolvable premium → no trade; the signal keeps state CONFIRMED with a journaled `paper_trade_error` and stays approvable.
- When the trade closes (stop/target/spot-mirror/square-off), the marker transitions the signal to `EXITED`.

Approval transitions:

- Approve: `CONFIRMED → TRIGGERED → ACTIVE` and (in paper mode) creates a paper trade at the resolved option premium. Premium unavailable → HTTP 409, signal stays CONFIRMED. Never duplicates an auto-created trade.
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

If multiple deployments fire on the same `(instrument, candle_ts)`, only the highest-score signal is kept as actionable; the rest are journaled with `tracked_for_pnl=false` and reason `concurrency_lower_score`. The auto-paper hook runs after this rule so a trade can never open for a signal that is demoted moments later.

## Auto Paper Trading (`backend/app/paper_auto.py`)

- **Entry price** (`resolve_option_entry_price`): live WS tick for the chosen contract, else a stored `options_1m` candle at most 5 minutes old, else refuse — NEVER the spot index level. A refusal journals `paper_trade_error` on the signal.
- **Risk levels** (`compute_auto_risk_levels`): the strategy's `risk_hints` (captured on every signal: `target_pct`/`stop_pct` as % of premium, `spot_target_pts`/`spot_stop_pts`, time stop) win over the deployment fallbacks. Deployment fallbacks resolve points first (`auto_paper_target_pts`/`auto_paper_stop_pts`, ₹ of premium), then percent (`auto_paper_target_pct`/`auto_paper_stop_pct`) — the same points-over-percent rule as the backtest's `option_levels` mode, so a premium-SL/target backtest can be replicated live. Long-premium semantics; stop floors at ₹0.05.
- **Spot-mirror exits**: built-in strategies define exits in SPOT POINTS — the live equivalent of the backtest's `spot_exit` mode. Trades carry direction-aware `spot_exit` levels (CE target above entry spot, PE below); the marker closes the option at its current premium when the underlying hits a level (`spot_target_hit`/`spot_stop_hit`).
- **Per-minute marker** (`mark_open_deployment_trades`): during market hours the server loop marks OPEN deployment trades to the latest option tick, fires premium stop/target and spot-mirror exits, and transitions the linked signal to EXITED. Writes are conditional on `status=OPEN` so a concurrent manual close wins; tickless trades are not touched.
- **Single-trade guarantee**: an atomic claim on the signal (`paper_trade_claim`) is shared by the auto hook and the approve route, so one signal can never produce two trades. A stale claim with no trade (crash inside the claim→insert window) blocks later auto-trades for that signal and is visible on the signal doc for audit.
- Kill switches govern auto trades unchanged; `max_open_paper_trades` blocks the signal, so no trade opens.

## Kill Switches (`backend/app/deployment_kill_switch.py`)

Configured under `deployment.risk`; paper deployments only; evaluated right after the drift check:

- `max_consecutive_losses` → **PAUSE** (hard circuit-breaker) when the trailing run of losing closed paper trades reaches the limit.
- `daily_loss_cutoff_pct` → **PAUSE** when today's net realized paper P&L as a % of capital deployed today drops to/below the (negative) cutoff.
- `max_open_paper_trades` → **BLOCK** (soft) new signals while this many paper trades are OPEN; self-clears as trades close; does not pause.

A pause stamps `kill_switch_reason` + `kill_switch_inputs` on the deployment; the deployment card shows the reason.

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
- `GET /api/deployments/metrics?include_ineligible=0|1` (forward metrics; `include_ineligible=1` adds low-sample deployments)
- `GET /api/deployments/{id}/metrics`

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
| 12 | Per-deployment kill switches | Done |
| — | Auto paper trading on clean signals + low-sample forward metrics (2026-06-11 extension) | Done |

## Non-Goals For Current Phase

- No real broker order placement — ever. (Automated **paper** trading shipped 2026-06-11 as an explicit user decision; it is paper-only and opt-out via `risk.auto_paper`.)
- No per-tick strategy evaluation by default.
- No signals from unsaved/unreviewed strategy files.
- No wide option-chain scanning until ATM/OTM1/ITM1 is reliable.

## Success Criteria

The deployment system is considered solid when:

- A saved Preset or Backtest Run can become a deployment with quality warnings surfaced.
- Pre-flight check catches data realism issues before the user creates a deployment.
- The 1m_close evaluator produces clean and blocked signals reliably during NSE market hours.
- Drift detection auto-pauses deployments when plugin source changes.
- Auto-paper (and approval) creates paper trades at real option premium with correct lot size and source provenance — one trade per signal, guaranteed.
- The per-minute marker fires premium and spot-mirror exits intraday; auto square-off at 15:00 IST closes the rest while honoring `allow_overnight`.
- Kill switches pause or block a misbehaving deployment without operator attention.
- Forward metrics per deployment surface profitability honestly with session completeness annotation; low-sample results are visible but clearly badged.
