# Strategy Deployments

The deployment model: how an **audited research artifact** becomes a running,
forward-testing (and optionally real-money) strategy — and every safety gate,
kill switch, and chokepoint on the way.

> **Cross-links:** the live-trading safety model in
> [DEVELOPER_GUIDE.md](./DEVELOPER_GUIDE.md); the manual real-money go-live drill
> in [live-readback-checklist.md](./live-readback-checklist.md); the technical
> module map + the L0–L3 gate chain in [ARCHITECTURE.md](./ARCHITECTURE.md); the
> HTTP routes in [API_REFERENCE.md](./API_REFERENCE.md).

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
| `instrument` | `NIFTY` \| `BANKNIFTY` \| `SENSEX` |
| `timeframe` / `confirmation_mode` | `1m` / `1m_close` (`tick` reserved, not yet evaluated) |
| `option_policy` | `{ moneyness: [atm/otm1/itm1], expiry_policy: "next_available", dte_filter }` — `dte_filter` default `[0..6]` |
| `pretrade_profile` | Conservative / Balanced / Aggressive (or custom) |
| `mode` | **`signal_only`**, **`paper`**, or **`live`** (real-money; reachable only via `POST /live/enable` — see below) |
| `risk` | The whole risk block: `allow_overnight`, `default_lots`, `auto_paper`, the `auto_paper_*` premium exits, `friction`, the pinned `sizing` replay, the **kill-switch** fields, `daily_caps`, and the nested **`live`** sub-object — a pure CONFIG doc (caps + catastrophe band); it carries no authorization field, `mode` alone authorizes |
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
suppresses the paper path for that signal) whenever the broker is connected and
it is before the daily 15:00 IST new-entry cutoff; `LIVE_AUTOPLACE_ARMED`
remains the one env master switch on top. `mode` cannot be set to `"live"` at
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
drawdown, selection bias, and option-rupee-OOS.

If **any** warning is present, the create call returns
**`400 acknowledgment_required`** unless the request carries
`acknowledged_warnings=true`. On success the full `quality` snapshot + the ack
flag are stored on the deployment. The gate **warns, never silently blocks** —
the user makes a conscious choice.

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
   `drift_reason="strategy_source_drift"`). Pre-pin deployments are exempt.
2. **Kill switches** (`check_deployment_kill_switches`, paper only) — a pause
   switch auto-pauses; the block switch adds a blocker to this bar.
3. Load the latest ~200 closed 1-minute candles (needs ≥50 bars), enrich with
   indicators + regime + features, and `strategy.evaluate()` the freshest bar.
4. If direction is `CE`/`PE`, resolve the **pretrade filter**, the **option
   contract** (ACTIVE-expiry only — never an expired strike), and the guards:
   time-of-day window (**block 09:15–09:25 and 14:50–15:30 IST**), **expiry-day
   15:00 cutoff** (from `option_contracts.expiry_date`, never weekday-hardcoded),
   and recent-option-data.
5. **Journal one signal**:
   - clean → `CONFIRMED` (`blocked=false`);
   - blocked → `AUDITED` with a human-readable `blockers[]`.
   Full audit context is captured: `bar_ts`, `decision_ts`, strategy hash + SHA,
   pretrade snapshot, regime, chosen contract, `risk_hints` (the strategy's own
   exit definition), `next_expiry_iso`, and `tracked_for_pnl`.

Deployments are intentionally **independent** (2026-06-12): two strategies firing
on the same instrument/minute both journal and both may trade — enabling honest
head-to-head comparison. The old highest-score concurrency demotion was removed;
exposure is governed per-deployment by `max_open_paper_trades`.

### Idempotency
`last_evaluated_ts` gates re-evaluation of the same bar, and a unique partial
index over `(deployment_id, candle_ts)` makes a duplicate insert a silent skip.

---

## Sink routing (per clean signal)

After journaling, each clean `CONFIRMED` signal is re-read (guarding against
concurrent mutation) and routed to **exactly one** sink (`if/elif`, never both;
the shared atomic `paper_trade_claim` also enforces one-trade-per-signal):

- **Armed + broker-connected** (`auto_live_enabled`) → **`auto_live`** (a REAL
  order); the paper path is suppressed for that signal.
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
- **Per-minute marker** (`mark_open_deployment_trades`): during market hours,
  marks OPEN paper trades to the latest option tick, fires premium stop/target,
  trailing/breakeven ratchet, time-stop, and spot-mirror exits, and transitions
  the signal to `EXITED`. Writes are conditional on `status=OPEN`; stale/tickless
  trades are left untouched.
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

---

## Live path (real-money)

Real-money auto-placing is **off by default and heavily gated**. Enabling it
(v0.56.0: `POST /live/enable`, replacing the old per-session ARM) is a
deliberate, one-time-per-deployment act — it persists across sessions until
explicitly disabled or stopped — with the env master gate (`LIVE_AUTOPLACE_ARMED`)
still on top of it.

