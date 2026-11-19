# Independent chart terminal review checkpoint

**Root correction checkpoint (2026-11-01):** The findings below describe the
reviewed, pre-correction implementation. All four now have corrections and
executable regressions in `test_chart_stream_leases.py`, `test_chart_terminal.py`
and `useChartData.test.jsx`. Selected combined Python check: 112 passed; chart UI:
19 passed; pure chart math/model: 18 passed; production build passes. The original
reviewer was interrupted by a usage limit before post-fix review; root reproduced
the reported boundaries and checked the fixes. Browser and market-session
acceptance are still pending. Additional derivative strike/side contradictions
are rejected. An unsubscribe delivered before a send error is re-subscribed when
execution takes ownership. REST watermark tests exercise queued old ticks and
corrected extrema across subsequent stream updates, not only pure helper calls.

Confirmed: the independent review found an execution-feed coverage defect after a failed chart subscription. Do not accept the shared-feed change until finding 1 has a regression test and correction. The other findings can misrepresent contract prices or protection. This is a review of source/offline behavior, not market-session acceptance.

Review scope: `backend/app/upstox_stream.py` diff, the ownership comparison in `backend/app/runtime.py`, `backend/app/chart_stream.py`, `backend/app/chart_positions.py`, `backend/app/routers/charts.py`; inspected consuming UI and existing broker/conditional adapters where needed to establish consequences. No application source was edited by this review. Root owns corrections.

## Ranked actionable findings

### 1. P1 — Uncertain chart subscription is promoted to execution ownership without a subscription retry

Confirmed by an isolated fake-socket reproduction using the actual manager. `acquire_chart_instrument` catches a send failure, removes the lease, but retains the key in `_session.instrument_keys` and `_chart_pending_removals`. `ensure_instruments` then adds that key to `_required_keys`, removes pending-removal state, sees no `added` key, and returns without sending. A socket that failed **before delivery** and then recovered produced:

```json
{"sent_after_recovery": [], "required_keys": ["NSE_FO|12345"], "pending_removals": [], "session_keys": ["NSE_FO|12345", "NSE_INDEX|Nifty 50"]}
```

Locations at inspection: `upstox_stream.py:695`–`703` (`ensure_instruments`) and `:762`–`768` (chart acquire failure). `runtime.py:2691` now consumes execution ownership, so the false required-key state can also make auto-follow report coverage. The socket need not reconnect immediately after a single failed send; until it does, a live execution dependency can receive no price updates.

Correction/test: preserve uncertainty until an explicit subscribe succeeds; force an idempotent `sub` when promoting a pending/uncertain key even if it is listed in session keys. Also test an unsubscribe delivered before its send raises: promotion must restore the subscription in that uncertainty case. Do not solve this by dropping required ownership or restarting the execution feed from the chart path.

### 2. P1 — Recycled derivative token can bind an expired, unparseable broker position to the current contract

Confirmed by calling `broker_matches` and `build_chart_positions` with the actual parser. Selected instrument: current November future, `NSE_FO|12345`, expiry `2026-11-24`; broker row: `NIFTY27OCT26F`, same exchange/token, quantity 50. The imported `parse_position_identity` handles option naming, not futures, so it returns no identity; `broker_matches` then accepts the token. With a fresh current-contract price of 200 and a broker old-contract price of 110/MTM 500, the resulting row says current November symbol, `fresh: true`, `pnl: 5000`, and gives no warning. The arithmetic is 500 + (200 − 110) × 50 = 5000; the contract join is wrong.

Locations at inspection: `chart_positions.py:25`–`35`. Unknown/missing symbols take the same fail-open path; BFO monthly formats without an exact parsed day also need explicit consideration. The existing expiry test only covers a successfully parsed expired option.

Correction/test: require verified derivative identity, including expiry and any available strike/side, before accepting token/key equality; support futures through a current-master identity or an exact dated broker identity. Missing identity must remain unmatched with an ambiguity warning. Add tests for expired futures, unparseable derivative symbols, and contradictory strike/side evidence as well as parsed options.

### 3. P2 — REST reconciliation can be undone by older or incompletely seeded stream candles

