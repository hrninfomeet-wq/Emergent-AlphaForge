# Live Cockpit Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-organise `/live-trading` into an always-on trader "cockpit" (market pulse + regime/S-R + analytics + positions + quick-trade always visible; deployments/backstop/controls in a config drawer; a professional tabbed account panel; a compact Upstox/Flattrade connection module) — reusing every existing live component and adding only read-only analytics.

**Architecture:** Frontend is a new `LiveCockpit` layout mounted by `LiveTrading.jsx`, consuming the existing `LiveDataProvider` (extended with two additive read-only slices). Existing components are RELOCATED, not rewritten. Two new read-only backend endpoints — `GET /market/analysis` (deterministic regime/trend/structure + S/R + option analytics, composed from existing `context_signals` + option chain + server-side BS greeks) and `GET /live-broker/holdings` — feed the new panels. No broker-mutating endpoints.

**Tech Stack:** FastAPI + Motor (backend), pytest (host tests), React + Tailwind (frontend), Docker Compose. Spec: `docs/superpowers/specs/2026-07-22-live-cockpit-redesign-design.md`.

---

## Conventions for every task

- Backend host tests: `.venv/Scripts/python.exe -m pytest tests/<file> -q -p no:cacheprovider`.
- Full suite gate before any push: `.venv/Scripts/python.exe -m pytest tests -q -p no:cacheprovider` (baseline **3,557 passed, 4 xfailed**).
- **Frontend rebuild MUST go direct** (OneDrive/Docker stale-context trap — see learning_log): from `frontend/`, `docker build --build-arg REACT_APP_BACKEND_URL=http://localhost:8001 -t emergent-alphaforge-frontend:latest .` then `docker compose up -d --force-recreate --no-build frontend`. Verify the served bundle via `index.html`'s `main.<hash>.js` and grep the `.js.map` (NOT the minified `.js`) for a source identifier.
- Commit messages end with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`; use `git commit -F <tempfile>` (PowerShell here-string mangles quotes).
- READ-ONLY reconnaissance before each frontend task: open the component being relocated so its props/exports are used verbatim.

---

## File Structure

**New backend files**
- `backend/app/market_analysis.py` — pure analysis functions (trend/structure/S-R/PCR/max-pain/IV-rank/straddle). One responsibility: compute the analysis payload from candles + option chain. No I/O.
- Route additions in the module that already serves `/market/header` (locate via `grep -rn '"/market/header"\|/market/header' backend/app`) — add `GET /market/analysis`.
- Route addition in `backend/app/routers/live_broker.py` — add `GET /live-broker/holdings`.

**New frontend files**
- `frontend/src/components/live/LiveCockpit.jsx` — assembled cockpit layout (replaces `LiveDashboard` as the mounted component).
- `frontend/src/components/live/cockpit/CommandBar.jsx` — market status + ticker + BrokerConnect + Kill + Configure.
- `frontend/src/components/live/cockpit/BrokerConnect.jsx` — Upstox + Flattrade chips + popover.
- `frontend/src/components/live/cockpit/AlertRail.jsx` — degraded / unguarded / no-backstop banners (extracted).
- `frontend/src/components/live/cockpit/MarketPulse.jsx` — structure + multi-TF trend + S/R range bar (Phase 2 data).
- `frontend/src/components/live/cockpit/MarketAnalysis.jsx` — analytics tiles + option chain (Phase 2 data).
- `frontend/src/components/live/cockpit/RiskKpis.jsx` — small KPI grid.
- `frontend/src/components/live/cockpit/QuickTrade.jsx` — thin wrapper around `LiveOrderTicket`.
- `frontend/src/components/live/cockpit/DeploymentSummary.jsx` — compact list + "open drawer".
- `frontend/src/components/live/cockpit/AccountTabs.jsx` — Funds/Holdings/Orders/Trades.
- `frontend/src/components/live/cockpit/ConfigDrawer.jsx` — slide-over hosting deployment strip + GttBook + OverallSettingsPanel.
- `frontend/src/components/live/liveHelpers.js` — the verbatim blotter/derivation helpers extracted from `LiveDashboard.jsx` (so nothing is rewritten).

**Modified**
- `frontend/src/pages/LiveTrading.jsx` — mount `LiveCockpit` instead of `LiveDashboard`.
- `frontend/src/components/live/LiveDataProvider.jsx` — add `marketAnalysis` + `holdings` slices.
- `frontend/src/lib/api.js` — add `marketAnalysis`, `liveBrokerHoldings`, and reconnect helpers if missing.

---

# PHASE 1 — Cockpit shell (frontend only; reuses existing data; ships immediately)

### Task 1: Extract shared helpers from LiveDashboard

**Files:**
- Create: `frontend/src/components/live/liveHelpers.js`
- Modify: `frontend/src/components/live/LiveDashboard.jsx` (temporary — imports the helpers; retired in Task 2)

- [ ] **Step 1: Create `liveHelpers.js`** by MOVING these verbatim from `LiveDashboard.jsx` (lines ~64–123, ~281–351): `pnlClass`, `signedINR`, `fmtAsOf`, `SLICE_LABEL`, `asPositionRows`, `asOrderRows`, `isOpenPosition`, `isWorkingOrder`, `deriveDayPnl`, `deriveCash`, plus the `PositionsBlotter`, `OrdersBlotter`, `ReconcileChip`, `SectionCard` components. Export each. Keep the code byte-identical.

- [ ] **Step 2: Point `LiveDashboard.jsx` at the module** — replace the moved definitions with `import { ... } from "@/components/live/liveHelpers";`.

- [ ] **Step 3: Build the frontend** (direct-build recipe) and hard-refresh `/live-trading`; confirm it renders identically (no visual change this task).

- [ ] **Step 4: Commit**
```
git add frontend/src/components/live/liveHelpers.js frontend/src/components/live/LiveDashboard.jsx
git commit -F <msg>   # "refactor(live): extract LiveDashboard helpers into liveHelpers.js (no behavior change)"
```

### Task 2: LiveCockpit skeleton + route swap

**Files:**
- Create: `frontend/src/components/live/LiveCockpit.jsx`
- Modify: `frontend/src/pages/LiveTrading.jsx`

- [ ] **Step 1: Create `LiveCockpit.jsx`** consuming `useLiveData()` (same destructure as `LiveDashboard`) and rendering, in order: `<CommandBar/>`, `<AlertRail/>`, a `core` grid `<div className="grid grid-cols-1 lg:grid-cols-[1.55fr_1fr] gap-3">` with a left `colstack` and right `colstack`, `<AccountTabs/>`, and `<ConfigDrawer/>` with an open/close state (`const [drawerOpen,setDrawerOpen]=useState(false)`). Stub each child as `null`-safe imports created in later tasks; for this task render placeholder `<div>` blocks so the build passes.

- [ ] **Step 2: Swap the route** in `LiveTrading.jsx`: `import LiveCockpit from "@/components/live/LiveCockpit"` and render `<LiveCockpit/>` inside `<LiveErrorBoundary>` instead of `<LiveDashboard/>`.

- [ ] **Step 3: Build + hard-refresh** `/live-trading`; confirm no crash (placeholders visible).

- [ ] **Step 4: Commit** — "feat(live): LiveCockpit skeleton mounted on /live-trading".

### Task 3: CommandBar (market status + ticker + Kill + Configure)

**Files:**
- Create: `frontend/src/components/live/cockpit/CommandBar.jsx`
- Verify: `api.marketHeader()` exists (`frontend/src/lib/api.js:23`); `nse_calendar.market_status` (backend) for the OPEN/CLOSED pill — Phase 1 may derive OPEN/CLOSED client-side from IST time + the existing `status`/feed data; a precise market-status source can wait for Phase 2.

- [ ] **Step 1:** Build `CommandBar` accepting props `{ status, onConfigure }`. Left: brand + a market-status pill (green pulsing "MARKET OPEN · 15:30 close" when IST time ∈ 09:15–15:30 on a weekday, else grey "CLOSED"). Middle: the index ticker — poll `api.marketHeader()` every 15s (own `useEffect`), render NIFTY/BANKNIFTY/SENSEX/VIX/expiry from its `items`. Right: `<BrokerConnect status={status}/>`, a Kill button that calls the SAME action `KillSwitchPanel` uses (reuse by rendering a compact trigger that scrolls to / opens the existing panel — do NOT duplicate the kill logic), and a "⚙ Configure" button calling `onConfigure`.

- [ ] **Step 2:** Wire `LiveCockpit` to pass `status` and `onConfigure={()=>setDrawerOpen(true)}`.

- [ ] **Step 3: Build + hard-refresh**; confirm the ticker populates and the pill reflects market hours.

- [ ] **Step 4: Commit** — "feat(live): cockpit command bar (market status + ticker)".

### Task 4: BrokerConnect (Upstox + Flattrade connection module)

**Files:**
- Create: `frontend/src/components/live/cockpit/BrokerConnect.jsx`
- Verify endpoints in `api.js`: `upstoxStatus` (`/upstox/status`), `flattradeStatus` (`/flattrade/status`), `upstoxAuthStart`, `disconnectUpstox`, `disconnectFlattrade`, and the Flattrade login/auth-start (grep `api.js` for `flattrade` auth; if the login start helper is missing, add `startFlattradeAuth: () => apiClient.get("/flattrade/auth/start")` mirroring the backend route used by the existing "Login to Flattrade" button — confirm the route name first).

- [ ] **Step 1:** Build two chips (Upstox = "data", Flattrade = "exec"). Each shows a dot (green if `connected && !expired`, amber if expiring < 30m, red otherwise), the token-validity countdown from status, and a click popover with **Reconnect** (calls `upstoxAuthStart`/flattrade auth-start), **Disconnect** (calls the disconnect helper then `refetch.all()`), and — when expired — a primary "Login to Upstox/Flattrade" that drives the same auth-start. Consume the already-polled `status` from `useLiveData` for Flattrade and poll `upstoxStatus` for Upstox (15s) — or add an `upstoxStatus` slice to the provider in Task 9 and consume it here.

- [ ] **Step 2:** Close popover on outside-click / Esc (single-layer; avoid the two-modal Radix trap — plain absolute popover is fine).

- [ ] **Step 3: Build + hard-refresh**; click each chip; verify reconnect/disconnect call the right endpoints (watch the network tab). Do NOT actually disconnect a live session during market hours in testing.

- [ ] **Step 4: Commit** — "feat(live): Upstox/Flattrade connection module with reconnect/disconnect".

### Task 5: AlertRail (extract banners)

**Files:**
- Create: `frontend/src/components/live/cockpit/AlertRail.jsx`

- [ ] **Step 1:** Move the degraded-data banner, unguarded-positions banner, no-broker-backstop banner, and auth message + `FeedHealthBanner` (lines ~479–562 of `LiveDashboard.jsx`) verbatim into `AlertRail`, taking the derived inputs (`health`, `unguardedPositions`, `noBackstopPositions`, `feedHealth`, `activeCount`, `authMsg`) as props or via `useLiveData`. Keep the `data-testid`s (`live-degraded-banner`, `unguarded-positions-banner`, `no-broker-backstop-banner`) — existing tests/inspection rely on them.

- [ ] **Step 2:** Render `<AlertRail/>` in `LiveCockpit` between CommandBar and core.

- [ ] **Step 3: Build + hard-refresh**; confirm banners still fire (the no-backstop banner should show for the current NIFTY24200CE state if present).

- [ ] **Step 4: Commit** — "feat(live): cockpit alert rail (relocated safety banners)".

### Task 6: Core — RiskKpis + reuse positions/kill/guard + QuickTrade + DeploymentSummary

**Files:**
- Create: `RiskKpis.jsx`, `QuickTrade.jsx`, `DeploymentSummary.jsx`

- [ ] **Step 1: RiskKpis** — a 3×2 grid of `MetricCard`s (reuse) for Day P&L, Open pos, Guard, Available margin, Working orders, Day-stop, derived from `useLiveData` (`deriveDayPnl`, `deriveCash`, counts) — reuse the exact derivations from `liveHelpers`.

- [ ] **Step 2: QuickTrade** — a thin card wrapping the existing `LiveOrderTicket` (pass `mode`); no logic duplicated.

- [ ] **Step 3: DeploymentSummary** — read `deployments` from `useLiveData`, render up to 3 compact chips (name + mode/status pill) and a "Manage deployments →" button calling `onManage` (opens the drawer). For live/paused states reuse the same status classes.

- [ ] **Step 4:** Assemble the core grid in `LiveCockpit`: LEFT colstack = MarketPulse placeholder + MarketAnalysis placeholder; RIGHT colstack = RiskKpis + (reuse) positions blotter card + `KillSwitchPanel` + `GuardPanel` (compact) + QuickTrade + DeploymentSummary.

- [ ] **Step 5: Build + hard-refresh**; confirm positions, kill, guard, ticket all work as before.

- [ ] **Step 6: Commit** — "feat(live): cockpit core (risk KPIs, positions, quick-trade, deployment summary)".

### Task 7: ConfigDrawer (slide-over)

**Files:**
- Create: `frontend/src/components/live/cockpit/ConfigDrawer.jsx`

- [ ] **Step 1:** Build a right slide-over (`open` prop + `onClose`), scrim + Esc-to-close (single layer). Inside, render the EXISTING `LiveDeploymentStrip` (pass `onArmedSummaryChange` up to `LiveCockpit` for the banner), `GttBook`, and `OverallSettingsPanel` (scope="overall"), each under a labelled section. Respect `prefers-reduced-motion`.

- [ ] **Step 2:** Wire `drawerOpen`/`setDrawerOpen` in `LiveCockpit`; the CommandBar ⚙ and DeploymentSummary "Manage" both open it.

- [ ] **Step 3: Build + hard-refresh**; open the drawer, confirm the deployment strip enable/disable/stop + GTT + overall controls all function (the DeployToLivePanel consent flow must still work — re-verify the C5 flow briefly).

- [ ] **Step 4: Commit** — "feat(live): cockpit config drawer (deployments · backstop · overall controls)".

### Task 8: AccountTabs (Funds / Orders / Trades; Holdings placeholder)

**Files:**
- Create: `frontend/src/components/live/cockpit/AccountTabs.jsx`

- [ ] **Step 1:** Build a tabbed panel (single-dialog-free plain tabs). **Funds & Margin**: parse `limits` from `useLiveData` into stat cells (available/used margin, opening balance, cash+collateral, realised/unrealised M2M, span+exposure) + a utilisation bar — use defensive field extraction like `deriveCash` (Noren field-name variance). **Order book**: reuse `OrdersBlotter` with ALL statuses (not just working) — add an `allStatuses` prop or a sibling that skips the `isWorkingOrder` filter. **Trade book**: reuse `LiveBlotter`/`LiveTradeStats`. **Holdings**: placeholder "coming online" (wired in Phase 2).

- [ ] **Step 2:** Render `<AccountTabs/>` below the core in `LiveCockpit`.

- [ ] **Step 3: Build + hard-refresh**; verify Funds populates from broker limits and Orders/Trades render.

- [ ] **Step 4: Commit** — "feat(live): tabbed account panel (funds/orders/trades)".

### Task 9: Frontend source-contract test + Phase 1 verification

**Files:**
- Create: `tests/test_live_cockpit_ui.py` (grep-the-JSX contract test, mirroring `tests/test_premium_momentum_advisory_ui.py`)

- [ ] **Step 1: Write the contract test** asserting the cockpit source wires the key pieces:
```python
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
def _src(p): return (ROOT / "frontend" / "src" / p).read_text(encoding="utf-8")
def test_cockpit_mounted_and_wired():
    assert "LiveCockpit" in _src("pages/LiveTrading.jsx")
    cb = _src("components/live/cockpit/CommandBar.jsx")
    assert "marketHeader" in cb
    bc = _src("components/live/cockpit/BrokerConnect.jsx")
    for needle in ("disconnectUpstox", "disconnectFlattrade"):  # reconnect/disconnect wired
        assert needle in bc
    acct = _src("components/live/cockpit/AccountTabs.jsx")
    assert "limits" in acct
    # H8 consent flow preserved (DeployToLivePanel still reachable via the drawer)
    drawer = _src("components/live/cockpit/ConfigDrawer.jsx")
    assert "LiveDeploymentStrip" in drawer
