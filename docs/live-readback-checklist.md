# Live Trading — Supervised Real-Money Readback Checklist

> **Purpose:** a step-by-step runbook to validate the real-money live-execution path
> (Flattrade) under hard gates, with full human control.
>
> **Corresponds to:** local `main` @ `4478979` (UNPUSHED — 38 commits ahead of `origin/main`).
> Covers the full live-safety stack: strategy-deploy-to-live · Live-page upgrade (safety pack /
> operator visibility / cleanup / consolidated polling / close-loop) · **PC-down OCO backstop**
> (NRML + resting OCO, margin pre-check, depth-aware square, reboot reconciliation) · **OCO
> observability + net-Greeks** (per-position OCO chip, plan_squareoff product parity, net-Δ/Θ card).
>
> **Standing safety rule:** the assistant NEVER arms, places, squares, or flips a gate — every
> arm / Place / Square / gate-flip is done by **you**. The assistant only watches, verifies
> reads / reconcile / blotter / close-loop, tails logs, and confirms each go/no-go gate.

---

## Timeline (this weekend)

- **Saturday** — Flattrade site under maintenance. Nothing to do.
- **Sunday** — Flattrade login works, but the **market is closed**. Do everything in **§B
  (read-only / connectivity / render checks)**. You CANNOT open a position, so the OCO / fill /
  square / Greeks-with-a-position checks must wait.
- **Monday (market open, 09:15–15:30 IST)** — the real readback: **§C pre-market**, then **§D**
  staged sequence, then **§E** stand-down.

> Re-verify every value below live — don't trust this doc's numbers.

---

## 0. State to RE-VERIFY at the start

Check `GET /api/flattrade/status` and `/live-trading`:

- **Env gates** (`backend/.env`): `LIVE_AUTOPLACE_ARMED` and `LIVE_GUARD_ARMED` — both must be
  `0` (safe) until a stage needs them. Confirm on the **Execution-State strip** (green "SAFE").
- **Flattrade:** `connected: true`, `expired: false`, your UID, your **static IP** present.
- **Deployments:** which are `ACTIVE`; none `armed-live` yet.
- **Running stack == `main`:** rebuild with `docker compose up -d --build` so the running code is
  `4478979` (the OCO-backstop + Greeks work). The Greeks card + OCO chip only exist on this build.

---

## A. Decisions to lock FIRST

1. **How far to go.**
   - **Stage 2 — manual single-shot (MIS, no OCO):** you place + square ONE real order by hand.
     Validates transmit → fill → square → close-loop **and** that the **Greeks card prices a live
     position**. Needs no strategy signal, no OCO. *Recommended first real-money touch.*
   - **Stage 3 — armed auto-place (NRML + resting OCO):** the ONLY path that exercises the
     **PC-down OCO backstop** (a deployed NRML entry places the resting OCO). Tests the full
     deploy-to-live path + the OCO chip + cancel-on-square + reconcile. **There is no manual
     "place an NRML+OCO" path** — OCO validation requires a real deployed trade.
   - ⚠️ If your only ACTIVE strategy is a known loser (`confluence_scalper`), a Stage-3
     validation trade can take a small **real loss**. Either accept it as the cost of validating
     the backstop, or **activate a strategy you actually want live** before Stage 3.
2. **Lots + caps for any live arm** (you set these — they override strategy sizing):
   *recommended 1 lot/signal, max_lots/day = 1, max_concurrent = 1, daily-loss-cap ≈ ₹2–3k.*
   **Account ceiling is 20 lots (hard).**
3. **Catastrophe band (PC-down OCO levels) — Stage 3 only.** On the **Deploy-to-Live** arm form,
   the two optional fields **"Catastrophe stop %"** / **"Catastrophe target %"** set the resting
   OCO band. **Leave them blank** to use the derived defaults (stop floor **50%**, target **135%**;
   the stop is auto-widened to stay ≥15pp clear of the software-guard stop, so the guard always
   exits first while the PC is on and the OCO is a pure last-resort). Set them only if you want a
   tighter/wider catastrophe band.
4. **Order price style:** Flattrade is **limit / SL-limit only — NO market orders.** Use a
   *marketable* limit (slightly through the touch) on a liquid ATM option.
5. **Guard staging:** enable real auto-exits (`LIVE_GUARD_ARMED=1`) alongside entries, or watch
   the guard in dry-run first then flip it. A true readback wants both on eventually.

---

## B. SUNDAY — Flattrade UP, market CLOSED (read-only / connectivity / render)

Everything here is safe (no order can fill with the market closed). Both gates stay `0`.

