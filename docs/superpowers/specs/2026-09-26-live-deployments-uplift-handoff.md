# Handoff — Live Deployments pane uplift (`/live-trading`)

**Paste everything below the line into a fresh session.** It is self-contained.

Written 2026-09-26, after auditing an earlier set of recommendations against the
code. Two of those recommendations were **wrong**, one was **mispriced**, and one
whole enforcement layer had been **missed**. Those corrections are called out
inline — do not re-derive them from the original advice.

---

## 0. Orientation

**AlphaForge Trading Lab** — local-first research + forward-test app for Indian
index options (NIFTY / BANKNIFTY / SENSEX). React (CRA + craco) frontend, FastAPI
backend, MongoDB, all in Docker Compose. Frontend `:3000`, backend `:8001` (every
route under `/api`). Upstox = market data; Flattrade (Noren) = live execution.

Read first: `docs/HANDOFF.md`, then `CHANGELOG.md` (top entries), then
`docs/DEVELOPER_GUIDE.md` §E for the live-trading safety model.

**The work:** the Live Deployments pane on `/live-trading`
(`frontend/src/components/live/LiveDeploymentStrip.jsx`) tells the operator *what
is enabled* but not *what is happening or about to happen*. This handoff turns it
into something a trader can steer by.

### Non-negotiable project conventions

- **Measure before optimising.** This codebase has repeatedly found that the
  assumed bottleneck was not the real one. Profile, then change.
- **Safety gates fail CLOSED.** An unverifiable condition refuses; it never
  proceeds. Never fabricate a number — render "—" instead.
- **Restart is not rebuild.** Backend code is baked into the image; only
  `strategies/plugins` is bind-mounted. Deploy with
  `docker compose up -d --build`, then verify with
  `MSYS_NO_PATHCONV=1 docker exec alphaforge_backend grep -c ...`.
- **Tests need mutants.** After writing a test, break the code it guards and
  confirm the test fails. A test that passes against the broken version is worth
  nothing. This has caught tautological tests twice.
- **Host venv is the gate:** `./.venv/Scripts/python.exe -m pytest tests/ -q`.
  The in-container run reds pre-existing path-contract tests.
- **Frontend gate:** `cd frontend && CI=true npx --no-install craco build` —
  warnings are errors.
- **Flattrade MCP: read tools only** (`get_positions`, `get_limits`,
  `get_order_book`). **Never** `login` / `logout` — last-login-wins would kill
  AlphaForge's own token. Never place/modify/cancel orders through it.
- **Per-changeset push approval.** Commit locally; ask before pushing.
- Measure browser timings from `localhost:3000`, never `127.0.0.1:3000`
  (CORS-blocked); measure to React commit, not `requestAnimationFrame`.

---

## 1. Audited findings

Every claim below was verified against the code on 2026-09-26. File:line
references are given so you can re-check rather than trust.

### 1.1 The payload already carries what the row throws away

`_live_status_payload` (`backend/app/routers/deployments.py`) returns
`caps{lots,max_lots_per_day,max_concurrent,daily_loss_cap}`, `today{orders,lots,
realized_pnl}`, `open_positions[{id,tsym,qty,entry_price,stop_level,target_level,
seen_filled}]`, `last_entry{signal_id,error,intended,at}`, `autoplace_armed`,
`guard_armed`, `live_paused`.

`LiveRow` consumes only `today.*`, `open_positions.length`, `live_paused`, and
`last_entry.error`. **`caps` is never touched. `open_positions` detail is never
rendered. `last_entry.intended` is never rendered.**

### 1.2 ⚠️ CORRECTION 1 — do NOT compute loss headroom from `today.realized_pnl`

The earlier advice said to render `today.realized_pnl ÷ caps.daily_loss_cap`.
**That is wrong.** `check_live_caps` (`backend/app/live_deploy_governor.py:297`)
gates `daily_loss_cap` on **realized + open-unrealized** — see
`open_unrealized_today` at `:163`. A headroom bar built on realized alone
**under-reports consumption exactly when a losing position is open**, i.e. when it
matters most.

