# Live-market validation plan — the session that makes automation trustworthy

**Written 2026-08-11 for the next market session.** Supersedes nothing; sits alongside
[`phase5b-market-validation-runbook.md`](phase5b-market-validation-runbook.md) (the older
Phase-5B runbook) and [`live-readback-checklist.md`](live-readback-checklist.md).

> **Why this plan exists.** Between 2026-07-29 and 2026-08-11 the real-money path was
> substantially rewritten: the day-stop learned to see open risk, P&L moved onto real
> fills with charges, the guard stopped adopting positions it does not own, orphans
> self-heal at boot. **Eight of those twelve changes have never run in a market
> session.** This plan exists to exercise exactly those, in an order where each step's
> failure is cheap.

---

## 0. The one paragraph to read if you read nothing else

The app has ONE real trade in its history (2026-08-04, NIFTY 24550 PE). It recorded
`entry_price` as the pre-trade **reference** (33.35) rather than the fill (33.20),
`realized_pnl` as **null**, and closed it via `reconciled_closed` hours later. Every one
of those defects is now fixed **and none of the fixes has ever seen a real fill.** That
is the gap this session closes. Until it does, no automation claim about this app is
evidence-backed.

---

## 1. Posture and hard limits

| | |
|---|---|
| **Agent may** | read broker state, read logs, query Mongo, restart the backend, run tests, edit code |
| **Agent may NOT** | place / modify / cancel / square any order, flip a deployment to live, call the Flattrade MCP's `login`/`logout` |
| **Operator only** | every arming action, every live-enable, every real order |

**Current state going in** (verified 2026-08-11 20:45 IST):

- `LIVE_AUTOPLACE_ARMED=1` — the master switch is **ON**
- **0 ACTIVE live deployments** — this, and only this, is what prevents real orders today
- 3 ACTIVE paper deployments · `live_trades` holds 1 (closed) row
- Suite 4,573 passed / 4 xfailed / 0 failed

**Stop the session immediately if:** an order appears that nobody intended · the guard
squares a position AlphaForge did not open · `realized_pnl` or a cap value is non-finite ·
the broker book and the app's blotter disagree on an open position · any halt fires
without a measured breach.

---

## 2. Pre-open (08:30 – 09:15 IST)

1. `docker compose up -d --build backend` — the container bakes code in; a stale image
   silently invalidates the whole session.
2. Confirm in the boot log, all four:
   ```
   Pre-open readiness loop initialized (08:45 IST)
   Risk supervisor loop initialized
   Live-feed supervisor loop initialized
   Reconciled N orphaned warehouse run(s)   /   Boot reconcile: squared N paper trade(s)
   ```
3. Operator completes **Upstox** OAuth, then **Flattrade** OAuth.
4. At 08:45 the readiness doc should appear. Check it says what you expect:
   ```bash
   docker exec alphaforge_mongo mongosh --quiet alphaforge --eval \
     'printjson(db.preopen_readiness.findOne({}, {_id:0}))'
   ```
   **Binary:** `ready:true`, `checks.upstox.connected:true`, `warehouse.pending_actions`
   is a number you can explain. If Upstox is not yet connected it MUST say
   `ready:false` with `upstox_not_connected` — a green verdict on a disconnected feed is
   a bug in the check itself.
5. **Roller green before 09:15.** This is the headline regression: on 2026-08-04 it
   started at 10:26 and the morning was structurally dead.

---

## 3. Phase A — paper, observational (09:15 – 11:00)

No live deployment. Everything here validates machinery that needs an *open position*,
at zero capital risk.

| # | What it proves | Binary check |
|---|---|---|
| A1 | Candles roll from the open | `candles_1m` latest `datetime` advances every minute from 09:15 |
| A2 | A paper position opens | a `paper_trades` row with `status:"OPEN"` |
| A3 | **Position marking** *(never runtime-verified)* | on that row, `unrealized_pnl` and `marked_at` **change between two reads ~30s apart** |
| A4 | Stale-mark honesty | stop the Upstox stream briefly; `marked_at` stops advancing and the entry path refuses with `exposure_unknown` rather than summing a stale number |
| A5 | Paper exits fire on the clock | a deployment with `exit_time` squares at that time, not at 15:00 |
| A6 | Risk supervisor is quiet | log shows the loop running, no halt, no pause, with 0 live deployments |

**A3 is the single most important check in Phase A.** Everything the day-stop does rests
on that field being written.

---