- [ ] **Re-login to Flattrade** (daily OAuth token; expires ~6 AM IST). Banner = **Connected ·
      <your UID> · static IP ✓**, `expired: false`.
- [ ] **Confirm the PC is on the Flattrade-registered static IP.** If the ISP changed it over the
      weekend, **every order Monday is broker-rejected** — re-register the new IP now.
- [ ] **Rebuild the stack from `main`:** `docker compose up -d --build` (repo dir, on `main`).
      Confirm all 3 containers up + backend healthy.
- [ ] `/live-trading` loads: Execution-State strip = **green "SAFE — no live entries armed"**;
      Positions / Orders / Available-Cash tiles populate; Reconcile chip green; no UNGUARDED banner.
- [ ] **Greeks card renders** (below the Live Deployment Blotter): "PORTFOLIO GREEKS", Net Δ /
      Net Θ. With no open positions it shows **₹0 / ₹0 + "No open live positions."** — confirm it
      renders with **no error** (the populated numbers come Monday with a live position).
- [ ] **`GET /api/live-broker/greeks`** returns **200** with
      `{net_delta_rupees_per_point, net_theta_rupees_per_day, n_computed, n_skipped, positions}`
      (zeros when flat) — never a 500.
- [ ] **No console errors** on `/live-trading` (DevTools → Console).
- [ ] *(optional)* **Margin pre-check sanity:** `GET /api/live-broker/margin-probe?exch=NFO&
      tsym=<an ATM option tsym>&qty=<lot size>&prc=<a plausible premium>` returns `stat:"Ok"` with
      `cash` + `marginused`. (Real parity confirmation is Monday with the actual traded premium.)

> Items that **cannot** be done Sunday (need an open position → Monday): OCO rests / OCO chip /
> Greeks populated with a position / square-cancels-OCO / OCO-fires / reboot-reconcile /
> kill-switch sweep / margin parity on the real fill / depth-aware square price.

---

## C. MONDAY pre-market (before 09:15 IST)

- [ ] Re-login if the token rolled over again (~6 AM IST) — banner **Connected**, `expired:false`.
- [ ] Re-confirm the **static IP** (re-check; ISPs can change it overnight).
- [ ] **Funds / margin** in the account for ≥ 1 lot (NRML for Stage 3, MIS for Stage 2 — both ≈
      the full option premium for a long buy).
- [ ] Stack still == `main` (`docker compose up -d --build` if unsure). **Both gates `0`.**
- [ ] Pick + (if Stage 3) **ACTIVATE the strategy you want to validate** on the Paper page.

---

## D. MONDAY market-hours — staged real-money readback

The **Execution-State strip** is your source of truth at every gate.

### Stage 1 — connectivity (gates OFF)
- [ ] Banner = **Connected**; strip = **green "SAFE — no live entries armed"**.
- [ ] Positions / Orders / Available-Cash tiles load with **live** quotes (market open now).
- [ ] Reconcile chip green; **no UNGUARDED-positions banner**.

✅ → proceed.

### Stage 2 — manual single-shot (MIS · human-gated · NO env gate · NO OCO)
- [ ] Order Ticket: 1 lot, ATM option, marketable **LIMIT** (SELL disabled — long-only).
- [ ] Strip flips **red "LIVE · entries: TRANSMIT"** when armed.
- [ ] **Place → confirm** (REAL order the instant you confirm — be deliberate).
- [ ] **PositionMonitor** shows the live position + 10-min countdown; it appears in **Open
      Positions** + the **Live Blotter** (status LIVE, broker MTM).
