# Live guard: confirm‑flat before finalizing a square (Layer 1)

**Date:** 2026-07-09
**Status:** Approved design; Layer 1 of 2 (Layer 2 = widening re‑price, follow‑up)
**Area:** `backend/app/live/live_position_guard.py`
**Origin:** 5‑agent adversarial review during audit item #6 (branch `fix/broker-truth-integrity`, commit `4d826fb`). Pre‑existing since audit item #3 (the OCO‑cancel gate). Tracked as spawned task `task_09dfc043`.

---

## 1. Problem

`LivePositionGuard` treats a *place‑accepted* exit as a *closed* position. On a guard breach it
squares via the injected `square_fn` (`auto_square.square_position`), which returns
`{"squared": True, "via": "exit_order"}` the **moment `client.place_order()` returns ok**
([auto_square.py:652‑660](../../../backend/app/live/auto_square.py)) — that is
place‑**acceptance**, not a fill. There is no trade‑book / position‑book confirmation that
`netqty` reached 0.

On that place‑accept signal, `_square_and_record`
([live_position_guard.py:376‑418](../../../backend/app/live/live_position_guard.py)) performs three
**irreversible** actions that all assume the position is now flat:

1. removes the entry from the registry (done *before* the square even runs — line 382), so the
   guard never re‑examines it and the 15:00 EOD square (which iterates only the registry) can't
   catch it either;
2. cancels the resting OCO — the sole PC‑down backstop (lines 394‑405);
3. fires `on_close` → `close_loop` marks the `live_trades` doc **CLOSED**.

### Failure scenario (confirmed plausible)

`LIVE_GUARD_ARMED=1`; a fast down‑move on a short‑dated long option. The guard places a marketable
SELL LMT at `ref*(1 − band)` with `band_pct = 1.0` (`runtime._live_guard_square_fn`). On a fast
crash the 1% band is blown through, so the SELL LMT **rests unfilled** (a resting sell only fills on
the way back up). The broker accepts the order (`ok=True`) → `square_position` returns
`squared=True`. Result:

- position is **still OPEN**;
- it is now **UNPROTECTED** — the OCO was cancelled and the entry was dropped from the registry;
- yet `live_trades` reports it **CLOSED**.

Only the reconcile `unknown_broker_position` chip catches this — and only at the next startup.

### Blast‑radius constraint

`square_position` is a **shared executor primitive** with 6+ callers (arm‑abort in `executor.py`,
`LiveExitMonitor` in `live_sl_monitor.py`, the manual square route + 10‑min timer + mode square in
`live_broker.py`, the deployment stop in `deployments.py`, plus the guard). Its
"place the exit, return whether the place was accepted" contract is correct for those callers.
**The fix must not change `square_position`.** The confirmation belongs in the guard, whose 1.5s
poll loop already reads the broker `position_book()` every cycle — the exact signal needed.

---

## 2. Goal (Layer 1)

Restore one invariant in the guard:

> **OCO‑cancel, entry‑drop, and journal‑close fire ONLY when the broker position book confirms
> `netqty → 0` for that tsym — never on place‑acceptance.**

Until confirmed flat: keep the entry registered and the OCO resting, so the position is never
left unprotected and `live_trades` is never falsely CLOSED.

**Non‑goals (Layer 1):** re‑pricing / widening the band on repeated non‑fills (that is Layer 2);
any change to `square_position`, `close_loop` logic, or `runtime` wiring; journaling closes for
positions closed *outside* the guard (OCO‑fired / manual) — out of scope.

---

## 3. Design

Scope of change: **`backend/app/live/live_position_guard.py` only.** `close_loop.py`,
`auto_square.py`, and `runtime.py` are untouched in logic (a docstring tweak at most). Only *when*
the guard calls `on_close` moves.

### 3.1 Registry entry — two new fields

`LiveMonitorRegistry.register` adds to each item:

- `squaring: bool = False` — a guard square has been transmitted for this entry and the broker has
  **not yet** confirmed flat.