```
- [ ] **Step 2: Run it** — `pytest tests/test_live_cockpit_ui.py -q`; expected PASS once files exist.
- [ ] **Step 3: Full backend suite** — expected 3,557 passed, 4 xfailed (no regression; frontend-only phase).
- [ ] **Step 4: Frontend production build** (direct recipe) — expected "Compiled successfully".
- [ ] **Step 5: Chrome verify** `/live-trading` (hard refresh): command bar, brokers, alerts, core, account tabs, drawer all functional; the DeployToLivePanel consent flow still opens the typed-ENABLE step.
- [ ] **Step 6: Commit** — "test(live): cockpit source-contract test + Phase 1 verification".

---

# PHASE 2 — Analysis engine + holdings (new read-only endpoints)

### Task 10: `market_analysis.py` — trend classifier (TDD)

**Files:**
- Create: `backend/app/market_analysis.py`
- Create: `tests/test_market_analysis.py`

- [ ] **Step 1: Write the failing test** for `classify_trend`:
```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from app.market_analysis import classify_trend

def test_classify_trend_up_down_flat():
    up = [100,101,102,103,105,106,108]      # rising closes
    dn = [108,106,105,103,102,101,100]
    flat = [100,100.2,99.9,100.1,100.0,99.95,100.05]
    assert classify_trend(up) == "up"
    assert classify_trend(dn) == "down"
    assert classify_trend(flat) == "flat"