**Design consequence:** do not re-derive cap consumption in JavaScript. Expose the
governor's own computation. Two resolvers drifting apart is a defect class this
repo has already been bitten by twice (the BFO marking bug; request-vs-resolved
envelope). One source of truth, server-side.

### 1.3 ⚠️ MISSED LAYER — account-level caps also block entries

`auto_live.py:314` calls `check_account_caps` **before** `check_live_caps`
(`:324`). The account layer can refuse with `account_latched`,
`account_max_open_block`, `account_exposure_unavailable`,
`account_exposure_invalid`. None of it is surfaced per row. A deployment can be
perfectly within its own caps and still be unable to trade.

### 1.4 The governor's real contract

`check_live_caps(db, deployment, *, capped_lots, now_utc) ->
{"allow": bool, "reason": str, "pause": bool}`

Precedence, first match wins:
1. `daily_loss_cap` → `allow=False`, **`pause=True`**
2. `max_lots_per_day` → `allow=False`, `pause=False`
3. `max_concurrent` → `allow=False`, `pause=False`
4. else → `allow=True`, `reason="ok"`

Fail-closed states that must be surfaced, not hidden: `live_caps_missing`
(a live deployment with no caps refuses rather than trading unbounded) and
`invalid_daily_loss_cap` (non-finite cap). `open_unrealized_today` also returns an
`exposure_unknown` flag — an unknown exposure is not zero exposure.

### 1.5 The Day Stop card pools caps across deployments — a defect

`deriveDayStop` in `frontend/src/components/live/cockpit/RiskKpis.jsx:20` sums
`daily_loss_cap` across every live deployment into one figure. Enforcement is
**per deployment** (§1.4). One deployment at 95 % of its cap beside one at 0 %
renders ~48 % — reads safe, is not. Show the **worst** deployment, or break out
per deployment.

### 1.6 ⚠️ CORRECTION 2 — re-pin is cheaper than stated

`POST /deployments/{id}/repin-source` already exists
(`backend/app/routers/deployments.py:1733`) and `api.repinDeploymentSource` is
already in `frontend/src/lib/api.js`. Adding re-pin to the live pane is
**frontend wiring only** — no backend work.

### 1.7 Genuinely missing routes

Full route inventory of the deployments router: `pause`, `resume`, `stop`,
`stop-all`, `live/enable`, `live/disable`, `live/pause`, `live/resume`,
`live/stop`, `repin-source`, `archive`, `evaluate-on-close`, `evaluate-active`.

- **No live caps route.** `putPaperCaps` is paper-only. Live caps are written
  solely by `/live/enable` and are immutable while live —
  `DeployToLivePanel.jsx:271` states so. Resizing requires Disable → re-Enable,
  which walks the full consent flow **while the deployment is unarmed**.
- **No flatten-without-demote.** `/live/stop` flattens **and** sets `mode=paper`
  **and** `status=PAUSED`. There is no "exit this trade, keep the strategy
  running".

### 1.8 No time awareness

The 15:00 IST entry cutoff (`live/mode.py`, `entry_cutoff_today_ist`) and the EOD
square are surfaced nowhere as a countdown. The operator learns about the cutoff
only afterwards, as an `after_entry_cutoff` reason string.

### 1.9 Per-deployment supervision state is never exposed

`evaluate_risk_supervision` (`live_deploy_governor.py:222`) is **PURE — no DB, no
clock, no I/O**, so it is safe and cheap to call for read-only introspection.
`deployment_kill_switch.evaluate_kill_switches` knows `consecutive_losses` and the
pause decision. None reaches any payload the UI reads.

### 1.10 Smaller, verified

- Rows are `filter`ed, never sorted (`LiveDeploymentStrip.jsx:373-374`) — the
  deployment holding real money can render seventh.
- Not-live rows are not collapsed; nine "Enable Live Execution" rows push live
  ones off-screen.
