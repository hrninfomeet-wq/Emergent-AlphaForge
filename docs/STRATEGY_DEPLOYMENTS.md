# Strategy Deployments

The deployment model: how an **audited research artifact** becomes a running,
forward-testing (and optionally real-money) strategy — and every safety gate,
kill switch, and chokepoint on the way.

> **Cross-links:** the live-trading safety model in
> [DEVELOPER_GUIDE.md](./DEVELOPER_GUIDE.md); the manual real-money go-live drill
> in [live-readback-checklist.md](./live-readback-checklist.md); the market-session
> validation runbook in [LIVE_VALIDATION_PLAN_2026-08.md](./LIVE_VALIDATION_PLAN_2026-08.md);
> where the 2026-09-26..30 live-deployment code lives in [HANDOFF.md](./HANDOFF.md) §2.6;
> the technical module map + the L0–L3 gate chain in [ARCHITECTURE.md](./ARCHITECTURE.md);
> the HTTP routes in [API_REFERENCE.md](./API_REFERENCE.md).
>
> **Checked against the code on 2026-09-30.** None of the live controls added
> 2026-09-26..30 (tighten caps, flatten & hold, governor view, restart attribution,
> transmit fence) has run in a market session yet.

---

## Core principle

A strategy cannot be deployed from an unregistered raw file. A **deployment is
always an immutable snapshot** of a loaded, 1-minute-compatible **Strategy
Library** entry, a saved **Preset**, or a saved **Backtest Run** (`source_type` ∈
`{strategy, preset, backtest_run}`, `build_deployment_doc` in
`strategy_deployments.py`). The build fails if the source is missing a
`strategy_id`, supported instrument, 1-minute compatibility, or id/name.

The pipeline:

1. Select a loaded Strategy Library entry directly, or backtest it in the
   **Backtest Lab** (spot + paired-option) and save a Preset/Backtest Run.
2. Create a **deployment** from that source — its selected parameters and source
   SHA are frozen; quality warnings require acknowledgment but do not veto it.
3. The 1-minute-close **evaluator** journals signals every market minute.
4. Depending on the deployment's **mode**, a clean signal opens a
   **paper trade**, a **real broker order** (`mode == "live"`), or **nothing**
   (signal-only).
5. Review honest **forward metrics** before trusting — or going live with — the
   strategy.

---

## The deployment document

`strategy_deployments` collection, built by `build_deployment_doc`. Key fields:

