# Quick Trade independent failure review — 2026-10-25

Confirmed: the existing Quick Trade guard cannot preserve the user's exit contract across a backend restart (source inspection plus isolated execution below). This is a Sev-1 blocker to reusing it unchanged for durable conditional orders. No application source, broker state, or live order was changed during this review.

## Findings

| Severity | Finding and evidence | Required correction |
|---|---|---|
| Sev-1 | **Restart loses the stop, target, and trailing ratchet.** `backend/app/routers/live_broker.py:367` constructs a monitor and `:375` registers it in memory. `backend/app/live/session_store.py:75` persists only entry/SL IDs, status, heartbeat, symbol, and exchange. `backend/app/live/live_position_guard.py:1909` chooses recovery-time LTP before average fill, and `:1934` reconstructs a default 50% stop, no target, no trail. Executed reproduction below changes an already locked stop of 105 to 50 at current premium 100; no exit is requested. | Persist exact order-specific fill basis, original exit plan, high-water mark, current stop, activation state and exit-order state. Restore them before trading/monitoring resumes; reconcile broker state first. Never silently substitute a looser stop. |
| Sev-1 | **No broker-held stop on the manual path; loss of backend or authenticated broker access leaves protection unavailable.** `_make_arm` at `routers/live_broker.py:356-394` only registers the monitor and writes `sl_norenordno=None`; it does not place a protective sell. On session expiry, `live_position_guard.py:743-748` records an error and returns without a square. The executed expiry reproduction confirms zero exit calls and one retained registry row. | Capability/readback-tested broker protection where possible; otherwise explicitly disclose software-only protection and add an independently supervised heartbeat/alert path. Default flatten can be attempted while credentials/connectivity are usable; it cannot guarantee flattening through an invalid broker session or a dead process. Do not claim otherwise. |
| Sev-1 for the requested fill-based contract | **Static SL/target use reference premium, not actual entry fill.** `_make_arm` passes `ref_ltp` at `routers/live_broker.py:367-382`. `live_position_guard.py:821-851` records the position-book average and changes `state.entry` for trailing calculations but deliberately leaves initial stop/target unchanged. Parent owns the executable manual-flow drift reproduction. | Initialize the plan from proven order-specific filled quantities/prices. Define partial-fill updates and monotonic ratchet semantics before entry, with protection on each filled slice. Position-day average is not sufficient if multiple entries share a contract. |
| Sev-2, downstream risk-accounting concern | **Existing journal close can calculate P&L on ordered rather than filled quantity.** `backend/app/runtime.py:397-403` calls `close_live_trade` without quantity; `backend/app/live/close_loop.py:83-84` falls back to journal quantity. Source evidence only in this review. | Reconcile per-order fills and pass actual closed quantity and fill prices. Parent must decide whether this is in scope for a bounded Sev-1 fix; do not reinterpret the existing journal as fill evidence. |

## Executed evidence

Repeatable reproduction is now saved as `tests/test_quick_trade_recovery_audit.py`; run `.venv/Scripts/python.exe -m pytest tests/test_quick_trade_recovery_audit.py -q -s -p no:cacheprovider`. Confirmed: **4 passed in 0.66s**, with raw output in `docs/quick-trade-audit/recovery-reproduction.log`. The test additionally asserts the original locked plan **would exit** at the recovery premium, while the recovered plan does not. Network connections are blocked during scenarios. A fresh registry models lost process memory; no actual process or database was restarted.

The reproduction used the actual `LivePositionGuard.rehydrate_from_broker` and `_cycle`, `build_monitor_state` and `evaluate_exit`, with injected fake broker snapshots and recorded exit callbacks. The symbols below are deliberately synthetic labels; quantity 1 is a test fixture, **not** a valid exchange lot claim. This tests recovery semantics, not instrument-master or broker acceptance.

For each symbol: create entry premium 100, stop 10%, target 20%, lock activation premium 110, locked profit 5; evaluate premium 110 so the old stop is 105. Create a fresh registry (simulated process restart), expose a proven-owned held position at premium 100, rehydrate and run a guard cycle.