- `square_reason: Optional[str] = None` — the exit reason to journal when the position is confirmed
  flat (e.g. `"software_stop"`, `"eod_square"`).

Both fields always exist on an item so no `.get()` default drift.

### 3.2 Square becomes place‑and‑track (`_issue_square`)

Rename `_square_and_record` → `_issue_square`. It **issues** the exit and records the attempt, but
it **no longer** drops the entry, cancels the OCO, or journals. Behaviour by `square_fn` result:

| Result | `squaring` | Entry | OCO | Journal |
|---|---|---|---|---|
| dry‑run (`result.dry_run` truthy) | stays `False` | kept | kept | no |
| real, accepted (`squared` truthy, not dry‑run) | set `True`, `square_reason = exit_reason` | kept | kept | no (deferred to confirm‑flat) |
| real, failed (`squared` falsy) | stays `False` | kept | kept | no |

- The **dry‑run** row preserves validation semantics: `LIVE_GUARD_ARMED=0` transmits nothing, the
  position stays open, so nothing is cancelled or journaled. (Behaviour change vs today: the entry
  is **kept** rather than dropped — the guard keeps flagging the un‑actioned breach.) Because
  `squaring` stays `False`, the guard re‑evaluates and re‑logs the "would square" warning each cycle
  the breach persists; that repetition is acceptable in validation mode and needs no extra state.
  (The dry‑run `square_fn` is the sole side effect and is itself a no‑op transmit.)
- The **real‑failed** row quietly fixes a second pre‑existing hole: today remove‑before‑square drops
  the entry, so a rejected‑twice square orphaned the position from the registry. Keeping the entry
  means the next cycle retries.

The attempt is still appended to the cycle's `exits` list and `_stats["exits"]` is still bumped
(these count *actions taken this cycle*, not confirmed closes).

### 3.3 Re‑entrancy guard: the `squaring` flag replaces remove‑before‑square

Remove‑before‑square existed so a slow square is never re‑issued. That guarantee now comes from the
flag: **a `squaring` entry is skipped by every issue path each cycle** — the per‑position stop eval,
spot‑mirror, time‑stop, EOD, and overall‑basket. An exit is already working; the only ways out of
`squaring` are (a) the broker confirms flat → finalize, or (b) Layer 2 re‑prices. In Layer 1 there
is no (b), so a `squaring` entry simply waits, protected by its resting exit **and** the still‑intact
OCO, until it fills / the OCO fires / EOD‑as‑backstop.

Concretely in `_cycle`, for a live filled position, add `if entry.get("squaring"): continue`
*before* `evaluate_exit`. `_evaluate_eod_square` and `_evaluate_overall_basket` (which iterate the
registry directly) skip entries whose `squaring` is already `True`.

### 3.4 Confirmed‑flat is the sole finalizer

The existing `seen_filled && now‑flat` branch (`pos is None or netqty is None or netqty == 0`,
[live_position_guard.py:317‑328](../../../backend/app/live/live_position_guard.py)) becomes the one
place that finalizes:

```
if entry.get("seen_filled"):
    # CONFIRMED FLAT — the broker book says netqty == 0.
    if entry.get("oco_al_id") and hasattr(client, "cancel_oco"):
        best-effort cancel_oco(oco_al_id)          # no-op if the OCO fired; cleans an orphan
    if entry.get("squaring") and self._on_close is not None:
        best-effort on_close(entry, last-seen lp, square_reason, {"squared": True, "via": "confirmed_flat"})
    self._registry.remove(entry["id"])
else:
    # still pending its first fill — unchanged grace-window logic
```

- `cancel_oco` runs on **any** confirmed flat (when an `oco_al_id` exists): if the OCO itself fired
  it is a harmless no‑op; if the guard's exit filled it clears the now‑redundant OCO; if the
  position was closed elsewhere it clears an OCO that would otherwise be orphaned (a resting order
  against a flat account that could open a fresh naked short).
