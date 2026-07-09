# Live guard Layer 2 — over‑sell‑safe widening re‑price

**Date:** 2026-07-09
**Status:** FINALIZED after a 4‑lens adversarial red‑team (workflow `wf_cd41c541`). The draft's core
premise ("re‑price = re‑invoke `square_position`, no new over‑sell logic") was **rejected** — it hides
two confirmed over‑sell races. This is the approved design.
**Builds on:** Layer 1 confirm‑flat (`2026-07-09-live-guard-confirmed-flat-square-design.md`).
**Area:** `backend/app/live/live_position_guard.py`, `backend/app/live/auto_square.py` (new primitive),
`backend/app/runtime.py` (wiring).

---

## 1. Problem

Layer 1 places a guard exit once at `band_pct = 1.0` and never re‑prices. On a fast crash the 1 %
marketable SELL LMT rests unfilled; the position stays protected + honestly OPEN but is never
*force*‑filled. Layer 2 escalates the exit through a widening band (`1 → 2 → 4 %`) on a bounded
interval so it clears a blown‑through market — mirroring `kill_switch.panic_squareoff_verified`,
adapted to the guard's ~1.5 s cross‑cycle cadence.

## 2. Why a naive "re‑invoke `square_position` with a wider band" is WRONG (red‑team‑confirmed)

- **Over‑sell race.** `square_position` Step 3.5 is a *binary* flat/not‑flat gate; Step 5 places
  `qty = abs(netqty)` from the **passed dict**, never re‑read after the Step 4 cancel. A partial fill
  in the cancel window → it over‑sells the already‑filled qty → opens a short. (`panic_squareoff_verified`
  re‑reads `fillshares` *after* the cancel; `square_position` does not.)
- **Empty/partial `order_book()` double‑exit.** The guard's `position` dict carries no
  `working_norenordno`, so `_cancel_all_working_for_scrip` relies on `order_book()` discovery and
  returns `cleared=True` on an empty/hiccup book — the prior resting exit is not cancelled and a
  SECOND resting exit is placed → double‑sell.
- **`band_pct` TypeError‑mask.** Threading `band_pct` through the existing `square_fn` would break all
  four test recorders (`(self, client, position, *, reason)`), and `_issue_square` swallows the
  TypeError → the square silently fails. Deployment hazard.

**Conclusion:** keep Approach **A** (cross‑cycle scheduling — non‑blocking; a synchronous verified loop
would block the single guard task ~6 s × N legs during the exact basket crash we target), but the
re‑price **step** goes through a NEW, over‑sell‑safe primitive `reprice_exit_leg` reusing
`panic_squareoff_verified`'s per‑leg logic, invoked through a **separate injected `reprice_fn`**. The
first square is byte‑identical to Layer 1.

## 3. Resolved decisions (OQ1–OQ6)