def test_classify_trend_short_series_is_flat():
    assert classify_trend([100]) == "flat"
    assert classify_trend([]) == "flat"
```
- [ ] **Step 2: Run → FAIL** (`ImportError`/`AttributeError`).
- [ ] **Step 3: Implement `classify_trend`** — pure, causal:
```python
from typing import List, Optional
def classify_trend(closes: List[float], *, flat_pct: float = 0.0015) -> str:
    """up|down|flat from a close series via net drift over its length, thresholded.
    flat_pct is the minimum |total return| (fraction) to call a direction."""
    xs = [float(c) for c in (closes or []) if c is not None]
    if len(xs) < 3:
        return "flat"
    ret = (xs[-1] - xs[0]) / xs[0] if xs[0] else 0.0
    # confirm with a simple higher-highs / lower-lows majority to avoid whipsaw
    ups = sum(1 for a, b in zip(xs, xs[1:]) if b > a)
    downs = sum(1 for a, b in zip(xs, xs[1:]) if b < a)
    if ret > flat_pct and ups >= downs:
        return "up"
    if ret < -flat_pct and downs >= ups:
        return "down"
    return "flat"
```
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** — "feat(market-analysis): trend classifier".

### Task 11: `structure`/regime bucketing (TDD)

**Files:** Modify `backend/app/market_analysis.py`, `tests/test_market_analysis.py`

- [ ] **Step 1: Write the failing test** for `classify_structure`:
```python
from app.market_analysis import classify_structure
def test_structure_trending_vs_range():
    # strong drift + high adx → trending/up bucket 4; low adx + tight range → choppy bucket 2
    up = classify_structure(closes=[100,101,102,104,106,108,110], adx=28.0)
    assert up["kind"] == "trending" and up["regime_bucket"] == 4 and 0 <= up["confidence"] <= 1
    ch = classify_structure(closes=[100,100.3,99.8,100.2,99.9,100.1,100.0], adx=12.0)
    assert ch["kind"] in ("range", "choppy") and ch["regime_bucket"] == 2
