# Live Execution Safe Core (L0–L2) — design spec

- Date: 2026-06-21
- Status: Draft for review
- Scope: The host-testable, **zero-real-order** foundation of AlphaForge live trading via Flattrade. L3 (real 1-lot Offline/One-Click) and L4 (fully-auto) are **separate, user-gated specs**.
- Route: new `/live-trading` (Execution nav group)

## 1. Context

AlphaForge executes nothing live today — the broker layer is Upstox **data-only**; there is **zero broker-order code** anywhere. This spec builds the greenfield Flattrade execution foundation, but deliberately stops short of placing a real order: everything here is provable correct with a **mock broker** and host tests, so the dangerous parts are fully audited before a rupee is at risk.

**Reuse spine (the live engine is a new *sibling*, not a fork of paper):**
`execution_policy.py` (exit-level truth, sim/paper/live parity), `deployment_kill_switch.py` (paper-only switches → promoted), `paper_squareoff.py` + `/deployments/{id}/stop` + `/stop-all` (square-off template), `live_exit_monitor.py` (tick loop), `paper_auto.resolve_deployment_lots` (sizing replay), `live_friction.py`, `upstox_client.py` + the Upstox token store (the client + token patterns to mirror), `live_option_universe.py` (contract/expiry source for symbol mapping).

## 2. Scope decision

This is one cohesive sub-project: **everything I can build AND fully test myself, with no real orders.** It maps exactly to the research's L0–L2 and to the testability boundary.

- **In scope (L0–L2):** Flattrade async client; daily-token auth; Upstox→Noren symbol resolver; read-only account/positions/orders + reconciliation; the pre-trade safety engine; kill switch; order state machine; a **mock Noren broker**; the Live Trading page (display-only + safety controls + a **dry-run** order ticket).
- **Out of scope (separate specs, user-gated):** L3 = real `/PlaceOrder` in **Live-Offline (alert-only)** then **Live One-Click (confirm each)**; L4 = Fully-Auto strategy execution. No real order fires until the user validates L3 at 1 lot.

## 3. Locked decisions

