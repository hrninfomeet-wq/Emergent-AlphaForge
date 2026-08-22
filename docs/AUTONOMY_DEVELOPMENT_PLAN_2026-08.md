# AlphaForge autonomy development plan — offline work after the 2026-08-15 audit

**Decision:** the next substantial development is a **durable live execution
episode ledger with a fail-closed admission fence**. Do not add another strategy
plugin first. The immediate pre-session hardening items are smaller: split the
15:30 spot-feed clock from the 15:40 option-exit-monitor clock, and expose a
read-only risk-supervisor heartbeat.

This order serves the autonomy goal: the app must first know, durably and after a
restart, what money-moving action it attempted, what the broker accepted, what
exposure exists, and what protection/exit plan owns that exposure.

## 1. Confirmed gap

The current live flow has two durable records separated by a broker side effect:

1. `live_orders` stores an order intent and transitions it to `SUBMITTING`.
2. The executor calls the sole broker `place_order`, stores the broker order ID,
   and registers the in-memory software guard.
3. Only after the executor returns does `auto_live` insert the `live_trades`
   exposure row and link the signal.

A database failure or process death after broker acceptance but before the
`live_trades` insert can therefore leave a real position without the row used by
account/deployment cap calculations. Startup recovery can adopt a broker order by
`remarks == cid`, but it does not create the missing trade projection or restore
the exact exit plan for a generic deployment; generic recovery explicitly uses a
catastrophe fallback stop when the original plan is unavailable.

This is more severe than a missing dashboard or strategy type because the wrong
version can admit more capital while the app's own exposure count is incomplete.

## 2. Architecture direction

Use one durable execution-episode document as the source of truth and treat
`live_trades` as its idempotent reporting projection. A one-document state machine
avoids depending on multi-document Mongo transactions.

Suggested primary states:

`PLANNED -> SUBMITTING -> BROKER_ACCEPTED -> OPEN_CONFIRMED -> EXITING -> CLOSED`

Exceptional reconciliation:

`SUBMITTING -> RECONCILING -> BROKER_ACCEPTED | TERMINAL_NO_ORDER`

Protection, projection and recovery health are orthogonal fields, not inferred from
the primary state:

- `protection.software_guard`: `PENDING | ACTIVE | FAILED | RELEASED`
- `protection.oco`: `NOT_APPLICABLE | PENDING | RESTING | REJECTED | RELEASED | UNKNOWN`
- `projection.live_trade_id` and `projection.state`
- `recovery.status`, last broker evidence and last error

The pre-transmit record freezes:

- unique `cid`, signal, deployment and session identity;
- exact order intent, broker-space contract, expected lots/quantity and reference premium;
- resolved exit plan plus a canonical hash;
- account/deployment cap reservation;
- authorization version/fingerprint and final transmit-fence evidence;
- timestamps for every compare-and-swap transition.

## 3. Non-negotiable invariants

1. Only the executor can claim `PLANNED -> SUBMITTING` and call `place_order`.
2. The existing fresh authorization fence remains mandatory. The ledger never
   enables live, resumes a deployment, resets a latch or manufactures consent.
3. Every unresolved, accepted, partially filled or open episode consumes a
   worst-case account and deployment slot. Unknown exposure blocks new entries but
   never blocks exits.
4. Recovery never resends from an inconclusive book. It first proves whether a
   broker order/position exists using authenticated order, trade and position books.
5. Every broker-accepted episode produces exactly one `live_trades` projection.
   Add partial unique indexes for non-empty `cid` and `norenordno` after auditing
   legacy rows.
6. A known episode restores its exact exit plan. Corrupt/unreadable plan data blocks
   new entries and raises an operator-visible recovery condition; it must not
   silently degrade to the generic fallback.
7. Accepted is not filled, and exit submitted is not flat. Episode transitions use
   confirmed broker state.
8. Exit order identity and confirmed average fill belong in the episode. Final
   charges and realized P&L use the confirmed exit fill; a mark remains explicitly
   estimated until then.
9. All transitions and projections are compare-and-swap and idempotent. Recovery
   cannot duplicate an entry, OCO, exit or trade row.

