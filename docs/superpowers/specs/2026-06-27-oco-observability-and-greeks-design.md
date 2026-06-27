# Live OCO Observability + Net-Greeks — Design

> **Status:** approved design (brainstorm 2026-06-27). Next: writing-plans → implementation.
> **Branch:** `feat/pc-down-oco-backstop` (worktree `af-wt-oco`), continuing the PC-down OCO
> backstop work. Builds on [[2026-06-26-pc-down-oco-backstop-design]].
> **Anchor:** three follow-ups that make the just-shipped PC-down OCO backstop **observable** and
> add the one option-buyer risk number that is otherwise invisible — scoped down from a longer
> wishlist after an explicit value pass (margin-probe button and the per-position Greeks grid were
> dropped as low-value; see "Explicitly dropped").

## Goal

1. **plan_squareoff parity** — the kill-switch *preview* shows each position in its own product
   (NRML vs MIS), matching what the executed panic path now transmits.
2. **OCO chip** — each live position shows whether a broker OCO backstop is **resting** (and at what
   SL/TP), so the operator can confirm the PC-down protection exists without opening the broker
   terminal.
3. **Net-Δ/Θ card** — a compact portfolio card showing **net delta (₹ per index point)** and **net
   theta (₹/day decay)** across open live positions, computed server-side via Black-Scholes with IV
   solved from the live premium.

All three touch only the **read/observability** surface. **None** touch the order-transmit path.
The assistant never transmits or squares a real order.

## Why these three (the value pass)

The system **never acts on Greeks** — exits are governed by premium stops/targets/trails/time-stops
plus the resting OCO. So a per-position Δ/Γ/Θ/V grid is decoration; the only decision-relevant,
otherwise-invisible numbers for an option buyer are **net theta (daily decay "rent")** and **net
delta (aggregate index exposure)**. The OCO chip is the missing half of the backstop feature: an
unobservable safety net is half-built. plan_squareoff parity finishes an already-landed safety fix.

## Verified Flattrade API facts (decoded reference, chapter 3 Market + #54/#57)

- **No Flattrade endpoint exposes market IV.** `#31 GetOptionChain` returns contract **metadata
  only** (`exch/tsym/token/optt/strprc/pp/ti/ls`) — no LTP, no IV. `#32 GetOptionGreek` is a
  **forward** Black-Scholes calculator: it **consumes** `volatility` as an input and returns
  price + Δ/Γ/Θ/ρ/vega; it does **not** return IV. So any path must solve IV from the premium
  first — at which point a local BS is strictly better than a network round-trip to #32.
- **`#54 GetQuotes` returns the option premium and the underlying spot in one snapshot at one
  timestamp** (`lp`, 5-level depth `bp1/sp1…`, `uc/lc`, and `sptprc` [Spot Price] + `und_tk`/
  `und_exch` for derivatives). This is the single consistent `(premium, spot)` source the IV solve
  needs — no cross-feed timestamp skew.
