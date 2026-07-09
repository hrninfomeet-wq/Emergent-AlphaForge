# Remove the manual LIVE_TEST 10‑minute auto‑square timer

**Date:** 2026-07-09
**Status:** Approved design
**Sequence:** Implement **before** the Layer 1 guard confirm‑flat fix
([2026-07-09-live-guard-confirmed-flat-square-design.md](2026-07-09-live-guard-confirmed-flat-square-design.md)).
**Areas:** `backend/app/routers/live_broker.py`, `backend/app/live/auto_square.py`,
`backend/app/live/session_store.py`, `backend/app/live/live_position_guard.py`,
`frontend/src/components/live/PositionMonitor.jsx`.

---

## 1. Context & decision

The 10‑minute auto‑square (`SQUARE_HORIZON_SEC = 600`, "L3.3 hard cap for live‑test positions") was
test‑only scaffolding. It exists **exclusively** on the manual single‑shot LIVE_TEST arm
(`live_broker._make_arm` → `_schedule_auto_square` → `_auto_square_task` /
`_check_and_square_if_due`, using `auto_square.deadline_iso` / `is_due` and the `SessionStore`
`deadline`). Deployed strategies are **already exempt** — `live_deploy_context.arm_for`
([live_deploy_context.py:95‑109](../../../backend/app/live_deploy_context.py)) registers the
strategy's stop/target/trailing/time‑stop/spot‑mirror with the software guard **and** places a
resting broker OCO (the PC‑down backstop), and never schedules the 10‑minute square.

**Decision (user):** deployed strategies already follow the correct model (strategy rule‑based
entry/exit + the deploy‑to‑live form's optional PC‑down OCO). The manual test arm should **keep**
placing a guard‑monitored test order but **lose the 10‑minute timer**. Because manual positions are
currently EOD‑exempt, removing the timer would let a forgotten test position sit open indefinitely —
so manual positions become **15:00 IST EOD‑squared** (the replacement "never left open" backstop).

**Explicitly out of scope:** adding a resting OCO to the manual arm (it never had one; the OCO is a
deployed‑strategy feature). Noted as an optional future add‑on. `build_sl_backstop_intent` stays
(unrelated builder); only its now‑unused import is dropped.

---

## 2. Goal

- The manual single‑shot LIVE_TEST arm no longer computes a deadline or schedules any auto‑square
  timer. The armed position is protected by the **software guard's premium stop** (deep‑default 50%
  when the order carries none) and, as the "not left open" backstop, the **15:00 IST EOD square** —
  plus manual **Square** / **Kill** as today.
- All 10‑minute‑timer code is removed from backend and frontend; no dead countdown remains.
- Deployed strategies are untouched (already correct).

---

## 3. Backend changes

### 3.1 `live_broker.py` — stop scheduling the timer

- `_make_arm._arm`: drop `dl = deadline_iso(now)`; call `session_store.arm(entry_norenordno=…,
  sl_norenordno=None, now_iso=now)` **without** a deadline; **remove** the `_schedule_auto_square(…)`
  call. Keep guard registration (unchanged) and the session record.
- **Delete** `_schedule_auto_square`, `_auto_square_task`, `_check_and_square_if_due`, and
  `_TIMER_CHECK_INTERVAL`.
- Drop the now‑unused imports `deadline_iso` and `build_sl_backstop_intent`; keep `square_position`
  (manual Square route + mode/timer‑independent square) and the `auto_square` module import if still
  referenced elsewhere in the file (verify; the manual Square route uses `square_position`).
- `GET /live-broker/test-session`: **keep** the route and its rejected‑entry auto‑detection (still
  valuable). **Remove** `deadline` and `remaining_secs` from the response (drop the
  `ss.remaining_secs(...)` call and the terminal‑zeroing branch). The response keeps `position`,
  `status`, `sl_norenordno`, `reject_reason`, `heartbeat`. Update the route + module header docs.

### 3.2 `auto_square.py` — delete the time‑cap primitives

- **Delete** `SQUARE_HORIZON_SEC`, `deadline_iso`, `is_due`, and the `_to_utc` helper (used only by
  those two). Confirmed no other backend caller.
- **Keep** `square_position`, `build_sl_backstop_intent`, `_marketable_prc`,
  `_cancel_all_working_for_scrip` — all still load‑bearing for the executor path.
- Rewrite the module docstring: it is now the margin‑safe **square executor + SL‑LMT builder**, not a
  "10‑minute hard cap." Remove the "never open past 10 min" language.