Confirmed two paths with actual `chart_event_stream`/`ChartCandleAccumulator`:

1. During initial history loading, the queue receives price 105 stamped four seconds before the returned REST snapshot. History returns current-bar close 120 with `as_of_ms` equal to the current clock. The accumulator starts `last_trade_ms` at zero and replays the queued older tick; the next SSE snapshot regresses close to 105. Reconnect/overflow reseeding repeats the same ordering risk. Locations: `chart_stream.py:42`–`43`, `:89`–`94`, `:121`–`127`.
2. Server accumulator seed has high 110/low 90. A later browser REST refresh corrects the same 15-minute bar to high 150/low 80. The next server tick at 112 emits high 112/low 90 because periodic browser reconciliation never updates the server accumulator. The UI full-bar merge overwrites the corrected extrema. Locations: `chart_stream.py:62`, `:115`–`122` (only overflow/reconnect reseed); consuming `useChartData.js` refresh and `onSnapshot` full-bar merge.

Correction/test: define a REST/tick ordering watermark and preserve authoritative extrema across subsequent stream updates, or reconcile the long-lived server accumulator on a bounded cadence. Tests must deliberately enqueue older ticks during the history await and inject a REST correction between two streaming ticks for a multi-minute forming candle. Checking only initial seeding and ordered ticks misses both paths.

### 4. P2 — Conditional intraday protection attaches to carry-forward broker positions

Confirmed with a broker row `prd: "M"` and an active conditional live order for the same contract. `build_chart_positions` attached that order's stop 95 and target 130 to `broker:...:M`. The conditional broker adapter actually transmits `prd="I"` (`backend/app/conditional/broker.py:224`), so those levels belong to a different product. The guard loop correctly checks `prd`; the conditional loop does not.

Location at inspection: `chart_positions.py:134`–`142`. Correction/test: match conditional protection to its actual execution product, resolved exchange/symbol, and compatible position direction/quantity context. Add two broker rows for the same contract with products I/M and assert that the conditional plan attaches only to the intended exposure.

## Executed checks and observed boundaries

Confirmed: this command reported **47 passed**:

```powershell
.venv/Scripts/python.exe -m pytest -q tests/test_chart_stream_leases.py tests/test_upstox_stream.py tests/test_upstox_stream_health.py tests/test_live_option_universe.py tests/test_premium_pin.py tests/test_conditional_adapters.py
```

Confirmed by separate inline Python scripts using fake sockets and the actual run loop:

- A baseline key and a required held key were persisted; the chart-only key and an unowned foreign tick were not. Reconnection subscribed exactly the baseline and held keys after the chart lease was released.
- Last-viewer chart release sent unsubscribe for the chart key only.
- Cancellation while chart acquire was blocked in socket send left no lease; its uncertain key remained budgeted, as intended.
- Cancellation while initial history loading was blocked removed the SSE queue and chart lease and left only the baseline subscription.

Observed run-loop output:

```json
{"stored_keys": ["BASE", "HELD"], "reconnected_keys": ["BASE", "HELD"], "released_chart_was_unsubscribed": true, "foreign_tick_dropped": true}
{"leases": 0, "queues": 0, "remaining_keys": ["BASE"]}
```

Confirmed from the adapter's checked stale-book branch and its focused fixture: stale broker rows keep their frozen broker mark/MTM rather than being re-marked from fresh ticks. The failure above is the contract join, not that stale-book guard. Paper and broker quantities stay separate in the reviewed adapter. Chart routes expose GET endpoints only.

## Limits and lesson for root's learning log

Not done: live market acceptance, browser rendering, broker API acceptance, and verification of root's subsequent corrections. The tests use isolated inputs and do not touch the live account or restart services.

Critical risk: subscription ownership is desired state, not evidence that the broker received a subscription. If those are treated as equivalent after a transport failure, execution can silently lose its price feed. Confirmed approach: drive the real manager loop with fake sockets and attack failed-send promotion independently of ordinary lease release. Dead end to avoid: accepting passing lease tests as proof of uncertain-delivery correctness, or accepting an exchange token as complete derivative identity.