- **Rate rule (#57):** general API **40/s, 200/min**; the stricter **10/s, 40/min** applies only to
  the *order* API. GetQuotes is general-API, so per-position Greeks polling carries no order-API
  pressure. Keep Greeks off the ~1.5s guard loop regardless (compute on the FE's slow poll).

**Decision:** server-side Black-Scholes, IV-from-premium, sourced entirely from GetQuotes. Rejected:
GetOptionGreek (#32) — must supply IV anyway, so the call is pure overhead; Upstox stream Greeks —
exchange-grade but couples live Greeks to the data-only feed's per-strike subscription, dies on a
feed outage, and is hard to test deterministically. (Upstox IV remains a possible future
cross-check, not the engine.)

---

## Components & data flow

### A. plan_squareoff product parity (backend only)

- `backend/app/live/kill_switch.py` `plan_squareoff(...)` builds each preview row's exit
  `OrderIntent` at **line 343** with a hardcoded `prd="I"`. Change it to
  `prd=(str(pos.get("prd")) if pos.get("prd") else "I")` — **byte-identical** to the already-fixed
  `panic_squareoff` (line 513). The position row carries `prd` from the broker Positions Book.
- The route (`POST /live-broker/kill-switch`, `live_broker.py:1958` degraded + `:1961` connected)
  returns the plan dict as-is; the preview now matches what panic would transmit. No FE change (the
  FE does not render the per-row product).
- The `str(None)` trap: `str(pos.get("prd")) or "I"` yields `"None"` for a missing key (truthy);
  the `if pos.get("prd") else "I"` form is correct for present-"M" / present-"I" / missing → "I".

### B. OCO chip (backend one line + frontend)

1. **Backend.** `backend/app/live/live_blotter.py` `build_live_blotter(...)` already passes
   `oco_error` on each row; add `"oco_al_id": t.get("oco_al_id")` (the `live_trades` doc carries it,
   set at `auto_live.py` entry journaling). One line + a passthrough test.
2. **Frontend** (`frontend/src/components/live/LiveBlotter.jsx`), per **OPEN** row, in the status
   cell (where the amber "no broker net" chip already lives):
   - `oco_error` truthy → keep the existing amber **"no broker net"** chip (unchanged).
   - else `oco_al_id` truthy → green **"OCO"** chip. Tooltip = **`SL ₹{x} · TP ₹{y}`**, obtained by
     matching `oco_al_id` against the `gtt` OCO book already in `LiveDataProvider` context
     (`gtt.find(g => (g.al_id || g.Al_id) === row.oco_al_id)`), reading the two `oivariable` legs
     (`var_name:"x"` = SL trigger `d`, `var_name:"y"` = TP trigger `d`).
   - else → render nothing (no clutter).
   - If `oco_al_id` is present but no matching `gtt` entry is found (book not yet refreshed, or the
     OCO already fired/cancelled), show the **"OCO"** chip without levels (tooltip: "resting OCO").
   - CLOSED/FLAT rows show no OCO chip.

   Chip styling matches the existing pattern: amber = `border-amber-500/40 bg-amber-500/10
   text-amber-300`; the positive OCO chip uses the emerald pattern already used for the guard
   "Filled" pill: `border-emerald-500/50 bg-emerald-500/10 text-emerald-300`.

### C. Net-Δ/Θ card (backend math + orchestration + route + frontend card)

1. **`backend/app/live/greeks.py`** (NEW — pure math, **no I/O**, numpy + `math` only):
   - `norm_cdf(x)` via `math.erf` (no scipy).
   - `bs_price(spot, strike, t_years, rate, vol, is_call)`, `bs_delta(...)`, `bs_gamma(...)`,
     `bs_vega(...)`, `bs_theta_per_year(...)` — standard Black-Scholes (continuous, no dividend;
     Indian index options have no carry beyond rate).
   - `implied_vol(premium, spot, strike, t_years, rate, is_call) -> (iv | None, confidence)`:
     Newton's method on vega with a **bisection fallback** over a bracketed band; IV clamped to
     `[IV_MIN=0.01, IV_MAX=5.0]` (1%–500%); returns `None` when the premium is below intrinsic or
     the solve cannot bracket; `confidence="low"` when vega at the solution is below a threshold
     (deep ITM/OTM — Δ/Θ still reportable, IV unreliable).
   - `compute_greeks(spot, strike, t_years, rate, premium, is_call) -> dict | None`:
     `{iv, delta, gamma, theta_per_day, vega, confidence}` where `theta_per_day =
     bs_theta_per_year / 365.0` (calendar-day decay). Returns `None` if IV is unsolvable.
   - Input guards: `spot>0`, `strike>0`, `t_years>0` (caller floors it), `premium>0`; otherwise
     `None`.
2. **`backend/app/live/portfolio_greeks.py`** (NEW — orchestration with **injected** dependencies
   so it is testable with no network):
   - `compute_portfolio_greeks(positions, *, get_quote_fn, resolve_contract_fn, today, spot_fallback=None, rate=RATE)
     -> dict`.
   - **Position source:** the route passes the **guard registry snapshot**
     (`_get_live_registry().snapshot()`) — the app's view of managed live positions — because that
     is where the contract `token` reliably lives (refreshed each guard cycle); each entry carries
     `tsym`, `exch`, `prd`, and a signed quantity (`position.netqty`, fall back to `qty`).
   - Per position:
     - **Contract:** `(strike, expiry_date, is_call, token) = resolve_contract_fn(tsym, exch)`
       (route resolves from the `option_contracts` collection → SearchScrip fallback → `None`); the
       resolver also supplies `token`, so a missing/None registry token is recovered here.
     - **Quote:** `q = get_quote_fn(exch, token)`; premium = bid/ask mid `(bp1+sp1)/2` when both
       present and sane, else `lp`; spot = `q["sptprc"]` when present, else the injected
       `spot_fallback` (the route passes the guard's spot value); if neither → skip.
     - **TTE:** `t_years = max(days_to_expiry, INTRADAY_FLOOR_DAYS) / 365.0`, days from
       `expiry_date - today` (calendar). `INTRADAY_FLOOR_DAYS = 0.25` avoids div-by-zero on 0DTE.
     - `g = compute_greeks(...)`; if any input/contract/quote is missing or `g is None`, the
       position is **skipped** and counted in `n_skipped` (never raises).
   - **Aggregate (signed netqty, so shorts net correctly):**
     `net_delta_rupees_per_point = Σ g.delta × netqty` (₹ P&L per 1 index point);
     `net_theta_rupees_per_day = Σ g.theta_per_day × netqty` (negative = decay cost for longs);
     plus `n_computed`, `n_skipped`, and the per-position list (for transparency/testing). Deployed
     positions are long buys (netqty > 0); signed qty keeps the math correct if a short ever appears.
   - `RISK_FREE_RATE = 0.065` module constant.
3. **Route** `GET /live-broker/greeks` in `backend/app/routers/live_broker.py`:
   - Fail-soft: not connected / no open positions → `{net_delta_rupees_per_point: 0,
     net_theta_rupees_per_day: 0, n_computed: 0, n_skipped: 0, positions: []}` (HTTP 200).
   - Wires the real `client.get_quotes`, an `option_contracts` resolver, the guard's spot value, and
     `date.today()` (IST) into `compute_portfolio_greeks`. General-API call; not on the hot path.
4. **`flattrade_client.get_quotes` + MockNoren:** the real `get_quotes` returns the raw GetQuotes
   dict on success (so `sptprc`/`bp1`/`sp1`/`uc`/`lc` are already present) and `{}` on non-Ok —
   confirm it is **not** trimmed to `{stat, lp, token}`; if it is, widen it to pass the full snapshot
   through. `MockNoren.get_quotes` must return `sptprc` + `bp1/sp1` so the route/orchestration tests
   exercise the real path offline.
5. **Frontend:**
   - `frontend/src/lib/api.js`: `getLiveGreeks: () => apiClient.get("/live-broker/greeks").then(r => r.data)`.
   - `frontend/src/components/live/LiveDataProvider.jsx`: add a `greeks` slice on the **slow (15s)**
     poll (Greeks drift slowly), expose `greeks` + `errors.greeks` + `refetch` coverage in context.
   - `frontend/src/components/live/GreeksCard.jsx` (NEW): a compact card showing **Net Δ (₹/point)**
     and **Net Θ (₹/day)** with a small "n of m positions priced" note (from `n_computed` /
     `n_computed + n_skipped`); zeros render a neutral "—". Mounted on the live dashboard near the
     open-positions summary. The route returns per-position data, but the UI renders **only the net
     card** (per scope).

---

## Configuration

- `RISK_FREE_RATE = 0.065` (module constant in `greeks.py` / `portfolio_greeks.py`); no env gate.
- `IV_MIN = 0.01`, `IV_MAX = 5.0`, `INTRADAY_FLOOR_DAYS = 0.25`, low-vega confidence threshold —
  module constants, documented inline.
- No new env gates. No change to `LIVE_AUTOPLACE_ARMED` / `LIVE_GUARD_ARMED`.

## Error handling & edge cases

- **Greeks never raise into the page** — per-position failures (no quote, no contract, sub-intrinsic
  premium, unsolvable IV) are skipped and surfaced as `n_skipped`; the route is fail-soft.
- **Deep ITM/OTM** — vega ≈ 0 → IV solve ill-conditioned → `confidence="low"`; Δ/Θ still computed.
- **0DTE / near expiry** — `t_years` floored at `INTRADAY_FLOOR_DAYS/365` to avoid div-by-zero.
- **Mid vs last** — premium prefers the bid/ask mid (`bp1/sp1`); falls back to `lp` when depth is
  empty/zero, so a stale last-trade doesn't poison the IV.
- **Spot source** — `sptprc` from the option's GetQuotes first (same instant as the premium); the
  guard's spot value only as a fallback.
- **OCO chip with no matching gtt entry** — chip shows without levels (al_id present = it was
  placed); no error.
- **plan vs panic parity** — both flatten paths now read the position's own `prd`.

## Testing strategy (scrupulous — host tests, no broker)

- `tests/test_greeks.py` (NEW): put-call parity (`C − P ≈ S − K·e^{−rT}`), Δ bounds (call ∈ [0,1],
  put ∈ [−1,0]), Γ > 0, vega > 0, **IV round-trip fuzz** (price at vol₀ → `implied_vol` ≈ vol₀
  across a moneyness × TTE × vol grid), Θ sign (long Θ < 0) + per-day scaling, and the edge cases
  (deep ITM/OTM low-confidence, near-expiry floor, sub-intrinsic → `None`, non-positive inputs →
  `None`).
- `tests/test_portfolio_greeks.py` (NEW): net aggregation across injected mock positions; mid-vs-lp
  premium selection; `sptprc`-vs-fallback spot; `n_skipped` on unresolved contract / missing quote;
  empty list → zeros; never raises.
- `tests/test_live_greeks_route.py` (NEW): route wiring with `MockNoren.get_quotes`, the
  `option_contracts` resolver, fail-soft (not connected / empty), and the response shape.
- `tests/test_live_kill_switch.py`: `TestPlanSquareoff` parity — NRML row → `would_flatten[0]["prd"]
  == "M"`; missing prd → `"I"`; existing plan tests stay green.
- `tests/test_live_blotter.py`: `oco_al_id` passthrough on the row (present + absent).
- Frontend: `CI=true npm run build` (post-merge, in the main repo — the worktree has no
  `node_modules`) + Chrome verify of the OCO chip + the Greeks card.

## Explicitly dropped (value pass)

- **Margin-probe button** — the automatic margin *gate* at transmit already blocks unaffordable
  trades; the NRML-margin parity check is a one-time readback better served by a throwaway call,
  not permanent per-row UI.
- **Per-position Δ/Γ/Θ/V grid** — the system does not act on Greeks; the grid is decoration. Only the
  net-Δ/Θ card is decision-relevant.
- **Greeks on the guard loop / real-time** — Greeks compute on the FE slow poll; the ~1.5s guard
  loop stays a pure exit loop.