- [ ] **Greeks card populates:** Net Δ (₹/point) and Net Θ (₹/day) now show **non-zero** values
      from this position; card says **"1 of 1 priced"**. *(A manual MIS position has NO OCO, so
      NO "OCO ✓" chip and NO "no broker net" chip on its blotter row — that's correct.)*
- [ ] **Square now** in PositionMonitor (or let the 10-min backstop). Confirm FLAT/CLOSED.
- [ ] **Close-loop:** the row shows **CLOSED + realized P&L** in the Live Blotter and the Journal
      **Live** lane; Greeks card returns to ₹0 / "No open live positions".

✅ = real transmit + read + square + close-loop + Greeks-pricing all work, zero env gates.

### Stage 3 — armed auto-place (NRML + resting OCO) — the PC-down backstop path
Do this only per §A.1 (accept the possible validation loss, or use a strategy you want live).

- [ ] (a) **Arm the deployment on the Paper page** (makes it deployable to live).
- [ ] (b) On `/live-trading`, open **Deploy-to-Live**, set your lots + caps, and the **catastrophe
      band** fields (leave blank for the 50% / 135% derived defaults). Arm it **LIVE — BEFORE
      15:00 IST** (arming is rejected after 15:00). The confirm summary notes "deployed entries arm
      as NRML with a resting OCO backstop".
- [ ] (c) Set `LIVE_AUTOPLACE_ARMED=1` (+ `LIVE_GUARD_ARMED=1` for real auto-exits) in
      `backend/.env`, then `docker compose up -d backend` (add `--force-recreate` if the gate
      doesn't take — verify on the strip).
- [ ] (d) Strip shows **LIVE · entries: TRANSMIT** (+ **auto-squares: TRANSMIT** if guard armed).
- [ ] **Margin pre-check (Gate 3):** when a signal fires, confirm in the backend log that the
      pre-trade `GetOrderMargin` ran and **passed** (`cash ≥ marginused`) — an unaffordable order
      is blocked *before* transmit, not rejected after.
- [ ] **Margin parity (decision-critical):** for the actual traded option, **NRML `marginused` ≈
      the full premium** (premium × lot size). If NRML margin is materially higher than the
      premium, the "NRML carry is margin-neutral for a long option" assumption is **wrong** —
      STOP and reassess before relying on the backstop (you'd be tying up unexpected capital).
- [ ] Signal → **1-lot NRML auto-place** → it appears in the **Live Blotter** + **GuardPanel**
      (guard watches stop / target / spot-mirror / time-stop / 15:00 EOD).

### Stage 4 — PC-down OCO backstop deep checks (with the Stage-3 position open)
- [ ] **OCO rests at the broker:** the **GTT / OCO book** (GttBook panel, or
      `GET /api/live-broker/gtt`) shows a resting OCO for this position, with two SELL legs
      (SL trigger `x`, TP trigger `y`) in the **catastrophe band** (wider than the guard's stop).
- [ ] **OCO chip:** the position's **Live Blotter** row shows a green **"OCO ✓"** chip; hovering
      it shows the resting **SL ₹x · TP ₹y** band. *(If a chip shows the amber "no broker net"
      instead, the OCO failed to place — that position is software-guard-only; note it.)*
- [ ] **Greeks card:** Net Δ / Net Θ include this position; **"N of N priced"** with N matching the
      open count. ⚠️ If it says **"N of M priced" with M < N** (a position not priced), the
      Greeks contract resolver (`SearchScrip` by tsym + underlying-prefix fallback) didn't return
      the contract on the live API — note the tsym; the documented fallback is to persist
      strike/expiry/side at arm time. *(Δ/Θ are still correct for the priced positions.)*
- [ ] **plan_squareoff product parity:** open the **Kill-switch** preview (do NOT execute) — the
      planned flatten for this deployed position shows product **NRML (`M`)**, not MIS — matching
      what the panic path would actually transmit.
- [ ] **Square cancels the OCO (normal exit):** square via the guard (let a stop/target/time-stop
      fire, or square manually). Confirm: the position goes FLAT/CLOSED **and** the resting OCO is
      **cancelled** (gone from the GTT/OCO book) — the cancel happens **after** the confirmed real
      square fill (no orphan OCO left behind).
- [ ] **Depth-aware square price:** the square used a fresh `GetQuotes` mid (not a stale mark) —
      sanity-check the exit price is sane vs the live bid/ask.

#### Stage 4b — OCO-fires-while-PC-down + reboot reconciliation (optional, deeper)
Only if you want to prove the core PC-down promise. **This intentionally lets a real OCO fire.**
- [ ] With a Stage-3 position open + its OCO resting, **simulate the PC going down**: stop the
      backend (`docker compose stop backend`) so the software guard is dead but the broker's OCO
      stays resting.
- [ ] Let the market move (or accept it may not hit a 50%/135% band intraday) — if a leg triggers,
      the **broker sells to close** with no local process alive. *(If neither leg triggers in your
      window, you've still proven the OCO RESTS while the PC is down — bring the backend back up.)*
- [ ] **Restart the backend** (`docker compose up -d backend`). On startup the **reboot
      reconciliation** runs: if the OCO fired, the trade is **journaled CLOSED with the true fill
      price** (Live Blotter + Journal Live lane); a still-resting OCO on a still-held position is
      **re-linked** to the guard (so a later square can cancel it); an orphan OCO (position gone)
      is **swept/cancelled**. Confirm the blotter/journal reflect reality, not a stale OPEN.

#### Stage 4c — kill-switch sweep (optional)
- [ ] With a deployed NRML position + resting OCO, hit the **Kill switch** → confirm it **flattens
      the position in its own product (NRML)** AND **sweeps the resting OCO** (the GTT/OCO book is
      cleared) — no naked short, no orphan alert left armed.

### Stage 5 — close-loop + stand down
- [ ] Every squared trade shows **CLOSED + realized P&L** in the Live Blotter AND the Journal
      **Live** lane; reconcile chip green; no orphan OCO in the GTT/OCO book; Greeks card back to ₹0.
- [ ] **Disarm** the deployment; set **both gates → 0**; `docker compose up -d backend`.
- [ ] Strip back to **green SAFE**. Account flat. No resting GTT/OCO.

---

## E. Abort controls — know these BEFORE you start

| Control | Where | Effect |
|---|---|---|
| **Kill switch** | PositionMonitor (red) | Flattens everything **in each position's own product** + **sweeps all resting GTT/OCO** |
| **Square now** | PositionMonitor | Closes the manual position (cancels its OCO if any, after the real fill) |
| **Stand down** | Execution-State strip | Reverts manual LIVE_TEST → SAFE (stops further manual transmits) |
| **Stop / Stop-all** | Live Deployment strip | Disarms + squares a deployment / all deployments (cancels their OCOs) |
| **Hard off** | `backend/.env` | Both gates → 0 + `docker compose up -d backend`; for the manual path also Stand-down / don't click Place |

- **15:00 IST:** deployed positions auto-square (if guard armed); no new arming after 15:00.
- **Broker-resting OCO survives a backend stop** — to remove it, either let a close path cancel it,
  or cancel it from the GTT/OCO book (or the Flattrade terminal).

---

## F. Why each gate exists (so you trust the strip)

- **Manual single-shot entry (MIS)** transmits when: **connected** + you arm LIVE_TEST + click
  Place. No env gate — purely human-gated. No OCO is placed on the manual path.
- **Deployment auto-entry (NRML + OCO)** transmits when: **connected** + an **armed** deployment +
  `LIVE_AUTOPLACE_ARMED=1`. The resting OCO is placed best-effort **after** the entry registers
  with the guard (a failed OCO never unwinds a filled entry — it just flags "no broker net").
- **Guard auto-exit** transmits when: **connected** + `LIVE_GUARD_ARMED=1`. The guard is a pure
  software *exit* loop; it **never auto-retries an OCO** (no order-placing path in the poll loop).

The Execution-State strip computes and shows exactly this (`would_transmit_entry` /
`would_transmit_exit`) — if it says SAFE/dry-run, nothing real will fire.

---

## G. New-feature live-check matrix (quick reference)

| Feature (since `0722ac2`) | Live check | Where | Stage |
|---|---|---|---|
| NRML product for deployed entries | auto-placed order is `prd=M` (NRML) | Live Blotter / order log | 3 |
| Resting OCO on a deployed fill | OCO appears in the GTT/OCO book, 2 SELL legs, catastrophe band | GttBook / `/gtt` | 4 |
| Catastrophe band derived ≥15pp wider than guard | OCO SL trigger is a lower premium than the guard stop | GttBook vs GuardPanel | 4 |
| **OCO chip** | green "OCO ✓" + SL/TP tooltip on the LIVE row | Live Blotter | 4 |
| "no broker net" chip | amber chip iff the OCO failed to place | Live Blotter | 4 |
| **Net-Δ/Θ Greeks card** | non-zero Net Δ/Θ + "N of N priced" with a position open | below the blotter | 2 (MIS) + 4 (NRML) |
| Greeks resolver coverage | "N of N" (not "N of M", M<N) on the live API | Greeks card | 4 |
| **plan_squareoff parity** | kill-switch preview shows NRML `M` for deployed | Kill-switch preview | 4 |
| Margin pre-check (Gate 3) | unaffordable order blocked before transmit | backend log | 3 |
| Margin parity (M vs I) | NRML marginused ≈ full premium (long option) | margin-probe / log | 3 |
| Cancel-OCO-after-real-fill | square removes the OCO (no orphan) | GTT/OCO book | 4 |
| Depth-aware square (GetQuotes) | exit price = fresh bid/ask mid, not stale | square fill vs book | 4 |
| Reboot reconciliation | OCO-fired-while-down → journaled CLOSED w/ true fill; orphan swept; re-link | startup log / blotter | 4b |
| Kill-switch OCO sweep + product | flattens NRML + clears all resting OCO | PositionMonitor → GTT book | 4c |