- **Rollout = offline-first:** Paper → Live-Offline → Live One-Click → Fully-Auto (L3/L4, later).
- **Real read-only, mock orders:** read endpoints hit the **real** Flattrade API (user does daily login); all **order-placement runs against `MockNoren`** in this spec.
- **Client = thin custom async httpx client** (mirrors `upstox_client.py`), NOT the dated sync NorenRestApiPy lib.
- **Symbol mapping via `/SearchScrip`** (resolve to the broker's exact `tsym`), not hand-constructed symbols.
- **Exit parity invariant:** live exit *decisions* route through `execution_policy.py`; live differs only in *actuation*.

## 4. Flattrade hard constraints (verified — these constrain everything)

- Transport: `POST` to `https://piconnect.flattrade.in/PiConnectAPI/<Route>` with body `jData=<json>&jKey=<susertoken>`. WS `wss://piconnect.flattrade.in/PiConnectWSAPI/`.
- **Order types via API: `LMT` and `SL-LMT` ONLY** — `MKT`/`SL-MKT`, Cover/Bracket, IOC are rejected (v2). The ticket must not render "Market".
- `prd`: **`I`** (MIS/intraday) for option-buying. `ret`: **`DAY`**. `trantype`: `B`/`S`. `qty` = lots×lot_size as a **string**, exact lot-size multiple.
- **`SL-LMT` requires `trgprc`** (trigger) AND `prc` — omit `trgprc` → error.
- **Daily `jKey`** (OAuth: API key+secret → daily token, interactive TOTP), valid ~24h, invalidated ~5–6 AM IST → **regenerate after 6 AM**.
- **Static IP mandatory** (orders only from the whitelisted IP); **<10 orders/sec**; one API key/user.
- Order-update WS `om` messages carry `status`, `reporttype`, `rejreason`, `fillshares` (cumulative), `avgprc`. Lifecycle: `PENDING→NEW/OPEN`, `TRIGGER_PENDING→OPEN→COMPLETE`, or `REJECTED`/`CANCELED`.
- **Symbol hazard (Tier-0):** the `option_contract` carries an **Upstox** symbol; Flattrade needs the **Noren `tsym`** on `NFO` (NIFTY/BANKNIFTY) / `BFO` (SENSEX). Wrong `tsym` = silent reject or wrong-strike fill.

## 5. Architecture & components

New module group `backend/app/live/` (keeps the live engine bounded + separable):

- **`live/flattrade_client.py`** — async httpx client. Methods: `place_order`, `modify_order`, `cancel_order`, `order_book`, `position_book`, `trade_book`, `limits`, `single_order_history`, `search_scrip`; plus an `order_update` WS consumer (dispatch on `t`; `om`→callback). Pure transport; takes a `jKey` provider. A `BrokerClient` Protocol so `MockNoren` is a drop-in.
- **`live/flattrade_token.py`** — daily-token store in Mongo (`live_broker_tokens`), OAuth exchange, `get_status()` (connected / expired / static_ip_ok / regenerate-after-6AM), mirroring `upstox_client`.
- **`live/flattrade_symbol.py`** — `resolve(option_contract) -> {tsym, token, exch, lot_size}` via `search_scrip` + a Mongo cache (`live_symbol_map`); lot-size cross-check vs the warehouse (NIFTY 65 / SENSEX 20 / BANKNIFTY 30, BANKNIFTY-35 overlap flagged). **Pure mapping fn + a parity test battery.**
- **`live/safety.py`** — pure pre-trade checks: `check_fat_finger(intent, cap)` (default-deny if no cap), `check_price_band(intent, ref_ltp, pct)` (block on stale/no ref), `throttle` (token-bucket; **never blocks a cancel/exit**), `validate_order_jdata(intent)` (lot multiple, SL trgprc present, allowed prctyp/prd/ret). Returns structured allow/deny + reason.
- **`live/idempotency.py`** — `client_order_id` (UUID) generation + a persisted intent store (`live_orders`): write **INTENT before any POST**; never re-POST an intent that already has a `norenordno`. Restart-survivable.
- **`live/order_sm.py`** — order state machine fed by `om` events + `single_order_history`: `INTENT→SUBMITTED→ACKED→OPEN/TRIGGER_PENDING→PARTIAL→COMPLETE / REJECTED / CANCELED`; tracks `fillshares`/`avgprc`; classifies rejections (transient vs terminal) for bounded retry (always reusing the idempotency key).
- **`live/reconcile.py`** — fetch `order_book`+`position_book`, diff vs `live_orders`/internal positions; **halt-and-alert on mismatch**; run on startup, reconnect, and a timer.
- **`live/kill_switch.py`** — `panic_squareoff()`: (a) block new entries (set a latch), (b) cancel all working orders, (c) flatten open positions via marketable-limit exits; operable from a dedicated route independent of any signal loop; cancels bypass the throttle. Plus the **account-level guardrail** evaluator: broker-level max-loss / max-profit-lock / max-open / auto-squareoff-time, with a **"blocked until manual reset"** latch after a trip.
- **`live/mock_noren.py`** — deterministic mock `BrokerClient`: scripted `stat`/`norenordno`/`rejreason` and an injectable `om` event stream, so every safety/kill/reconcile/state path is host-tested.
- **`routers/live_broker.py`** — routes (see §7).
- **Frontend `pages/LiveTrading.jsx`** + `components/live/*` — display-only account/positions/working-orders, the safety-rails panel, broker-status + token countdown, the bold LIVE banner, and a **dry-run order ticket** (builds+validates jData, shows what it *would* send; submit disabled in L0–L2). Reuses the redesigned paper page's blotter/card patterns.

## 6. Safety tiers → components (the foolproof model)

- **Tier 0:** paper↔LIVE boundary (only `live/` may reach a broker; `LIVE_TRADING_ENABLED` env + per-deployment `live_armed` default-false, auto-disarm daily) · kill switch (`kill_switch.py`) · fat-finger + price-band + jData validation (`safety.py`) · idempotency (`idempotency.py`) · reconciliation (`reconcile.py`) · daily-loss→kill + account-level broker-stop-loss (`kill_switch.py` guardrails).
- **Tier 1:** max-open + capital caps (count working orders too; check `limits`) · fail-safe time-cutoff square-off (dedicated scheduler tick, fires even if the engine wedges) · marketable-limit pricing + reprice/chase (`order_sm.py` + `safety.py` band-clamped) · partial-fill/retry · disconnect/token handling.
- **Tier 2:** order-rate throttle (cancels exempt) · immutable audit trail + push alerts on kill-trip/reconcile-break/disconnect · default-deny everywhere.

All actuation in this spec targets `MockNoren`; the **real**-broker version of the order-placing paths is L3.

## 7. Routes (`/api/...`, read-only + safety-config + dry-run only)

- `GET /flattrade/status`, `GET /flattrade/auth/start`, `GET /flattrade/auth/callback`, `POST /flattrade/disconnect` — daily-token OAuth (mirrors Upstox routes).
- `GET /live-broker/positions`, `/orders`, `/trades`, `/limits` — **real** read-only.
- `GET /live-broker/reconcile` — diff report (read-only).
- `GET/PUT /live-broker/safety-config` — the account guardrails (max-loss, max-profit-lock, max-open, fat-finger lots, price-band %, auto-squareoff time).
- `POST /live-broker/order/dry-run` — builds + validates the jData for an intent, returns what *would* be sent + every safety verdict. **Does not transmit.**
- `POST /live-broker/kill-switch` — exercised against `MockNoren` in this spec (panic path proven; real actuation gated to L3).
- `GET /live-broker/symbol/resolve?...` — resolver preview (verify a contract maps to the right `tsym`).

## 8. Data model (Mongo)

- `live_broker_tokens` — `{ user, jKey, issued_at, expires_at, actid, uid }`.
- `live_symbol_map` — cached `{ upstox_key → {tsym, token, exch, lot_size, verified_at} }`.
- `live_orders` — the intent/idempotency/state store: `{ client_order_id, deployment_id?, mode (mock|live), intent{...}, state, norenordno?, fills[], avgprc?, rejreason?, ts{intent,submitted,acked,final} }`.
- `live_safety_config` — singleton guardrails doc + the `blocked_until_reset` latch + `last_trip`.

## 9. Testing strategy

- **Host tests (pytest, the 880+-suite style):** symbol-resolver parity battery (each index, strike, expiry, BFO vs NFO, lot-size cross-check); safety-engine (every reject: no-cap default-deny, over-cap, out-of-band, stale-ref, bad lot multiple, missing trgprc, throttle-but-allow-cancel); idempotency (no double-POST across simulated restart); order state machine (every `om` transition incl. partial fill, reject, trigger→open→complete); kill switch (cancel-all + flatten against `MockNoren`); reconciliation (halt-on-mismatch); **exit-level parity vs `execution_policy`** (live decider == sim decider, byte-identical).
- **Adversarial audit subagents:** dedicated reviewers try to break each safety control (can a duplicate order slip? can a cancel be throttled? can a stale price pass the band? can a restart double a position? does the kill switch leave a working order?).
- **Live read-only verification:** against the user's real Flattrade account — token flow, `search_scrip` returns the right `tsym`, `position_book`/`order_book` parse, reconciliation diff is empty on a clean account.

## 10. Out of scope (next specs, user-gated)

- **L3:** wire the order-placing paths to the **real** Flattrade client behind Live-Offline (alert-only) → Live One-Click (confirm each). User does the real 1-lot conformance (symbol accepts, `avgprc` sane, `om` fills flow, SL-LMT rests/triggers, real cancel, RMS/margin, static-IP/token reality, a real kill-switch flatten).
- **L4:** Fully-Auto strategy→live execution (reuses the deployment evaluator + this safe core), needs the always-on static-IP host; ships behind a loud "no validated edge yet" banner.

## 11. Risks / open items

- **Symbol mapping** is the top silent-failure hazard → `/SearchScrip`-based + parity tests + live verification (Tier-0, not "later").
- **Operational reality:** static IP + daily post-6AM token + "must run 9:15–15:30" mean live is only as safe as the (currently unvalidated) strategy — surface a persistent banner.
- `MockNoren` fidelity: it must model `om` ordering, partial fills, and rejection shapes faithfully or the state machine is tested against a fiction — the adversarial audit explicitly checks mock-vs-documented-Noren-behavior.
