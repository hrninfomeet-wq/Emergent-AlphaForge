# Live-Feed Health — Frontend Implementation Plan (Plan 2 of 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the deployment status LED tell the truth — green only when the strategy can actually trade (live candles flowing) — and show a prompt banner with a one-click Connect-Upstox action when the feed is offline, on both the Paper and Live pages.

**Architecture:** A pure helper `deploymentLiveness(deployment, feedHealth)` maps `deployment.status × feedHealth.state` → an LED (dot color, label, tooltip). The Paper strip (`DeploymentControlStrip`) and the page poll `GET /live-feed/health` and use the helper; a new `FeedHealthBanner` renders the prompt. The Live page reuses the same via `LiveDataProvider`. Built on Plan 1's `/live-feed/health` endpoint (states: LIVE / WARMING_UP / DEGRADED / NEEDS_LOGIN / MARKET_CLOSED).

**Tech Stack:** React (CRA + craco), Tailwind, lucide-react, axios (`apiClient`). Frontend `C:\Users\haroo\af-wt-livefeed\frontend`. Branch `feat/live-feed-health`. Spec: `docs/superpowers/specs/2026-06-29-live-feed-health-truthful-liveness-design.md`. Plan 1 (backend) is merged on this branch.

> cwd for build commands = `C:\Users\haroo\af-wt-livefeed\frontend`. The project has **no JS unit-test runner** — verification is `yarn build` (compile/lint clean) + a visual render check. `deploymentLiveness` is a pure function; its full truth table is given below so it can be reasoned + visually confirmed. Match existing Tailwind class names exactly (`bg-emerald-400`/`text-emerald-300`, `bg-amber-400`/`text-amber-300`, `bg-rose-400`/`text-rose-300`, `bg-dimmer`/`text-dimmer`/`text-dim`).

## File structure
- **Create** `frontend/src/lib/deploymentLiveness.js` — pure LED helper.
- **Create** `frontend/src/components/live/FeedHealthBanner.jsx` — the prompt banner + Connect-Upstox / Restart-feed CTAs.
- **Modify** `frontend/src/lib/api.js` — `getLiveFeedHealth` + `upstoxAuthStart` (if missing) + `restartLiveFeed`.
- **Modify** `frontend/src/components/live/LiveDataProvider.jsx` — poll feed health, expose `feedHealth`.
- **Modify** `frontend/src/components/paper/DeploymentControlStrip.jsx` — accept `feedHealth`, render the LED via `deploymentLiveness`.
- **Modify** `frontend/src/pages/PaperTrading.jsx` — poll feed health, pass to the strip, render the banner.
- **Modify** `frontend/src/pages/LiveTrading.jsx` (or the Live dashboard) — render the banner from `useLiveData().feedHealth` (reuse).

---

## Task 1: the `deploymentLiveness` pure helper

**Files:**
- Create: `frontend/src/lib/deploymentLiveness.js`