- `on_close` fires **only** when a guard square was pending (`squaring`), with the tracked reason
  and the last‑seen broker mark as the exit price (an estimate — reboot‑reconcile already back‑fills
  the true fill price from the trade book). A position that went flat while **not** squaring keeps
  today's silent drop (no journal).
- `on_close` is passed a **synthesized** result `{"squared": True, "via": "confirmed_flat"}`;
  `should_journal_close` (unchanged) returns True for it (`squared` truthy, no `dry_run`,
  `source != "manual"`). `close_loop.py` needs **no logic change**.

### 3.5 Effect on the failure scenario

Guard breaches → places the 1% SELL LMT → it rests unfilled → `square_position` returns
`squared=True` → `_issue_square` sets `squaring=True` and **stops there**. The entry stays
registered, the OCO stays resting, `live_trades` stays OPEN. When the position actually goes flat —
the exit fills, the OCO fires, or EOD acts — the confirmed‑flat branch cancels the OCO and journals
the close truthfully. The position is never simultaneously open, unprotected, and reported closed.

### 3.6 Acknowledged Layer‑1 boundary

Layer 1 does not re‑price a resting exit. On a fully blown‑through market the guard's own 1% exit
may sit unfilled until the OCO, the 10‑minute manual auto‑square cap, or EOD acts. Layer 1's
contract is *keep it protected and honestly journaled*; **Layer 2** (follow‑up) adds the
1 → 2 → 4 % escalation (mirroring `kill_switch.panic_squareoff_verified`) that forces the fill, plus
interval‑gated retry so a hard‑rejecting square doesn't re‑place every cycle.

---

## 4. Alternatives considered

1. **Synchronous verified square inside `square_position`** (place → poll → re‑fetch position book,
   like `panic_squareoff_verified`). Rejected: `square_position` has 6+ callers with a legitimate
   place‑accept contract; changing it ripples across the whole live path and blocks the guard cycle
   for seconds per breached position.
2. **Bare minimal gate, no state fields.** Rejected: without a `squaring` marker the guard either
   re‑issues every cycle (place spam) or loses re‑entrancy safety. Two fields are the minimum.
3. **Journal every confirmed flat (incl. closed‑elsewhere / OCO‑fired).** Deferred: more correct in
   the limit but expands scope into non‑guard closes with ambiguous reasons/prices; reboot‑reconcile
   already covers trade‑book back‑fill. Layer 1 journals only guard‑driven squares.

---

## 5. Test plan (TDD — red first)

Contract **changes** (rewrite from one‑cycle‑drops to two‑phase issue→confirm):
- `test_real_fill_cancels_oco_after_square` → OCO **not** cancelled on the place‑accept cycle;
  cancelled only on a later cycle where the book reports the tsym flat.
- `test_real_dry_run_field_present_but_false_cancels`, `test_failed_square_does_not_cancel_oco`,
  the on‑close tests, `test_remove_before_square_no_double_square`,
  `test_stop_breach_squares_once_and_removes`, and any test asserting drop‑after‑one‑cycle.

New tests:
- place‑accept keeps the entry registered **and** the OCO resting (`squaring=True`, no cancel, no
  journal);
- confirmed‑flat (book shows tsym flat next cycle) cancels the OCO, journals the close once, drops
  the entry;
- a **failed** real square keeps the entry and retries next cycle (no orphan);
- a **dry‑run** square keeps the entry and OCO and never journals;
- a position that goes flat while **not** squaring is dropped without a journal (closed‑elsewhere),
  but its OCO is cancelled (orphan cleanup);
- re‑entrancy: a `squaring` entry is not re‑squared by the stop / EOD / overall paths.

Existing green tests to keep: pending‑fill grace window, stale‑lp‑never‑squares, cycle‑never‑raises,
rehydrate, spot/time/overall breach *issuing* (assert the square_fn was called, not that the entry
dropped).

**Verification:** run the guard + close‑loop host suites; then exercise the two‑phase flow
end‑to‑end against `MockNoren` (breach cycle → open book → confirm‑flat cycle) to observe OCO intact
then cancelled and `live_trades` OPEN then CLOSED.