- Flatten confirmations are bare `window.confirm` strings.
- **No notification of any kind** — no desktop notification, no sound, on fill,
  exit, halt or entry-refusal. Grep for `Notification(` / `new Audio` returns
  nothing. The operator's PC is often not attended during market hours.
- `GuardPanel` is read-only; stop/target cannot be edited from the UI.
- A session timeline needs **no new collection** — `/signals`,
  `/signals/enriched` and `live_trades` already carry the events.

---

## 2. Implementation plan

Ordered by dependency, not by appeal. Each phase is independently shippable and
independently verifiable. **Do not start a phase before its predecessor's gate
passes.**

### Phase 0 — land the pending fix (BLOCKER)

There is uncommitted work from 2026-09-16 that every later phase depends on:
`backend/app/live/reboot_reconcile.py` (modified) and
`tests/test_reconcile_partial_fill_pnl.py` (untracked).

It fixes `_match_exit_fill_price` failing on **partial fills** — a real 5-lot
SENSEX exit filled as 60 + 40 one second apart, the matcher required exactly one
SELL, returned None, and the trade closed with `realized_pnl = null`. Six of
twelve closed live trades carry a null. It adds a quantity-weighted aggregate
bounded by time and quantity, plus a `_repair_missing_realized` backfill phase.

**Why it blocks everything:** every cap-consumption number in Phase 1 is built on
realized P&L. Until this lands, those numbers are wrong for any reconcile-closed
trade.

**Gate:** full suite green; `docker compose up -d --build`; confirm via
`docker logs alphaforge_backend | grep "backfilled realized_pnl"` that the repair
ran, and re-read the affected trade in Mongo to confirm `realized_pnl` now matches
the broker's `rpnl`. Verify the repair did **not** touch the `never_filled` row.

### Phase 1 — backend: one honest answer to "why can't this trade right now?"

Add **read-only** governor introspection. Do not re-implement any rule.

Build a function (suggested: `live_deploy_governor.describe_live_caps(db,
deployment, *, now_utc)`) that reuses the same code paths as `check_live_caps` and
returns, per deployment:

- `caps`: the four configured values (null where unset)
- `consumed`: `lots_today`, `concurrent_now`, `loss_consumed` **(realized +
  open-unrealized — see §1.2)**, `exposure_unknown`
- `verdict`: `{allow, reason, pause}` from the deployment layer
- `account_verdict`: `{allow, reason, pause}` from `check_account_caps`

Surface it on `/live/status` and the batched `?ids=` route under a new
`governor` key. **Additive only** — no existing field changes shape.

**Hard requirements**
- Read-only. No writes, no order path, no broker calls.
- Must never raise out of the status route; degrade to `null` with a reason.
- `exposure_unknown` must be representable and must not render as zero.
- Fail-closed reasons (`live_caps_missing`, `invalid_daily_loss_cap`) surface as
  themselves, not as "ok".

**Gate:** unit tests pinning that `describe_live_caps` agrees with
`check_live_caps` on the same inputs — including a case with an **open losing
position**, which is precisely where a realized-only computation diverges.
Mutation-check: make the describe path use realized-only and confirm that test
fails.

### Phase 2 — frontend: render it, and fix the misleading card

1. **Cap headroom on each live row**, from `governor` only: lots used/max,
   concurrent used/max, loss consumed/cap. Show the **binding** constraint
   prominently — the reason the governor would refuse. Render "—" for unset caps;
   never imply a limit that is not configured.
2. **Expandable row** listing open positions: `tsym`, `qty`, `entry_price`,
   `stop_level`, `target_level`, distance to stop. Reuse existing formatters.
3. **`last_entry.intended`** alongside `last_entry.error`.
4. **Fix `deriveDayStop`** (§1.5) — worst deployment, not pooled. This is a
   correctness fix; pin it with a test asserting a 95 % + 0 % pair does not render
   as ~48 %.

**Gate:** `CI=true` build clean; suite green; rebuild; verify the new strings are
present in the served nginx bundle
(`docker exec alphaforge_frontend grep -c ... /usr/share/nginx/html/static/js/main.*.js`).