```
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement `classify_structure`** returning `{label, kind, regime_bucket(0..4), buckets:5, confidence, why}`. Map: trend direction from `classify_trend`; `kind` = "trending" when `adx>=25`, "range"/"choppy" when `adx<20` (range if drift small, choppy if noisy), "breakout" reserved (needs range-break detection — v1 may omit and treat as trending). Buckets: Bearish(0)/Down(1)/Choppy(2)/Up(3)/Strong(4) from (direction, strength). `confidence` = clamp(adx/40, 0..1) blended with drift magnitude. `why` = assembled string. Keep it pure (adx passed in, computed by the endpoint from candles).
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** — "feat(market-analysis): structure/regime bucketing".

### Task 12: Option analytics — PCR, max-pain, straddle, implied move (TDD)

**Files:** Modify `market_analysis.py`, `tests/test_market_analysis.py`

- [ ] **Step 1: Write failing tests** for `put_call_ratio`, `max_pain`, `atm_straddle`:
```python
from app.market_analysis import put_call_ratio, max_pain, atm_straddle
CHAIN = [  # {strike, ce_oi, pe_oi, ce_ltp, pe_ltp}
  {"strike":24100,"ce_oi":120000,"pe_oi":290000,"ce_ltp":168.4,"pe_ltp":41.0},
  {"strike":24200,"ce_oi":260000,"pe_oi":240000,"ce_ltp":78.2,"pe_ltp":92.4},
  {"strike":24300,"ce_oi":220000,"pe_oi":90000,"ce_ltp":29.8,"pe_ltp":172.5},
]
def test_pcr(): assert round(put_call_ratio(CHAIN),2) == round((290000+240000+90000)/(120000+260000+220000),2)
def test_max_pain_is_a_listed_strike(): assert max_pain(CHAIN) in {24100,24200,24300}
def test_atm_straddle(): s = atm_straddle(CHAIN, spot=24187.7); assert s["strike"]==24200 and round(s["straddle"],1)==170.6
```
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** the three pure functions. `put_call_ratio` = ΣPE_OI/ΣCE_OI (guard zero). `max_pain` = strike minimising Σ writer payout over all strikes (for each candidate strike K: Σ_j ce_oi_j*max(0,K−strike_j)+pe_oi_j*max(0,strike_j−K)); return argmin. `atm_straddle` = nearest-strike-to-spot CE_ltp+PE_ltp with `implied_move_pct = straddle/spot`.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** — "feat(market-analysis): PCR, max-pain, ATM straddle".

### Task 13: IV rank with honest fallback (TDD)

**Files:** Modify `market_analysis.py`, `tests/test_market_analysis.py`

- [ ] **Step 1: Write failing test** for `iv_rank`:
```python
from app.market_analysis import iv_rank
def test_iv_rank_from_history():
    r = iv_rank(current_iv=14.0, iv_history=[10,11,12,13,14,15,16,17,18,20])
    assert r["source"]=="atm_iv" and 0.0 <= r["rank"] <= 1.0 and r["warning"] is None