### Enable / disable / stop (`routers/deployments.py`)

`POST /deployments/{id}/live/enable` sets `mode="live"` and writes `risk.live`
**only** if every operational guard passes: deployment exists and is `ACTIVE`,
strategy not retired, not drift-paused, **broker ready** (a current post-06:00
Flattrade daily session is stored and static-IP metadata is configured), the
`LiveEngine.can_trade()` is True, and `confirm` is the literal
boolean `True` (StrictBool). The body sets `lots`, `max_lots_per_day`,
`max_concurrent`, `daily_loss_cap`, and optional catastrophe stop/target %; all
three count caps must be ≥1 and the daily loss cap must be positive. Lots and
concurrent positions must also fit the current account-level safety ceilings.

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

- `POST /deployments/{id}/live/disable` — revert `mode` to `"paper"`; stops new
  live placing. Does **not** flatten open positions — they stay registered with
  the guard and keep their stop/target/trail and the resting OCO. The live
  config (`risk.live` caps + catastrophe band) is retained so re-enabling
  doesn't require re-entering it.
- `POST /deployments/{id}/live/stop` — flatten THIS deployment's open live
  positions (margin-safe square path), revert `mode` to `"paper"`, **and** set
  `status="PAUSED"` — reverting mode alone would leave an ACTIVE deployment
  free to re-enter on the very next confirmed signal; `status=PAUSED` is the
  actual halt (`evaluate_all` only iterates `{"status": "ACTIVE"}`).
- `POST /deployments/stop-all` — square all paper, pause every ACTIVE
  deployment, and revert-to-paper + pause every `mode == "live"` deployment
  (selector `{"mode": "live"}` — **not** the old `{"risk.live.armed": True}`,
  which would now silently match zero documents and turn Stop-ALL into a
  no-op for live positions).
- `GET /deployments/{id}/live/status` (and the batched `?ids=`) reports live
  state, caps, today's counters, open positions, and the transmit gate
  (`autoplace_armed`; `guard_armed` is retained in the payload for
  compatibility and is always `true`).

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
(`resolve_capped_lots`) — **not** the sizing-replay path.

### Per-deployment live caps governor (`live_deploy_governor.py`)
Before each live entry, `check_live_caps` enforces (first match wins):
`daily_loss_cap` (realized + open-unrealized today → **disarm + PAUSE**),
`max_lots_per_day` (block), `max_concurrent` (block). No cap configured → the DB
is never queried.

### The executor chokepoint (`live/executor.place_deployed_order`)
The **single real-order site** for deployed orders. Long-only (`side="B"`);
a full gate chain runs before any transmit: authorization (`allow_fn` = the arm
gate), a fresh server-side dry-run, **margin must cover the FULL
`capped_lots × lot_size`** (broker-authoritative `order_margin`), all verdicts
pass, a lot-cap defense-in-depth check, `engine.can_trade()`, the transmit
boundary (`LIVE_AUTOPLACE_ARMED`), and a SEBI rate throttle — only then does
`_transmit_and_arm` place the order and arm protection.

### Catastrophe backstop + software guard
On a real fill, `arm` best-effort places a **resting broker OCO** (NRML product,
the PC-down catastrophe net) whose `al_id` is journaled (`oco_al_id`;
`oco_error="no_broker_backstop"` if it couldn't rest). Independently, the
position is registered with the **`LivePositionGuard`** (`live_position_guard.py`)
— a ~1.5 s loop that reads the **broker** position book and squares via the
margin-safe cancel-all-then-close path when a stop/target/trailing/spot-mirror
breaches. (Why software, not a resting SL: a resting SELL stop on a long option
needs naked-short SPAN margin an option-buyer account lacks, so a broker SL is
rejected every time — proven live 2026-06-24.) A position is removed from the
registry **before** its square is issued, so a slow square is never double-sent.
If the guard's retries are exhausted it **re-adds** the position in an escalated
`square_stopped` state rather than dropping it silently — the broker OCO/GTT and
the 15:00 IST EOD square (which explicitly bypasses `square_stopped`) remain the
ultimate backstops. (A prior manual-position-only 10-minute auto-square timer was
**removed** — the EOD square is now the sole time-based backstop for a manual
position; deployed strategies exit on their own rules + the resting OCO.)

---

## `premium_momentum`: a lock-driven deployment variant

`premium_momentum` deploys through the identical pipeline above (Preset/Run → deployment → quality
gate → evaluator → paper/live sink), but the **evaluator step is different**. Instead of calling
the strategy's `evaluate()` (the plugin's is inert — it registers schema/metadata only),
`deployment_evaluator.py` has a dedicated branch for `strategy_id == "premium_momentum"` that calls
`premium_momentum_live.evaluate_premium_momentum_bar` per bar:

