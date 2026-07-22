# Live Trading page redesign — "Live Cockpit" (design)

Status: **design approved (mockup iterated 3×), spec for review.** Item 4 of the
2026-07-21 program (`docs/AGENT_TODO.md`). Interactive mockup committed alongside this
spec: `docs/superpowers/specs/2026-07-22-live-cockpit-mockup.html` (open in a browser —
click the Upstox/Flattrade chips, the account tabs, and ⚙ Configure).

## 1. Problem & goal

The current `/live-trading` page (`LiveDashboard.jsx`, 719 lines) is **feature-complete
but poorly organised for a trader**: one long vertical scroll of ~15 stacked sections,
with **no market context** (spot/VIX/expiry/option-chain live only on the separate
`/live` page). A trader can never see *market + my risk + my controls* in one glance.

Goal: re-organise `/live-trading` into a **trader cockpit** — an always-on core that keeps
market intelligence, positions, and quick actions in view, plus new market-analysis
capability (regime/trend/structure + S/R + option analytics), a compact broker-connection
module, and a professional tabbed account panel. **Reuse every existing component**; this
is a re-organisation + additive read-only analytics, not a rewrite. `/live` stays as-is.

### Hard invariants (do not violate)
- **No new broker-mutating endpoints.** Every new endpoint is READ-ONLY. Order placement,
  enable/disable/stop, kill, reconnect/disconnect all route through the EXISTING chokepoints.
- **Flattrade MCP stays agent-side** — a dev/verification tool, never a page runtime dependency.
- **Sparse broker polling while live is armed** (shared token rate budget). New reads reuse
  the existing `LiveDataProvider` cadence; the analysis endpoint is cached server-side.
- **TDD + host/container test split** (DEVELOPER_GUIDE §G/H). Every new backend function
  and endpoint gets host tests; frontend gets contract/source tests where the repo already
  uses them (grep-the-source pattern).
- The consent-gated live-enable flow (typed ENABLE + full frozen config, finding H8) is
  PRESERVED and surfaced in the drawer — never weakened.

## 2. Approved layout

```
┌ COMMAND BAR (sticky) ───────────────────────────────────────────────┐
│ LIVE COCKPIT · ●MARKET OPEN · [NIFTY BANKNIFTY SENSEX VIX exp] ·     │
│ [Upstox ●4h12m▾] [Flattrade ●4h41m▾] · ◼Kill · ⚙Configure           │
└─────────────────────────────────────────────────────────────────────┘
 (conditional alert banners: no-broker-backstop / unguarded / degraded)
┌ ALWAYS-ON CORE (2-col) ─────────────────┬───────────────────────────┐
│ LEFT — market intelligence              │ RIGHT — book & actions     │
│  • Market Pulse (compact):              │  • Live risk KPIs          │
│     structure + multi-TF trend + S/R    │  • Open positions + guard  │
│     range bar                           │  • Quick trade (1-click)   │
│  • Market analysis: PCR·maxpain·IVrank· │  • Deployments summary     │
│     straddle·netΔΘ + option chain       │                            │
└─────────────────────────────────────────┴───────────────────────────┘
┌ ACCOUNT (tabbed) ───────────────────────────────────────────────────┐
│ [Funds & Margin] [Holdings] [Order book] [Trade book]               │
└─────────────────────────────────────────────────────────────────────┘
⚙ CONFIG DRAWER (slide-over): deployment control · GTT/OCO backstop ·
   overall controls (basket SL/target/trail)
```

## 3. Architecture

### 3.1 Frontend