### Phase 3 — time awareness

Countdown to the 15:00 IST entry cutoff and the EOD square, derived from
`entry_cutoff_today_ist` — do not hardcode 15:00 in the frontend. Show it where
the operator already looks (execution strip or pane header). Must read correctly
before the open, during the session, after the cutoff, and on a non-trading day.

### Phase 4 — the controls a trader reaches for (new routes)

1. **Tighten-only live caps.** New route, e.g.
   `POST /deployments/{id}/live/caps`. It may only **tighten**: lower `lots`,
   lower `max_lots_per_day`, lower `max_concurrent`, lower `daily_loss_cap`. Any
   loosening → **409**, and the operator uses Disable → re-Enable.

   *Rationale — follow the existing doctrine:* the repo already uses this
   asymmetry for pause (unconditional, restrictive) vs resume (CAS-guarded,
   permissive), documented in `runtime.py::_set_deployment_status`. Tightening is
   the restrictive direction and is safe without re-consent; loosening is
   permissive and keeps the ceremony. Guard the write with the same `updated_at`
   compare-and-swap `/live/resume` uses.

2. **Flatten-without-demote.** New route, e.g.
   `POST /deployments/{id}/live/flatten`. Squares this deployment's open live
   positions through the **existing** margin-safe path
   (`_square_live_positions_for_deployment`) and leaves `mode="live"` and
   `status="ACTIVE"` untouched. Do not duplicate exit logic.

   Out of market hours the order half will be rejected by the exchange and land in
   `failed_tsyms`; the position stays registered with the guard. Say so in the UI —
   do not report a flatten that did not happen.

3. **Re-pin on the live pane** — frontend wiring only (§1.6).

**Gate:** tests proving a loosening attempt 409s and leaves the document
unchanged; that a concurrent Stop beats a late-landing caps write; and that
flatten does not alter `mode` or `status`. Mutation-check each.

### Phase 5 — experience

1. **Sort rows**: open positions → held → live-idle → not-live. Collapse not-live
   behind a count + expander.
2. **Replace `window.confirm`** on flatten / Stop / Stop-ALL with a real dialog
   naming contract, quantity and approximate value.
3. **Session timeline** from `/signals` + `live_trades` (§1.10) — no new
   collection. Chronological: signals, fills, exits, refusals, halts.
4. **Notifications** — desktop notification and/or sound on fill, exit, halt,
   entry-refusal. Permission must be explicitly requested, never auto-granted, and
   the whole feature must be switchable off and default off.

---

## 3. Sequencing rationale

- Phase 0 first because Phases 1–2 render numbers derived from realized P&L.
- Phase 1 before 2 because the UI must not re-derive enforcement (§1.2).
- Phase 3 is independent — it can be done any time after 0, and is a good
  low-risk warm-up if you want one.
- Phase 4 introduces the only new write paths; it deliberately follows the
  read-only phases so the operator can already *see* caps before being able to
  *change* them.
- Phase 5 is additive polish and must not block the rest.

## 4. Explicitly out of scope

- The Market Pulse / S-R rework — separately specified in
  `docs/superpowers/specs/2026-09-08-live-controls-and-market-pulse-design.md` §4.
- Re-enabling the broker OCO. It is OFF by default since 2026-09-03 because its
  stop leg fires at placement instead of resting. Do not turn it on; do not build
  features that assume a resting OCO exists.
- Any change to **entry** evaluation. Entries stay gated on the CLOSED 1-minute
  bar — that is what keeps live and backtest in parity. Exits and display are
  tick-driven; latency work belongs there.

## 5. Definition of done

- Every phase's gate passed, in order.
- Full suite green on the host venv; `CI=true` frontend build clean.
- Images rebuilt and the change **verified inside the running container**, not
  assumed.
- Each new test mutation-checked.
- `CHANGELOG.md` entry per phase, stating what was measured, not just what was
  changed.
- Committed locally; **ask before pushing**.
