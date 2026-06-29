# Live-Feed Health, Auto-Reconcile, and Truthful Deployment Liveness

**Date:** 2026-06-29
**Status:** Design v1 (approved in brainstorming) → ready for plan
**Branch:** `feat/live-feed-health` (worktree `af-wt-livefeed`, off `main`).

## 1. Context & motivation (the incident)

On 2026-06-29, two paper deployments showed **ACTIVE** all session but produced **zero signals and zero paper trades**. Root cause, confirmed from data + code + logs:

- Paper evaluation only fires on a **new closed 1-minute bar** in `candles_1m` (`runtime.py:_deployment_evaluator_loop` "new-bar gate", ~line 369). Its docstring: *"if the stream is down, it simply finds no fresh bar."*
- Those intraday bars are produced by the **`live_candle_roller`** (ticks → 1-min OHLC → `candles_1m`). The roller **subscribes to the Upstox stream** but must be **started separately**.
- The roller auto-starts **only once, at app startup, and only if the Upstox token is already valid at that instant** (`server.py:113–126`).
- The Upstox token is a **daily OAuth** completed in the browser *after* launching the app (that day at 09:33 IST, ~18 min after the 09:15 open). So at startup the token was missing/expired and the roller was skipped.
- Neither user action that *looks* like "go live" starts the roller: the **OAuth callback** (`broker.py:156`) only saves the token + triggers a historical catch-up; the **"Start Stream" button** (`POST /upstox/stream/start`, `broker.py:194`) starts the tick stream but **not** the roller.

