# Live-market validation plan — Monday, 2026-08-17 (IST)

**Updated 2026-08-15.** This is the controlling runbook for the next market
session. It replaces the stale 2026-08-11 counts and claims in the prior version,
and should be read with [`live-readback-checklist.md`](live-readback-checklist.md).

The target date is Monday, 2026-08-17. It is absent from the NSE's
[published 2026 F&O holiday list](https://nsearchives.nseindia.com/content/circulars/FAOP71777.pdf).
If NSE announces an extraordinary closure, record the run as `NO_SESSION` and do
not reinterpret missing activity as a product failure.

## 0. Decision and safety posture

Monday is an **evidence session, not a feature-development session**. Scheduled
Codex jobs are read-only observers. They may read application endpoints, logs and
Mongo state, but they must not:

- call Flattrade MCP `login` or `logout`;
- place, modify, cancel or square an order through any interface;
- change `LIVE_AUTOPLACE_ARMED`, a deployment status or a deployment mode;
- invoke Stop, Disable, kill switch, recovery POSTs or service restarts;
- edit code or configuration during the session.

OAuth, process restarts, live enablement, arming, Stop/Disable/kill actions and all
real-order decisions belong to the operator. Connecting Flattrade is not authority
to trade.

**Confirmed baseline from 2026-08-15:** 4,896 tests passed, 4 xfailed, 0 failed;
the services were healthy; Mongo held two CLOSED live-trade rows, zero OPEN
live-trade rows, zero live deployments and two ACTIVE paper deployments.

**Confirmed warning:** the running backend reported `LIVE_AUTOPLACE_ARMED=1`. That
value cannot transmit by itself: a deployment must also be ACTIVE, in live mode,
broker-connected, before the 15:00 entry cutoff and pass the downstream gates. Zero
live deployments prevented transmission at the snapshot. The pre-open gate therefore
requires **zero live-mode deployments**. `LIVE_AUTOPLACE_ARMED=0` is recommended
defense-in-depth for paper-only work, but `1` is a warning rather than a blocker. If
it remains `1`, enabling one deployment live can make the next eligible signal
transmit without another prompt.

Stop the session immediately if an unintended order appears, broker and app
disagree about exposure, an unknown/empty broker response is interpreted as flat,
a non-finite money value appears, or a halt fires without a measured breach.
Broker state is the exposure truth.

## 1. Result vocabulary

Every target receives exactly one outcome:

- `PASS` — the named binary check was exercised and satisfied.
- `FAIL` — it was exercised and contradicted the expected behavior.
- `NOT_EXERCISED` — its triggering event never occurred, such as no paper signal.
- `BLOCKED` — a prerequisite failed, so downstream work did not run.
- `NO_SESSION` — the exchange did not conduct the expected session.

No signal and no trade are never silently called passes. A read-only observer
does not repair, restart or manufacture an event in order to obtain evidence.

## 2. Before Monday

1. Freeze the exact build that Monday will validate. Do not rush the execution
   episode ledger or a broad strategy-engine refactor into this baseline.
2. Run the full backend suite and the relevant frontend checks after the final
   pre-session code change. Rebuild images because the backend image bakes code in.
3. Confirm zero live-mode deployments. Optionally set `LIVE_AUTOPLACE_ARMED=0` as
   defense-in-depth; this is not required when every deployment remains paper.
4. Prepare two ACTIVE paper deployments that together cover NIFTY and either
   BANKNIFTY or SENSEX. At least one should have an exit time early enough to
   observe during the session.
5. Use an ignored local evidence directory:
   `tmp/live-validation/2026-08-17/`. Do not commit raw broker books, account data,
   tokens or order IDs.

## 3. Dated job schedule

| Time (IST) | Job | Authority | Binary completion test |
|---|---|---|---|
| 08:20 | Exact-build start | Operator | Backend health reports DB OK, frontend returns HTTP 200, and the running revision/build matches the frozen checkout. |
| 08:30-08:40 | OAuth | Operator | Upstox is connected before readiness runs. Flattrade OAuth is needed only for broker readback or the optional live phase, and must use AlphaForge only. |
| **08:50** | **LV-1 pre-open gate** | Scheduled read-only | Latest readiness row is for `2026-08-17`, `ready:true`, Upstox connected, warehouse pending actions is an explained integer, and no deployment is live. `LIVE_AUTOPLACE_ARMED=1` produces a prominent warning, not failure, while that condition holds. |
| **09:25** | **LV-2 opening data gate** | Scheduled read-only | Feed state is LIVE; stream, roller and exit monitor run; closed one-minute candles advance; each intended index has no unexplained leading or internal gap. |
| 09:25-10:55 | LV-3 paper continuity | Observer/read-only | Each instrument's deployment evaluates when that instrument advances; any open paper row's `updated_at`, `last_price` and finite `unrealized_pnl` advance across two reads. |
| **11:05** | **LV-4 restart decision** | Scheduled read-only | Account is flat, no live deployment, no unexpected working order/GTT, guard count zero and paper evidence so far is clean. The job reports `SAFE_TO_RESTART` or `DO_NOT_RESTART`; it never restarts. |
| 11:10 | One backend restart | Operator, optional | Only after `SAFE_TO_RESTART`. One deliberate restart is enough. Do not restart with live exposure. |
| 11:13-11:20 | LV-5 restart recovery | Read-only after operator restart | Health returns; feed/roller/exit monitor recover; the paper row continues marking; recovery is complete; no broker position is adopted from an empty account; premium locks do not falsely complete. |
| **11:45** | **LV-6 live-readback gate** | Scheduled read-only | Produces `LIVE_ELIGIBLE_FOR_OPERATOR_DECISION` only if LV-1 through LV-5 are PASS, broker reads agree with app exposure, caps are finite, one-lot policy is visible, and there is no unresolved order episode. It never enables live. |
| 12:00-14:30 | Optional one-lot readback | Operator only | Run only after a fresh in-session operator authorization. The agent observes and reconciles; it never causes an order. |
| Configured exit time + 60 s | LV-7 clock exit | Read-only | An eligible paper row closes with the configured clock reason even when the latest option tick is stale. No eligible row means `NOT_EXERCISED`. |
| 15:02 | LV-8 cutoff/EOD | Read-only | No new live entry after 15:00; non-overnight paper rows close through the scheduled path; any unexplained OPEN row is `FAIL`. |
| **15:45** | **LV-9 final evidence** | Scheduled read-only | Spot session coverage is 375/375 for NIFTY, BANKNIFTY and SENSEX; broker/app exposure agree; no unexpected working order or GTT remains; every target has an explicit outcome. |

The scheduled jobs are intentionally sparse. Broker reads occur at the pre-live
gate, around an actual live event, and at final reconciliation—not every few
seconds—because the broker rate budget is shared with AlphaForge.

**Created 2026-08-15:** `alphaforge-lv-1-pre-open-gate` (thread heartbeat), plus
standalone local observers `alphaforge-lv-2-opening-data-gate`,
`alphaforge-lv-4-restart-decision`, `alphaforge-lv-6-live-readback-gate` and
`alphaforge-lv-9-final-evidence`. The app permits one heartbeat per task, so later
checkpoints are separate local project jobs. All five are one-time ACTIVE schedules
and carry the read-only authority boundary above.

## 4. Read-only evidence surfaces

Prefer AlphaForge's own routes and Mongo projections over the Flattrade MCP:

- `/api/health`, `/api/upstox/status`, `/api/flattrade/status`;
- `/api/live-feed/health`, `/api/live-exit-monitor/status`;
- `/api/live-broker/arm-state`, `/positions`, `/orders`, `/gtt`, `/reconcile`;
- `/api/live-broker/guard-status`, `/recovery-status`, `/blotter`, `/greeks`;
- `/api/deployments/overview`, paper trades and paper open positions;
- the latest `preopen_readiness`, `candles_1m`, `paper_trades`, `live_orders` and
  `live_trades` rows, with secrets and account identifiers redacted from evidence.

Endpoint names should be confirmed against the running OpenAPI document before the
session; a 404 caused by route drift is a `BLOCKED` observer, not evidence about the
underlying trading behavior.

## 5. Phase A — paper and liveness

The paper phase validates machinery without capital risk.

| ID | Claim under test | Pass evidence |
|---|---|---|
| A1 | The open is captured | The first closed minute is present and subsequent `ts` values advance without an unexplained gap. |
| A2 | Instrument-independent wakeup works | Advancing SENSEX or BANKNIFTY while NIFTY is unchanged advances the matching deployment's evaluation state. |
| A3 | Paper marking is live | An OPEN row's `updated_at`, `last_price` and finite `unrealized_pnl` change across two observations. Paper does not use live trade `marked_at`. |
| A4 | Clock exits survive a stale option tick | The time-driven exit closes using the last known premium or explicit no-price fallback and labels any estimate honestly. |
| A5 | 15:00 scheduler wiring works | The scheduled square-off runs without the prior misplaced-argument `TypeError` and honours `allow_overnight` only for the EOD sweep. |
| A6 | Risk supervisor is observable | A future status surface shows cycles advancing and the last verdict/error. Until that telemetry exists, Monday can only mark this `NOT_EXERCISED`; absence of a halt with zero live deployments is vacuous. |
| A7 | Option-session boundary is correct | An eligible overnight paper position remains marked/protected until 15:40, not 15:30. This is a known source-level defect until the supervisor clock is split and regression-tested. |

Do not deliberately stop the Upstox stream during the baseline session. Natural
degradation may be observed, but an automated job must not create it.

## 6. Phase B — operator restart, paper only

The restart is optional and operator-owned because startup reconciliation mutates
internal state and may cancel a broker-confirmed orphan OCO. Run it only after the
flat-account precheck.

After one operator restart, require health, feed, candle roller, exit monitor and
paper marking to recover. `maybe_run_live_recovery` must report complete when broker
books are readable. A conditional orphan-repair log is not required when there was
nothing to repair. Empty broker positions must remain `UNKNOWN` if the read itself is
unreadable; they may be treated as flat only after an authenticated successful read.

## 7. Optional one-lot live readback

This phase is optional and cannot be initiated by a scheduled job. Its purpose is
operational truth, not profitability.

Before the operator enables one deployment, verify all earlier gates, long-only one
lot, `max_concurrent=1`, `max_lots_per_day=1`, a finite daily loss cap, no overnight
permission, current exit preview, registered static IP and independent broker-terminal
access. If `LIVE_AUTOPLACE_ARMED` is changed to `1`, treat live enablement as immediately
consequential.

For an actual fill, reconcile the following:

| ID | Evidence | Binary rule |
|---|---|---|
| L1 | Broker identity | `cid`, `norenordno`, `noren_tsym` and `exch` are non-empty and map to one broker order. |
| L2 | Entry economics | `entry_slippage = entry_fill_price - entry_ref_price`; zero slippage is valid. Quantity equals one resolved broker lot. |
| L3 | Open risk | `marked_at` and finite `unrealized_pnl` advance while the broker position is open. |
| L4 | Protection | Software guard contains the position. Any claimed OCO must be independently visible as resting; accepted is not resting. |
| L5 | Exit truth | Order acceptance is not flat. Finalize only after broker position, exit order, guard/OCO and journal agree. |
| L6 | P&L truth | Current normal guard close uses the last broker mark as an **estimate** unless a confirmed exit fill is supplied. Compare the stored exit price with broker `avgprc`; label mismatch `ESTIMATED_EXIT`, not PASS. |
| L7 | Charges | Charges and net P&L are valid only against the stored exit basis. They become broker-fill verified only when L6 confirms the exit fill. |
| L8 | Account caps | The decision uses a fresh finite mark and counts all known or indeterminate exposure. Until the durable execution ledger exists, a broker-accepted-but-unjournaled crash window remains an explicit limitation. |

Do not manufacture a lost ACK, kill-switch event or connection failure against a
live broker. Those require offline fault injection or a separately authorized drill.

## 8. Abort and stand-down rules

Any unexpected exposure, duplicate broker order, missing software guard, absent
claimed OCO, incomplete recovery, unexplained candle gap, non-finite cap value or
stale authorization blocks the next phase. The scheduled observer records evidence
and stops; it does not attempt a repair.

At stand-down, the operator confirms the broker account flat, no pending or rejected
close, no orphan OCO, all app live rows reconciled, deployments paper/paused and the
machine-level autoplace switch returned to `0`. If application and broker disagree,
the operator manages risk from the broker terminal first and preserves evidence.

## 9. What Monday cannot prove

- Offline tests cannot prove Flattrade's `remarks`, order-book completeness or wire
  behavior; compare live responses to the decoded official Pi reference.
- A session with no relevant signal does not validate the order path.
- One round trip does not prove a strategy edge or general reliability.
- Lost-ACK handling must not be induced against the live broker.
- Ownership refusal needs an independently held position and separate authorization.
- The current accepted-before-`live_trades` crash gap is not closed by careful
  observation; it needs the durable execution episode ledger.

## 10. After the session

1. Write a timestamped outcome table with one result vocabulary value per target.
2. Reconcile broker fill/order IDs to redacted app IDs without committing account data.
3. Add every defect as a failing behavioral regression before its fix.
4. Update `learning_log.md`, `docs/HANDOFF.md` and `docs/AGENT_TODO.md` with what was
   actually runtime-verified and what remains unverified.
5. Do not promote capital because this run passed. Profitability requires independent,
   cost-adjusted out-of-sample and forward evidence.