def test_iv_rank_thin_history_uses_vix_proxy():
    r = iv_rank(current_iv=None, iv_history=[], vix=11.82, vix_history=[10,11,12,13,14,15,16,18,20,22])
    assert r["source"]=="vix_proxy" and r["warning"]=="iv_history_thin"
```
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement `iv_rank`** = percentile of `current_iv` within `iv_history` (min≥N points, e.g. 8) → `{rank, source:"atm_iv", warning:None}`; else fall back to VIX percentile → `{rank, source:"vix_proxy", warning:"iv_history_thin"}`; if neither available → `{rank:None, source:"unavailable", warning:"iv_unavailable"}`. Never guess silently.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** — "feat(market-analysis): IV rank with VIX-proxy fallback".

### Task 14: S/R adapter over context_signals (TDD)

**Files:** Modify `market_analysis.py`, `tests/test_market_analysis.py`

- [ ] **Step 1: Read** `backend/app/context_signals.py` `support_resistance(...)` signature/return.
- [ ] **Step 2: Write failing test** for `levels_from_sr` — given a fake S/R output + spot, returns `{spot, pivot, supports:[...], resistances:[...], nearest_wall, position_in_range}` with supports strictly below spot and resistances strictly above, `0<=position_in_range<=1`.
- [ ] **Step 3: Implement `levels_from_sr(sr, spot)`** partitioning levels around spot, nearest-first; `position_in_range = (spot−S1)/(R1−S1)` clamped; `nearest_wall` = the closer of S1/R1 with its touch count if available.
- [ ] **Step 4: Run → PASS. Commit** — "feat(market-analysis): S/R levels adapter".

### Task 15: `GET /market/analysis` endpoint

**Files:**
- Modify: the module serving `/market/header` (add the route) — locate first.
- Create/modify: `tests/test_market_analysis_route.py`

- [ ] **Step 1: Write the failing route test** (FastAPI TestClient, monkeypatching the candle/chain loaders to fixtures) asserting a 200 with the documented payload keys (`structure`, `trend`, `levels`, `options`, `warnings`) and that a thin-IV fixture yields `options.iv_rank_source=="vix_proxy"` + a warning.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement the route** `GET /market/analysis?instrument=NIFTY`: load today's 1m/5m spot candles + daily candles (existing warehouse accessors), compute ADX/EMA columns (reuse `indicators.py`), call the pure functions (trend per TF, structure, levels_from_sr via context_signals, PCR/max-pain/straddle/iv_rank from the option chain the `/live` page already builds), attach net Δ/Θ from the existing portfolio-greeks source, assemble the payload, and **cache server-side ~8s** (module-level timestamped cache keyed by instrument). Never raise — on any missing input, return partial payload + `warnings`.
- [ ] **Step 4: Run → PASS.** Full suite green.
- [ ] **Step 5: Commit** — "feat(market): read-only GET /market/analysis (regime + S/R + option analytics)".

### Task 16: `GET /live-broker/holdings`

**Files:**
- Modify: `backend/app/routers/live_broker.py`
- Create/modify: `tests/test_live_broker_holdings.py`

- [ ] **Step 1: Read** how `/live-broker/positions` is implemented (its client call + last-known-on-failure pattern).
- [ ] **Step 2: Write failing test** — a fake Flattrade client returning holdings rows yields 200 + rows; a raising client yields 200 + `{stale:true, rows:last_known}` (never 500).
- [ ] **Step 3: Implement** `GET /live-broker/holdings` mirroring the positions route, calling the broker holdings read (the same client the app uses; NOT the MCP), with the identical degraded/last-known envelope.
- [ ] **Step 4: Run → PASS. Commit** — "feat(live-broker): read-only holdings endpoint".

### Task 17: Wire the analytics into the UI

**Files:**
- Modify: `frontend/src/lib/api.js` (add `marketAnalysis: (instrument="NIFTY") => apiClient.get("/market/analysis",{params:{instrument}}).then(r=>r.data)` and `liveBrokerHoldings: () => apiClient.get("/live-broker/holdings").then(r=>r.data)`)
- Modify: `frontend/src/components/live/LiveDataProvider.jsx` (add `marketAnalysis` slice @10s, `holdings` slice @30s, each `.catch()`'d with `lastSuccess`/degraded tracking like the others)
- Create: `MarketPulse.jsx`, `MarketAnalysis.jsx`; Modify: `AccountTabs.jsx` (Holdings tab)

- [ ] **Step 1: Extend the provider** with the two slices (follow the exact per-slice pattern already in `LiveDataProvider`).
- [ ] **Step 2: Build `MarketPulse`** from `marketAnalysis`: structure label + confidence bar; the 4-cell multi-TF trend (`trend.intraday/daily/weekly/monthly` → ▲/▼/▬ with up/down/flat colours); the S/R range bar using `levels` (spot marker at `position_in_range`, S1/R1 ends, pivot). Loading/`warnings` → show "—" not fake numbers.
- [ ] **Step 3: Build `MarketAnalysis`** tiles (PCR + meter, max-pain vs spot, IV rank + meter + source note, ATM straddle + implied move, net Δ, net Θ) + the ATM±2 option chain table (reuse the `/live` chain data source).
- [ ] **Step 4: Wire the Holdings tab** in `AccountTabs` from `holdings`.
- [ ] **Step 5: Replace the Phase-1 placeholders** in `LiveCockpit` with `<MarketPulse/>` + `<MarketAnalysis/>`.
- [ ] **Step 6: Build (direct recipe) + Chrome verify** with a hard refresh: regime meter, multi-TF trend, S/R bar, analytics tiles, chain, holdings all populate from live data. Verify the served `.map` contains `MarketPulse`.
- [ ] **Step 7: Commit** — "feat(live): wire market-analysis + holdings into the cockpit".

### Task 18: Phase 2 verification + docs

- [ ] **Step 1:** Extend `tests/test_live_cockpit_ui.py` to assert `marketAnalysis`/`liveBrokerHoldings` in `api.js` and `MarketPulse`/`MarketAnalysis` wired in `LiveCockpit`.
- [ ] **Step 2: Full backend suite** — expected all green (baseline + new tests).
- [ ] **Step 3: Frontend production build** — "Compiled successfully".
- [ ] **Step 4:** Update `CHANGELOG.md` (0.57.0 — live cockpit), `docs/AGENT_TODO.md` (item 4 done), `learning_log.md` (lessons), and `docs/HANDOFF.md`/`USER_MANUAL.md` live-page section.
- [ ] **Step 5: Commit** — "docs: live cockpit shipped (CHANGELOG 0.57.0 + handover)".

---

## Self-Review (completed)

- **Spec coverage:** command bar ✓(T3) · broker module ✓(T4) · market status ✓(T3) · alert rail ✓(T5) · always-on core (risk/positions/quick-trade/deployments) ✓(T6) · config drawer ✓(T7) · account tabs ✓(T8,T16) · market-analysis engine (trend/structure/S-R/PCR/max-pain/IV-rank/straddle/greeks) ✓(T10–T15) · holdings ✓(T16) · provider slices ✓(T17) · H8 consent preserved ✓(T7,T9) · no broker-mutating endpoints ✓(all new routes GET) · honest IV fallback ✓(T13) · phasing ✓.
- **Placeholder scan:** frontend tasks intentionally reference existing components by name rather than reproducing their internals (relocation, not rewrite) — each step names exact files, endpoints, and the verification; backend tasks carry full test+impl code.
- **Type consistency:** payload keys in T15 match the spec §3.2 and the UI consumers in T17 (`structure`, `trend.{intraday,daily,weekly,monthly}`, `levels.{supports,resistances,position_in_range}`, `options.{pcr_oi,max_pain,iv_rank_30d,iv_rank_source,atm_straddle,implied_move_pct,net_delta_rupee,net_theta_rupee}`, `warnings`). Function names (`classify_trend`, `classify_structure`, `put_call_ratio`, `max_pain`, `atm_straddle`, `iv_rank`, `levels_from_sr`) are used consistently across T10–T15 and T15's route.

## Known risk / note
- **Breakout structure** is deferred (treated as trending in v1) — noted in T11; add later if the range-break signal proves valuable.
- **Market-status source**: Phase 1 derives OPEN/CLOSED client-side; Phase 2 can swap to a backend `nse_calendar.market_status` field if precise holiday handling matters.