New page composition (thin, like today's `LiveTrading.jsx`):

```
LiveTrading (route /live-trading)
 └ LiveDataProvider  (extended: + marketAnalysis, + holdings polls)
    └ LiveErrorBoundary
       └ LiveCockpit                    NEW — the assembled layout
          ├ CommandBar                  NEW — market status + ticker + brokers + kill + cfg
          │   ├ MarketStatusPill        NEW — OPEN/CLOSED from nse_calendar
          │   ├ (reuse) MarketHeader data via api.marketHeader()
          │   └ BrokerConnect           NEW — Upstox + Flattrade chips + popover
          ├ AlertRail                   NEW — extracted from LiveDashboard (no-backstop/unguarded/degraded)
          ├ core grid
          │   ├ MarketPulse             NEW — structure + multi-TF trend + S/R range bar
          │   ├ MarketAnalysis          NEW — analytics tiles + option chain
          │   ├ RiskKpis                NEW — small; derived from existing limits/positions
          │   ├ (reuse) PositionsBlotter + KillSwitchPanel + GuardPanel(compact)
          │   ├ QuickTrade              NEW-thin wrapper around (reuse) LiveOrderTicket
          │   └ DeploymentSummary       NEW-thin — reads deployments, opens drawer
          ├ AccountTabs                 NEW — Funds/Holdings/Orders/Trades
          │   └ (reuse) OrdersBlotter, trade-history table, limits
          └ ConfigDrawer                NEW — slide-over hosting:
              ├ (reuse) LiveDeploymentStrip + DeployToLivePanel
              ├ (reuse) GttBook
              └ (reuse) OverallSettingsPanel
```

**Reuse map (existing → where it lands):**
| Existing component | New home |
|---|---|
| `LiveOrderTicket` | QuickTrade (core, right) + full form reachable |
| `KillSwitchPanel` | core right + command-bar Kill mirrors it |
| `PositionMonitor` / positions blotter | core right (Open positions) |
| `GuardPanel` | core right (compact) |
| `LiveDeploymentStrip` / `DeployToLivePanel` | ConfigDrawer |
| `GttBook` | ConfigDrawer (Backstop) |
| `OverallSettingsPanel` | ConfigDrawer (Overall controls) |
| `GreeksCard` | feeds MarketAnalysis net Δ/Θ |
| `LiveBlotter` / `LiveTradeStats` | AccountTabs (Trade book) |
| `MetricCard` | RiskKpis / Funds cells |
| `LiveBanner` / `ExecutionStateStrip` / `FeedHealthBanner` | folded into CommandBar + AlertRail |

The current `LiveDashboard.jsx` is retired in favour of `LiveCockpit.jsx` (its verbatim
blotter/derivation helpers move into small shared modules so nothing is rewritten, only
relocated).

### 3.2 Backend — two NEW read-only endpoints

**`GET /market/analysis?instrument=NIFTY`** — the deterministic market-analysis engine.
Composes existing capability; NEVER mutates. Server-side cached ~5–10s (so cockpit polling
doesn't hammer the option warehouse / broker). Response:

```jsonc
{
  "instrument": "NIFTY",
  "as_of": "2026-07-22T10:16:03+05:30",
  "spot": 24187.70,
  "structure": { "label": "UPTREND", "kind": "trending",      // trending|range|choppy|breakout
                 "regime_bucket": 4, "buckets": 5,             // 0..4 → Bearish/Down/Choppy/Up/Strong
                 "confidence": 0.72, "why": "higher-highs · above VWAP & 20-EMA · ADX 27 · RSI 61 no divergence" },
  "trend": { "intraday": "up", "daily": "up", "weekly": "flat", "monthly": "up" },  // up|down|flat
  "levels": { "spot": 24187.7, "pivot": 24205, "supports": [24135, 24060],
              "resistances": [24240, 24320], "nearest_wall": {"side":"R","price":24240,"touches":3},
              "position_in_range": 0.52 },
  "options": { "pcr_oi": 1.24, "max_pain": 24200, "iv_rank_30d": 0.28, "iv_rank_source": "atm_iv|vix_proxy",
               "atm_straddle": 212.0, "implied_move_pct": 0.88,
               "net_delta_rupee": 214, "net_theta_rupee": -1180 },
  "warnings": []   // e.g. ["iv_history_thin"] — honest degradation, never a silent guess
}
```

Computation (all from existing pieces — see §4). Endpoint lives in a new
`backend/app/market_analysis.py` (pure functions) + a thin route in
`routers/market.py` (or wherever `/market/header` lives).

**`GET /live-broker/holdings`** — read-only Flattrade holdings passthrough for the
Holdings account tab (mirrors the existing `/live-broker/positions` pattern; returns
last-known + as-of on a failed read, never raises).

### 3.3 Data flow — extend `LiveDataProvider`

`LiveDataProvider` already owns all polling (one fetch per endpoint per cadence, each
`.catch()`'d, with `lastSuccess` stamps + degraded-slice tracking). Add two slices:
- `marketAnalysis` — `GET /market/analysis` every ~10s (server-cached; cheap).
- `holdings` — `GET /live-broker/holdings` every ~30s (holdings change slowly).

Both follow the existing per-slice error handling so a failed read shows last-known +
"STALE", never a frozen-looks-live value. Connection status (`upstox/status`,
`flattrade/status`) is already polled; BrokerConnect consumes it.

## 4. The market-analysis engine (grounded in existing code)

All inputs already exist; the engine only composes them.

- **Multi-timeframe trend** (`intraday/daily/weekly/monthly` → up|down|flat): intraday from
  today's 1m/5m spot candles; daily/weekly/monthly from the daily warehouse
  (resample). Per-TF classifier = sign of a robust composite (price vs EMA20/EMA50 +
  higher-high/higher-low structure + linear-fit slope), `flat` when |slope| and ADX are
  below thresholds. Deterministic, causal.
- **Structure / regime** (`context_signals` already computes regime signals + swing S/R):
  classify intraday into trending / range(consolidation) / choppy / breakout via ADX (trend
  strength), ATR/range compression (consolidation), and swing sequence. Map to the 5-bucket
  meter (Bearish/Down/Choppy/Up/Strong) + a normalised confidence. `why` is assembled from
  the same signals, in plain English.
- **Support/Resistance**: reuse `context_signals.support_resistance` (swing-cluster levels +
  proximity). Emit nearest support(s) below and resistance(s) above spot, day pivot,
  nearest wall (level with most touches), and `position_in_range` for the range bar.
- **PCR (OI)** = Σ(PE OI)/Σ(CE OI) over the ATM±N chain (option_contracts + latest OI).
- **Max pain** = strike minimising total writer payout across the chain (standard calc).
- **IV rank (30d)** = current ATM IV percentile vs its trailing-30d range from stored option
  candles; when IV history is thin, fall back to a **VIX-percentile proxy** and set
  `iv_rank_source:"vix_proxy"` + `warnings:["iv_history_thin"]` (honest, never silent).
- **ATM straddle** = ATM CE LTP + ATM PE LTP → `implied_move_pct` = straddle/spot.
- **Net Δ / Θ (book)** = reuse the existing server-side Black-Scholes portfolio greeks
  (`GreeksCard`'s source).

The option chain the analysis reads is the SAME source the `/live` page's ATM±3 chain uses
(option stream / options_1m + option_contracts); no new market data plumbing.

## 5. Broker connection module (`BrokerConnect`)

Two compact chips (Upstox = market data, Flattrade = execution). Each shows: a
connection dot (green valid / amber expiring-soon / red expired-or-disconnected), the
token-validity countdown, and a click-out popover with:
- **Reconnect** → existing `api.upstoxAuthStart()` / Flattrade login flow.
- **Disconnect** → existing `api.disconnectUpstox()` / `api.disconnectFlattrade()`.
- When the token is expired, the primary control reads **"Login to Upstox/Flattrade"** and
  drives the same OAuth start (Flattrade: after 06:00 IST; never via the MCP).
State comes from the already-polled `upstox/status` + `flattrade/status`. A `MarketStatusPill`
shows OPEN (pulsing) / CLOSED with the close/next-open time from `nse_calendar.market_status`.
No new mutating endpoints — reconnect/disconnect/login all already exist.

## 6. Account tabs (`AccountTabs`) — professional broker dashboard

| Tab | Source (existing unless noted) |
|---|---|
| Funds & Margin | `/live-broker/limits` → available/used margin, opening balance, cash+collateral, realised/unrealised M2M, span+exposure, utilisation bar |
| Holdings | `/live-broker/holdings` (NEW read-only endpoint) |
| Order book | `/live-broker/orders` (reuse OrdersBlotter, all statuses incl. REJECTED reasons) |
| Trade book | `/live-broker/trade-history` + `LiveBlotter` (deployment-attributed) |

Funds tab models the fields a Kite/Flattrade funds screen shows, laid out as stat cells +
a margin-utilisation bar; degraded reads show last-known + as-of.

## 7. Phasing (build order — approved: shell first)

- **Phase 1 — cockpit shell (reuses only existing data):** new `LiveCockpit` layout —
  command bar (market status + ticker + BrokerConnect from existing status), AlertRail,
  always-on core with existing positions/kill/guard/quick-trade/deployment-summary,
  ConfigDrawer hosting the existing deployment/backstop/overall components, and the
  AccountTabs Funds/Orders/Trades (existing endpoints). MarketPulse + MarketAnalysis render
  as "loading/coming online" placeholders. **Ships a better-organised page immediately;
  frontend build + source-contract tests green.**
- **Phase 2 — analysis engine + holdings:** `backend/app/market_analysis.py` pure functions
  + `GET /market/analysis` + `GET /live-broker/holdings`, host-tested; wire MarketPulse
  (structure + multi-TF trend + S/R range bar), MarketAnalysis (PCR/maxpain/IVrank/straddle/
  greeks + chain), and the Holdings tab. Chrome-verify with a hard refresh.

Each phase is independently verifiable and committable.

## 8. Error handling & degraded states
- Every new poll is individually `.catch()`'d in the provider; a failed slice keeps
  last-known + shows "STALE" via the existing degraded-banner mechanism.
- `/market/analysis` returns `warnings` (e.g. thin IV history, missing option chain) instead
  of guessing; the UI renders the metric as "—" with a tooltip, never a fabricated number.
- Off-hours: MarketStatusPill = CLOSED; analysis serves the last session's close with an
  "as of last close" stamp.
- Broker disconnected: BrokerConnect shows red + Login; the core still renders last-known
  broker data with the STALE treatment.

## 9. Testing
- **Backend (host):** pure `market_analysis` functions — trend classifier per TF, structure/
  regime bucketing + confidence, S/R extraction, PCR, max-pain, IV-rank (+ vix_proxy
  fallback + warning), straddle/implied-move; endpoint shape + degraded/warning paths;
  `/live-broker/holdings` last-known-on-failure. Fixtures from stored candles/chain.
- **Frontend:** source-contract tests (the repo's grep-the-JSX pattern) pinning that the
  cockpit renders BrokerConnect reconnect/disconnect, the AccountTabs, MarketPulse, and that
  the consent flow strings (H8) remain; a render smoke test if the harness supports it.
- Full suite must stay green; frontend production build must pass.

## 10. Non-goals (v1)
- No charting/candlestick widget (out of scope; the analytics + chain suffice for v1).
- No contextual LLM commentary (the deterministic engine ships first; AI layer is future).
- No changes to `/live` (LiveSignals).
- No new order types or broker-mutating actions.
- Order modify/cancel from the Order book tab is display-only in v1 (cancel is a mutating
  action — defer unless explicitly requested).

## 11. Regression safety
- Existing components are relocated, not rewritten; their own tests continue to cover them.
- The route (`/live-trading`) and `LiveDataProvider` contract are preserved; only additive
  slices.
- `LiveDashboard.jsx` retirement: keep its verbatim helpers (blotter/derivations) in a
  shared module imported by `LiveCockpit`, so behaviour is identical and any source-contract
  test that greps those helpers still passes.