```jsonl
{"case":"restart","symbol":"NIFTY_AUDIT_CE","original_stop":105.0,"recovered_stop":50.0,"original_target":120.0,"recovered_target":null,"original_mode":"lock","recovered_mode":"none","current_premium":100,"exit_calls":0}
{"case":"restart","symbol":"BANKNIFTY_AUDIT_CE","original_stop":105.0,"recovered_stop":50.0,"original_target":120.0,"recovered_target":null,"original_mode":"lock","recovered_mode":"none","current_premium":100,"exit_calls":0}
{"case":"restart","symbol":"SENSEX_AUDIT_CE","original_stop":105.0,"recovered_stop":50.0,"original_target":120.0,"recovered_target":null,"original_mode":"lock","recovered_mode":"none","current_premium":100,"exit_calls":0}
{"case":"expired_session","exit_calls":0,"registered":1,"last_error":"token expired — reconnect Flattrade"}
```

All assertions passed in the final reproduction. An initial expiry fake supplied two positional arguments to the one-argument `BrokerReadError` constructor; that fixture produced a TypeError and was discarded as expiry evidence. The corrected fake raises `BrokerReadError('Session Expired : Invalid Session Key')` and explicitly asserts the reconnect message.

Existing isolated test command executed:

```text
.venv/Scripts/python.exe -m pytest tests/test_live_guard_failopen.py tests/test_guard_ownership_boundary.py tests/test_executor_lost_ack.py tests/test_live_idempotency.py tests/test_live_guard_tick_primary.py tests/test_guard_known_book_ageout_race.py -q -p no:cacheprovider
107 passed, 1 warning in 4.31s
```

The warning was the installed Starlette multipart pending-deprecation warning. Passing tests establish their checked behaviours; they do not certify a live session or the missing durable contract.

## Existing safeguards and their limits

- Confirmed: atomic intent claim and lost-ack halt are exercised by the selected idempotency/lost-ack tests. `executor.py:165-205` rechecks `engine.can_trade`, reserves the intent before transmit, and marks an exception indeterminate while halting. This is a useful primitive, not a complete conditional-order lifecycle.
- Confirmed: pending-cancel/fill races remain guarded in the selected age-out tests; cancellation acceptance is not treated as proof of no fill.
- Confirmed: stale/missing Upstox ticks fall back to broker position-book LTP (`live_position_guard.py:1471-1501` and selected tick-primary tests). The fast tick path stands down when its broker snapshot is stale (`:1530`). This is not a default stale-feed flatten policy.
- Confirmed by source: `runtime.py:1299-1395` risk supervision only halts/pauses entries and skips when no active live deployment exists; it does not independently flatten a Quick Trade. The feed supervisor at `:1447-1514` restarts feeds/monitors and retries recovery inside the same backend process. `docker-compose.yml` defines Mongo, backend, and frontend, with restart policies but no independent watchdog service. This search does not establish whether an external operator-managed monitor exists outside the repository.
- Confirmed by source: live kill trips the persistent live safety latch, halts the shared entry engine, and disarms live deployments before broker reads (`routers/live_broker.py:2535-2594`). `LiveEngine.can_trade` checks in-memory halt, persisted halt, and latch (`engine.py:360-367`). The live config store uses `live_safety_config` (`kill_switch.py:1398`). The paper-only stop route explicitly performs no live latch/halt (`routers/deployments.py:1232-1254`). There is no conditional-order engine here to verify against a distinct paper latch, and paper stop-all does not establish one.

## Review decision and gaps

Likely: extending the current guard unchanged would violate the proposed durable exit contract, because the executed restart path discards the exact state the new contract promises to retain. Parent must independently review this blocker and apply the user's audit checkpoint before any Part B coding.

Not verified: live broker order acceptance, real exchange tick/lot/freeze metadata, live outage recovery, any out-of-repository watchdog, and actual DB crash/restart. Those were intentionally outside this isolated read-only review. No fix was applied.

Core lesson: restart recovery must be tested against the pre-crash protection state, not merely against whether a position is registered again. Confirmed approach: injected broker snapshots plus the real recovery/guard methods exposed the weakened stop without any live IO. Dead end to avoid: treating retained registry visibility or a passing generic recovery test as preservation of the original trading contract.