| Field | Purpose |
|---|---|
| `id` / `name` | Stable id + user-facing name |
| `source_type` / `source_id` | `strategy` \| `preset` \| `backtest_run` + strategy id / preset name / run id |
| `source_snapshot` | Frozen name/metrics of the source at creation |
| `strategy_id` / `strategy_version` / `strategy_hash` | The plugin + audit hash |
| `strategy_source_sha` | SHA of the plugin `.py` at creation — pinned for **drift detection** |
| `params` | Frozen strategy parameters (from the source's applied params) |
| `premium_trigger` | Optional premium-trigger config block for a premium-native strategy; when present it is merged OVER `params` (`strategy_deployments.effective_premium_params`) |
| `instrument` | `NIFTY` \| `BANKNIFTY` \| `SENSEX` |
| `timeframe` / `confirmation_mode` | `1m` / `1m_close` (`tick` reserved, not yet evaluated) |
| `option_policy` | `{ moneyness: [atm/otm1/itm1], expiry_policy: "next_available", dte_filter }` — `dte_filter` default `[0..6]` |
| `pretrade_profile` | Conservative / Balanced / Aggressive (or custom) |
| `mode` | **`signal_only`**, **`paper`**, or **`live`** (real-money; reachable only via `POST /live/enable` — see below) |
| `risk` | The whole risk block: `allow_overnight`, `default_lots`, `auto_paper`, the `auto_paper_*` premium exits, `exit_controls`, `friction`, the pinned `sizing` replay, the **kill-switch** fields, `daily_caps`, the paper caps (`lots_override`, `max_concurrent`, `capital`; `PUT /deployments/{id}/paper-caps`, paper mode only), the optional entry window (`trade_window_start` / `trade_window_end`), and the nested **`live`** sub-object — a pure CONFIG doc (caps, catastrophe band, `paused` hold, `evidence_consent`, `last_caps_change`); it carries no authorization field, `mode` alone authorizes |
| `manual_approval_required` | Always `false` (the legacy approval gate was retired) |
| `status` | `ACTIVE` \| `PAUSED` \| `ARCHIVED` |
| `quality_at_creation` / `acknowledged_warnings` | Quality snapshot + the user's ack |
| `last_evaluated_ts` | Idempotency cursor for the evaluator (epoch-ms of the last evaluated bar) |
| `drift_*` | `drift_reason` / `drift_pinned_sha` / `drift_current_sha` / `drift_detected_at` on an auto-pause |
| `kill_switch_reason` / `kill_switch` / `kill_switch_inputs` | Stamped when a kill switch auto-pauses |
| `created_at` / `updated_at` / `audit` | Provenance |

### Sizing replay

If the source run carries a position-sizing policy, `deployment_sizing_from_source`
pins it to `risk.sizing` (`{sizing_config, lots, source_id}`). Live paper trades
then **replay the source run's sizing** (`resolve_deployment_lots`): lot COUNT is
sized from the pinned config while lot SIZE always comes from the live contract,
so rupee-risk is held constant across instruments. No pin → `risk.default_lots`.

---

## Modes (the real enum)

`ALLOWED_MODES = {"signal_only", "paper", "live"}` — but a deployment can never
be **created** in live mode: `CREATABLE_MODES = {"signal_only", "paper"}` is the
set `build_deployment_doc` accepts at creation time. `mode == "live"` is reached
only by calling `POST /deployments/{id}/live/enable` on an existing deployment
(v0.56.0 — no per-session ARM ceremony any more; see
[Live path](#live-path-real-money) below). **The old `shadow` / `recommendation`
/ manual-approval framing is gone.** Legacy stored values map on read:
`shadow → signal_only`, `recommendation → signal_only` (`LEGACY_MODE_MAP`). The
evaluator only ever opens a trade when `mode` is `"paper"` or `"live"`.

### `signal_only`
The evaluator journals every clean and blocked signal for audit, but **never
opens a trade**. Use this first for a new deployment to confirm it fires cleanly
without hindsight.

### `paper`
Same journaling, plus — when `risk.auto_paper` is true (the wizard default) —
**every clean CONFIRMED signal auto-opens a paper trade** at the real option
premium (`paper_auto.auto_paper_trade_for_signal`). There is no manual
Approve/Skip step; signal outcomes are auditable without an operator present.

### `live` (real-money — v0.56.0: deploying live IS the authorization)
`mode == "live"` **is itself the authorization** — there is no separate
per-session arm record and no arm expiry. A clean CONFIRMED signal from a live
deployment routes to a **real order** through the executor chokepoint (and
suppresses the paper path for that signal) whenever the broker is connected,
no operator hold (`risk.live.paused`) is set, and it is before the daily 15:00
IST new-entry cutoff, within the caps; `LIVE_AUTOPLACE_ARMED` remains the one
env master switch on top. `mode` cannot be set to `"live"` at
creation or via an ordinary update — the only writer is
`POST /deployments/{id}/live/enable`, which runs the full preflight chain and
requires risk caps. See [Live path](#live-path-real-money).

---

## Quality gate + acknowledgment

At `POST /api/deployments`, `evaluate_source_quality` (`deployment_quality.py`)
inspects the source plus **out-of-sample evidence** gathered by
`_gather_deployment_evidence`: the latest honest walk-forward
(efficiency / consistency / option-rupee OOS), exact-params option-rupee evidence
(re-rank job or option backtest run), and the optimizer trial count behind the
params (the selection-bias signal for a deflated Sharpe). Checks include missing
walk-forward, walk-forward divergence, low trade count, weak Sharpe, large
drawdown, selection bias, option-rupee-OOS, incomplete/failed source-run status,
and failed optimizer guardrail/survival screens.

If **any** warning is present, the create call returns
**`400 acknowledgment_required`** unless the request carries
`acknowledged_warnings=true`. On success the full `quality` snapshot + the ack
flag are stored on the deployment. The evidence gate **warns, never vetoes a
technically executable source** — the user makes a conscious choice. Technical
capability is still enforced centrally: the strategy must be registered, support
the selected instrument and the live 1-minute evaluator, use only schema-valid,
range-valid finite params, and not be retired. Non-finite execution values
(including trailing/breakeven controls, daily caps, friction, capital and
premium-trigger fields) are rejected rather than stored. Paper creation and live
enable remain independent decisions; live still requires the separate user act
and every operational/capital preflight.

Companion informational routes (never block): `GET /deployments/quality`
(preview at custom thresholds), `GET /deployments/readiness` (was the honest
validation done?), and `GET /deployments/preflight` (data-realism: spot coverage,
upcoming expiries, active vs expired contracts, Upstox token state).

Retired strategies cannot be deployed / resumed / re-pinned (`is_retired` → 409).

---

## The 1-minute-close evaluator

`deployment_evaluator.evaluate_active_deployments` runs every ACTIVE deployment
independently each minute (scheduler + `POST /deployments/evaluate-active`).
Per deployment (`evaluate_deployment_on_close`), in order:

1. **Drift check** — if `strategy_source_sha` was pinned and the plugin file's
   current SHA no longer matches, **auto-pause** (`status=PAUSED`,
   `drift_reason="strategy_source_drift"`). A deployment that was NEVER pinned is not exempt: it
   auto-pauses with `drift_reason="strategy_source_never_pinned"` (fail-closed since 2026-08-20;
   `deployment_evaluator.py`).
2. **Kill switches** (`check_deployment_kill_switches`, paper only) — a pause
   switch auto-pauses; the block switch adds a blocker to this bar.
3. Load the latest closed 1-minute candles — `max(200, live_lookback_bars)`
   (`StrategyBase.live_lookback_bars`, default 200), capped at 1,000
   (`LIVE_LOOKBACK_MAX`; a strategy declaring more is SKIPPED with
   `live_window_exceeds_cap`, never silently truncated). Fewer than 50 bars skips
   the bar (`insufficient_candles`); a window shorter than the declared lookback is
   still evaluated but carries a `degraded_window` note. Required-data columns join
   onto the raw window, then enrich with indicators + regime + features, and
   `strategy.evaluate()` the freshest bar. (Session-anchored indicators must not
   anchor to the window start — see the HANDOFF.md traps.)
4. If direction is `CE`/`PE`, resolve the **pretrade filter**, the **option
   contract** (ACTIVE-expiry only — never an expired strike), and the guards:
   the entry window (`entry_window.resolve_entry_window`: default **09:25–14:50
   IST**, overridable per deployment via `risk.trade_window_start/end`, never later
   than 15:00), **expiry-day 15:00 cutoff** (from `option_contracts.expiry_date`,
   never weekday-hardcoded), and recent-option-data.
5. **Journal one signal**:
   - clean → `CONFIRMED` (`blocked=false`);
   - blocked → `AUDITED` with a human-readable `blockers[]`.
   The signal's bar is the top-level **`candle_ts`** (also `context.candle.ts`).
   The full audit record is stored under `context`: `bar_ts` (the same minute —
   it exists only there, never at the top level), `decision_ts`, strategy hash +
   SHA, pretrade snapshot, regime, `next_expiry_iso`, `tracked_for_pnl`. The chosen
   contract (`option_contract`) and `risk_hints` (the strategy's own exit
   definition) are top-level. Read a signal's bar through
   `signal_lifecycle.signal_bar_ms`.

Deployments are intentionally **independent** (2026-06-12): two strategies firing
on the same instrument/minute both journal and both may trade — enabling honest
head-to-head comparison. The old highest-score concurrency demotion was removed;
exposure is governed per deployment (paper: `max_open_paper_trades` and the paper
caps; live: the live caps) and, for live, by the account-level caps.

### Idempotency
`last_evaluated_ts` gates re-evaluation of the same bar, and a unique partial
index over `(deployment_id, candle_ts)` makes a duplicate insert a silent skip.

---

## Sink routing (per clean signal)

After journaling, each clean `CONFIRMED` signal is re-read (guarding against
concurrent mutation) and routed to **exactly one** sink (`if/elif`, never both;
the shared atomic `paper_trade_claim` also enforces one-trade-per-signal):

- **Live-allowed** (`auto_live_enabled` → `live.mode.is_deployment_live_allowed`:
  `mode == "live"`, no `risk.live.paused` hold, broker connected, before the
  15:00 IST entry cutoff) → **`auto_live`** (a REAL order, subject to
  `LIVE_AUTOPLACE_ARMED`); the paper path is suppressed for that signal.
- **Else, `auto_paper` on** → open a paper trade.
- Else (signal-only, or auto_paper off) → nothing; the signal stays journaled.

### Paper sink (`paper_auto.py`)
- **Entry price** (`resolve_option_entry_price`): live WS tick for the contract,
  else a stored `options_1m` candle ≤5 min old, else **refuse** (journal
  `paper_trade_error`) — **never** the spot index level.
- **Premium exits** (`compute_auto_risk_levels`): strategy `risk_hints`
  (`target_pct`/`stop_pct`) win over deployment fallbacks; fallbacks resolve
  **points before percent** (`auto_paper_*_pts` then `auto_paper_*_pct`) — the
  same rule as the backtest's `option_levels` mode, so a premium-SL/target
  backtest can be replicated live. Long-premium semantics; stop floors at ₹0.05.
- **Spot-mirror exits** (`compute_spot_exit_levels`): built-in strategies define
  exits in SPOT POINTS — the live equivalent of the backtest's `spot_exit` mode.
- **Execution realism** (`risk.friction`): when opted in, the entry is slipped and
  charged with the SAME model the backtest used so forward P&L doesn't overstate.
- **Marker** (`mark_open_deployment_trades`, driven by `LiveExitMonitor`: every
  ~1.5 s and woken by ticks on held contracts, at most every 0.2 s): during the
  options session, marks OPEN paper trades to the latest option tick, fires
  premium stop/target, trailing/breakeven ratchet, time-stop, per-deployment
  `exit_time` (`risk_hints.square_at_ist`) and spot-mirror exits. Writes are
  conditional on `status=OPEN`; a clock exit with a stale tick closes on the last
  known premium and labels it. Every close moves the linked signal
  `ACTIVE → EXITED` (`signal_lifecycle.exit_linked_signal`).
- **15:00 IST square-off** (`paper_squareoff.square_off_open_paper_trades`) is
  the backstop when no exit fired; `risk.allow_overnight` opts out.
- **Single-trade guarantee**: an atomic `paper_trade_claim` on the signal is
  shared by both sinks and the (retired) approve route — one signal, one trade.

---

## Kill switches (`deployment_kill_switch.py`)

Two governors, both **paper-mode only** (a signal-only deployment has no realized
P&L to act on).

**Hard circuit-breakers** (`risk`, checked by the evaluator before it fires):

- `max_consecutive_losses` → **PAUSE** when the trailing run of losing closed
  paper trades reaches the limit.
- `daily_loss_cutoff_pct` → **PAUSE** when today's net realized P&L, as a % of
  capital deployed today, drops to/below the (negative) cutoff.

Both stamp `kill_switch_reason` / `kill_switch` / `kill_switch_inputs` and set
`status=PAUSED`; the deployment card shows the reason and stays paused until the
user resumes it.

**Soft blocks** (self-clear as trades close, never pause):

- `max_open_paper_trades` → **BLOCK** new signals while that many paper trades are
  OPEN (adds a blocker to the bar's signal; the deployment stays ACTIVE).
- **Soft daily governor** (`check_soft_daily_governor` over `risk.daily_caps`) →
  **HALT new entries** for the session when today's realized cum-extremum trips
  the loss / target cap or the entry count reaches `max_trades`. Stateless
  (auto-resets next session); blocks entries only.

A live deployment has none of these; its day-level halt is the mandatory live
`daily_loss_cap` enforced by the caps governor (below).

---

## Live path (real-money)

Real-money auto-placing is **off by default and heavily gated**. Enabling it
(v0.56.0: `POST /live/enable`, replacing the old per-session ARM) is a
deliberate, one-time-per-deployment act — it persists across sessions until
explicitly disabled or stopped — with the env master gate (`LIVE_AUTOPLACE_ARMED`)
still on top of it.

### Enable / disable / stop (`routers/deployments.py`)

`POST /deployments/{id}/live/enable` sets `mode="live"` and writes `risk.live`
**only** if every guard passes, in this order: `confirm` is the literal boolean
`True` (StrictBool); cap values valid (`lots`, `max_lots_per_day`,
`max_concurrent` all ≥ 1; `daily_loss_cap` **required**, positive and finite;
catastrophe stop/target % positive when given); deployment exists (404) and is
`ACTIVE`; persisted params still competent; strategy not retired (409); not
drift-paused; **broker ready** (credentials configured, connected, today's
post-06:00 session not expired, a static IP configured); `LiveEngine.can_trade()`
is True (else 400 naming the Reset control on the safety-latch banner); the
**data-integrity gate** passes (`check_live_data_gate` for the instrument; else
**409 `incomplete_market_data`** — fail-closed, scoped to this one transition,
never consulted by an exit); `lots` and `max_concurrent` fit the account
ceilings (400 `account_lot_ceiling_exceeded` / `account_position_ceiling_exceeded`).
The final write is a compare-and-swap on `updated_at` + `status: ACTIVE`: if a
Stop / Disable / pause / archive landed during the preflight it returns
**409 `deployment_changed_during_enable`** and does not go live.

Forward evidence is presented before this activation. If
`promotion_allowed=false` (including unavailable validation), the route returns
`409 explicit_unvalidated_live_consent_required` unless the operator separately
sets strict `accept_unvalidated_live=true`. The failed checks, evidence snapshot,
user, and timestamp are persisted under `risk.live.evidence_consent`. That
consent overrides only the research veto; none of the operational, broker,
capital, order-safety, idempotency, or protection gates above are bypassed.
Unlike the old arm, **enabling does not expire** — there is no `armed_until`
and no "cannot enable after 15:00" check: enabling in the evening simply means
the deployment goes live at the next session's open (the daily 15:00 IST cutoff
still applies to every individual entry, every day). The accepted account
ceilings are snapshotted at activation, and the executor re-applies the current
ceiling at order time.

- `POST /deployments/{id}/live/pause` — the **reversible HOLD** (2026-09-08).
  Sets `risk.live.paused = true`, which `is_deployment_live_allowed` checks. This
  gates NEW ENTRIES ONLY: `mode` stays `"live"` and `status` stays `ACTIVE`, so
  the deployment keeps managing its open book (guard, exit monitor, and the
  broker OCO if one rests). **Paused is not flat.** Refused (409
  `deployment_not_live`) on a paper deployment. It writes only its own leaves
  (`risk.live.paused`, `risk.live.paused_at`), so a concurrent caps tighten is
  never reverted.
- `POST /deployments/{id}/live/resume` — lift the hold. Not a re-authorization:
  `mode` never left `"live"`, so no consent is re-collected. Compare-and-swap
  guarded on `updated_at` (409 `deployment_changed_during_resume`: the permissive
  direction must lose a race against a concurrent Stop); `pause` is deliberately
  unconditional, matching the rule in `runtime.py::_set_deployment_status`.
- `POST /deployments/{id}/live/caps` — **tighten-only** (2026-09-29). Lowers any
  of `lots`, `max_lots_per_day`, `max_concurrent`, `daily_loss_cap` without
  leaving live. Any increase — even one field of a request that lowers others —
  is refused whole (**409 `caps_loosening_refused`**, nothing written); to raise a
  cap, Disable then re-Enable. Uses `/live/enable`'s own value and account-ceiling
  validators; does NOT need the broker, the data gate or forward validation.
  Only an ACTIVE live deployment qualifies (409 `deployment_not_live`); stored
  caps the governor already rejects give 409 `stored_caps_invalid`. The write is
  a CAS on `updated_at` + ACTIVE + live (409
  `deployment_changed_during_caps_update`), records `risk.live.last_caps_change`,
  applies to signals evaluated after it, and returns an `already_at_<cap>`
  advisory when the new cap is already reached.
- `POST /deployments/{id}/live/flatten` — square THIS deployment's open live
  positions and **stay live** (mode and status untouched). Body `{"hold": true}`
  first sets the live HOLD so the next signal cannot re-enter. Refuses out of
  market hours (**409 `market_closed`**, nothing sent); skips a contract shared
  with another registry entry (`skipped_shared_tsyms`); lists OPEN journal rows
  the guard does not hold for this deployment as `unguarded_open_tsyms` (never
  squared automatically); `fill_confirmed` is always `false` — the guard
  finalizes after consecutive flat reads.
- `POST /deployments/{id}/live/disable` — revert `mode` to `"paper"`; stops new
  live placing. Does **not** flatten open positions — they stay registered with
  the guard and keep their stop/target/trail (and a broker OCO if one rests). The
  live config (`risk.live` caps + catastrophe band) is retained so re-enabling
  doesn't require re-entering it, and any `paused` hold is cleared so a stale
  one cannot be inherited by a later `/live/enable`.

> **Why the hold is a separate flag and not `status`.** Every other stop path
> routes through `_set_deployment_status`, which demotes `mode: live -> paper` on
> ANY transition out of `ACTIVE` (the v0.56.0 invariant). That is deliberate — it
> stops resume / re-pin / un-retire from silently re-authorizing real money — but
> it means a `status`-based pause costs the operator their live authorization and
> the full caps + consent ceremony to undo. Gating in the authorization predicate
> instead leaves the invariant untouched.
- `POST /deployments/{id}/live/stop` — flatten THIS deployment's open live
  positions (margin-safe square path), revert `mode` to `"paper"`, **and** set
  `status="PAUSED"` — reverting mode alone would leave an ACTIVE deployment
  free to re-enter on the very next confirmed signal; `status=PAUSED` is the
  actual halt (`evaluate_active_deployments` only evaluates ACTIVE deployments).
- `POST /deployments/{id}/stop` (the generic Stop) also flattens a live
  deployment's real positions through the same path before pausing it.
- `POST /deployments/stop-all` — square all paper, flatten + revert-to-paper every
  `mode == "live"` deployment (listed in `disarmed_live_deployment_ids`, with
  per-deployment `live_exit_reports`), then pause every ACTIVE deployment
  (selector `{"mode": "live"}` — **not** the old `{"risk.live.armed": True}`,
  which would now silently match zero documents and turn Stop-ALL into a
  no-op for live positions). The account kill switch
  (`POST /live-broker/kill-switch`) separately trips the latch, halts the engine
  and sets every live deployment to paper + PAUSED
  (`stop_all.disarmed_deployment_ids`) before flattening.
- Every flatten reports separate states — `exit_submitted_tsyms`,
  `already_flat_tsyms`, `cancel_confirmed_tsyms`,
  `flat_confirmation_pending_tsyms`, `deferred_tsyms`, `failed_tsyms` — because
  **exit submitted is not flat** (see `live-readback-checklist.md`).
- `GET /deployments/{id}/live/status` (and the batched
  `GET /deployments/live/status?ids=`, at most 200 ids, unknown ids omitted)
  reports `live_mode`, `live_paused`, `caps`, `today` counters, `open_positions`
  (from the guard registry), `last_entry` (the latest signal's
  `live_trade_error` / `live_intended`), `autoplace_armed`, `entry_window`
  (`effective_end` = the earlier of the deployment's window end and the 15:00
  cutoff), and **`governor`** — the governor's own read-only answer to "why
  can't this trade now?" (`describe_live_caps`). The UI renders it rather than
  re-deriving enforcement. `guard_armed` is kept for compatibility and is always
  `true`; `armed` mirrors `live_mode`.
- `GET /deployments/{id}/timeline?date=YYYY-MM-DD` — read-only, never 500s: one
  ascending list of that IST day's signals, refusals, entries, exits, orders and
  hold/disable/caps events, plus `gaps` naming what is not recorded.
- `GET /live-broker/session-clock` — the server trading clock
  (`live/session_clock.describe_session`): phase, entry cutoff, EOD square and
  next event as absolute epoch-ms, so browser countdowns cannot drift.

### The one remaining env gate
`mode == "live"` authorizes the deployment — there is no `risk.live.armed`
field any more. Transmit of a real **entry** is still a separate env-level
concern:
- **`LIVE_AUTOPLACE_ARMED`** — the executor's transmit boundary. Unless set, a
  live deployment is **offline-first**: the executor validates and returns
  `dry_run=True` / `would_send`, transmitting **nothing** (journaled as
  `live_intended` on the signal).

**`LIVE_GUARD_ARMED` is REMOVED.** The software guard's squares (and its
Layer-2 widening re-price) now always transmit — there is no exit-side env
gate at all.

### Live sink (`auto_live.py`) — a structural clone of the paper sink
Same claim / lifecycle / journaling as paper; the one difference is the success
side-effect is a **real order** through the executor chokepoint, journaled to
`live_trades`. Stricter than paper on two points:

- **Entry ref_ltp must be a FRESH live OPTION tick** (`resolve_premium`,
  `fresh is True`); a stale tick / last candle / absent tick is **refused** (a
  stale ref would mis-band the LMT). Never spot, never a stale candle.
- **Never unprotected**: if no premium stop is configured,
  `resolve_live_exit_plan` seeds a **50% catastrophe premium stop** so the
  software guard can always register the position.

Lots are the user's fixed `risk.live.lots` clamped to the account ceiling
(`resolve_capped_lots`) — **not** the sizing-replay path. Paper replays the pinned
sizing, live uses flat `risk.live.lots`: a deliberate divergence, so paper rupee
figures are produced at a lot count live does not use.

### Live caps governor (`live_deploy_governor.py`)
Before each live entry `auto_live` checks the **account** caps first
(`check_account_caps`: account `max_open_positions` / `daily_loss_limit` across
every deployment; a loss breach trips the engine latch; unreadable exposure
refuses), then the **per-deployment** caps (`check_live_caps`, first match wins):

| # | Verdict | Effect |
|---|---|---|
| 0 | `live_caps_missing` / `invalid_daily_loss_cap` | refuse + **PAUSE and demote to paper** |
| 1 | `exposure_unknown` (a mark missing or stale while a loss cap is set) | refuse |
| 1 | `daily_loss_cap` (realized + open-unrealized today) | refuse + **PAUSE and demote to paper** |
| 2 | `lots_unmeasurable` / `max_lots_per_day` | refuse |
| 3 | `max_concurrent` | refuse |

The checks are pure steps — `precheck_live_caps → measure_exposure →
decide_live_caps` (and `decide_account_caps`) — shared by enforcement and the
read-only `describe_live_caps`, so the screen shows the governor's own numbers.
Unknown exposure is `null`, never 0. A pause verdict pauses AND demotes to paper
in one write, so going live again needs a fresh `/live/enable` (a plain Resume
cannot re-authorize real money). The **risk supervisor**
(`runtime._risk_supervisor_loop`, every 10 s, 09:15–15:30 IST) re-evaluates the
same caps for ACTIVE live deployments, halts the engine on an account breach and
pauses a deployment past
its loss cap; it never squares. (Its per-deployment pause was a `TypeError` from
2026-08-06 to 2026-09-29 and never fired; fixed in `35a33fe`.) Every refusal is
written to the signal's `live_trade_error`.

### The executor chokepoint (`live/executor.place_deployed_order`)
The **single real-order site** for deployed orders. Gates, in order: 0 long-only
(`side="B"`); 1 authorization (`allow_fn`); 2 a fresh server-side dry-run;
3 **margin must cover the FULL `capped_lots × lot_size`** (broker-authoritative
`order_margin`); 4 all verdicts pass; 5 lot-cap defense-in-depth; 6
`engine.can_trade()`; then the **transmit boundary** (`LIVE_AUTOPLACE_ARMED`
unset → returns `dry_run` / `would_send`, transmits nothing); 7 the **transmit
fence** (`recheck_fn` = `auto_live._recheck_authorization`: re-reads the
deployment and the clock after the broker round-trips — status must still be
ACTIVE, `is_deployment_live_allowed` must still pass, fresh `lots` must not be
below the size already built, and `check_live_caps` must pass on the fresh
doc; refusals read `stale_authorization:<reason>`, e.g.
`stale_authorization:caps_tightened:lots 2->1`); 8 the SEBI rate throttle — only
then does `_transmit_and_arm` place the order and arm protection.

### Catastrophe backstop + software guard
> **⚠ OFF BY DEFAULT SINCE 2026-09-03.** The resting broker OCO does not rest: its
> stop leg reached the ORDER BOOK one second after the fill and was LPP-rejected,
> because the `oivariable` x/y -> leg pairing is SWAPPED (leg1/`x` is the ABOVE
> slot). It is now opt-in via `LIVE_BROKER_OCO_ENABLED` (default `0`). **With it
> off there is NO PC-down net** — the in-process software guard is the only
> protection and it runs only while the app runs. Re-enable only after the pairing
> readback in `docs/live-readback-checklist.md` §E1.

On a real fill the position is registered with the **`LivePositionGuard`**
(`live_position_guard.py`). It decides on a fresh Upstox premium tick (≤ 2 s old,
passes at most every 0.2 s) and falls back to the broker's `lp` from its ~1.5 s
position-book read; it squares via the margin-safe cancel-all-then-close path
when a stop/target/trailing/spot-mirror/`exit_time` breaches, and keeps the entry
registered until the broker book confirms flat. Only when
`LIVE_BROKER_OCO_ENABLED=1` does `arm` also try a **resting broker OCO** (NRML,
the PC-down net), journaling `oco_al_id`, or `oco_error="no_broker_backstop"` if
it could not rest (the risk supervisor re-checks the GTT book and flags an OCO
that stopped resting).

Why software, not a resting SL: a resting SELL stop on a long option needs
naked-short SPAN margin an option-buyer account lacks, so a broker SL is rejected
every time (proven live 2026-06-24). A guard square is place-and-track: on a
breach the entry is marked `squaring` (the flag stops a re-issue) and stays
registered until the broker book confirms flat; only then does `_finalize_flat`
journal the close and drop it. A failed square retries each cycle up to
`max_square_retries`, then escalates and stops re-issuing (`square_stopped`) while
the entry STAYS registered — the 15:00 IST EOD square (which explicitly bypasses
`square_stopped`) and, only if enabled, the broker OCO remain the backstops. (A
prior manual-position-only 10-minute auto-square timer was **removed** — the EOD
square is now the sole time-based backstop for a manual position; deployed
strategies exit on their own rules.)

**After a restart** live recovery re-attaches a still-open position to its
deployment only when that is provable (`live/ownership.resolve_rehydrate_attribution`
/ `attribution_for`: every non-CLOSED journal row for the tsym names the same
deployment and the held qty does not exceed what they ordered; with exactly one row
the entry is keyed by that row's `norenordno`). It starts `seen_filled` at the
default catastrophe stop; that recovered stop never arms a premium-momentum lazy
leg. An unprovable position is guarded but unattributed and shows up in
`/live/flatten`'s `unguarded_open_tsyms`. The boot reconcile attributes an exit
price only on proof from today's trade book. A row entered on an earlier IST day
that closes without such proof (the book shows it flat, or two confirmed-empty
book reads 1.5 s apart) is stamped `exit_day_unknown` and excluded from
`daily_realized_summary`; a same-day OPEN row next to an empty book stays OPEN
unless its order ended unfilled (`never_filled`). See HANDOFF.md §2.6.

---

## `premium_momentum`: a lock-driven deployment variant

`premium_momentum` deploys through the identical pipeline above (Preset/Run → deployment → quality
gate → evaluator → paper/live sink), but the **evaluator step is different**. Instead of calling
the strategy's `evaluate()` (the plugin's is inert — it registers schema/metadata only),
`deployment_evaluator.py` routes every **premium-native** strategy
(`premium_trigger_dispatch.is_premium_trigger_strategy`, judged from the strategy's declared
defaults, not its id) to `premium_momentum_live.evaluate_premium_momentum_bar` per bar. The config
is the deployment's `premium_trigger` block merged over its `params`
(`strategy_deployments.resolve_deployment_premium_trigger` / `effective_premium_params`); a block
that is present but does not validate refuses the bar (`config_invalid`) rather than falling
through to the ordinary spot path.

1. At a configurable reference time, lock the CE/PE strike from spot and capture each side's
   premium from fresh WS ticks into `premium_locks` (unique per `(deployment_id, session_date)`).
   **Late-lock policy:** the lock happens on the first bar at/after `reference_time` with fresh
   ticks; a stale or absent tick HOLDs (blocker `ref_premium_unavailable`) and retries next bar;
   no lock by `late_lock_cutoff` (default `10:15`) ends the session (`done_for_day`, reason
   `no_lock`); a failed strike resolution ends it with `strike_lock_failed`. The actual
   `locked_at` is recorded.
2. Monitor both sides' premium against the momentum threshold every bar.
3. The first side to trigger journals a signal through the **normal** audit pipeline — same
   `signals` collection, same CONFIRMED/AUDITED states, same idempotency index. The pretrade filter
   is explicitly **bypassed** for this branch (with an audit-context marker) since a lock-driven
   trigger isn't a confidence-scored signal in the usual sense; the contract is taken from the
   **lock**, never re-resolved from (possibly drifted) spot.
4. **Only after the journal insert succeeds** is the trigger atomically latched
   (`premium_lock_store.latch_trigger`) — if the latch is refused (a race, or the lock flipped to
   `done_for_day` mid-bar), the signal's outcome is downgraded so the sink tee never routes a trade
   for a journaled-but-unlatched signal.

From there it is **routed exactly like every other deployment** — the same sink routing
(live-mode+connected → `auto_live`, else `auto_paper`, else journal-only), the same
`mode == "live"` + `LIVE_AUTOPLACE_ARMED` + caps + 15:00 IST entry-cutoff authorization (v0.56.0 —
no per-deployment ARM ceremony, no `LIVE_GUARD_ARMED`), the same executor
chokepoint. **There is no premium-momentum-specific arming gate** — this was an explicit design
decision (an earlier draft spec had a 10-paper-session validation gate; it was removed on request),
and none should be added without being asked. `auto_live.py` adds one extra safety check specific to
this strategy: a **last-line re-check** of the momentum trigger right before transmit (premium can
move between the bar's journal and the actual order), releasing the claim and journaling
`premium_trigger_not_met` if it no longer holds — the lock itself is untouched so a later bar can
retry.

Exits can use a new guard trail mode, `stepped_xy` (`risk.exit_controls = {"mode": "stepped_xy",
"x": ..., "y": ...}`) — an AlgoTest-style discrete ratchet (raise the stop by `y` for every `x` of
favorable premium move) — alongside the ordinary stop/target fields. It delegates to the same
stepped-trail helper the backtest uses. **Do not re-express it as `lock_trail`:** `lock_trail` steps
from the trigger on ltp with no high-water cap and diverges from the backtest when `y > x`.

On restart, `rehydrate_premium_momentum` (`runtime.py`) re-registers already-entered locks with the
guard using the **persisted entry premium** (not a generic default), skipping any lock whose order
id or trading symbol the guard already has watched, so a recovery re-run can never double-watch
(and double-square) one position. This step is load-bearing: without it a restart falls to the
generic rehydrate, which watches the position at the 50% catastrophe stop with no stepped trail and
no close-loop link. A lock whose position is gone is closed out as `done_for_day`, reason
`exited_while_down`.

**Session lifecycle** (fields on the `premium_locks` doc): created at the reference bar with the
locked strikes and refs → a side crosses its threshold and the signal journals clean → the
trigger latches (never before a clean journal, so a trigger refused by the entry window does not
burn the session's single entry) → entered (`entered_norenordno`; per leg `<prefix>_entered_norenordno`
in both mode) → done. `done_for_day`
with reason `exited` is written **only** by the guard's confirmed-flat close hook, never on
order acceptance. A lock whose `session_date` is not today is simply superseded; no cron cleans it.
Lock state lives in its own collection, not on the deployment doc, on purpose: a field on the
deployment would race the read-modify-write `risk.*` writers. The collection is also the
subscription-pin source (`premium_lock_store.today_locked_keys`), the recovery source and the
audit trail.

Refusal and outcome vocabulary: `premium_trigger_not_met` (the last-line re-check, written to
`signals.live_trade_error`), `ref_premium_unavailable`, `no_lock`, `strike_lock_failed`,
`vix_gate`, `vix_unverifiable`, `day_stop`. A trigger at or after the entry-window end (default
14:50) is refused and journaled by the ordinary entry window.

### Multi-leg mode (Phase 5B, v0.55.0)

Everything above describes the default `leg_mode: "first_to_trigger"`, which is **byte-identical to
the original Track-B behavior** (source-pinned by tests). Setting `leg_mode: "both"` in the
deployment params switches the evaluator branch to the multi-leg engine, where CE and PE are
**independent primaries** — each side latches, journals, and enters on its own (same
`premium_locks` doc; the primaries `pce`/`ppe` reuse the `ce`/`pe` storage — `ce_ref_premium`,
`ce_triggered`, `ce_entered_norenordno`, `ce_exited` — and the lazy legs use flat `lce_*`/`lpe_*`
fields; per-leg atomic latch/unlatch/entered transitions in `premium_lock_store.py`). The other 5B params (all optional, all flowing through the standard
`merged_params` allow-list — no schema migration):

- `lazy_enabled` + `lazy_momentum_pct`/`lazy_stop_pct`/`lazy_target_pct`/`lazy_moneyness` — a
  **one-shot lazy reversal leg**: when a primary leg exits via a STOP-class reason
  (`stop`/`breakeven_stop`/`trailing_stop`/`spot_stop_hit` — never target/EOD/exit_time/basket
  reasons), the **opposite** side arms a fresh strike lock with its own snapshot (`lce_*`/`lpe_*`
  fields). Arming runs on **both rails** through one shared gate,
  `premium_momentum_live.lazy_arm_side` (primary leg only, STOP-class only, `lazy_enabled` with a
  lazy momentum set, before `entry_cutoff`): live in the guard's confirmed-flat close hook
  (`runtime._live_guard_on_close`, reasons `LIVE_STOP_CLASS_REASONS`), paper in the exit marker
  (`paper_auto._maybe_arm_paper_lazy_leg`, reason `stop_hit`; since v0.56.4). One shot per
  primary side; the next evaluator bar picks up the armed leg, locks its fresh strike and captures
  its ref from ticks. A restart-recovered live entry (default-level stop) never arms a lazy leg.
- `entry_cutoff` (IST HH:MM) — no new triggers or lazy armings at/after this time.
- `exit_time` (IST HH:MM) — per-deployment square time, **clamped strictly below the 15:00 system
  EOD** (which always wins); registered per guard entry as `square_at_ist`. The resulting
  `exit_time` exit reason is deliberately NOT STOP-class (it never arms a lazy leg).
- `session_max_loss_rupees` / `session_max_profit_rupees` — a **realized-only** day-stop evaluated
  before the engine each bar: on breach it atomically fires once (`mark_day_stop`: flag + done in
  one write), **squares open live positions once** via the standard deployment-stop path, and
  **blocks** further entries. In paper mode it blocks only (paper positions are left to their own
  exits) — an intentional asymmetry.
- `vix_min` / `vix_max` — an INDIAVIX session gate resolved as-of session start, **only when
  configured**; an unverifiable VIX with a configured gate refuses with `vix_unverifiable` (visible
  strip label), never a silent pass.

Three things a maintainer must not un-learn: (1) **all HH:MM comparisons must pass through
`normalize_hhmm`** (`premium_momentum.py`) — raw lexicographic compares are fail-open for unpadded
input like `"9:30"`; (2) whole-doc session finalize (`done_for_day`) happens **only when both
primaries have exited and no leg — including a freshly-armed lazy leg — is still in play**
(`legs_unresolved`); (3) restart recovery resolves every leg's trading symbol **exclusively through
the broker order book's `norenordno→tsym` join** — the lock's persisted `trading_symbol` is the
UPSTOX symbol and must never be matched against the Noren-keyed broker position book; an
unresolvable order number is skipped to the generic rehydrate, never marked exited.

The failed edge verdict (`docs/PREMIUM_MOMENTUM_EDGE_VERDICT_2026-07.md`) travels with every
multi-leg deployment as an **informational** `premium_edge_verdict` advisory (in the
`arm_advisories` of the `/live/enable` response and the deploy panel) — it never gates going live,
per the same no-new-gates decision.

Three more implementation facts:

- **Leg identity on close.** A guard entry carries no side, so `_live_guard_on_close` finds the leg
  by matching the entry's `id` (the `norenordno`) against `ce_` / `pe_` / `lce_` /
  `lpe_entered_norenordno` (legs `pce` / `ppe` / `lce` / `lpe`; the primaries alias the
  `ce` / `pe` storage). Basket-level `overall_*` exits and kill-switch squares are not
  STOP-class, so they never arm a lazy leg: a reversal into a basket stop would fight the
  operator's own risk control.
- **Pinning.** `premium_lock_store.today_locked_keys` scans `ce`, `pe`, `lce` and `lpe`; without
  the lazy legs a strike locked mid-session would never be pinned into the option stream.
- **Day-stop is not the governor.** The live governor's `daily_loss_cap` is mark-to-market
  (realized + open unrealized). The premium day-stop is realized-only, so it is its own query of
  this deployment's session trades (`_resolve_realized_today_rupees`, over `live_trades` for live
  and `paper_trades` otherwise), not a reuse of the governor.

**Scope.** Multi-leg is deliberately NOT exposed through `PremiumTriggerConfig` or the general
Optimizer search space: `lazy_enabled` is `"fixed": False`, the day-stop and VIX params are
`"fixed": None` (risk controls, not search dimensions), and `str` params such as `leg_mode` never
enter `_build_param_space`. The `/premium-momentum` page plus its tuner
(`POST /api/premium-momentum/tune`) is the multi-leg research surface. Opening the plugin
`parameter_schema` for 5B deliberately reopened a seam that Phase 5A had kept closed only to stop
silent backtest/live divergence while live support was missing. Not built: re-entries beyond one
lazy shot, a mark-to-market day-stop, paper day-stop squaring, non-NIFTY instruments.

#### Backtest ↔ live parity divergences

The rules come from shared pure helpers (`lock_reference_strike`, `momentum_triggered`, the
stepped-trail helper, `lazy_arm_side`). What still differs, and in which direction:

| Area | Live / paper | Backtest | Direction of error |
|---|---|---|---|
| Ref and trigger premium | fresh tick at evaluation (bar close + seconds) | option-bar close | either way; measure it |
| Stop evaluation | guard decides on fresh ticks (≤ 2 s old) or the broker `lp` (~1.5 s book read) | trail ratchets on 1-minute closes | live stops strictly tighter (earlier exits) — safe direction |
| Entry vs exit mark | entry marked on the Upstox tick, exit on the tick or broker `lp` | both on option bars | small basis; measure it |
| Last-line re-check | can refuse an entry the backtest would fill (`premium_trigger_not_met`) | fills at the trigger-bar close | live more conservative — intentional |
| Same-bar double-cross (both mode) | ONE entry decision per deployment-bar: CE this bar, PE next bar via its still-unlatched leg | both legs enter at the same bar close | live later / fewer — conservative |
| Lazy arming | on the confirmed-flat STOP-class close (live) or the `stop_hit` close (paper); the NEXT bar locks the fresh strike and takes the ref from ticks | at the stop-out bar, ref = that bar's close | live later by flat-confirm + up to 1 bar — conservative |
| Day-stop | realized-only; live blocks entries/armings and squares once via `_square_live_positions_for_deployment(reason="premium_day_stop")`; paper blocks only | realized-only; open legs force-closed at the breach bar | paper more permissive on open legs |
| `exit_time` | stamped as `square_at_ist` only when strictly before 15:00; a later value (EXP2's 15:13) is ignored and the 15:00 EOD square governs | sliced verbatim (15:13 allowed) | live exits earlier — conservative |
| VIX gate | last stored INDIAVIX close as-of the lock bar, 5-day staleness, checked once before the strike lock | same as-of rule at the session reference bar (`vix_by_session_map`) | equivalent; live may read an older close if today's VIX bars are not stored yet |

---

## Signal lifecycle (`signal_lifecycle.py`)

States: `WATCHING → FORMING → CONFIRMED → TRIGGERED → ACTIVE → EXITED → AUDITED`,
plus `SKIPPED` (from `TRIGGERED`); any non-terminal state may go to `AUDITED`
(`ALLOWED_TRANSITIONS`). "Blocked" is not a state: it is `blocked: true` on an
`AUDITED` signal. The evaluator produces a **clean** `CONFIRMED` signal or a
**blocked** `AUDITED` signal.

| Transition | Who | Notes |
|---|---|---|
| `CONFIRMED → TRIGGERED → ACTIVE` | the paper or live sink | trade linked as `paper_trade_id` / `live_trade_id`; `paper_trade_claim` is the shared one-trade claim |
| `ACTIVE → EXITED` | every paper and live close (`exit_linked_signal`, from `paper_auto`, `paper_squareoff` and `live/close_loop`) | before 2026-09-29 closes left signals ACTIVE; 649 orphans were retired to AUDITED (operator-approved) |
| `CONFIRMED → AUDITED`, reason `unactioned_bar_passed` | `expire_unactioned_signals`, at boot (`server.py`) and in the 15:00 IST sweep (`runtime.py`); operator-approved | only a clean signal whose bar is more than 15 min old with no claim and no trade link; keeps the signal's own `updated_at`. The first run (2026-09-29) retired 1,366. Bars after 14:45 are under 15 min old at 15:00 and wait for the next boot or sweep. |

Audit invariants every signal carries: top-level `candle_ts` (the bar),
`option_contract` (strike/side/instrument_key/lot_size), `risk_hints` and all
`blockers` as strings; under `context`: `bar_ts`, `decision_ts`, strategy
version/hash + source SHA, `pretrade_profile_name` + full snapshot, `regime`,
`candle`, `tracked_for_pnl`, `next_expiry_iso`.

Journal: `POST /signals/purge` takes `blocked` (true = blocked only); the Signal
Journal's retention auto-purge deletes **blocked** signals only, so clean history
is kept. The Journal date filter / sort and the overview's "Signals today" use
`candle_ts`.

---

## Undeploy + forward metrics

- **Archive** (`POST /deployments/{id}/archive`) stops signal generation and
  paper trading; `?purge=1` also deletes its journaled signals and CLOSED paper
  trades (OPEN paper trades are kept so the marker / square-off can finish them;
  `live_trades` are never purged).
- **Forward metrics** (`forward_metrics.py`, `GET /deployments/metrics` and
  `/{id}/metrics`) aggregate honest per-deployment results (win-rate, avg P&L,
  profit factor) gated on complete forward sessions. Low-sample deployments are
  hidden from the Strategy Library gate unless `include_ineligible=1`.
- **Overview** (`GET /deployments/overview`) powers the Deployments page: one row
  per non-archived deployment with today's signals + open/realized P&L, lifetime
  results, `last_evaluated_ts`, and a holiday-aware `market_status`. OPEN rows
  entered on an earlier IST day are counted as `open_carried`; OPEN rows whose
  P&L is not in `open_unrealized` are counted as `open_unverified`; a demoted
  deployment's real-money `live_trades` are reported separately from its paper
  book, never summed and never dropped (`overview_open.py`).

---

## API surface (implemented)

Lifecycle: `GET/POST /deployments`, `GET /deployments/{id}`,
`POST /deployments/{id}/pause|resume|stop|archive`, `POST /deployments/stop-all`,
`POST /deployments/{id}/repin-source`, `PUT /deployments/{id}/paper-caps`.

Evaluation: `POST /deployments/{id}/evaluate-on-close`,
`POST /deployments/evaluate-active`, `GET /deployments/{id}/signals`.

Evidence: `GET /deployments/preflight`, `/deployments/quality`,
`/deployments/readiness`, `/deployments/metrics`, `/deployments/{id}/metrics`,
`/deployments/overview`.

Live: `POST /deployments/{id}/live/enable|disable|pause|resume|stop|caps|flatten`,
`GET /deployments/{id}/live/status`, `GET /deployments/live/status?ids=`,
`GET /deployments/{id}/timeline?date=`, `GET /live-broker/session-clock`.

See [API_REFERENCE.md](./API_REFERENCE.md) for the full route reference and the
Flattrade broker endpoints in [`Resources/flattrade-pi-api/`](./Resources/flattrade-pi-api/).

---

## Non-goals / invariants

- **No real broker order except through the live path** (`mode == "live"` plus
  every gate above), and even then only when `LIVE_AUTOPLACE_ARMED` is set —
  offline-first is the default everywhere.
- Deployed live entries are **long-only** (option BUYS); a naked short is never
  opened.
- Everything is IST. Spot/index runs 09:15–15:30; since 2026-08-03 options run
  to 15:40 (`session_spec`). New entries stop at the entry-window end (default
  14:50) and the live 15:00 cutoff; the EOD square is 15:00. Holidays and expiry
  dates come from the calendar / `option_contracts`, never a hardcoded weekday.
- No signals from an unregistered file — a deployment is always an immutable
  snapshot of a loaded 1m-compatible Strategy Library entry, saved Preset, or
  Backtest Run, with the current strategy source SHA pinned.