These patterns align with two mature-engine comparables:
[NautilusTrader](https://nautilustrader.io/docs/latest/how_to/configure_live_trading/)
performs startup and continuous order/position reconciliation in a single live node,
while [QuantConnect](https://www.quantconnect.com/docs/v2/writing-algorithms/live-trading/brokerages)
requires live order state to be synchronized after brokerage reconnection.
AlphaForge additionally needs its local-first projection and explicit operator
authorization semantics.

## 4. Bounded work packages and binary checks

### P0 — pre-Monday hardening

1. Split the supervisor's clocks: spot stream/roller uses the 15:30 cash close;
   paper option exit monitoring uses the date-aware 15:40 option close.
   **Done when:** a behavioral supervisor regression at 15:35 keeps the exit monitor
   running while the spot roller is stopped, and boundary tests pass at 15:29,
   15:30, 15:39 and 15:40.
2. Add a read-only risk-supervisor status snapshot: running, cycles, last cycle,
   last verdict, last error and exposure-unknown.
   **Done when:** a driven loop test observes cycles advance, an injected DB error is
   visible without killing the loop, and the status route performs no write.

P0 is small enough to land before Monday only if it gets red-before-green regressions,
the full suite, image rebuild and an adversarial review. Otherwise keep Monday's
baseline frozen and mark the affected checks unverified.

### P1 — durable episode schema and admission reservation

- Add the episode collection/model, indexes, canonical exit-plan hash and CAS helpers.
- Create and reserve the episode before any broker POST.
- Make account/deployment caps count nonterminal episodes conservatively and dedupe
  their `live_trades` projections.

**Done when:** two concurrent entries with a cap of one yield one reservation and
one broker call; a nonterminal episode blocks a later entry; legacy duplicate/null
IDs do not break index creation.

### P2 — executor transitions and idempotent projections

- Persist broker acceptance, protection outcome and projection identity before
  returning a successful placement to `auto_live`.
- Make `live_trades` and signal linkage repairable idempotent projections.
- Preserve the current lost-ACK halt and fresh authorization fence.

**Done when:** fault injection after each transition cannot create an uncounted
accepted order, duplicate projection, duplicate broker call or lost exact exit plan.

### P3 — startup and continuous reconciliation

- Reconcile every nonterminal episode against authenticated broker books.
- Materialize/repair missing projections and signal links.
- Restore the exact guard plan and relink an existing OCO without creating another.
- Surface unresolved state in readiness/live cockpit and block entries.

**Done when:** restart after ACK, guard registration, OCO acceptance, projection
failure and signal-link failure converges to one episode, one broker order, one
projection and the exact original exit plan.

### P4 — confirmed exit economics

- Track the exit order through acceptance, partial/complete fill and confirmed flat.
- Use broker average exit fill for `exit_price`, gross P&L and charges.
- Preserve estimate fields separately when the confirmed fill is not yet available.

**Done when:** normal guard close and reboot reconciliation produce the same closed
economics from the same broker fixture, and no CLOSED episode lacks a truth label for
its exit basis.

## 5. Mandatory fault-injection matrix

The offline suite must drive, not grep, these boundaries:

1. crash after `PLANNED`: zero broker calls and no automatic stale-signal resend;
2. lost ACK with `remarks=cid` found: adopt once, one broker call total;
3. complete authenticated books prove no order: terminalize without resending;
4. ACK followed by projection insert failure: next entry blocked; restart repairs one row;
5. restart after guard registration: exact exit plan restored;
6. signal-link failure: link repaired without duplicate trade;
7. OCO success followed by persistence failure: existing OCO relinked, not recreated;
8. rejected, unfilled, partially filled, cancelled and expired orders release or retain
   exposure conservatively;
9. pause/disable during pre-transmit awaits: zero broker calls after authorization stales;
10. malformed/non-finite exposure: block entry without fabricated P&L;
11. empty/unreadable broker books: remain `UNKNOWN`, never infer flat;
12. real-container Mongo index and compare-and-swap behavior matches the fake store.

Mutation checks must prove the tests fail if unresolved episodes stop contributing to
caps, projection uniqueness is removed, or exact-plan restoration is replaced by a
fallback.

## 6. Strategy and research autonomy after execution truth

The strategy sequence is:

1. **Append-only Experiment/Cohort Ledger + deterministic next-action controller.**
   Freeze hypothesis, source SHA, parameters, data content manifest, exclusions,
   friction, sizing, split boundaries, search budget/seed, objective and kill
   criteria. Name stages `selection`, `validation`, `holdout` and `forward`;
   record holdout consumption. The controller may label `live_eligible` but never
   call live enable.
2. **Real-LLM capability acceptance.** Run one ordinary and one premium-trigger
   strategy through author -> compile -> install -> backtest -> optimize -> paper on
   synthetic or selection data. Do not spend an untouched holdout to test plumbing.
3. **Unified strategy-engine adapter.** Remove the bespoke ordinary versus
   premium-native dispatch only after Monday's baseline is preserved.
4. **One pre-registered new strategy hypothesis at a time.** Add OI/option-context or
   another family only after its historical fields have causal availability and
   quality evidence. Feature count and headline P&L are not acceptance criteria.

Adding another plugin before the experiment ledger increases multiple-testing risk
without improving autonomous decision quality. No current strategy has a proven edge,
and no item in this plan makes a profitability promise.

## 7. Review, checkpoints and stop conditions

Checkpoint each package as a small green milestone:

1. red behavioral regression;
2. minimal implementation;
3. focused tests;
4. fault/mutation review;
5. full suite and container-path check;
6. update `learning_log.md`, `AGENT_TODO.md` and `HANDOFF.md`.

Stop and replan if broker acceptance cannot be made durable before projection work,
if legacy data cannot support safe partial indexes, if reconciliation needs to guess
from an unreadable book, or if the slice begins changing the operator's live-enable
authority. Do not land a partially integrated episode ledger on the Monday baseline.

## 8. Risks

- **Critical:** an accepted order without a counted episode can permit excess
  exposure. If the ledger does not become the cap source, the main risk remains.
- Conservative unresolved accounting can freeze entries during a broker outage. That
  is preferable to excess exposure; surface the exact recovery evidence required.
- Production Mongo may be standalone, so multi-document transactions may be
  unavailable. The design deliberately uses one-document CAS plus projections.
- Broad paper/live unification would enlarge the capital-risk surface. Keep this
  slice limited to the external live-order seam.
- Monday can validate broker interface behavior, but one session cannot prove
  crash-window safety or profitability.