## 4. Phase B — the restart test (11:00 – 11:30)

**With a paper position still open**, `docker compose restart backend`. Within ~2 minutes:

- health OK; roller resumes without a manual poke
- the open paper trade keeps being marked (blotter columns move again)
- **guard adopts only what AlphaForge owns** — with a flat broker book it must adopt
  NOTHING and log `SKIPPING … no AlphaForge entry order`
- `live startup recovery … status=ok` (on 2026-08-04 this looped `INCOMPLETE` for 30+ min)
- `premium_locks` untouched; no `exited_while_down` for a paper position

---

## 5. Phase C — one lot, real money (12:00 – 14:30, operator-armed)

**Only after A and B are clean.** This is the only way to validate the fill/charges chain.

**Operator actions:** enable live on ONE deployment, `lots: 1`, tightest caps, then watch.

| # | What it proves | Binary check on the `live_trades` row |
|---|---|---|
| C1 | Broker-space identifiers reach the journal | `noren_tsym` and `exch` are **present and non-empty** |
| C2 | **True entry fill captured** | `entry_fill_price` present; `entry_ref_price` ≠ it; `entry_slippage` is the difference |
| C3 | **Open risk is visible** | `unrealized_pnl` and `marked_at` advance while the position is open |
| C4 | Blotter joins correctly | the Live page shows the position as **at broker** with a live LTP (before the fix this always read not-held) |
| C5 | **Charges journalled** | on close: `total_charges` > 0, `charges` breakdown present, `net_realized_pnl` = `realized_pnl` − `total_charges` |
| C6 | P&L measured from the fill | `realized_pnl` = qty × (exit − **`entry_fill_price`**), not the reference |
| C7 | Exit reason is honest | `exit_reason` names the real cause — NOT `reconciled_closed` |
| C8 | Day-stop can act | with a real open position, `check_live_caps` returns a decision based on a **fresh** mark |

**Compare every figure against the Flattrade order book independently** — that is the
whole point. `docker exec` a read of `/api/live-broker/orders` and reconcile
`avgprc` against `entry_fill_price`.

---

## 6. Phase D — the deliberate failure drills (14:30 – 15:00)

Cheap to run, and they exercise paths that only appear when something breaks.

| Drill | How | Expected |
|---|---|---|
| Kill switch | operator hits it with a live position open | position flattens; deployment PAUSED; response lists the deployment id — **an empty list with a live deployment present is a stop-the-session bug** |
| Stop vs Disable | operator uses each | Disable → back to paper, position untouched. Stop → flatten + paper + PAUSED |
| Entry cutoff | observe after 15:00 IST | live strip stops counting the deployment as able to transmit (`after_entry_cutoff`) |
| EOD square | 15:00 | paper squares via the paper path; live squares via the guard |

---

## 7. Evidence to capture (do this as you go, not after)

```bash
docker logs alphaforge_backend --since 8h > /tmp/session.log
docker exec alphaforge_mongo mongosh --quiet alphaforge --eval \
  'db.live_trades.find({}).forEach(t => print(JSON.stringify(t)))' > /tmp/live_trades.json
```
Plus screenshots of: the readiness verdict, the Live cockpit with a position open, the
blotter row, and the final closed row with charges.

**A silent non-event is a finding.** No lock by reference time; no mark for 60s; a trigger
that never fired despite a visible qualifying move — capture the timestamp and the logs.

---

## 8. What this session CANNOT prove

State it plainly in whatever write-up follows:

- **Lost-ACK handling** (`be04cca`) needs a network failure mid-POST. Do not manufacture
  one against a live broker.
- **The ownership boundary** needs an *open* position AlphaForge does not own. The
  2026-08-11 attempt could not verify it because the book was flat — `netqty=0` on every
  row. If the operator holds a manual position, that is the moment to check the guard
  refuses it.
- **Sizing parity.** Paper replays the pinned sizing policy; live uses a flat
  `risk.live.lots` **by design**. So a paper cohort's rupee figures — drawdown, ruin, the
  promotion checks — are produced at a lot count live will not use. This is an open
  **policy decision for the operator**, not a bug, and it means Phase A is not a
  rupee-accurate rehearsal of Phase C.

---

## 9. After the session

1. Record every finding in `learning_log.md` — one core lesson, confirmed approaches,
   dead ends.
2. Update `docs/HANDOFF.md` §2 with what is now runtime-verified vs still assumed.
3. Anything that failed becomes a red test **before** it becomes a fix.
