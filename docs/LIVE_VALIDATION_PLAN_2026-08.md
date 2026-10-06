# Live-market validation plan — the next market session (IST)

**Updated 2026-09-30. The single market-session validation runbook.** Read it with
[`live-readback-checklist.md`](live-readback-checklist.md) (the supervised real-money drill
the live phase follows) and [`STRATEGY_DEPLOYMENTS.md`](STRATEGY_DEPLOYMENTS.md) (routes,
fields and gates).

**Status.** The session this plan was first written for (Monday 2026-08-17) has no recorded
outcome in HANDOFF, AGENT_TODO, learning_log or CHANGELOG, so treat every check below as
never run. Everything shipped 2026-09-26..30 (the Live Deployments uplift: tighten caps,
flatten & hold, the governor view, restart attribution, the transmit fence, the signal
lifecycle) is unit- and mutation-tested and was checked in the browser pane on 2026-09-29,
but **none of it has run in a market session** — the broker token was expired and the
operator was off the static IP. The operator will run §1 as soon as they are back on the
Flattrade static IP. The one-time cloud reminder fired Tue 2026-10-06 09:00 IST. **§1 is the one
canonical market-session checklist:** AGENT_TODO, HANDOFF §2.3, README and the takeover prompt
point here and do not keep their own copy. Record its outcome per §11.

Before the session, confirm the date is not on NSE's published F&O holiday list. If NSE
announces an extraordinary closure, record the run as `NO_SESSION` and do not reinterpret
missing activity as a product failure.

## 0. Decision and safety posture

A validation session is an **evidence session, not a feature-development session**. Any
scheduled job or assistant is a read-only observer. It may read application endpoints, logs
and Mongo state, but it must not:

