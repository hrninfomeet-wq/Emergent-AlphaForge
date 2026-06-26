# PC-Down OCO Backstop + Execution-Quality Wins — Design

> **Status:** approved design (brainstorm 2026-06-26). Next step: writing-plans → implementation.
> **Anchor:** closes the real-money gap surfaced by the live-page safety audit — *a deployed
> live position has NO broker-resting protection if the PC/backend goes down* (the software
> guard is an in-process loop that dies with the process; no OCO/GTT is auto-placed on entry).
> **Branch base:** `main` @ `0722ac2`.

## Goal

When a deployed strategy auto-places a live option-buy entry, rest a **broker-side OCO
(stop + target) that fires even if the backend/PC is down** — so a sudden large loss is
capped and a large profit is booked without any local process alive. Bundle three
execution-quality wins that the same code path needs: a broker-truth **margin pre-check**,
**depth-aware square pricing**, and (Phase C) **reboot reconciliation** that journals an
OCO-that-fired-while-down with the true fill price.

## Architecture (one sentence each)

- Deployed entries are placed as **NRML** (`prd="M"`) instead of intraday MIS — margin-neutral
  for a long option (premium is paid in full either way) but the only product a broker GTT/OCO
  will attach to.
- On each deployed fill, alongside registering the in-process **software guard** (unchanged,
  tight stop/target, primary exit while the PC is on), the system **places a resting OCO** whose
  stop/target sit in a **wider catastrophe band** — so the guard always exits first while alive,
  and the OCO is a pure last-resort that only fires when the guard is dead.
- Every close path (software guard, manual square, deployment stop/stop-all, kill-switch) and a
  **reboot reconciliation** keep the resting OCO and the `live_trades` journal coherent.

## Tech stack / touch-points

Backend (FastAPI + motor, Python 3.12): `app/live/order_builder.py`, `app/auto_live.py`,
`app/live_deploy_context.py`, `app/live/executor.py`, `app/live/gtt.py`,
`app/live/flattrade_client.py`, `app/live/auto_square.py`, `app/live/close_loop.py`,
`app/live/live_position_guard.py`, `app/runtime.py`, `app/routers/deployments.py`,
`app/routers/live_broker.py`. Frontend (CRA React): the live page (a "no broker backstop"
alert, OCO column on the blotter/GuardPanel). Broker: Flattrade pi/PiConnect (decoded reference
in `docs/Resources/flattrade-pi-api/`).

---

## Background: why this is needed (from the safety audit)

- The software guard (`LivePositionGuard`) is an **in-process asyncio loop + in-memory registry**;
  it polls the broker ~1.5s and squares in software. It dies the instant the backend/PC stops.
- **No resting broker order is placed on a deployed entry** — `arm_for` only registers the
  software guard. A resting *SL-LMT* on a long option is margin-rejected (naked-short SPAN), which
  is exactly why the software guard exists. So today, PC-down = **unprotected**.
- A **GTT/OCO is immune to that margin trap** (it rests on the broker's alert server, blocks no
  margin, only fires a real sell-to-close order once triggered) — but it is **NRML-only** and is
  currently **manual-only** (no auto-placement; `api.placeGtt` is even dead FE code).

## Verified Flattrade API facts (from the decoded reference + adversarial check)

- **OCO endpoints exist + vision-verified:** `#21 PlaceOCOOrder`, `#22 ModifyOCOOrder`,
  `#23 CancelOCOOrder`, `#19 GetPendingGTTOrder`, `#20 GetEnabledGTTs`.
- **OCO shape:** `oivariable = [{d: sl_trigger, var_name:"x"}, {d: tp_trigger, var_name:"y"}]`
  with `place_order_params` (leg-1 = SL, SELL) + `place_order_params_leg2` (leg-2 = TP, SELL);
  `ai_t = "LMT_BOS_O"`. The broker **infers above/below from each leg's trigger vs LTP** — so the
  *one* readback-confirmed code (`LMT_BOS_O`) covers **both** the stop and target legs. (Only a
  standalone single-*above* GTT would need the still-`[INFERRED]` `LTP_A_O`, which this design does
  not use.) `build_oco_intent` in `gtt.py` already builds exactly this shape.