1. At a configurable reference time, lock the CE/PE strike from spot and capture each side's
   premium from fresh WS ticks into `premium_locks` (unique per `(deployment_id, session_date)`).
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
favorable premium move) — alongside the ordinary stop/target fields. On restart,
`rehydrate_premium_momentum` re-registers already-entered locks with the guard using the
**persisted entry premium** (not a generic default), skipping any lock whose order id or trading
symbol the guard already has watched, so a recovery re-run can never double-watch (and
double-square) one position.

### Multi-leg mode (Phase 5B, v0.55.0)

Everything above describes the default `leg_mode: "first_to_trigger"`, which is **byte-identical to
the original Track-B behavior** (source-pinned by tests). Setting `leg_mode: "both"` in the
deployment params switches the evaluator branch to the multi-leg engine, where CE and PE are
**independent primaries** — each side latches, journals, and enters on its own (per-leg fields
`pce_*`/`ppe_*` in the same `premium_locks` doc; per-leg atomic latch/unlatch/entered transitions in
`premium_lock_store.py`). The other 5B params (all optional, all flowing through the standard
`merged_params` allow-list — no schema migration):

- `lazy_enabled` + `lazy_momentum_pct`/`lazy_stop_pct`/`lazy_target_pct`/`lazy_moneyness` — a
  **one-shot lazy reversal leg**: when a primary leg exits via a STOP-class reason
  (`stop`/`breakeven_stop`/`trailing_stop`/`spot_stop_hit` — never target/EOD/exit_time/basket
  reasons), the **opposite** side arms a fresh strike lock with its own snapshot (`lce_*`/`lpe_*`
  fields). Arming happens in the live guard's on-close hook, subject to `entry_cutoff`.
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
multi-leg deployment as an **informational** `premium_edge_verdict` arm advisory (shown in the
deploy/arm panel) — it never gates arming, per the same no-new-gates decision.

---

## Signal lifecycle

States: `WATCHING → FORMING → CONFIRMED → TRIGGERED → ACTIVE → EXITED → AUDITED`,
plus side states `SKIPPED` / `BLOCKED`. The evaluator produces a **clean**
`CONFIRMED` signal or a **blocked** `AUDITED` signal. On a sink open,
`CONFIRMED → TRIGGERED → ACTIVE` with the trade linked (`paper_trade_id` or
`live_trade_id`); on close the marker/guard transitions it to `EXITED`.

Audit invariants every signal carries: `bar_ts`, `decision_ts`, strategy
id/version/hash + source SHA, `pretrade_profile_name` + full snapshot, `regime`,
the chosen `option_contract` (strike/side/instrument_key/lot_size), the
`tracked_for_pnl` flag, `next_expiry_iso`, and all `blockers` as strings.

---

## Undeploy + forward metrics

- **Archive** (`POST /deployments/{id}/archive`) stops signal generation and
  paper trading; `?purge=1` also deletes journaled signals and CLOSED trades
  (OPEN trades are kept so the marker / square-off can finish them).
- **Forward metrics** (`forward_metrics.py`, `GET /deployments/metrics` and
  `/{id}/metrics`) aggregate honest per-deployment results (win-rate, avg P&L,
  profit factor) gated on complete forward sessions. Low-sample deployments are
  hidden from the Strategy Library gate unless `include_ineligible=1`.
- **Overview** (`GET /deployments/overview`) powers the Deployments page: one row
  per non-archived deployment with today's signals + open/realized P&L, lifetime
  results, `last_evaluated_ts`, and a holiday-aware `market_status`.

---

## API surface (implemented)

Lifecycle: `GET/POST /deployments`, `GET /deployments/{id}`,
`POST /deployments/{id}/pause|resume|stop|archive`, `POST /deployments/stop-all`,
`POST /deployments/{id}/repin-source`.

Evaluation: `POST /deployments/{id}/evaluate-on-close`,
`POST /deployments/evaluate-active`, `GET /deployments/{id}/signals`.

Evidence: `GET /deployments/preflight`, `/deployments/quality`,
`/deployments/readiness`, `/deployments/metrics`, `/deployments/{id}/metrics`,
`/deployments/overview`.

Live: `POST /deployments/{id}/live/enable|disable|stop`,
`GET /deployments/{id}/live/status`, `GET /deployments/live/status?ids=`.

See [API_REFERENCE.md](./API_REFERENCE.md) for the full route reference and the
Flattrade broker endpoints in [`Resources/flattrade-pi-api/`](./Resources/flattrade-pi-api/).

---

## Non-goals / invariants

- **No real broker order except through the armed live path**, and even then only
  when `LIVE_AUTOPLACE_ARMED` is set — offline-first is the default everywhere.
- Deployed live entries are **long-only** (option BUYS); a naked short is never
  opened.
- Everything is IST; the NSE session is 09:15–15:30 with a 15:00 square-off;
  holidays and expiry dates come from the calendar / `option_contracts`, never a
  hardcoded weekday.
- No signals from an unregistered file — a deployment is always an immutable
  snapshot of a loaded 1m-compatible Strategy Library entry, saved Preset, or
  Backtest Run, with the current strategy source SHA pinned.