- call Flattrade MCP `login` or `logout` (last-login-wins would kill AlphaForge's token);
- place, modify, cancel or square an order through any interface;
- change `LIVE_AUTOPLACE_ARMED`, a deployment status or a deployment mode;
- invoke Stop, Disable, Flatten, kill switch, recovery POSTs or service restarts;
- edit code or configuration during the session.

OAuth, process restarts, live enablement, Stop/Disable/Flatten/kill actions and all
real-order decisions belong to the operator. Connecting Flattrade is not authority to trade.

**`LIVE_AUTOPLACE_ARMED`.** Read the running backend's value at pre-open. `1` cannot transmit
by itself: a deployment must also be ACTIVE, `mode == "live"`, not held, broker-connected,
before the 15:00 entry cutoff, within the account and per-deployment caps, and pass the
executor gates. But if it is `1`, enabling one deployment live makes the next eligible signal
transmit without another prompt. Paper-only work should run with zero live-mode deployments;
`LIVE_AUTOPLACE_ARMED=0` is recommended defense-in-depth.

**Baseline.** The dated suite baseline lives in [`HANDOFF.md`](HANDOFF.md) §2. Record the
running build (`git rev-parse HEAD`, and confirm the container carries it:
`MSYS_NO_PATHCONV=1 docker exec alphaforge_backend grep -c <symbol> /app/app/<file>.py`).
Deploy with `docker compose up -d --build` — a plain restart runs stale backend code.

Stop the session immediately if an unintended order appears, broker and app disagree about
exposure, an unknown or empty broker response is interpreted as flat, a non-finite money value
appears, or a halt fires without a measured breach. Broker state is the exposure truth.

## 1. Live Deployments uplift — the first checks (canonical; formerly the AGENT_TODO reminder block)

Run these first, on the static IP. They need a live deployment, so the operator performs every
action; the assistant reads. Use one deployment at **1 lot**. What each check exercises:
[`HANDOFF.md`](HANDOFF.md) §2.6 and the
[uplift handoff spec](superpowers/specs/2026-09-26-live-deployments-uplift-handoff.md). Open
follow-ups (e.g. O1, partial fills) are tracked in [`AGENT_TODO.md`](AGENT_TODO.md).

| ID | Operator action | Pass evidence |
|---|---|---|
| U1 | Log in to Flattrade **from AlphaForge** after 06:00 IST | The execution strip drops its "Flattrade session expired" alert; `GET /api/flattrade/status` connected and not expired. |
| U2 | With **nothing open**, `docker compose up -d --build` | Backend log shows `live startup recovery: reboot reconcile ... status=ok` (or `status=flat_confirmed`), then `live recovery: completed`; `GET /api/live-broker/recovery-status` agrees. Any other status keeps recovery INCOMPLETE and retrying — FAIL until explained. |
| U3 | Open the Live Deployments pane | Each live row shows cap headroom and the binding chip (from the `governor` key of `GET /api/deployments/live/status?ids=`); a `blocked: …` reason whenever it cannot trade; a countdown to the entry cutoff that matches `GET /api/live-broker/session-clock`. |
| U4 | Enable one deployment live at 1 lot, then tighten its caps | A lower loss cap is accepted (`changed` lists it; `risk.live.last_caps_change` recorded). Raising any cap returns 409 `caps_loosening_refused` with "disable and re-enable" and changes nothing. |
| U5 | When it takes a position, expand the row | Positions, distance to stop, and the timeline (`GET /api/deployments/{id}/timeline`) show the entry. |
| U6 | Restart once (`docker compose up -d --build`) while that position is open | The rehydrated guard entry still belongs to the deployment: it appears in the row's `open_positions`, and Flatten finds it (it is NOT listed under `unguarded_open_tsyms`). |
| U7 | **Flatten & hold** | The toast says "exit submitted — awaiting fill confirmation", never "flattened"; the deployment stays `mode == "live"` + ACTIVE with the hold set; `fill_confirmed: false`. After the guard confirms flat, compare journal `realized_pnl` with the broker's own P&L for the trade: the guard journals its exit at the last-seen broker `lp`, so a difference is `ESTIMATED_EXIT` (see L6), not PASS; a partial fill is journaled on the ORDERED quantity (AGENT_TODO O1). The Day Stop card counted the open loss while the position was open. |
| U8 | Resume; optionally turn on the opt-in alerts (default off) | Entries resume under the unchanged caps; no consent is re-collected. |

Then continue with §4 onward. LV-1 and LV-4 assume zero live deployments: either disable the
§1 deployment first, or record it as the live deployment under test and skip the paper-only
restart (LV-4/LV-5) while it holds a position.

## 2. Result vocabulary

Every target receives exactly one outcome:

- `PASS` — the named binary check was exercised and satisfied.
- `FAIL` — it was exercised and contradicted the expected behavior.
- `NOT_EXERCISED` — its triggering event never occurred, such as no paper signal.
- `BLOCKED` — a prerequisite failed, so downstream work did not run.
- `NO_SESSION` — the exchange did not conduct the expected session.

No signal and no trade are never silently called passes. A read-only observer does not repair,
restart or manufacture an event in order to obtain evidence.

## 3. Before the session

1. Freeze the exact build under test. Do not rush the execution episode ledger (E1) or a broad
   strategy-engine refactor into it.
2. Run the full backend suite and the frontend build gate after the final pre-session change
   (commands in [`HANDOFF.md`](HANDOFF.md) §3), then rebuild the images.
3. Confirm zero live-mode deployments before the paper phase.
4. Prepare two ACTIVE paper deployments that together cover NIFTY and SENSEX (or BANKNIFTY).
   At least one should have an early `exit_time` or time stop so a clock exit is observable.
5. Keep evidence in the ignored local directory `tmp/live-validation/<date>/`. Do not commit
   raw broker books, account data, tokens or order IDs.

## 4. Job schedule

| Time (IST) | Job | Authority | Binary completion test |
|---|---|---|---|
| 08:20 | Exact-build start | Operator | Backend health reports DB OK, frontend returns HTTP 200, the running build matches the frozen checkout. |
| 08:30-08:40 | OAuth | Operator | Upstox connected before readiness runs. Flattrade OAuth (after 06:00) is needed for broker readback, §1 and the live phase, and must use AlphaForge only. |
| **08:50** | **LV-1 pre-open gate** | Read-only | `GET /api/live-broker/preopen-readiness` holds today's 08:45 verdict with `ready: true`; Upstox connected; warehouse pending actions an explained integer; no deployment live (unless §1 is being run). `LIVE_AUTOPLACE_ARMED=1` is a prominent warning, not a failure, while no deployment is live. |
| **09:25** | **LV-2 opening data gate** | Read-only | Feed LIVE; stream, roller and exit monitor running; closed one-minute candles advance; no unexplained leading or internal gap per index (`GET /api/live-feed/health` `completeness`). |
| 09:25-10:55 | LV-3 paper continuity | Read-only | Each instrument's deployment evaluates when that instrument advances; any OPEN paper row's `updated_at`, `last_price` and finite `unrealized_pnl` advance across two reads. |
| **11:05** | **LV-4 restart decision** | Read-only | Account flat, no live deployment, no unexpected working order/GTT, guard count zero, paper evidence clean so far. Reports `SAFE_TO_RESTART` or `DO_NOT_RESTART`; never restarts. |
| 11:10 | One backend restart | Operator, optional | Only after `SAFE_TO_RESTART`. One deliberate restart is enough. Never restart with live exposure except as U6. |
| 11:13-11:20 | LV-5 restart recovery | Read-only | Health returns; feed/roller/exit monitor recover; the paper row keeps marking; recovery completes (§7); no broker position adopted from an empty account; premium locks do not falsely complete. |
| **11:45** | **LV-6 live-readback gate** | Read-only | `LIVE_ELIGIBLE_FOR_OPERATOR_DECISION` only if LV-1..LV-5 PASS, broker reads agree with app exposure, caps are finite, one-lot policy visible, no unresolved order episode. Never enables live. |
| 12:00-14:30 | Optional one-lot readback (§8) | Operator only | Only after a fresh in-session operator decision. The assistant observes and reconciles; it never causes an order. |
| Configured exit time + 60 s | LV-7 clock exit | Read-only | An eligible paper row closes with its clock reason even if the latest option tick is stale. No eligible row = `NOT_EXERCISED`. |
| 15:02 | LV-8 cutoff/EOD | Read-only | No new live entry after 15:00 (`governor.authorization.reason == "after_entry_cutoff"` on a live row); non-overnight paper rows closed by the 15:00 sweep; unactioned CONFIRMED signals older than 15 min moved to AUDITED (`unactioned_bar_passed`); any unexplained OPEN row is `FAIL`. |
| **15:45** | **LV-9 final evidence** | Read-only | Spot coverage 375/375 for each traded index; option coverage per `backend/scripts/audit_cas_session_coverage.py` (a complete post-2026-08-03 contract-day is 385 bars); broker/app exposure agree; no unexpected working order or GTT; every target has an explicit outcome. |

Broker reads happen at the pre-live gate, around an actual live event and at final
reconciliation — not every few seconds — because the broker rate budget is shared with
AlphaForge (and with the Flattrade MCP, per key).

## 5. Read-only evidence surfaces

Prefer AlphaForge's own routes and Mongo projections over the Flattrade MCP:

- `/api/health`, `/api/upstox/status`, `/api/flattrade/status`;
- `/api/live-feed/health`, `/api/live-exit-monitor/status`;
- `/api/live-broker/arm-state` (with its `session` clock), `/session-clock`,
  `/preopen-readiness`, `/recovery-status`, `/guard-status`;
- `/api/live-broker/positions`, `/orders`, `/trades`, `/gtt`, `/reconcile`, `/blotter`,
  `/greeks`;
- `/api/deployments/overview`, `/api/deployments/live/status?ids=`,
  `/api/deployments/{id}/timeline?date=`;
- the latest `preopen_readiness`, `candles_1m`, `signals`, `paper_trades`, `premium_locks`,
  `live_orders` and `live_trades` rows, with secrets and account identifiers redacted.

Confirm route names against the running OpenAPI document before the session; a 404 from route
drift is a `BLOCKED` observer, not evidence about trading behavior.

## 6. Phase A — paper and liveness

| ID | Claim under test | Pass evidence |
|---|---|---|
| A1 | The open is captured | The first closed minute is present and later `ts` values advance without an unexplained gap. |
| A2 | Instrument-independent wakeup works | Advancing SENSEX or BANKNIFTY while NIFTY is unchanged advances the matching deployment's evaluation state. |
| A3 | Paper marking is live | An OPEN row's `updated_at`, `last_price` and finite `unrealized_pnl` change across two observations. |
| A4 | Clock exits survive a stale option tick | The time-driven exit closes on the last known premium (or an explicit no-price fallback) and labels any estimate honestly. |
| A5 | 15:00 scheduler wiring works | The scheduled square-off runs (the misplaced-argument `TypeError` was fixed 2026-08-15; this is its first market-session regression check) and honours `allow_overnight` only for the EOD sweep. |
| A6 | Risk supervisor is observable | There is still no supervisor status route. Its per-deployment daily-loss pause was a `TypeError` from 2026-08-06 to 2026-09-29 and never fired (fixed `35a33fe`). With zero live deployments the loop skips every cycle (`NOT_EXERCISED`); otherwise the only evidence is the backend log (`Risk supervisor loop initialized` at boot, then `risk supervisor: …` lines on a breach). |
| A7 | Option-session boundary | The paper exit monitor and the live guard run to 15:40 on post-2026-08-03 days (`session_spec(OPTIONS)`). The risk supervisor still bounds on 09:15–15:30 (`runtime._risk_supervisor_loop`). Record what an overnight-permitted position shows between 15:30 and 15:40. |
| A8 | Signal lifecycle | A paper close moves its signal `ACTIVE → EXITED`; the Journal's date filter and "Signals today" count by the signal's bar (`candle_ts`); chips read EXPIRED / NOT ACTED ON / RETIRED as appropriate. |

Do not deliberately stop the Upstox stream. Natural degradation may be observed, but no job may
create it.

### 6a. Optional — premium-momentum multi-leg (Phase 5B), paper

The premium-momentum family FAILED its edge gate
([`PREMIUM_MOMENTUM_EDGE_VERDICT_2026-07.md`](PREMIUM_MOMENTUM_EDGE_VERDICT_2026-07.md)); run
this only to validate the capability. Two paper deployments of `premium_momentum`:

- **A:** `leg_mode: "both"`, lazy fields set (`lazy_enabled: true`, `lazy_momentum_pct`,
  `lazy_stop_pct`), an early `exit_time` (e.g. 14:45), a wide VIX band (e.g. 5–60), day-stop
  generous or off.
- **B:** a VIX band that excludes today's value.

| ID | Pass evidence |
|---|---|
| P1 | B: today's `premium_locks` doc is `done_for_day` with `done_reason` `vix_gate` (or `vix_unverifiable`). A silent no-trade with no reason is a bug. |
| P2 | A: the informational `premium_edge_verdict` advisory appears in the deploy panel and does NOT block. |
| P3 | A: at the reference time today's `premium_locks` doc holds the locked `ce` / `pe` contracts and `ce_ref_premium` / `pe_ref_premium` captured from fresh ticks (a stale tick holds with `ref_premium_unavailable`; no lock by `late_lock_cutoff` ends the day with `no_lock`). |
| P4 | A: on a premium move of at least `momentum_pct`, that leg latches (`ce_triggered` / `pe_triggered`), the signal journals and a paper trade opens; the legs are independent. |
| P5 | A: a primary paper **stop-out** (`stop_hit`) arms the opposite lazy leg (`lazy_armed_ce` / `lazy_armed_pe`), one shot per primary side, never after `entry_cutoff`, never on a target / time / EOD close (paper lazy arming since v0.56.4). |
| P6 | A: the paper row closes at `exit_time` with reason `exit_time` (paper honours `exit_time`). |
| P7 | Once realized session P&L breaches the day-stop, new entries are blocked (`day_stop`); paper never squares open legs. |
| P8 | Restart with an open paper position: the `premium_locks` doc is untouched (not done, no re-lock, no duplicate entry on the next bar) and no log claims `exited_while_down` for it. A paper-only restart does NOT exercise the broker order-book symbol join: paper writes no `norenordno`. |

## 7. Phase B — operator restart, paper only

The restart is optional and operator-owned because startup reconciliation mutates internal
state and may cancel a broker-confirmed orphan OCO. Run it only after the flat-account
precheck.

After one restart require health, feed, candle roller, exit monitor and paper marking to
recover. Recovery is complete only when the reboot reconcile ends `ok` or `flat_confirmed`
and `live recovery: completed` is logged; any other status keeps it retrying. Empty broker
positions stay `UNKNOWN` when the read itself failed; flat requires two confirmed-empty reads.

## 8. Optional one-lot live readback

Optional; never initiated by a scheduled job. Its purpose is operational truth, not
profitability. Follow [`live-readback-checklist.md`](live-readback-checklist.md) stages A–H.

Before the operator enables one deployment: all earlier gates PASS; long-only one lot;
`max_concurrent=1`, `max_lots_per_day=1`, a finite `daily_loss_cap` (mandatory); no overnight
permission; current exit preview; registered static IP; independent broker-terminal access.
If `LIVE_AUTOPLACE_ARMED` is `1`, live enablement is immediately consequential.

### 8a. Authorization-model control checks (no real order needed)

Run with `LIVE_AUTOPLACE_ARMED` unset. Several of these checks (strategy retired, the
data-integrity gate, forward-validation consent) run ONLY in the enable preflight — the
evaluator re-detects source drift (auto-pause + demote) and the auto-live path re-checks mode,
hold, status, broker, engine and caps, but nothing re-checks these — so a refusal that passes
silently there is enforced nowhere.

| ID | Action | Expected |
|---|---|---|
| C1 | Enable live with `lots`, `max_lots_per_day` or `max_concurrent` = 0, or with no `daily_loss_cap` | 400, nothing written |
| C2 | Enable on a PAUSED deployment / a retired strategy / a disconnected or expired broker / a halted engine | 400 / 409 / 400 / 400, each with its own message |
| C3 | Enable when today's candles cannot be verified | 409 `incomplete_market_data` (the data-integrity gate; intentional, fail-closed) |
| C4 | Enable after 15:00 IST | Succeeds; live mode does not expire. The row's `governor.authorization.reason` reads `after_entry_cutoff` for the rest of that day (then `before_market_open` until 09:15, or `market_closed_today` on a non-trading day) |
| C5 | Disable | Back to paper; open positions untouched and still guarded |
| C6 | Stop | Flattens, returns to paper and PAUSED |
| C7 | Stop-all / kill switch | The live deployment is listed in `disarmed_live_deployment_ids` (Stop-all) or `stop_all.disarmed_deployment_ids` (kill) and ends paper + PAUSED. An empty list while a live deployment exists: stop immediately. |
| C8 | After a kill | The deployment does not re-enter on the next confirmed signal |

Live mode persists across sessions: end a live day by explicitly disabling it.

### 8b. For an actual fill, reconcile

| ID | Evidence | Binary rule |
|---|---|---|
| L1 | Broker identity | `cid`, `norenordno`, `noren_tsym` and `exch` are non-empty and map to one broker order. |
| L2 | Entry economics | `entry_slippage = entry_fill_price - entry_ref_price`; zero slippage is valid. Quantity equals one resolved broker lot. |
| L3 | Open risk | `marked_at` and finite `unrealized_pnl` advance while the broker position is open. |
| L4 | Protection | The software guard holds the position. The broker OCO is off by default (`LIVE_BROKER_OCO_ENABLED=0`): `oco_al_id` null and a "no broker net" chip is the PASS condition. If the OCO is enabled, it must be independently visible as resting; accepted is not resting. |
| L5 | Exit truth | Order acceptance is not flat. Finalize only after broker position, exit order, guard (and any OCO) and journal agree. |
| L6 | P&L truth | The guard journals its close at the last-seen broker mark — an **estimate**. Compare the stored exit price with the broker's fill (`avgprc` / trade book); label a mismatch `ESTIMATED_EXIT`, not PASS. |
| L7 | Charges | Charges and net P&L are valid only against the stored exit basis; broker-fill verified only when L6 confirms the fill. |
| L8 | Account caps | The decision uses a fresh finite mark and counts all known or indeterminate exposure. Until the durable execution ledger (E1) exists, a broker-accepted-but-unjournaled crash window remains an explicit limitation. |

### 8c. Live-only 5B checks (only if a premium-momentum deployment is live)

Proven only on a one-lot live day: guard-driven per-leg exits with confirmed-flat finalize;
lazy reversal arming off a real guard STOP-class exit (a restart-recovered entry at the default
stop must NOT arm it); the `exit_time` square; and restart recovery's symbol join (broker order
book `norenordno → tsym`, `fa1432c`), exercised by a backend restart with the live position open.