- **Margin parity (decision-critical):** the docs are silent on MIS-vs-NRML margin; for a **long
  option buy** (a fully-paid debit) NRML(`M`) ≈ MIS(`I`) ≈ the full premium — confirmed by
  option-buying first principles, **to be proven live** by calling `#8 GetOrderMargin` twice
  (`prd="M"` vs `prd="I"`, same option) and comparing `marginused`.
- **Pre-trade margin:** `#8 GetOrderMargin` returns `marginused` (this order) + `cash` (credits
  available) in one call → a clean affordability gate before transmit.
- **Depth-aware price:** `#54 GetQuotes` returns fresh LTP + 5-level depth + circuit limits
  (`uc`/`lc`) for a depth-sane square/limit price.
- **Source gotchas to preserve verbatim (do NOT "fix"):** PlaceGTT success `stat:"Oi created"`;
  Cancel `"Oi delete success"`; `Al_id` vs `al_id` casing varies; jData/jKey are sent as a
  **form body** despite `Content-Type: application/json`; `tsym` must be URL-encoded. Order-API
  **rate limit 10/s, 40/min** (#57) — keep OCO/GTT calls off the hot poll path.

---

## Components & data flow

### Phase A — NRML product + margin pre-check

1. **Product switch.** `order_builder.build_intent` currently pins `prd="I"`. Thread a product
   choice so the **deployed** path requests `prd="M"` (NRML); the **manual single-shot path stays
   `prd="I"` (MIS)** (short-lived, 10-min cap, no OCO). Resolve/validate via the margin pre-check
   below (fail-closed if NRML is not placeable for the account/exchange).
2. **Margin pre-check.** Add `flattrade_client.order_margin(jdata)` → `#8 GetOrderMargin`. In the
   deployed-entry path (and the manual preview/dry-run), before transmit, call it with the
   candidate order; **block** (clean pre-trade verdict, surfaced in the deploy governor + the
   preview) when `cash < marginused`. This also catches "NRML not enabled" as a clean reject
   instead of a transmit failure.
3. **Margin-parity probe (one-off, logged):** a helper that calls `order_margin` for `prd="M"`
   and `prd="I"` on a sample option and logs the comparison — run during the supervised readback
   to confirm the parity assumption before relying on it.

### Phase B — OCO auto-placement + cancel-on-every-close-path

1. **Place on fill.** In `arm_for` (after `_transmit_and_arm` arms the software guard), build a
   resting OCO via `build_oco_intent` and place it via `client.place_oco`, **gated by the same arm
   as the entry** (`LIVE_AUTOPLACE_ARMED` + an armed deployment — it is protective, placed only
   when a real entry was). Store the returned `al_id` (handle `Al_id`/`al_id` casing) on the
   `live_trades` doc (`oco_al_id`) **and** the guard registry entry.
2. **Catastrophe band (the OCO levels) — DERIVED strictly wider than the guard stop.**
   Premium-based. The configured `catastrophe_stop_pct` is a **floor**, not the final value: the
   effective stop is `eff = max(configured_pct, guard_stop_pct + MIN_GAP_PP)` (MIN_GAP_PP = 15),
   so the OCO SL trigger is **always a lower premium than the software guard's own stop** — for
   every config path including the deep-default (the guard's default premium stop is **50%**, so a
   fixed 50% catastrophe would COLLIDE and could fire at the same instant → double-sell; deriving
   it past the guard removes that race). SL trigger = `entry × (1 − eff/100)`; TP trigger =
   `entry × (1 + target_pct/100)`. SELL-leg limits are a small `CROSS_PCT` (≈2%) **below** each
   trigger so the fired leg clears (Flattrade is limit-only).
   - `catastrophe_stop_pct` — configured floor, default **50** (your range 45–50); effective stop
     widens past the guard as above (e.g. a no-configured-stop deployment → guard 50% → OCO ≈ 65%).
   - `catastrophe_target_pct` — default **135** (range 120–150).
   - **Config:** global defaults (`oco_levels.py`) + **per-deployment override** in `risk.live`,
     threaded to the OCO placement via the **per-signal** `arm_for(plan, signal_doc, ref_ltp,
     catastrophe_*_pct=…)` call (NOT via the deployment-agnostic live context).
3. **Cancel-on-close (coordination + no double-sell — audit-hardened).** **(a)** Before any square,
   re-confirm `netqty` from a fresh non-empty broker read and **abort if flat** (an already-fired
   OCO must not be double-sold; `cancel_oco` cannot stop an already-triggered alert). **(b)** Cancel
   the OCO **only after a confirmed real square fill** (`result["squared"] and not
   result["dry_run"]`) — cancelling on a dry-run guard breach (`LIVE_GUARD_ARMED=0`, the default
   while validating) would strip the net without squaring. Paths that close a deployed position
   then cancel its OCO: the software guard's `_square_and_record` (after-fill), the
   `_square_live_positions_for_deployment`
   (deployment stop + stop-all, user-initiated → cancel-then-square is fine), and the
   **kill-switch** (sweeps `GetPendingGTTOrder` → cancels **all** resting GTT/OCO, since
   panic-squareoff cancels working orders but not resting alerts). The **manual single-shot square
   does NOT sweep** (it is MIS and never places an OCO; a tsym-match sweep there could cancel a
   deployed position's legitimate OCO sharing the strike). The double-sell/naked-short impossibility
   rests on the three audit-hardened invariants: derived-wider band + netqty-re-confirm-before-square
   + cancel-after-real-fill.
   Also: **exit product must match the open position** — `square_position` exits with the position's
   own `prd` (NRML for deployed, MIS for manual), not a hardcoded MIS, or an NRML position would not
   net.
4. **OCO-place failure (after the entry filled).** Keep the position **software-guard-only**,
   raise a loud **"no broker backstop on this position"** alert (a new live-page banner, sibling to
   the UNGUARDED banner; also stamp `oco_error` on the `live_trade`), and **retry placement on the
   next guard cycle** (best-effort). **Never** auto-square the filled entry.

### Phase C — reboot reconciliation + depth-aware square

1. **Reboot reconciliation** (extend `runtime.live_startup_recovery`, after
   `rehydrate_from_broker`): for each `live_trades` doc still `OPEN` whose tsym is **flat/absent**
   in the broker position book → look it up in the **trade book** (`#13`, by `norenordno`/tsym);
   if a sell-to-close fill exists, **journal the close** via `close_live_trade` using the **true
   fill price** (`flprc`/`avgprc`). And **cancel any orphan OCO** (`GetPendingGTTOrder` whose
   underlying position no longer exists) so it can't fire on a later unrelated position.
2. **Depth-aware square price.** Add `flattrade_client.get_quotes(exch, token)` → `#54 GetQuotes`;
   in `auto_square.square_position`, price the marketable-limit exit off a **fresh LTP + depth**
   (with `uc`/`lc` sanity) instead of `band_pct` on a possibly-stale `lp`. Store the contract
   `token` at entry (or resolve via the already-wired `SearchScrip`) for both the square and the
   OCO legs.

### Phase D (DEFERRED to a follow-up spec) — real-time fill-feed

Order-Update WebSocket (`#49`) and/or SHA256 Postback (`#55`) to advance `live_orders`
SUBMITTED→COMPLETE in real time and feed the **live** exit fill price into the close-loop (Phase C
already gets the true fill on *reboot* via the trade book; Phase D makes it real-time while
running and cuts the 1.5s poll). Heaviest piece (connection lifecycle / reconnect / heartbeat,
or a public webhook URL on the static-IP VM) — its own brainstorm + spec.

Also deferred to **v2:** **OCO trailing** (`#17 ModifyGTT`/`#22 ModifyOCO`) — ratchet the resting
stop up as the software trail advances; needs a live modify-readback first (the modify docs are
thin and `ModifyOCO`'s `ai_t*` is flagged a likely typo for `al_t`).

---

## Configuration schema (additions)

Per-deployment live-safety (`risk.live`) gains optional overrides; global defaults back them:

```
catastrophe_stop_pct      number  default 50   (45–50)   # premium-loss % → OCO SL trigger
catastrophe_target_pct    number  default 135  (120–150) # premium-gain % → OCO TP trigger
```

Deployed `live_trades` doc gains: `prd` (= "M"), `oco_al_id` (str|None), `oco_error` (str|None),
`token` (for GetQuotes/OCO). No env-gate changes — the OCO rides the existing
`LIVE_AUTOPLACE_ARMED` entry gate; `LIVE_GUARD_ARMED` is unchanged.

## Error handling & edge cases

- **Double-sell / naked short** — prevented by the catastrophe band (OCO wider than the guard) +
  cancel-OCO-before-square + the guard's `netqty==0` drop. (If the OCO fired first, the guard sees
  flat and does not square.)
- **OCO place fails post-fill** — software-guard-only + loud alert + retry; never auto-square.
- **OCO fired while PC down** — caught on reboot (Phase C) and journaled with the true fill.
- **Orphan OCO** (position closed but OCO still resting) — cancelled by the close paths + the
  reboot sweep.
- **NRML not enabled for the account/exchange** — fail-closed at the margin pre-check / arm with a
  clear message (don't place an entry you can't protect).
- **Overnight carry** (the NRML trade-off) — while the PC is on, the guard's 15:00 square still
  flattens; if the PC is down through EOD, the position carries protected only by the resting OCO
  (a stop-*limit*, so a violent gap-through the limit is the residual, accepted risk — documented
  in the readback runbook).
- **Cancel fails** — log + the next reconciliation sweep catches the orphan.
- **Rate limit** (10/s order API) — OCO place/cancel are per-entry/per-exit events, off the hot
  poll path; the reboot sweep is one-shot.

## Testing strategy

Host tests (no broker; existing `FakeDB` + `mock_noren` patterns):
- Catastrophe-band math (stop/target triggers from entry + pct, defaults + per-deployment override).
- `build_oco_intent` wiring from a deployed fill (legs, NRML, qty, `LMT_BOS_O`).
- Cancel-on-close from every path + **no double-sell** (guard `netqty==0` path).
- OCO-place-failure → software-only + `oco_error` + alert flag (no auto-square).
- Reboot reconciliation: OPEN+flat → close-loop with trade-book fill; orphan-OCO cancel.
- Margin pre-check gate (block when `cash < marginused`); margin-parity probe helper.
- GetQuotes-priced square (depth-aware limit, `uc`/`lc` clamp).
- `mock_noren` gains `PlaceOCOOrder`/`CancelOCOOrder`/`GetPendingGTTOrder`/`GetOrderMargin`/
  `GetQuotes` so the full path is exercisable offline.

Broker-side validation (supervised market-hours readback — extends the Monday runbook):
GetOrderMargin parity probe (M vs I) → arm 1 lot NRML → confirm the OCO **rests**
(`GetPendingGTTOrder`) → software square **cancels** it → (separately) let the OCO **fire** and
confirm the sell-to-close fills + the reboot reconciliation journals it.

## Open items to confirm in the readback (not blockers to building)

1. NRML ≈ MIS margin for the actual traded option (GetOrderMargin M vs I).
2. The OCO **fires + the sell-to-close limit clears** at the catastrophe triggers (leg pricing).
3. Cancel-OCO returns success and removes the resting alert (`Al_id` casing handled).

## Non-goals / explicitly deferred

- Phase D real-time fill-feed (WS/postback) — follow-up spec.
- OCO trailing (ModifyOCO) — v2.
- Applying OCO to the manual single-shot (stays MIS).
- Greeks / option-chain strike intelligence (separate enhancement).