**Truth table (the spec's §3 LED rules):**
| deployment.status | feedHealth.state | dot | label |
|---|---|---|---|
| ACTIVE | LIVE | emerald | ACTIVE · LIVE |
| ACTIVE | WARMING_UP | amber | ACTIVE · STARTING |
| ACTIVE | NEEDS_LOGIN | rose | ACTIVE · FEED OFFLINE |
| ACTIVE | DEGRADED | rose | ACTIVE · NO LIVE CANDLES |
| ACTIVE | MARKET_CLOSED | dimmer | ACTIVE · MARKET CLOSED |
| ACTIVE | (null/unknown) | dimmer | ACTIVE (tooltip "checking feed…") — no false green |
| PAUSED | any | amber | PAUSED (tooltip = paused reason) |
| other/ARCHIVED | any | dimmer | the raw status |

- [ ] **Step 1: Implement the helper**

Create `frontend/src/lib/deploymentLiveness.js`:

```javascript
// Maps a deployment's lifecycle status + the GLOBAL live-feed health into the
// truthful status LED. Green ("ACTIVE · LIVE") appears ONLY when the strategy can
// actually trade right now (fresh candles_1m bars). Pure — no React, no imports.
// feedHealth = { state, reason, cta } from GET /live-feed/health (null while loading).
export function deploymentLiveness(dep, feedHealth) {
  const status = String(dep?.status || "").toUpperCase();
  if (status === "PAUSED") {
    return {
      dot: "bg-amber-400", text: "text-amber-300", label: "PAUSED",
      tooltip: dep?.paused_reason || dep?.kill_switch_reason || "Paused",
    };
  }
  if (status !== "ACTIVE") {
    return { dot: "bg-dimmer", text: "text-dimmer", label: status || "—", tooltip: status || "—" };
  }
  const state = feedHealth?.state;
  const reason = feedHealth?.reason || "";
  switch (state) {
    case "LIVE":
      return { dot: "bg-emerald-400", text: "text-emerald-300", label: "ACTIVE · LIVE",
               tooltip: reason || "Receiving fresh candles." };
    case "WARMING_UP":
      return { dot: "bg-amber-400", text: "text-amber-300", label: "ACTIVE · STARTING",
               tooltip: reason || "Feed starting — first candle shortly." };
    case "NEEDS_LOGIN":
      return { dot: "bg-rose-400", text: "text-rose-300", label: "ACTIVE · FEED OFFLINE",
               tooltip: reason || "Upstox isn't connected — connect to go live." };
    case "DEGRADED":
      return { dot: "bg-rose-400", text: "text-rose-300", label: "ACTIVE · NO LIVE CANDLES",
               tooltip: reason || "Live feed stalled." };
    case "MARKET_CLOSED":
      return { dot: "bg-dimmer", text: "text-dimmer", label: "ACTIVE · MARKET CLOSED",
               tooltip: "Market is closed." };
    default:
      // feedHealth not loaded yet — DO NOT claim green; show neutral "checking".
      return { dot: "bg-dimmer", text: "text-dim", label: "ACTIVE", tooltip: "Checking live feed…" };
  }
}
```

- [ ] **Step 2: Verify it compiles (lint via build later; sanity-trace now)**

Run: `cd "C:/Users/haroo/af-wt-livefeed/frontend" && node --input-type=module -e "import('./src/lib/deploymentLiveness.js').then(m=>{const f=m.deploymentLiveness;console.log(f({status:'ACTIVE'},{state:'LIVE'}).label==='ACTIVE · LIVE', f({status:'ACTIVE'},{state:'NEEDS_LOGIN'}).dot==='bg-rose-400', f({status:'ACTIVE'},null).label==='ACTIVE'&&f({status:'ACTIVE'},null).dot==='bg-dimmer', f({status:'PAUSED'},{state:'LIVE'}).label==='PAUSED');})"`
Expected: `true true true true` (LIVE→green label; NEEDS_LOGIN→rose; null-feed→neutral not green; PAUSED ignores feed). If `node` can't resolve the ESM import, skip this and rely on the `yarn build` + visual check in Task 7 — the function is pure and the truth table above is authoritative.

- [ ] **Step 3: Commit**

```bash
cd "C:/Users/haroo/af-wt-livefeed" && git add frontend/src/lib/deploymentLiveness.js && git commit -m "feat(livefeed-ui): deploymentLiveness pure LED helper (status x feed health)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: api methods

**Files:**
- Modify: `frontend/src/lib/api.js`

- [ ] **Step 1: Add the methods**

In `frontend/src/lib/api.js`, add to the `api` object (near `upstoxStatus`, ~line 63). First check whether `upstoxAuthStart` already exists (grep it); add only what's missing:

```javascript
  getLiveFeedHealth: () => apiClient.get("/live-feed/health").then((r) => r.data),
  upstoxAuthStart: () => apiClient.get("/upstox/auth/start").then((r) => r.data),
  restartLiveFeed: async () => {
    // Best-effort manual bring-up: clears the supervisor's manual-stop suppression
    // and starts stream + roller. The supervisor keeps them up thereafter.
    await apiClient.post("/upstox/stream/start", {}).catch(() => {});
    return apiClient.post("/live-candles/start", {}).then((r) => r.data);
  },
```

(Confirm the base path: existing calls use paths like `/upstox/status` against `apiClient` whose baseURL already includes `/api`. Match that — do NOT prefix `/api` again.)

- [ ] **Step 2: Verify build**

Run: `cd "C:/Users/haroo/af-wt-livefeed/frontend" && yarn build 2>&1 | tail -8`
Expected: `Compiled successfully` (or the project's success line), no errors.

- [ ] **Step 3: Commit**

```bash
cd "C:/Users/haroo/af-wt-livefeed" && git add frontend/src/lib/api.js && git commit -m "feat(livefeed-ui): api getLiveFeedHealth + upstoxAuthStart + restartLiveFeed

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: the `FeedHealthBanner` component

**Files:**
- Create: `frontend/src/components/live/FeedHealthBanner.jsx`

- [ ] **Step 1: Implement**

Create `frontend/src/components/live/FeedHealthBanner.jsx` (modeled on `LiveBanner.jsx`'s styling + login pattern):

```jsx
import { useState } from "react";
import { AlertTriangle, Loader2, PlugZap, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";

/**
 * FeedHealthBanner — surfaces when ACTIVE deployments exist but the live DATA feed
 * (Upstox stream -> candle roller -> fresh candles_1m) is NOT delivering, so the
 * trader is told immediately instead of waiting all day. Shown on Paper + Live pages.
 *
 * Renders only for feedHealth.state in {NEEDS_LOGIN, DEGRADED, WARMING_UP} with >=1
 * active deployment (LIVE / MARKET_CLOSED show nothing — the state already encodes hours).
 */
export default function FeedHealthBanner({ feedHealth, activeCount = 0 }) {
  const [busy, setBusy] = useState(false);
  const state = feedHealth?.state;
  if (!feedHealth || activeCount < 1) return null;
  if (state !== "NEEDS_LOGIN" && state !== "DEGRADED" && state !== "WARMING_UP") return null;

  const connect = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const res = await api.upstoxAuthStart();
      const url = res?.login_url;
      if (url) { window.location.href = url; return; }
    } catch { /* fall through */ }
    setBusy(false);
  };
  const restart = async () => {
    if (busy) return;
    setBusy(true);
    try { await api.restartLiveFeed(); } catch { /* surfaced by next poll */ }
    setBusy(false);
  };

  const warming = state === "WARMING_UP";
  const tone = warming
    ? "border-amber-500/40 bg-amber-500/10 text-amber-300"
    : "border-2 border-danger bg-danger/10 text-danger";

  return (
    <div className={`rounded-lg px-4 py-3 flex items-center gap-3 flex-wrap ${tone}`} data-testid="feed-health-banner">
      {warming ? <Loader2 className="w-5 h-5 shrink-0 animate-spin" /> : <AlertTriangle className="w-5 h-5 shrink-0" />}
      <div className="min-w-0 flex-1">
        <div className="text-sm font-semibold">
          {state === "NEEDS_LOGIN" && `${activeCount} strateg${activeCount === 1 ? "y is" : "ies are"} active but the live data feed is offline`}
          {state === "DEGRADED" && "Active strategies, but no live candles are arriving"}
          {warming && "Live data feed is starting…"}
        </div>
        <div className="text-xs opacity-90">{feedHealth.reason || ""}{state === "NEEDS_LOGIN" ? " They will not trade until you connect." : ""}</div>
      </div>
      {state === "NEEDS_LOGIN" && feedHealth.cta === "connect_upstox" && (
        <button type="button" onClick={connect} disabled={busy}
          className="inline-flex items-center gap-1 px-3 py-1 rounded-md border border-info/40 bg-info/10 text-info text-xs font-mono hover:bg-info/20 disabled:opacity-60 transition-colors"
          data-testid="feed-connect-upstox">
          {busy ? <Loader2 className="w-3 h-3 animate-spin" /> : <PlugZap className="w-3 h-3" />}
          {busy ? "Opening…" : "Connect Upstox"}
        </button>
      )}
      {state === "DEGRADED" && (
        <button type="button" onClick={restart} disabled={busy}
          className="inline-flex items-center gap-1 px-3 py-1 rounded-md border border-danger/40 bg-danger/10 text-danger text-xs font-mono hover:bg-danger/20 disabled:opacity-60 transition-colors"
          data-testid="feed-restart">
          {busy ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />}
          Restart feed
        </button>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Verify build**

Run: `cd "C:/Users/haroo/af-wt-livefeed/frontend" && yarn build 2>&1 | tail -8`
Expected: `Compiled successfully`, no errors. (If `PlugZap`/`RefreshCw` aren't valid lucide-react exports in the installed version, substitute existing ones — e.g. `Plug`, `RotateCw` — and keep building until clean.)

- [ ] **Step 3: Commit**

```bash
cd "C:/Users/haroo/af-wt-livefeed" && git add frontend/src/components/live/FeedHealthBanner.jsx && git commit -m "feat(livefeed-ui): FeedHealthBanner (Connect Upstox / Restart feed prompt)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: truthful LED in the Paper strip

**Files:**
- Modify: `frontend/src/components/paper/DeploymentControlStrip.jsx`

- [ ] **Step 1: Thread `feedHealth` + use the helper**

In `DeploymentControlStrip.jsx`:

(a) Import the helper at top:
```javascript
import { deploymentLiveness } from "@/lib/deploymentLiveness";
```

(b) In `DeploymentControlRow`, accept `feedHealth` and replace the status-derived `dot`/`statusText`/label (current lines 10–24) with the helper. The row signature becomes `function DeploymentControlRow({ dep, open, busy, feedHealth, onPause, onResume, onStop })` and:
```javascript
  const status = String(dep.status || "").toUpperCase();
  const isActive = status === "ACTIVE";
  const isPaused = status === "PAUSED";
  const live = deploymentLiveness(dep, feedHealth);
```
Then the dot + label spans use `live`:
```jsx
      <span className={`w-2 h-2 rounded-full shrink-0 ${live.dot}`} title={live.tooltip} />
```
```jsx
      <span className={`ml-2 text-[11px] uppercase tracking-wider ${live.text}`} title={live.tooltip}>{live.label}</span>
```
Keep `isActive`/`isPaused` for the Pause/Resume button visibility logic (unchanged).

(c) The default export `DeploymentControlStrip` accepts `feedHealth` and passes it to each row:
```javascript
export default function DeploymentControlStrip({ liveDeployments, perDeployOpen, busy, feedHealth, onPause, onResume, onStop, onStopAll }) {
```
```jsx
            <DeploymentControlRow
              key={dep.id}
              dep={dep}
              open={perDeployOpen[dep.id]}
              busy={busy}
              feedHealth={feedHealth}
              onPause={onPause}
              onResume={onResume}
              onStop={onStop}
            />
```

- [ ] **Step 2: Verify build**

Run: `cd "C:/Users/haroo/af-wt-livefeed/frontend" && yarn build 2>&1 | tail -8`
Expected: `Compiled successfully`.

- [ ] **Step 3: Commit**

```bash
cd "C:/Users/haroo/af-wt-livefeed" && git add frontend/src/components/paper/DeploymentControlStrip.jsx && git commit -m "feat(livefeed-ui): Paper strip LED reflects live-feed health

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: wire the Paper page (poll feed health + banner)

**Files:**
- Modify: `frontend/src/pages/PaperTrading.jsx`

- [ ] **Step 1: Poll feed health + pass it down + render the banner**

In `PaperTrading.jsx`:

(a) Imports:
```javascript
import FeedHealthBanner from "@/components/live/FeedHealthBanner";
```
(b) Add feed-health state + a poll alongside the existing deployment polling. Find where `deployments` is loaded (`api.listDeployments(...)`, ~line 145/385) and add a parallel poll. Use the same `useState`/`useEffect` interval pattern the page already uses (match it). Minimal version near the other state:
```javascript
  const [feedHealth, setFeedHealth] = useState(null);
  useEffect(() => {
    let alive = true;
    const tick = () => api.getLiveFeedHealth().then((h) => { if (alive) setFeedHealth(h); }).catch(() => {});
    tick();
    const id = setInterval(tick, 5000);
    return () => { alive = false; clearInterval(id); };
  }, []);
```
(c) Compute the active count from the page's existing non-archived `deployments` (the `liveDeployments` passed to the strip):
```javascript
  const activeCount = (deployments || []).filter((d) => String(d.status || "").toUpperCase() === "ACTIVE").length;
```
(d) Render `<FeedHealthBanner feedHealth={feedHealth} activeCount={activeCount} />` directly ABOVE the `<DeploymentControlStrip ... />` element, and pass `feedHealth={feedHealth}` into `<DeploymentControlStrip>`:
```jsx
      <FeedHealthBanner feedHealth={feedHealth} activeCount={activeCount} />
      <DeploymentControlStrip
        liveDeployments={/* existing prop */}
        perDeployOpen={/* existing */}
        busy={/* existing */}
        feedHealth={feedHealth}
        onPause={/* existing */} onResume={/* existing */} onStop={/* existing */} onStopAll={/* existing */}
      />
```
(Match the EXISTING `<DeploymentControlStrip>` call's prop expressions — only ADD `feedHealth={feedHealth}` and the banner above it. Do not change the other props.)

- [ ] **Step 2: Verify build**

Run: `cd "C:/Users/haroo/af-wt-livefeed/frontend" && yarn build 2>&1 | tail -8`
Expected: `Compiled successfully`.

- [ ] **Step 3: Commit**

```bash
cd "C:/Users/haroo/af-wt-livefeed" && git add frontend/src/pages/PaperTrading.jsx && git commit -m "feat(livefeed-ui): Paper page polls feed health + shows the prompt banner

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: Live page — feed health in the provider + banner

**Files:**
- Modify: `frontend/src/components/live/LiveDataProvider.jsx`
- Modify: `frontend/src/pages/LiveTrading.jsx` (the page that wraps the Live dashboard in `<LiveDataProvider>`)

- [ ] **Step 1: Add the feed-health poll to the provider**

In `LiveDataProvider.jsx`, add a poll (10s is fine — match `DEPLOY_MS`) and expose `feedHealth`:
```javascript
  const { data: feedHealth, error: eFeedHealth, refetch: rFeedHealth } = usePoll(() => api.getLiveFeedHealth(), DEPLOY_MS);
```
Add `feedHealth` to the `value` object's data block and `feedHealth: eFeedHealth` to `errors`, and `rFeedHealth` into `refetchAll`’s body + dep array (mirror the existing entries exactly).

- [ ] **Step 2: Render the banner on the Live page**

In `LiveTrading.jsx` (inside `<LiveDataProvider>`, above the deployment strip), consume `useLiveData()` and render the banner. If the page's top-level component isn't already a `useLiveData()` consumer, render it from a small child that is — mirror how `LiveDeploymentStrip` calls `useLiveData()`. Compute `activeCount` from `deployments`:
```jsx
  const { deployments, feedHealth } = useLiveData();
  const activeCount = (deployments || []).filter((d) => String(d.status || "").toUpperCase() === "ACTIVE").length;
  ...
  <FeedHealthBanner feedHealth={feedHealth} activeCount={activeCount} />
```
(Import `FeedHealthBanner`. This is additive — it appears only when a feed problem exists.)

- [ ] **Step 3: Verify build**

Run: `cd "C:/Users/haroo/af-wt-livefeed/frontend" && yarn build 2>&1 | tail -8`
Expected: `Compiled successfully`.

- [ ] **Step 4: Commit**

```bash
cd "C:/Users/haroo/af-wt-livefeed" && git add frontend/src/components/live/LiveDataProvider.jsx frontend/src/pages/LiveTrading.jsx && git commit -m "feat(livefeed-ui): Live page feed-health poll + prompt banner

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7: build + visual verification

**Files:** none (verification only)

- [ ] **Step 1: Clean production build**

Run: `cd "C:/Users/haroo/af-wt-livefeed/frontend" && yarn build 2>&1 | tail -12`
Expected: `Compiled successfully`, no errors/warnings introduced by these changes.

- [ ] **Step 2: Confirm additive diff**

Run: `cd "C:/Users/haroo/af-wt-livefeed" && git diff --stat 0f539e7..HEAD -- frontend/`
Expected: only the 7 listed frontend files changed (2 new: `deploymentLiveness.js`, `FeedHealthBanner.jsx`).

- [ ] **Step 3: Visual check (manual / Docker render)**

This is the render verification (no JS test runner). On the running stack's Paper page during a feed-offline state (e.g. before the daily Upstox login): the `FeedHealthBanner` shows "data feed is offline" + `[Connect Upstox]`, and each ACTIVE row's LED is rose "ACTIVE · FEED OFFLINE". After connecting + the supervisor brings the feed up: within ~20 s the rows go amber "ACTIVE · STARTING" then green "ACTIVE · LIVE", and the banner disappears. Outside market hours: grey "ACTIVE · MARKET CLOSED", no banner. *(The controller/user performs this on the running stack; the implementer is not expected to drive Chrome.)*

---

## Self-Review

**1. Spec coverage (Plan 2 = the frontend half of spec §3, §4, §7):**
- Truthful LED = `status × feedHealth` on the Paper strip (the screenshot) → Tasks 1, 4, 5. ✓
- Same on the Live strip → Task 6. ✓
- Green only when genuinely LIVE; neutral (not green) when feed health unknown → Task 1 helper (default branch). ✓
- Prompt banner with Connect-Upstox (NEEDS_LOGIN) / Restart-feed (DEGRADED) / subtle starting (WARMING_UP), only with ≥1 active deployment, none on LIVE/MARKET_CLOSED → Task 3 + Tasks 5/6. ✓
- Feed-health poll (Paper page local + Live page via provider) → Tasks 5, 6. ✓

**2. Placeholder scan:** the `/* existing */` markers in Task 5(d) are explicit "keep the current prop expression" instructions (the surrounding real code is shown), not blanks — the only ADDED prop is `feedHealth`. Every new file/function has complete code. ✓

**3. Type/name consistency:** `deploymentLiveness` returns `{dot, text, label, tooltip}` and every consumer (the row spans) reads exactly those keys. `feedHealth` shape (`{state, reason, cta}`) matches Plan 1's endpoint. `getLiveFeedHealth`/`upstoxAuthStart`/`restartLiveFeed` are spelled identically in `api.js` and the components. The banner gates on the same state names the helper uses. ✓

**Notes for the executor:**
- No JS test runner exists — do NOT add one. Verify each task with `yarn build` (must stay `Compiled successfully`); the truth table in Task 1 is authoritative for the helper.
- Match existing Tailwind tokens (`text-dim`/`text-dimmer`/`bg-dimmer`, `border-danger`, `text-info`, `bg-emerald-400`…) — they're defined in the project's theme; don't invent new color classes.
- If a lucide-react icon name doesn't exist in the installed version, substitute the closest existing one and keep the build clean.
- Keep every change ADDITIVE to existing behavior; the banner and the richer LED appear only when feed health is known and relevant.