- **A vs B (OQ6):** A for scheduling + B's per‑leg pricing/sizing primitive for the one step.
- **Failed re‑price (OQ1):** stamp `square_last_ts` on every *attempt* (makes the interval a real rate
  gate); **never advance the band on failure**. Classify: `unpriced` → held, no stamp (benign);
  `cancel_unconfirmed` → place NOTHING (over‑sell‑safe), same band, retry next interval, set
  `last_error`; hard REJECT (place ok=False twice) → **STOP** re‑pricing that entry + terminal signal +
  page (RMS/margin won't cure); transport error → same band, retry.
- **Interval (OQ2):** fixed **4.0 s** (≈ 2–3 poll cycles) + a global **per‑cycle budget `K = 2`**
  (bounds a synchronized N‑leg burst; the per‑position interval alone does not de‑synchronize a basket).
- **Manual no‑OCO terminal (OQ3):** on schedule exhaustion or hard‑reject stop, set
  `reprice_exhausted` / `reprice_stopped`, a `status()` `stuck` counter, `last_error`, and a WARNING —
  a silent un‑fillable exit is the exact outcome this layer exists to prevent.
- **`already_flat` (OQ4):** advance the band ONLY on a genuine new placement (`via=="exit_order"`);
  on `already_flat` / `remaining<=0` do nothing (no band advance, no re‑stamp) — the next confirmed‑flat
  cycle finalizes.
- **Endpoint (OQ5):** keep `(1, 2, 4)`. Terminal effectiveness comes from bid‑anchoring + `lc`‑clamp in
  the primitive, not a wider raw band.

## 4. New defects the red‑team surfaced (folded into this design)

1. **`_finalize_flat` orphans the resting guard exit → naked short** (also latent in Layer 1). It
   cancels only `oco_al_id`; a guard SELL rests alongside the OCO, so if the OCO fills first the guard
   SELL is orphaned against a flat account. **Fix:** `_finalize_flat` also best‑effort cancels
   `entry["square_ordno"]` on confirmed‑flat. (Requires Layer 2's exit‑id tracking — so this fixes the
   latent Layer‑1 case too.)
2. **`square_reason` corruption:** written ONCE by the first `_issue_square`, never overwritten; the
   `_reprice` suffix is a local remarks string only (journal integrity).
3. **`exits` counter inflation:** `_stats["exits"]` counts first‑squares only; re‑prices increment a
   new `_stats["reprices"]`.
4. **Determinism:** thread the cycle's single `now` into `_issue_square` and `_reprice`; no mid‑cycle
   `now_fn()`.
5. **Post‑15:30 no escalation** (accepted/documented — can't place after hours; the 15:00 EOD square
   begins escalation before close).

## 5. Implementation

### 5.1 New over‑sell‑safe primitive — `auto_square.reprice_exit_leg(client, position, *, band_pct, prev_ordno, prev_qty, reason)`
Reuses `kill_switch._leg_price` / `_order_row` / `_normalize_status` / `TERMINAL` / `_parse_netqty`.
- **A.** Validate lp/ref BEFORE any cancel → bad lp → `{"squared": False, "reason": "unpriced"}` (no side effects).
- **B.** Cancel `prev_ordno` by id (+ discover/cancel any other working order for tsym); re‑fetch
  `order_book()` to CONFIRM none non‑terminal remain. Cannot confirm cleared (empty/raise) →
  `{"reason": "cancel_unconfirmed"}` (place nothing).
- **C.** Read `prev_ordno`'s `fillshares` post‑cancel → `filled`. Fresh `position_book()` → `book_netqty`
  (KNOWN) or UNKNOWN. `remaining`: filled‑readable + book‑KNOWN → `min(prev_qty‑filled, abs(book_netqty))`;
  filled‑readable + book‑UNKNOWN → `prev_qty‑filled` (fillshares authoritative); **filled‑UNREADABLE →
  `cancel_unconfirmed`** (cannot size → place nothing). `remaining <= 0` →
  `{"squared": True, "via": "already_flat", "remaining": 0}`.
- **D.** `get_quotes(token)` → `_leg_price` (bid `bp1` / ask `sp1` anchor, clamped `[lc, uc]`) → place
  LMT for `remaining`; retry ONCE. Accept → `{"squared": True, "via": "exit_order", "norenordno": …,
  "qty": remaining}`; two rejects → `{"squared": False, "failures": [...]}`.

### 5.2 `LivePositionGuard`
- **New per‑entry state** (`register`): `square_band_idx=0`, `square_last_ts=None`, `square_ordno=None`,
  `square_qty=0`, `reprice_exhausted=False`, `reprice_stopped=False`.
- **New constructor config:** `reprice_fn` (injected), `reprice_band_schedule=(1.0,2.0,4.0)`,
  `reprice_interval_seconds=4.0`, `reprice_max_per_cycle=2`; `_stats` gains `reprices:0`, `stuck:0`.
  Module const `_EPOCH0 = datetime(1970,1,1,tzinfo=timezone.utc)`.
- **`_issue_square`** gains a `now` param; on real+accepted sets `squaring`, `square_reason` (ONCE),
  `square_band_idx=1`, `square_last_ts=now`, `square_ordno=result.norenordno`, `square_qty=netqty`.
  Update its 5 call sites to pass the cycle `now`.
- **`_select_reprice_ids(now, by_tsym, book_is_known)`** — returns ≤ K entry ids to re‑price this cycle:
  empty on an UNKNOWN book; skips exhausted/stopped/band‑done/not‑live/interval‑not‑elapsed; sorts by
  oldest `square_last_ts` (round‑robin drain).
- **Re‑price manager** replaces the `squaring` skip: `if entry["id"] in reprice_ids: await
  self._reprice(...)` else wait; always `continue` (Layer‑1 no‑stop‑re‑eval invariant preserved).
- **`_reprice(client, entry, now, exits)`** — calls `reprice_fn` at the current band; classifies the
  result per §3 (stamp‑on‑attempt, advance band ONLY on `via=="exit_order"`, terminal signal on
  exhaustion, STOP on hard reject). NEVER raises.
- **`_finalize_flat`** — after the OCO cancel, best‑effort `cancel_order(entry["square_ordno"])`.

### 5.3 `runtime.py`
- New `_live_guard_reprice_fn(client, position, *, band_pct, prev_ordno, prev_qty, reason)` — dry‑run
  gated by `LIVE_GUARD_ARMED`; else delegates to `reprice_exit_leg`. Wire `reprice_fn=…` into the
  `LivePositionGuard(...)` construction.

### 5.4 Unchanged (deliberately)
`square_position`'s contract; the existing `square_fn` + its 4 test recorders (no `band_pct` threading).

## 6. TDD test list (assertions, in order)
1. First square byte‑identical (band 1 %, `square_band_idx==1`, `square_ordno`/`square_qty` captured).
2. Escalation timing: 2 % then 4 % once the interval elapses; not before; band/ts advance only on `exit_order`.
3. **Over‑sell race:** prev_qty 20, book 10, fillshares 10 → primitive places 0 (`already_flat`), no short.
4. **Empty‑book at re‑price:** `order_book()==[]` at cancel‑confirm → `cancel_unconfirmed`, places nothing, `last_error`.
5. Hard‑reject: repeated failures → attempts==1 (STOP), `reprice_stopped`, no spam over 12 cycles.
6. `cancel_unconfirmed` → same band retried after the interval, ts stamped.
7. `unpriced` → held, ts unchanged, `reprices` not incremented.
8. `already_flat` mid‑re‑price → no band advance, no re‑stamp; next cycle finalizes once.
9. Empty‑book UNKNOWN holds a squaring entry (no re‑price, OCO intact, state unchanged).
10. K‑budget: 6 eligible legs, K=2 → exactly 2 re‑prices/cycle (oldest‑ts first), rest next cycles.
11. Schedule exhausted → `reprice_exhausted`, `status().stuck>=1`, `last_error`, no further calls.
12. Finalize cancels BOTH the OCO and `square_ordno`; one journal.
13. `square_reason` never mutated (`on_close` reason == `"stop"`, not `"stop_reprice"`).
14. EOD still skips squaring while the re‑price loop escalates; `exits` unchanged, `reprices` advances.
15. Deterministic full crash: 1 %→2 %→4 %→fill→finalize+journal once; `_stats.exits==1`, `reprices==2`.

**Files:** `live_position_guard.py`, `auto_square.py` (new `reprice_exit_leg`), `runtime.py`,
`tests/test_live_position_guard.py` (+ a `reprice_fn` recorder). No change to `square_position` or the
existing `square_fn` recorders.