### 3.3 `session_store.py` — drop the deadline plumbing

- Remove `deadline` from `_EMPTY` and from `arm()` (drop the `deadline` parameter and field).
- Remove `_remaining` and `SessionStore.remaining_secs`.
- Keep `entry_norenordno`, `sl_norenordno`, `status`, `heartbeat_ts`, `reject_reason` and the
  `arm`/`update_status`/`bump_heartbeat`/`clear`/`get` lifecycle. Update the module docstring.

### 3.4 `live_position_guard.py` — manual positions become EOD‑squared

- `_evaluate_eod_square`: **remove** the `source == "manual"` skip so every non‑flat registered
  position (manual + `auto_live` + `rehydrated`) is squared at 15:00 IST. Update the method
  docstring.
- Update the stale "10‑minute cap" / "EOD‑exempt manual" language in `register`'s docstring
  ([:143‑152](../../../backend/app/live/live_position_guard.py)) and the module header
  ([:36](../../../backend/app/live/live_position_guard.py) "The 10‑minute auto‑square cap remains the
  ultimate backstop") → the backstops are now the guard stop + 15:00 EOD (+ OCO for deployed).
- **No behavioural change to the square path itself** here — that is Layer 1's job, which lands
  next. This change only flips manual EOD‑exemption + docs.

---

## 4. Frontend changes

### 4.1 `PositionMonitor.jsx` — remove the countdown

- Delete `formatCountdown`, `totalSecs = 10*60`, `remainingSecs`, `deadline`, `progressPct`,
  `isUrgent`, the "Time remaining" block, the progress bar, and the "Deadline: …" line.
- Keep the three‑state card model (active vs squared/killed vs none) and the **Square** / **Kill**
  buttons + action messaging. The active card is reframed as a **guarded position** (software stop +
  15:00 EOD; close manually any time) with **no timer**.
- Stop reading `session.remaining_secs` / `session.deadline`. `api.getLiveTestSession()` is
  unchanged (pass‑through); it simply no longer carries those fields.
- Verify no other component consumes `remaining_secs` / `deadline` (grep shows only this file).

---

## 5. Test plan (TDD)

**Backend — rewrite / remove:**
- `test_live_auto_square.py`: remove the `deadline_iso` / `is_due` / `SQUARE_HORIZON_SEC` tests; keep
  `square_position` + `build_sl_backstop_intent` tests.
- `test_live_l3_routes.py`: delete `test_check_and_square_if_due_*` and the `_schedule_auto_square`
  MagicMock patches; drop the deadline assertion in `test_place_in_live_test_session_has_deadline`
  (rename → asserts an armed session with **no** deadline); rewrite
  `test_test_session_returns_deadline_and_remaining` → asserts the fields are **absent**; update the
  rejected/active/terminal session tests to drop `remaining_secs` checks while keeping the
  status/`reject_reason`/mode‑revert assertions.
- session‑store tests (any calling `arm(deadline=…)` or `remaining_secs`): update to the new
  signature; drop remaining‑secs tests.
- `test_live_position_guard.py`: flip `test_eod_does_not_square_manual` → `test_eod_squares_manual`
  (a manual‑source entry IS squared at 15:00 IST); keep `test_eod_squares_deployed_at_1500_ist`.

**Backend — new:**
- arming a manual test order records a session with **no deadline** and schedules **no** timer task.
- a manual‑source guarded position is EOD‑squared at 15:00 IST.

**Frontend:**
- `PositionMonitor` renders the active guarded card with Square/Kill and **no** countdown / progress
  bar / deadline text; squared and none states unchanged. (Adjust existing component tests if
  present; otherwise a render smoke check.)

**Verification:** run the live host suites (`test_live_l3_routes`, `test_live_auto_square`,
`test_live_position_guard`, session‑store); build the frontend and load the Live page to confirm the
manual‑position card shows no timer and Square/Kill still work; confirm arming a test order no longer
starts a background square task (log/inspection).

---

## 6. Interaction with the Layer 1 confirm‑flat fix

Independent and compatible. This change edits `_evaluate_eod_square` + docstrings; Layer 1 edits the
square/confirm path (`_square_and_record` → `_issue_square`, confirmed‑flat finalizer) and adds a
`squaring`‑skip that will also apply inside `_evaluate_eod_square`. When Layer 1 lands, drop the
"10‑min cap" mention from its §3.6 (that backstop no longer exists; EOD + OCO remain). No merge
conflict of substance — different methods.