Evidence: `candles_1m` had **0 bars for the day** (latest June 26 12:35 IST) while the `ticks` collection held **10.77M ticks streaming** (stream ran, roller didn't aggregate). The evening restart (token then valid) auto-started the roller — proving the startup gate.

**Two goals (both approved):**
1. **Permanent feed reliability** — the candle roller must run whenever trading is intended, regardless of app-vs-login ordering or mid-session drops.
2. **Truthful deployment liveness** — the green "ACTIVE" LED must mean *actually able to trade right now* (live candles flowing, nothing blocking), and otherwise show the specific blocking reason and **prompt the user**. No more "active but silently dead."

## 2. Scope & decisions (locked)

- **Approach:** an **auto-reconcile supervisor** (user-chosen over manual/minimal). A background loop keeps the feed live during market hours; the only manual step is the daily Upstox OAuth, which is surfaced as a prominent prompt. The assistant/code never enters broker credentials.
- **"Fresh" threshold for green LED:** latest `candles_1m` bar **< 120 s old** during market hours.
- **When the feed runs:** during market hours (NSE Mon–Fri 09:15–15:30 IST, trading days) **whenever the Upstox token is valid** — not gated on whether a deployment is active (keeps the warehouse + header tiles current, avoids edge cases).
- **Connect-Upstox friction:** the banner CTA opens the OAuth flow **on click** (default). An optional env flag `LIVE_FEED_AUTO_OPEN_LOGIN` (default off) may later auto-open the login tab at market-open when the token is invalid — deferred, not in v1.
- **Scope:** backend feed-health model + reconciler + a health endpoint; frontend truthful LED on **both** the Paper and Live deployment strips + a prompt banner. Reuse existing `LiveBanner`, `LiveDeploymentStrip`, `LiveDataProvider`, `usePoll`.
- **Out of scope (this project):** any change to the `_deployment_evaluator_loop` logic itself (it already gates on new bars — which now arrive because the reconciler keeps the roller up); token auto-refresh (Upstox tokens are daily, no long-lived refresh); credential automation; persisting health history.

## 3. Architecture — one service, three consumers

```
┌──────────────────────────────────────────────────────────────────────┐
│ FEED HEALTH MODEL  (pure, host-testable)                               │
│   compute_feed_health(now_ist, token, stream, roller, last_candle_ts)  │
│     -> { state, reason, cta, market_open, token, stream_running,       │
│         roller_running, last_candle_age_sec, candles_fresh }           │
│   state ∈ LIVE | WARMING_UP | DEGRADED | NEEDS_LOGIN | MARKET_CLOSED    │
│   master signal = candles_1m freshness                                 │
└───────────────┬───────────────────────────────┬──────────────────────┘
                │ reconciled by                  │ exposed by
┌───────────────▼─────────────────┐ ┌────────────▼──────────────────────┐
│ RECONCILER (supervisor loop)     │ │ GET /live-feed/health             │
│  every ~20s: if market_open &&   │ │  (frontend polls via              │
│  token valid -> ensure stream +  │ │   LiveDataProvider/usePoll)       │
│  roller running; restart drops;  │ └────────────┬──────────────────────┘
│  if token invalid -> NEEDS_LOGIN │              │ consumed by
│  (cannot fix; surfaces prompt);  │ ┌────────────▼──────────────────────┐
│  session end -> stop feed        │ │ FRONTEND                          │
└──────────────────────────────────┘ │  - truthful LED = status × health │
                                      │    (Paper + Live strips)          │
                                      │  - prompt banner + Connect Upstox │
                                      └───────────────────────────────────┘
```

The reconciler and the health model share the same actual-state reads. The reconciler *acts* (brings the feed up); the health endpoint *reports* (drives UI). When the reconciler can't fix something (token needs human OAuth), that becomes the surfaced prompt.

## 4. The Feed Health model (`app/live_feed_health.py`, new)

**Pure function** (no I/O; inputs passed in) so it is host-testable without motor:

```python
FRESH_THRESHOLD_SEC = 120
WARMUP_GRACE_SEC = 90   # after roller (re)start / token-connect, before a missing bar is "stale"

def compute_feed_health(*, now_ist, is_trading_day, token, stream_running,
                        roller_running, roller_started_at, last_candle_ts,
                        supervisor_backoff_active, supervisor_last_error) -> dict:
    """token = {connected, expired, expires_at}. supervisor_* come from the
    reconciler's state. Returns the health dict; never raises."""
```

State decision (first match wins — unambiguous):
1. **MARKET_CLOSED** — not a trading day, or `now_ist` outside 09:15–15:30 IST. *(Deployments may be ACTIVE but the market is closed — neutral, not an error.)* grey.
2. **NEEDS_LOGIN** — market open AND (`not token.connected` OR `token.expired`). reason: *"Upstox isn't connected — connect to go live."* cta: `connect_upstox`. red.
   *(token valid + market open for all states below)*
3. **LIVE** — `last_candle_age_sec < FRESH_THRESHOLD_SEC`. green. (The happy end-to-end proof; checked first.)
4. **WARMING_UP** — the feed is coming up cleanly, i.e. NOT yet fresh but no real failure: either (stream+roller running and `now - roller_started_at < WARMUP_GRACE_SEC`), OR (stream/roller not yet running but the supervisor is mid-start — `not supervisor_backoff_active`). reason: *"feed starting — first candle shortly."* amber.
5. **DEGRADED** — anything else (token valid, market open, not fresh, not a clean warm-up): stream/roller down with the supervisor in backoff (`supervisor_backoff_active`, surface `supervisor_last_error`), OR running-but-stale past the grace window (ticks stopped / feed stalled → *"no live candles for N min"*). reason names the concrete failure. red.

`last_candle_age_sec` and `candles_fresh` are always included for the UI. The function never raises (defensive defaults).

## 5. The reconciler (`app/runtime.py`, new `_live_feed_supervisor_loop`)

A background asyncio task started **unconditionally** at boot (alongside `_deployment_evaluator_loop`), waking every `SUPERVISE_POLL_SEC = 20`:

```
loop:
  sleep 20s
  ist = now_ist()
  market_open = is_trading_day(ist) and 09:15 <= ist.time() < 15:30
  token = await upstox_client.get_connection_status()
  if not market_open:
      # session end / off-hours: stop the feed cleanly if we started it
      if roller.running or stream.running: stop both   (idempotent; once)
      continue
  if not (token.connected and not token.expired):
      record desired_but_blocked = "needs_login"   # cannot fix; health -> NEEDS_LOGIN
      continue
  # token valid + market open -> ensure the feed is up
  if not stream.running:  try start stream (instrument keys) ; backoff on fail
  if not roller.running:  try start roller                   ; backoff on fail
```

Key properties:
- **Fixes the ordering bug:** log in at any time → within ≤20 s the stream **and** roller are running. No Stream button, no hidden roller step.
- **Self-heals drops:** if either dies mid-session, it restarts next tick.
- **Never touches credentials:** when the token is missing/expired it does nothing but mark the blocked reason → the UI prompts the human.
- **Backoff:** repeated start failures (e.g. Upstox rate limit / transient) use exponential backoff (cap ~5 min) with `last_error` surfaced as the DEGRADED reason — never hammers Upstox.
- **Subsumes the brittle startup block:** the one-shot conditional bring-up at `server.py:113–126` is replaced by the supervisor (the supervisor's first tick does the same bring-up, and then keeps doing it). The `live_candle_roller` / `upstox_stream_manager` singletons and the manual start/stop endpoints stay (manual overrides + the supervisor respects an explicit user Stop — see §8).

`roller_started_at` is read from `live_candle_roller.status()["started_at"]` (already tracked) for the WARMING_UP grace.

## 6. Backend endpoint

`GET /api/live-feed/health` (in `broker.py` or a small new router) returns the health dict. It assembles the actual-state reads cheaply:
- `token` = `await upstox_client.get_connection_status()`.
- `stream_running` = `upstox_stream_manager.status()["running"]`.
- `roller_running`, `roller_started_at` = `live_candle_roller.status()`.
- `last_candle_ts` = latest `candles_1m.ts` for NIFTY (indexed `find_one` sort ts desc; cached ~5 s to avoid hammering on every poll).
…then calls `compute_feed_health(...)`.

The **per-deployment liveness** is derived **client-side** (the frontend already has each deployment's `status` + the global feed health), keeping the server change minimal and the feed health a single source of truth. (If a server-side field is later wanted, add `liveness` to `/deployments/overview` — out of scope for v1.)

## 7. Frontend

- **`LiveDataProvider.jsx`** — add a poll of `/live-feed/health` (via `usePoll`, ~5 s cadence, same pattern as existing live/status polls) and expose `feedHealth` in the provider context. (Memory: the provider owns polling; do not add a competing poller.)
- **Truthful LED** — a small pure helper `deploymentLiveness(deployment, feedHealth) -> {color, label, tooltip}`:
  - `ARCHIVED` → hidden/grey.
  - `PAUSED` (+ existing pause reason: kill-switch / drift / manual) → amber + that reason.
  - `ACTIVE` × feed:
    - LIVE → green · "Active · live".
    - WARMING_UP → amber · "Active · feed starting…".
    - NEEDS_LOGIN → red · "Active · feed offline — connect Upstox".
    - DEGRADED → red · "Active · no live candles".
    - MARKET_CLOSED → grey · "Active · market closed".
  Used by **both** the Paper page deployments strip (`PaperTrading.jsx`) and `LiveDeploymentStrip.jsx`.
- **Prompt banner** — extend/reuse `LiveBanner.jsx` to render a feed-health banner on the Paper **and** Live pages exactly when `feedHealth.state ∈ {NEEDS_LOGIN, DEGRADED, WARMING_UP}` AND there is ≥1 ACTIVE deployment. (The state already encodes market hours, so `LIVE` and `MARKET_CLOSED` show no banner.)
  - NEEDS_LOGIN → danger banner + **[Connect Upstox]** (the existing OAuth-connect action used elsewhere in the app).
  - DEGRADED → danger banner + **[Restart feed]** (calls the existing roller/stream start, or a `/live-feed/restart` convenience).
  - WARMING_UP → subtle info banner "feed starting…".
  - MARKET_CLOSED → no banner (expected state).
  The banner copy + LED match the approved mockup (sentence case, the red/amber/green/grey semantics).

## 8. Edge cases & error handling

- **Explicit user Stop:** if the user manually stops the stream/roller (existing endpoints) during market hours, the supervisor would restart it. Add a simple `feed_suppressed_until` / "manual stop" flag the supervisor honors (a user Stop suppresses auto-restart until the next session or an explicit Start), so the auto-reconcile never fights a deliberate human Stop.
- **Upstox not configured at all** (no client id/secret): supervisor no-ops; health = NEEDS_LOGIN with "Upstox not configured." (offline-first — must not crash.)
- **Health endpoint must be cheap + total:** cache the `last_candle_ts` read briefly; never throw (return a best-effort health with a reason on internal error).
- **Reconciler exceptions:** each tick wrapped in try/except; a failure records `last_error` and continues (never kills the loop).
- **Clock/timezone:** all market-hours math in IST (UTC+5:30), reusing `nse_calendar.is_trading_day` (already used by the roller + evaluator).

## 9. Testing strategy

- **`compute_feed_health` (pure):** exhaustive table-driven unit tests over the state matrix — every state + the warmup-vs-stale boundary + the freshness threshold + off-hours/holiday. Host-safe (no motor).
- **Reconciler decision (pure):** factor the per-tick decision into a pure `decide_feed_actions(market_open, token, stream_running, roller_running, suppressed) -> [actions]` and table-test it (start-stream, start-roller, stop-both, noop, blocked-needs-login, honor-manual-stop). The async loop is a thin wrapper that executes the actions.
- **Integration:** simulate token-becomes-valid-mid-session with mocked stream/roller managers → assert the supervisor issues start-stream + start-roller; simulate a mid-session roller drop → assert restart; simulate manual stop → assert no auto-restart.
- **Endpoint:** `/live-feed/health` returns the expected shape for representative states (mock the managers + token + last candle).
- **Frontend:** `deploymentLiveness` pure helper unit tests (status × health matrix); render tests that the banner shows/hides and the LED color/label match per state.
- **Regression:** existing live/broker/deployment tests stay green; `yarn build` clean.

## 10. Decisions resolved + YAGNI

**Resolved:** auto-reconcile supervisor; 120 s freshness; feed runs during market hours when token valid (not gated on deployments); Connect = click CTA (auto-open deferred behind a flag); scope = Paper + Live strips + global health endpoint + reconciler; per-deployment liveness derived client-side.

**Explicitly NOT building (v1):** auto-open login tab (deferred flag); token auto-refresh (not supported by Upstox daily OAuth); credential automation; server-side per-deployment liveness field; health-history persistence; any change to the deployment-evaluator's new-bar gate; touching the historical warehouse catch-up.

## 11. Rollout

1. Backend `compute_feed_health` + `decide_feed_actions` (pure, host-tested).
2. Reconciler loop wired at boot (replacing the brittle startup conditional) + manual-stop suppression.
3. `/live-feed/health` endpoint.
4. Frontend: provider poll + `deploymentLiveness` helper + LED on both strips + banner.
5. Verify on a worktree Docker stack during market hours (or a simulated clock): start app before connecting Upstox → confirm the red "feed offline" LED + banner appear, then connect → confirm within ≤20 s the LED goes green and `candles_1m` starts filling. Offline/after-hours → MARKET_CLOSED, no banner.

Offline-first throughout: every new path no-ops safely outside market hours and when Upstox is unconfigured, so nothing transmits or crashes when the PC is off-session.