Do not manufacture a lost ACK, kill-switch event or connection failure against a live broker.
Those need offline fault injection or a separately authorized drill.

## 9. Abort and stand-down rules

Any unexpected exposure, duplicate broker order, missing software guard, absent claimed OCO,
incomplete recovery, unexplained candle gap, non-finite cap value or stale authorization blocks
the next phase. The observer records evidence and stops; it does not attempt a repair.

At stand-down the operator confirms the broker account flat, no pending or rejected close, no
orphan OCO, all app live rows reconciled, deployments paper/paused (disable any live one) and
the machine-level autoplace switch back to `0`. If application and broker disagree, manage risk
from the broker terminal first and preserve evidence.

## 10. What one session cannot prove

- Offline tests cannot prove Flattrade's `remarks`, order-book completeness or wire behavior;
  compare live responses with the decoded Pi reference (`docs/Resources/flattrade-pi-api/`).
- A session with no relevant signal does not validate the order path.
- One round trip does not prove a strategy edge or general reliability.
- Lost-ACK handling must not be induced against the live broker (the manual ticket's
  transmission-unconfirmed branch is checklist stage D1, operator-run).
- Ownership refusal needs an independently held position and separate authorization.
- The accepted-before-`live_trades` crash gap is not closed by careful observation; it needs the
  durable execution episode ledger (E1, [`AUTONOMY_DEVELOPMENT_PLAN_2026-08.md`](AUTONOMY_DEVELOPMENT_PLAN_2026-08.md)).

## 11. After the session

1. Write a timestamped outcome table with one result-vocabulary value per target (U, LV, A, P,
   C, L IDs).
2. Reconcile broker fill/order IDs to redacted app IDs without committing account data.
3. Add every defect as a failing behavioral regression before its fix.
4. Update `learning_log.md`, `docs/HANDOFF.md` and `docs/AGENT_TODO.md` with what was actually
   runtime-verified and what remains unverified.
5. Do not promote capital because this run passed. Profitability needs independent,
   cost-adjusted out-of-sample and forward evidence
   ([`forward-validation-policy.md`](forward-validation-policy.md)).
