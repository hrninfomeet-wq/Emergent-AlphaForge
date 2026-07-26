# Live Cockpit page audit — findings register (2026-07-25)

> Persistent record of the /live-trading deep audit. Every finding below was
> produced by a dimension auditor reading the real code. STATUS is maintained by
> hand as items are verified/fixed. Two audit runs were killed by the usage limit;
> these were recovered from the workflow journal so nothing was lost.

Legend — **STATUS**: FIXED (landed + verified) · OPEN (verified real, not yet fixed) · UNVERIFIED (auditor claim, not yet checked) · N/A (refuted).


## CRITICAL

### C:/Users/haroo/OneDrive/Documents/New project/Emergent-AlphaForge/frontend/src/components/live/LiveCockpit.jsx:94

- **STATUS:** FIXED — Same duplicate-header fix.
- **Defect:** <MarketHeader /> is rendered a SECOND time inside the cockpit even though the app shell already renders it for every route (Layout.jsx:105), so ~270px of duplicated ticker is pushed into the page scroll area and the always-on core starts below the fold.
- **Failure:** Trader opens /live-trading on a 1366x768 laptop (~660px usable viewport). TopBar (56px) + the shell's MarketHeader (8 primary tiles wrapping to 2 rows in [grid-template-columns:repeat(auto-fit,minmax(132px,1fr))] at ~988px content width, plus status row + Global Markets toggle ≈ 274px) already consume ~330px of chrome. Inside page-content the sticky CommandBar (~46px) and the DUPLICATE MarketHeader (another ~274px) consume the remaining ~330px. Result: at page load the trader sees two identical NIFTY/SENSEX/BANKNIFTY ticker grids and nothing else — Market Pulse, Risk KPIs, Open Positions and the Kill Switch are all off-screen and require scrolling on a real-money terminal. Expanding 'Global Markets' in either copy makes it worse.
- **Fix:** Delete the <MarketHeader /> at LiveCockpit.jsx:94 (and its import at line 17) — the shell already mounts it at Layout.jsx:105. Removing it also kills a duplicate SSE connection + 1s poll fallback to /market/header.

### frontend/src/components/live/LiveCockpit.jsx:57

- **STATUS:** FIXED — ExecutionStateStrip re-mounted with Stand-down (3007f7d). Verified in browser.
- **Defect:** The cockpit polls `armState` but never renders `ExecutionStateStrip`, so the page has no "will a signal transmit a REAL order right now?" verdict, no `exit_gap` warning, and no Stand-down control — `mode` is computed and handed to `QuickTrade`→`LiveOrderTicket`, which ignores the prop entirely.
- **Failure:** `LiveTrading.jsx` renders only `LiveCockpit` (the old LiveDashboard is gone), and `ExecutionStateStrip.jsx` is imported nowhere (verified by grep). Two concrete breaks: (1) the backend's `arm-state.exit_gap`/`warning` alert — "Real entries armed but guard auto-squares are dry-run — only the broker OCO protects open positions" (ExecutionStateStrip.jsx:49-62) — can never appear, so a trader holding a live position sees GuardPanel's green/red "Auto-exit live" pill and believes software stops are transmitting when the arm-state says they are not. (2) `LiveOrderTicket.handlePlaceConfirmed` silently flips the GLOBAL mode to LIVE_TEST (LiveOrderTicket.jsx:335) and, if the best-effort stand-down at line 359 also fails (same network/broker outage that failed the place), the system is left ARMED with no UI anywhere showing the mode and no Stand-down button to disarm it — exactly the state the strip was built to expose.
- **Fix:** Mount `<ExecutionStateStrip armState={armState} onStandDown={...} />` in the always-on core (e.g. directly under AlertRail), wiring onStandDown to `api.setLiveMode("LIVE_OFFLINE")` + `refetch.all`.


## HIGH

### C:/Users/haroo/OneDrive/Documents/New project/Emergent-AlphaForge/frontend/src/components/live/DeployToLivePanel.jsx:255

- **STATUS:** FIXED — max-h-[85vh] overflow-y-auto added to shared DialogContent — fixes every dialog (58c158d).
- **Defect:** The 'Enable Live Execution' caps dialog uses shared DialogContent, which is `fixed top-[50%] translate-y-[-50%]` with NO max-height and NO overflow-y-auto (ui/dialog.jsx:32), so on short viewports the tall caps form is clipped off both the top and bottom of the screen with no way to scroll it.
- **Failure:** Trader opens Configure -> Deployment control -> 'Enable Live Execution' on a laptop whose browser viewport is ~640-720px tall (1366x768 with browser chrome, or any window that isn't maximised). The dialog body is ~700-750px: DialogHeader + real-money warning banner + forward-validation banner + four required Input fields (Lots/signal, Max lots/day, Max concurrent, Daily loss cap) + the PC-down OCO box with two more inputs + the Cancel/'Continue →' row, plus p-6 and gap-4 between 7 grid rows. Centred on a 660px viewport it overhangs ~45px at each end; the 'Continue →' submit button (line 434) sits below the viewport bottom, the dialog itself doesn't scroll, and the page behind it is scroll-locked by Radix. The trader physically cannot enable a deployment for live execution and gets no error explaining why.
- **Fix:** Add `max-h-[85vh] overflow-y-auto` to the DialogContent className at DeployToLivePanel.jsx:255 (or, better, to the shared DialogContent in frontend/src/components/ui/dialog.jsx:32 so every dialog in the app is bounded).

### C:/Users/haroo/OneDrive/Documents/New project/Emergent-AlphaForge/frontend/src/components/live/LiveCockpit.jsx:133

- **STATUS:** UNVERIFIED
- **Defect:** The `#kill-switch` anchor target has no scroll-margin-top, so the CommandBar's `sticky top-0 z-20` bar (CommandBar.jsx:28) parks itself directly over the top of the KillSwitchPanel after the Kill jump, covering the panel header and the 'N open · N working' count readout.
- **Failure:** During a live drawdown the trader clicks the red 'Kill' button in the command bar (CommandBar.jsx:45, `href="#kill-switch"`). The browser scrolls page-content so the #kill-switch div's top aligns with the container top — which is exactly where the ~46px sticky bar sits. The KillSwitchPanel's header row (KillSwitchPanel.jsx:150) — the 'KILL SWITCH' title, the blast-radius description, and the `kill-switch-counts` chip that tells them how many positions/orders are about to be flattened, or that broker state is UNKNOWN — is hidden behind a semi-transparent `bg-bg-1/90 backdrop-blur` bar. At narrower widths the command bar `flex-wrap`s to 2-3 rows (~110px) and swallows the 'Broker state UNKNOWN' warning (line 165) and part of the FLATTEN button too. The trader fires the most destructive control in the app without seeing what it is about to close.
- **Fix:** Add `scroll-mt-16` (or inline `style={{scrollMarginTop: '56px'}}`) to the `<div id="kill-switch">` at LiveCockpit.jsx:133.

### C:/Users/haroo/OneDrive/Documents/New project/Emergent-AlphaForge/frontend/src/components/live/cockpit/ConfigDrawer.jsx:46

- **STATUS:** FIXED — shrink-0 on sections + flex-1/minHeight:0 on body — measured 231/653 clipped before, 1215>507 scrollable after (3007f7d).
- **Defect:** The closed drawer is only translated off-screen (`translate-x-full`) — unlike the overlay at line 41 it gets no `pointer-events-none`, `inert` or `aria-hidden`, so every destructive control inside it stays mounted, focusable and Enter-activatable while completely invisible.
- **Failure:** With the drawer closed the trader tabs forward from the Configure button. Focus leaves the visible page and lands, invisibly, on the drawer's Close button, then LiveDeploymentStrip's 'Stop ALL live' (LiveDeploymentStrip.jsx:327), each row's Disable/Stop, GttBook's per-row 'Cancel' (GttBook.jsx:206) and OverallSettingsPanel's Save. There is no focus ring anywhere on screen, so the trader keeps pressing Tab/Enter — an Enter on 'Stop ALL live' pops a confirm whose text they have no visual context for, and confirming squares every open paper trade, pauses every deployment and disables every live deployment. Screen readers also announce the whole drawer as page content.
- **Fix:** On the `<aside>` add `inert={!open}` (or `aria-hidden={!open}` plus `pointer-events-none` and `visibility:hidden` after the transition) so the off-screen drawer is removed from the tab order and the a11y tree.

### frontend/src/components/live/BrokerConnect.jsx:57

- **STATUS:** FIXED — Open state lifted to a single openChip — only one popover at a time (58c158d).
- **Defect:** The chip button calls `e.stopPropagation()`, which prevents the *other* chip's document-level click listener from firing, so both broker popovers can be open and overlapping at the same time.
- **Failure:** Trader clicks the Upstox chip (popover A opens), then clicks the Flattrade chip. React's `stopPropagation` halts the native event before it reaches `document`, so A's `onDoc` handler (line 27) never runs and A stays open. Both popovers are `absolute right-0 w-56 z-30` anchored to chips narrower than 224px, so they overlap; the Flattrade popover paints over the right side of the Upstox one, leaving a sliver of Upstox's action row exposed. The trader clicks what looks like the visible popover's button and hits Upstox's Disconnect — or, worse, aims at Upstox's and hits Flattrade's Disconnect, killing the *execution* broker session while live positions are open.
- **Fix:** Lift `open` to a single `openChip` state in `BrokerConnect` (as `OverallSettingsPanel` already does with `openChip`/`toggleChip`), or drop `stopPropagation` and instead ignore the click when `ref.current.contains(e.target)` in the document handler, so opening one chip always closes the other.

### frontend/src/components/live/GttBook.jsx:206

- **STATUS:** OPEN — DEFERRED ITEM 2 — unconfirmed one-click cancel of the PC-down backstop.
- **Defect:** The Cancel button removes a resting broker OCO/GTT — the only PC-down protection on a real position — on a single click with no confirmation, and it is styled `border-line bg-bg-2 text-dim`, identical to the benign Refresh button in the same panel.
- **Failure:** In the config drawer's "GTT / OCO backstop" section, the panel header Refresh button (line 261-274) and each row's Cancel button (line 206-219) share the same neutral grey treatment and sit a few dozen pixels apart. A trader intending to refresh the book clicks Cancel on a row; `handleCancel` fires `api.cancelGtt` immediately (line 98-103), the row vanishes on the next poll with no warning, and the NRML position it protected is left with software-guard-only cover — i.e. nothing if the PC dies. Nothing on the page then says that position lost its backstop (AlertRail's no-backstop banner is driven by blotter `oco_error`, not by a manual cancel).
- **Fix:** Give Cancel danger styling distinct from Refresh and require an inline confirm ("Cancel the PC-down backstop for <tsym>? The position will have no broker-side protection.") before calling the API.

### frontend/src/components/live/GttBook.jsx:206

- **STATUS:** UNVERIFIED
- **Defect:** Cancelling a resting OCO/GTT — the only PC-down protection for a live NRML position — is a single unconfirmed click on a button styled identically to the benign "Refresh" button directly above it.
- **Failure:** Trader opens the GTT/OCO section in the drawer to check the backstop is in place. The row's "Cancel" (line 206-219, `border-line bg-bg-2 text-dim`) is the same neutral grey as the "Refresh" control at line 261-274 and carries no danger styling. One misdirected click calls `api.cancelGtt` immediately — no dialog, no "this removes the only PC-down net for NIFTY24000CE" warning. The row vanishes on the next 6s poll, there is no undo, and nothing on the page then tells the trader that position is now software-guard-only (the AlertRail no-backstop banner only fires on `oco_error` from placement, not on a manual cancel).
- **Fix:** Require a two-step confirm on GttRow cancel that names the symbol and states the consequence ("removes the PC-down backstop for <tsym>"), and style it with danger tokens so it is visually distinct from Refresh.

### frontend/src/components/live/GuardPanel.jsx:77

- **STATUS:** OPEN — DEFERRED ITEM 3 — rehydrated positions carry default stops with no badge.
- **Defect:** `GuardRow` never reads the guard payload's `source` field (nor the payload's `rehydrated_count`), so a position re-attached after a restart with a DEEP-DEFAULT catastrophe stop renders identically to one carrying the trader's original stop/target.
- **Failure:** The backend emits these fields specifically for this UI: live_broker.py:1466-1471 ("source == 'rehydrated' ⇒ re-attached after a restart with a DEEP-DEFAULT catastrophe stop (the original per-position levels were lost); the UI can flag 'levels reset to default — re-set your stop/target'") and live_position_guard.py:1213-1215. Grep shows no frontend file reads `rehydrated` or `source` from guard status. Scenario: trader has a live NIFTY CE with a 20% stop; the backend restarts mid-session; the guard rehydrates the position with the ~50% default band. GuardPanel shows a normal row with a Stop level, a "Filled" chip and "1 guarded", and RiskKpis shows Guard=ARMED. The trader believes their tight stop is still live and walks away; the position runs to the default deep stop instead.
- **Fix:** Render a loud per-row badge when `pos.source === "rehydrated"` (e.g. amber "LEVELS RESET TO DEFAULT — re-set your stop/target") and a header-level count from `status.rehydrated_count`; consider also surfacing it in AlertRail.

### frontend/src/components/live/LiveCockpit.jsx:57

- **STATUS:** UNVERIFIED
- **Defect:** The cockpit consumes only `armState.mode` and hands it to a component that ignores it, so the retired ExecutionStateStrip's LIVE/DRY_RUN verdict, the manual LIVE_TEST latch indicator, the "exit gate gap" warning and the Stand-down control are absent from the page entirely.
- **Failure:** Quick Trade arms LIVE_TEST (LiveOrderTicket.jsx:336) before every manual place and reverts on the failure path with a best-effort call whose catch is empty (LiveOrderTicket.jsx:359-362). If that revert request fails (network drop, backend restart), the account is left in LIVE_TEST with a live single-shot. The code comment at line 361 relies on "the hero Mode tile will still reflect reality on the next poll" — that tile no longer exists on this page. `mode` is passed to LiveOrderTicket at line 18 of QuickTrade.jsx and never read anywhere in that file, and ExecutionStateStrip.jsx is now orphaned (imported by nothing). The trader has no way to see they are armed and no Stand-down button to clear it. Separately, `armState.warning` / `armState.exit_gap` ("Real entries armed but guard auto-squares are dry-run") is now rendered nowhere.
- **Fix:** Mount ExecutionStateStrip in the cockpit (or an equivalent mode/verdict tile) wired to `armState` with the Stand-down action, and surface `armState.warning`/`exit_gap` in AlertRail alongside the other safety banners.

### frontend/src/components/live/LiveCockpit.jsx:122

- **STATUS:** FIXED — Duplicate MarketHeader removed; verified streamToggles 2 -> 1 (58c158d).
- **Defect:** `<MarketHeader />` is rendered a second time inside the cockpit even though `Layout.jsx:105` already renders one for every page, producing two independent copies of a stateful, mutating control (the Upstox Stream Start/Stop toggle) plus a duplicated SSE connection.
- **Failure:** On /live-trading the trader sees the ticker twice. Each MarketHeader opens its own `EventSource(/market/header/stream)` and its own 5s `upstoxStreamStatus` poll with private `streamStatus` state (MarketHeader.jsx:104,132). The trader clicks "Stop" on the in-page copy to stop the tick feed; that instance updates immediately, but the Layout copy still reads `running` and shows "Stop" for up to 5s — the trader clicks it, which calls `startUpstoxStream` and restarts the feed they just killed (or vice-versa: they read the stale copy as "live ticks" while the stream is actually down and assume the deployments are being fed). If SSE errors, both instances fall back to a 1s `marketHeader()` poll (line 88), doubling load to 2 req/s.
- **Fix:** Delete the `<MarketHeader />` at LiveCockpit.jsx:122 — the app shell already provides it. If the cockpit needs it below the CommandBar, remove it from Layout for this route instead, so exactly one instance owns the stream toggle.

### frontend/src/components/live/LiveDataProvider.jsx:105

- **STATUS:** FIXED — refetchSlow/refetchAll now return Promise.all (58c158d).
- **Defect:** `refetchAll` (and `refetchSlow`) return `undefined` rather than a promise, so `await refreshAll()` in LiveDeploymentStrip resolves instantly and `busy` is cleared before any refreshed data arrives.
- **Failure:** Trader clicks Stop on a live deployment, confirms the `window.confirm`. `api.liveStop` resolves, the toast fires, then `await refreshAll()` (LiveDeploymentStrip.jsx:216) returns immediately because `refetchAll` is a void arrow — `setBusy(false)` runs while the `/deployments` re-read is still in flight. The row is re-enabled and still renders `mode === "live"` with an armed Stop/Disable button for another network round-trip. Believing the click didn't register, the trader clicks Stop again and fires a second square-off/stop call against the same deployment. The same window exists for Disable and for "Stop ALL live" (line 242).
- **Fix:** Make `refetchSlow`/`refetchAll` return `Promise.all([...])` of the individual `refetch()` promises (each `usePoll.refetch` is already async), so awaiting callers actually gate on completion.

### frontend/src/components/live/LiveOrderTicket.jsx:353

- **STATUS:** OPEN — DEFERRED ITEM 1 — transmission-unconfirmed on a timed-out place.
- **Defect:** A transport/timeout failure of `approveOrder` (which may occur AFTER the order reached the broker) is reported as a flat "Place failed" with no "order may have been transmitted" warning, and `previewResult` is left intact so the red "Place order — REAL MONEY" button re-enables immediately.
- **Failure:** Trader clicks Confirm — Place Order; the backend transmits to Flattrade but the HTTP response is lost (timeout/proxy drop). `catch` at line 352-354 sets `queueError = "Place failed"`; `placedOk` stays false so `setPreviewResult(null)` at line 351 is skipped and the finally block reverts to LIVE_OFFLINE. The UI now reads as an unambiguous non-placement with the Place button live again. The trader clicks it again and buys a second lot on top of a position that is already open at the broker. Contrast KillSwitchPanel, which has an explicit `PLACED_UNCONFIRMED` / "UNFILLED · WORKING" outcome for exactly this case.
- **Fix:** On a thrown place error, render a distinct amber "TRANSMISSION UNCONFIRMED — the order may already be live; check the Order book before retrying" state, clear/disable the Place button until the order book is refetched, and force a `refetch.all()`.

### frontend/src/components/live/LiveOrderTicket.jsx:351

- **STATUS:** FIXED — onPlaced -> refetch.all wired via QuickTrade (58c158d).
- **Defect:** A successful real-money place from the Quick Trade ticket never triggers a refetch of the shared broker slices — the ticket has no access to `useLiveData` and `QuickTrade` passes it no post-place callback.
- **Failure:** Trader places a real order from Quick Trade and it fills. Positions/orders are on the 15s slow poll, so for up to 15 seconds the cockpit still shows "No open positions", `RiskKpis` shows Open Pos 0 and Day P&L unchanged, and — most dangerously — `KillSwitchPanel` shows "0 open · 0 working" and its confirm text reads "this cancels 0 working order(s) and flattens 0 position(s)", exactly when the trader is most likely to reach for the kill switch. Every other mutating surface refreshes explicitly (KillSwitchPanel.jsx:143, LiveDeploymentStrip.jsx:199) — the manual ticket is the one that doesn't.
- **Fix:** Add an `onPlaced` prop invoked after `setPlaceResult(placed)` and wire `QuickTrade` to call `refetch.all` (or at least `refetch.slow`) from `useLiveData`.

### frontend/src/components/live/OverallSettingsPanel.jsx:377

- **STATUS:** OPEN — DEFERRED ITEM 4 — failed GET silently yields an all-disabled config that Save writes back.
- **Defect:** A failed GET of the overall controls silently substitutes a fully-disabled config (SL off, Target off, Trail off) that is visually identical to a genuinely-off config, and Save then writes that all-disabled config back to the server.
- **Failure:** Trader opens the Config drawer during a transient backend blip while a live basket is open. The catch at line 377 sets `defaultConfig()` into both `config` and `loaded`, so the chips read "SL off · Target off · Trail off" — the only difference from reality is a 10px amber "defaults" chip at line 519. The trader, believing nothing is configured, toggles Target on and clicks Save (line 452, always enabled). `putOverallSettings` posts `sl.enabled:false`, wiping the previously-saved basket stop-loss while real positions are open. Nothing warns that the payload being saved was never loaded from the server.
- **Fix:** On load failure, do not present an editable form: render an explicit "Could not read the saved overall controls — values below are NOT your live config" error state and disable Save until a successful GET (or an explicit "start from scratch" acknowledgement) has occurred.

### frontend/src/components/live/cockpit/BrokerConnect.jsx:75

- **STATUS:** FIXED — Two-step confirm naming open positions + blast radius (3007f7d).
- **Defect:** "Disconnect" severs the Flattrade execution session in one click with no confirmation and no check for open positions, and it sits in a two-button flex row immediately beside the benign "Reconnect" at identical size.
- **Failure:** Trader opens the Flattrade chip popover intending Reconnect (line 74) and hits the adjacent Disconnect (line 75); `doDisconnect` calls `api.disconnectFlattrade()` immediately. With a live position open, the software guard can no longer reach the broker to transmit stop/target/EOD squares, and the kill switch itself degrades to KillSwitchPanel's "Broker read FAILED — positions UNKNOWN" path. The only feedback is the chip dot turning red; no banner says "you just disarmed every automated exit on N open positions".
- **Fix:** Require a confirm step for Disconnect that names the open-position count (from the shared context) and states that auto-exits and the kill switch stop working, and visually separate it from Reconnect.

### frontend/src/components/live/cockpit/BrokerConnect.jsx:61

- **STATUS:** FIXED — State glyph + sr-only label + aria-expanded/haspopup (58c158d).
- **Defect:** The broker chip conveys connected / token-expired / disconnected through a 6px coloured dot alone — the dot has no text or `aria-label`, and `stateLabel` appears only in the button's `title` tooltip and inside the popover, while the always-visible text shows the static `purpose` ("data"/"exec") instead.
- **Failure:** A red-green colourblind trader (or any trader glancing at the command bar) sees two chips reading `● Upstox data` and `● Flattrade exec` and cannot distinguish `bg-success` (connected) from `bg-danger` (disconnected) at 6px — both read as a grey dot. They place a Quick Trade order believing execution is connected, and only discover the daily Flattrade session died when the order fails. A screen-reader user gets no state at all: the accessible name computed from the button is "Upstox data", the dot contributes nothing, and `title` is unreliably announced. The `hint` from `tokenHint()` (line 17) is frequently empty, so it does not compensate.
- **Fix:** Render `stateLabel` as visible text in the chip (it is already computed at line 37) — e.g. `Flattrade · exec · connected` — or at minimum add `<span className="sr-only">{stateLabel}</span>` next to the dot plus a shape/glyph difference (✓ / ! / ×) so state is not carried by hue alone.

### frontend/src/components/live/cockpit/BrokerConnect.jsx:75

- **STATUS:** UNVERIFIED
- **Defect:** The Flattrade "Disconnect" button deletes the execution-broker token in one click, with no confirmation and no awareness of open positions, and sits as an equal-sized sibling immediately beside the benign "Reconnect".
- **Failure:** Trader has two live option positions and a flaky session, opens the Flattrade chip popover to hit Reconnect, and clicks the adjacent 50%-width Disconnect by mistake. `api.disconnectFlattrade()` fires immediately — no dialog, no "you have 2 open positions" warning. The software guard can no longer transmit stop/target exits, and the kill switch now returns `connected:false` and renders "Broker read FAILED — positions UNKNOWN" (KillSwitchPanel.jsx:236-239). The trader is unprotected and cannot flatten until they complete a full OAuth round-trip.
- **Fix:** Gate Disconnect behind a confirm that names the blast radius and the current open-position/working-order counts (available from useLiveData), e.g. a typed-DISCONNECT step when positions are non-zero. At minimum move it out of button-adjacency with Reconnect and require a second click on an explicit "Confirm disconnect" affordance.

### frontend/src/components/live/cockpit/CommandBar.jsx:32

- **STATUS:** FIXED — Theme tokens (success/info) instead of dark-only emerald/sky (58c158d).
- **Defect:** The market-status pill and the Configure button hardcode Tailwind dark-only palette values (`text-emerald-300` on `bg-emerald-500/10`, `text-sky-300` on `bg-sky-500/10`) instead of the theme tokens, so in the app's light theme (`[data-theme="light"]`, index.css:69, reachable via the Black/White/System toggle in Layout.jsx:131) they render at 1.39:1 and 1.51:1 contrast.
- **Failure:** A trader on the White theme (or on System with a light-mode OS) loads /live-trading during market hours. The `MARKET OPEN · 15:30 close` pill is pale mint text on a near-white mint wash — measured 1.39:1 against WCAG's 4.5:1 minimum, i.e. legible only as a smudge — so the single most important at-a-glance state on the page (is the market open?) is unreadable, and its only fallback signal is a 6px emerald dot at line 37 which is equally washed out. The Configure button at line 55 (`text-sky-300` on `bg-sky-500/10`, 1.51:1) is likewise near-invisible, so the trader cannot find the entry point to the deployment and backstop controls. Same defect in the same page: AlertRail.jsx:29 (the Flattrade login-success banner, `text-emerald-300`) and liveHelpers.js:229 (the `Reconciled ✓` chip on the Open Positions card — the trader cannot tell whether the broker book reconciled).
- **Fix:** Replace the hardcoded `-300`/`-500/10` pairs with the theme-aware tokens that already flip per theme: `text-success` + `bg-success/10 border-success/40` (index.css:105 gives `--color-success: #087A45` in light, which is AA on a light wash). Apply the same substitution at CommandBar.jsx:37 (`bg-emerald-400` dot → `bg-success`), CommandBar.jsx:55 (`text-sky-300` → `text-info`), AlertRail.jsx:29, and liveHelpers.js:229.

### frontend/src/components/live/cockpit/ConfigDrawer.jsx:46

- **STATUS:** UNVERIFIED
- **Defect:** The closed drawer stays mounted and fully interactive — it is only pushed off-screen with `translate-x-full`, with no `inert`, `hidden`, `pointer-events-none`, `visibility:hidden`, or conditional render — so every control inside it remains in the tab order and in the accessibility tree.
- **Failure:** A trader tabs through the cockpit (Configure → market pill → broker chips → KPIs → positions → kill switch → Quick Trade → account tabs) and, after the last visible element, focus silently disappears off-screen into the closed drawer for ~15+ stops: the drawer Close button, LiveDeploymentStrip's collapse toggle and its `Stop ALL live` button (LiveDeploymentStrip.jsx:327), each per-deployment enable/disable/stop control, GttBook's cancel buttons, and OverallSettingsPanel's SL/target/trailing inputs and switches. Nothing is visibly highlighted, so the trader keeps pressing Enter/Space to "activate what I'm on" and gets a `window.confirm("Stop ALL trading?")` popping out of nowhere (LiveDeploymentStrip.jsx:225) — or, on the OverallSettingsPanel controls which have no confirm gate, silently changes the basket stop-loss/trailing configuration while real positions are open. A screen-reader user reading the page linearly hears the whole deployment/GTT/overall-settings config announced as if it were on screen.
- **Fix:** Gate the whole subtree on `open`: add `inert={!open}` (or `{...(!open && {inert: ''})}` for older React) plus `aria-hidden={!open}` and `pointer-events-none` to the `<aside>` when closed. Best is to not render the children at all when closed (`{open && <>…</>}`) — that also stops LiveDeploymentStrip/GttBook polling while the drawer is shut.

### frontend/src/components/live/cockpit/ConfigDrawer.jsx:45

- **STATUS:** FIXED — role=dialog + aria-modal + focus in/restore, rAF-deferred (58c158d, 91fa367).
- **Defect:** The slide-over is a plain `<aside aria-label>` with no `role="dialog"`/`aria-modal="true"`, no focus moved into it on open, no focus trap, and no focus restored to the Configure button on close.
- **Failure:** The trader presses Enter on ⚙ Configure (CommandBar.jsx:52). The drawer slides in visually but keyboard focus stays on the Configure button behind the black/50 scrim, so reaching the deployment Stop control requires tabbing forward through the entire cockpit — every KPI card, blotter, kill switch, order ticket field and account tab — before entering the drawer. A screen-reader user gets no "dialog" announcement at all and keeps reading the page underneath, unaware anything opened. Then, pressing Escape (ConfigDrawer.jsx:32) closes the drawer and leaves focus on whatever element it had wandered to inside the now-hidden panel, so the next Tab restarts from an unpredictable point instead of returning to the Configure button.
- **Fix:** Add `role="dialog" aria-modal="true"` to the `<aside>`; on `open` transitioning true, store `document.activeElement`, then focus the drawer container (`tabIndex={-1}`) or the Close button; trap Tab/Shift+Tab within the aside while open; on close, restore focus to the saved element. LiveCockpit.jsx:146 should pass the trigger ref down, or the drawer can capture the previously-focused element itself.

### frontend/src/components/live/cockpit/RiskKpis.jsx:21

- **STATUS:** FIXED — Now `guard?.armed !== false`, matching GuardPanel (3007f7d).
- **Defect:** The Guard KPI uses `!!guard?.armed` while GuardPanel on the same screen uses `status?.armed !== false`, so the two can contradict each other, and the KPI labels the not-armed state "DRY-RUN" — a benign-sounding word for what GuardPanel correctly calls "Guard unreachable".
- **Failure:** Two distinct failures. (a) A guard payload that omits `armed` (partial/older backend response) makes the KPI render "DRY-RUN" in warn tone while GuardPanel two cards below renders "Auto-exit live" in danger tone — the trader has to pick which of two on-screen safety readouts to believe. GuardPanel.jsx:164-168 documents that defaulting to not-armed is "the fail-DANGEROUS direction", and the KPI does exactly that. (b) When the server genuinely reports `armed:false` (the guard cannot reach the broker and auto-exits are NOT transmitting — GuardPanel.jsx:267), the KPI says "DRY-RUN", which a trader reads as "harmless, logs only" rather than "your stops are not firing".
- **Fix:** Use the same derivation as GuardPanel (`guard?.armed !== false`) and relabel the false state to match its real meaning ("UNREACHABLE" / "NOT TRANSMITTING") in danger tone, not "DRY-RUN".


## MEDIUM

### C:/Users/haroo/OneDrive/Documents/New project/Emergent-AlphaForge/frontend/src/components/live/KillSwitchPanel.jsx:66

- **STATUS:** UNVERIFIED
- **Defect:** The post-kill LegReport table is the only blotter in this file family NOT wrapped in an `overflow-x-auto` div, and its container has `overflow-hidden` (line 148), so when its min-content width exceeds the narrow right column the per-leg outcome and broker reason are clipped with no way to scroll to them.
- **Failure:** Trader fires the kill switch on a BANKNIFTY position at a 1024-1280px window. The report table's min-content is driven by the `whitespace-nowrap` leg cell (line 78) — e.g. `BANKNIFTY31JUL2555000CE (1/3)` ≈ 29 glyphs at 11px mono ≈ 190px — plus Qty, the Outcome chip and the Reason column, totalling ~330px+, while the panel's usable width in the right column is ~247px at 1024 / ~345px at 1280. `table-layout: auto` renders the table at min-content, it overflows the panel, and `overflow-hidden` on the panel div clips it dead — no horizontal scrollbar appears. The rightmost 'Reason / detail' column, which carries the broker's rejection string for a leg that did NOT flatten, is unreadable exactly when the trader is deciding whether money is still exposed.
- **Fix:** Wrap the `<table>` at KillSwitchPanel.jsx:66-90 in `<div className="overflow-x-auto">`, matching PositionsBlotter/OrdersBlotter in liveHelpers.js:123 and 182.

### C:/Users/haroo/OneDrive/Documents/New project/Emergent-AlphaForge/frontend/src/components/live/LiveOrderTicket.jsx:402

- **STATUS:** UNVERIFIED
- **Defect:** The order-ticket form grid switches to `sm:grid-cols-3` on a 640px VIEWPORT breakpoint, but in the cockpit it lives in a ~347px-wide right-hand column, producing ~108px cells that non-shrinkable controls (notably `input[type="date"]`) overflow — and SectionCard's `overflow-hidden` (liveHelpers.js:95) clips the spill rather than scrolling it.
- **Failure:** Trader at 1280px width opens Quick Trade to place a manual hedge. QuickTrade's SectionCard gives LiveOrderTicket ~347px; `sm:grid-cols-3` with `gap-3` yields (347−24)/3 ≈ 108px per field. The 'Expiry (YYYY-MM-DD)' `<input type="date">` (line 540) has a Chrome intrinsic minimum of ~125-135px (dd/mm/yyyy segments + calendar icon) and `width:auto` will not shrink below it, so it overruns its cell and overlaps the Order Type select beside it; in the rightmost column the overrun is clipped by SectionCard. The 'Strike' input is squeezed to ~58px next to its shrink-0 ATM button, and the Side helper text wraps to 4-5 lines. The trader can't read or reliably click the expiry field on the real-money ticket.
- **Fix:** Make the ticket container-aware rather than viewport-aware: use `grid-cols-1 min-[420px]:grid-cols-2 min-[720px]:grid-cols-3` via a container query / ResizeObserver, or simply drop to `grid-cols-2` when rendered inside the cockpit column, and add `min-w-0 w-full` to the date/select inputs.

### C:/Users/haroo/OneDrive/Documents/New project/Emergent-AlphaForge/frontend/src/components/live/MetricCard.jsx:62

- **STATUS:** UNVERIFIED
- **Defect:** MetricCard's value line has no `truncate`/`min-w-0` (only the label at line 56 does) while RiskKpis packs three of them into a `grid-cols-3` inside the cockpit's narrow right column, so ₹ values overflow the card box and paint over the neighbouring tile at common laptop widths.
- **Failure:** Trader runs the cockpit at 1280x800 (or 1366). Right column = (1280 − 260 sidebar − 32 page padding − 16 grid gap) / 2.55 ≈ 381px; RiskKpis (RiskKpis.jsx:24) splits that into three 122px cards, leaving ~96px of content after `px-3`. 'Avail Margin' rendering `₹12,34,567` or 'Day P&L' rendering `−₹1,23,456` is 10 monospace glyphs at `sm:text-xl` (20px, ~12px/glyph) ≈ 120px — and the string has no spaces so it cannot wrap. The number spills ~24px past the card border and overprints the adjacent 'Working Ord' / 'Open Pos' tile, so the trader reads a garbled available-margin figure right before sizing an order. Only above ~1450px viewport width do the numbers fit.
- **Fix:** Add `truncate` (or `overflow-hidden text-ellipsis whitespace-nowrap`) to the value div at MetricCard.jsx:62-68 with a `title={String(display)}`, and/or drop RiskKpis to `grid-cols-2` and change the outer track at LiveCockpit.jsx:119 to `lg:grid-cols-[minmax(0,1.55fr)_minmax(0,1fr)]`.

### frontend/src/components/live/BrokerConnect.jsx:116

- **STATUS:** UNVERIFIED
- **Defect:** After a successful Upstox disconnect, `onChanged` only refetches the LiveDataProvider slices — the Upstox chip's status lives in this component's private 15s poll and is never re-read.
- **Failure:** Trader opens the Upstox chip and clicks Disconnect. The POST succeeds and the popover closes, but the local `upstox` state (set only by the interval at lines 103-109) still holds `connected: true`, so the chip keeps its green dot and "connected" label for up to 15 seconds, and re-opening it still offers Reconnect/Disconnect as if the session were live. The trader concludes the disconnect failed and clicks again, or walks away believing the data feed is still connected. (The Flattrade chip is unaffected — its status comes from the provider, which `onChanged` does refetch.)
- **Fix:** Expose the `load()` function (e.g. via `useCallback` + a `reload` ref) and call it in the Upstox `onDisconnect` handler after the POST resolves, in addition to `onChanged?.()`.

### frontend/src/components/live/LiveCockpit.jsx:47

- **STATUS:** FIXED — authMsg auto-dismisses and clears when the session drops (3007f7d).
- **Defect:** `authMsg` is set once from the OAuth redirect query param and is never cleared, so the green "Flattrade login successful — connected." banner stays pinned in the alert rail for the whole page session regardless of the broker's actual state.
- **Failure:** Trader returns from the Flattrade OAuth redirect at 09:10 and leaves the tab open. Later the token is revoked/expires or they click Disconnect. The BrokerConnect chip turns red but AlertRail (line 28-32) still renders the emerald "Flattrade login successful — connected." banner at the top of the safety rail — the most prominent connection statement on the page now contradicts reality, and a trader glancing at the rail concludes execution is still connected.
- **Fix:** Auto-dismiss `authMsg` after a few seconds, clear it whenever `status.connected` goes false/expired, or give it an explicit dismiss control.

### frontend/src/components/live/LiveCockpit.jsx:128

- **STATUS:** UNVERIFIED
- **Defect:** PositionMonitor (the live-session card carrying the per-position Square button) was dropped from the page, leaving the account-wide "FLATTEN EVERYTHING" kill switch as the only manual exit control on the cockpit.
- **Failure:** Trader places a manual Quick Trade entry, it fills, and they want out of that single leg while a deployment holds other positions. The right column offers PositionsBlotter (read-only, liveHelpers.js:106), GuardPanel (explicitly read-only, GuardPanel.jsx:19) and KillSwitchPanel. `api.squareLivePosition` (lib/api.js:305) is referenced by nothing the cockpit renders, and PositionMonitor.jsx is orphaned. The only way out is the kill switch, which also cancels every working order and flattens every deployment-held position. The provider still polls `getLiveTestSession` every 3s (LiveDataProvider.jsx:60) for a consumer that no longer exists.
- **Fix:** Re-mount PositionMonitor (or add a per-row square action to PositionsBlotter wired to `api.squareLivePosition`) so exiting one position does not require flattening the whole book.

### frontend/src/components/live/LiveCockpit.jsx:146

- **STATUS:** UNVERIFIED
- **Defect:** `onArmedSummaryChange={() => {}}` discards the `{armedCount, autoplaceArmed}` summary the deployment strip lifts, so the backend-dry-run state (LIVE_AUTOPLACE_ARMED unset — deployment entries do NOT transmit) is never displayed anywhere on the cockpit.
- **Failure:** Trader enables live execution on a deployment. `enableDeploymentLive` returns `autoplace_armed:false`, and DeployToLivePanel.jsx:202/232 renders the "Backend dry-run only — set LIVE_AUTOPLACE_ARMED=1 to transmit real orders" warning inside the NotLiveRow. `onArmed?.()` (line 207) triggers a roster refetch; the deployment flips to `mode:"live"`, moves to liveDeps, and the NotLiveRow — with the warning and its state — unmounts within one poll. LiveDeploymentStrip.jsx:272-285 lifts `autoplaceArmed` for exactly this purpose and LiveCockpit throws it away; LiveBanner.jsx:69-74 (the only renderer of that flag) is orphaned. DeploymentSummary then shows a red "Live" pill, so the trader believes signals are transmitting real orders while nothing is placed all day.
- **Fix:** Hold the armed summary in LiveCockpit state and render a persistent banner in AlertRail when `autoplaceArmed === false` and `armedCount > 0`.

### frontend/src/components/live/LiveCockpit.jsx:62

- **STATUS:** UNVERIFIED
- **Defect:** Both protection banners derive from `?? []` fallbacks, so "the reconcile/blotter data has not arrived" renders pixel-identical to "no unguarded positions and no missing backstops" — an empty alert rail always reads as all-clear.
- **Failure:** On mount, `reconcile` and `blotter` are null and their errors are also null (usePoll starts both at null), so `unguardedPositions` and `noBackstopPositions` are empty, no banner renders, and `health.degraded` is false because nothing has errored yet. ReconcileChip additionally returns null for a null payload (liveHelpers.js:226), so the Open Positions header shows no badge rather than an "unknown" badge. A trader landing on the page sees a clean rail and a clean positions header and concludes every position is guarded and backstopped, before the app has established either fact.
- **Fix:** Render an explicit "protection status unknown — reconcile/blotter not yet read" state (and an amber ReconcileChip) whenever `reconcile == null` or `blotter == null`, rather than falling back to an empty array.

### frontend/src/components/live/LiveCockpit.jsx:64

- **STATUS:** UNVERIFIED
- **Defect:** The OAuth post-redirect effect handles only `flattrade_connected`/`flattrade_error`, but the same command bar starts an Upstox OAuth whose callback returns with `upstox_connected` / `upstox_error` — never read or cleared on this page.
- **Failure:** Trader clicks "Login to Upstox" in the cockpit command bar (BrokerConnect.jsx:115). The backend callback redirects to `FRONTEND_POST_AUTH_URL` (broker.py:248/251), which in this deployment is the Flattrade default `.../live-trading` (live_broker.py:170). The trader lands back on the cockpit with `?upstox_connected=1` or `?upstox_error=<reason>` in the URL: no success banner, no error banner (only DataWarehouse.jsx:111-115 handles these params), no refetch, and the query string is left in the address bar because the `replaceState` cleanup at line 71 only fires for the flattrade params. A failed Upstox login is completely invisible — the chip just still reads disconnected with no reason.
- **Fix:** Extend the effect to also match `upstox_connected` / `upstox_error`, set `authMsg` accordingly, refetch, and include them in the `replaceState` cleanup condition.

### frontend/src/components/live/LiveCockpit.jsx:53

- **STATUS:** UNVERIFIED
- **Defect:** `handleStandDown` swallows the `setLiveMode("LIVE_OFFLINE")` failure in an empty catch with no toast or error state, so a failed stand-down is indistinguishable from a slow one.
- **Failure:** The manual ticket is in LIVE_TEST (armed single-shot). The trader clicks "Stand down" in the ExecutionStateStrip. The call 400s (session expired, backend restart, network). The spinner stops, no toast appears, and because arm-state is on the 15s poll the strip continues to read LIVE_TEST (armed). The trader reads that as poll lag, walks away, and the manual path stays armed. The comment claims the failure is "surfaced by the unchanged strip state on the next poll" — but an unchanged strip is exactly what a *pending* refresh looks like, so nothing distinguishes failure from success.
- **Fix:** `catch (e) { toast.error(getApiErrorMessage(e, "Stand down failed")); }` — the page already imports sonner elsewhere and the strip is the one control whose failure must be loud.

### frontend/src/components/live/LiveDataProvider.jsx:60

- **STATUS:** UNVERIFIED
- **Defect:** The provider polls 16 endpoints but never `/live-broker/recovery-status`, and `api.js` has no method for it, so the cockpit cannot show the warning the backend built for boot-before-OAuth.
- **Failure:** live_broker.py:1481-1488 states the endpoint exists so "the UI can show a red strip while a live position exists but recovery hasn't succeeded (boot-before-OAuth)" — `succeeded` flips true only once resume_pending + guard rehydrate + reboot reconcile + auto-square re-arm all complete for the current token. Scenario: the PC reboots overnight with a live position; the backend starts before the daily Flattrade OAuth, so recovery never ran. The trader opens the cockpit, logs in, sees positions listed and GuardPanel reporting "0 guarded / No positions under software guard" (an empty state that reads as benign), and no strip tells them the overnight position is unrecovered and unwatched.
- **Fix:** Add `api.liveRecoveryStatus()`, poll it on the fast cadence, and render a danger strip in AlertRail when a live position exists and `succeeded` is false.

### frontend/src/components/live/LiveOrderTicket.jsx:352

- **STATUS:** UNVERIFIED
- **Defect:** A transport-level failure of the approve/place call is reported as a flat "Place failed", collapsing the genuinely-unknown "may already be working at the broker" state into a definitive not-placed verdict.
- **Failure:** Trader confirms a real-money order. `api.approveOrder` (line 348) reaches the backend, the executor transmits to Flattrade, and the HTTP response is lost to a timeout. The catch at 352-354 sets `queueError = "Place failed"`, the finally block closes the confirm dialog and stands the mode down, and the only thing on screen is a small red "Place failed" line. The trader believes nothing was transmitted and re-runs Preview → Place, doubling the position. The codebase already models this state correctly elsewhere — KillSwitchPanel.jsx:44/54 renders `PLACED_UNCONFIRMED` as "UNFILLED · WORKING" — but the ticket does not.
- **Fix:** On a transport/timeout error (as opposed to a server verdict), render an amber UNKNOWN state that says the order may be live, and prompt the trader to check the Order book / positions before retrying; keep the Place button disabled until a fresh read confirms.

### frontend/src/components/live/cockpit/AccountTabs.jsx:130

- **STATUS:** FIXED — Loading-vs-unavailable states added for Funds/Holdings/Orders (3007f7d).
- **Defect:** The Funds/Holdings/Orders/Trades control uses `role="tab"` + `aria-selected` on the buttons but the wrapper is a plain `<div>` with no `role="tablist"`, the panel at line 147 has no `role="tabpanel"`, and there is no `id`/`aria-controls`/`aria-labelledby` linkage or arrow-key navigation.
- **Failure:** A screen-reader user reaches the account panel. Because `role="tab"` appears outside any `tablist`, the ARIA mapping is invalid: NVDA/JAWS announce four isolated "tab" controls with no "1 of 4" position and no relationship to the content region, and the panel below is announced as unrelated body text. Activating "Holdings" swaps the panel content with no programmatic association, so the user has no way to know which tab produced the DP/demat table they are now reading, and cannot use the standard Left/Right arrow keys to move between tabs (only Tab works, which contradicts the `tab` role they were just told about).
- **Fix:** Add `role="tablist"` to the wrapper div, give each button `id={`tab-${t.k}`}` + `aria-controls={`panel-${t.k}`}` + `tabIndex={tab === t.k ? 0 : -1}` with Left/Right/Home/End key handling, and wrap the line-147 content div in `role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`} tabIndex={0}`. The repo already has an accessible Radix implementation at frontend/src/components/ui/tabs.jsx that does all of this.

### frontend/src/components/live/cockpit/AlertRail.jsx:16

- **STATUS:** UNVERIFIED
- **Defect:** The safety banner rail renders alerts into a plain `<div className="space-y-3">` with no `role="alert"`, `aria-live`, or `role="status"` on any banner, so banners that appear during a polling cycle are never announced.
- **Failure:** The trader is filling in the Quick Trade ticket or reading the option chain when a poll returns a reconcile mismatch. The `N broker position(s) NOT under the software guard — no software stop / target / 15:00 square is watching …` banner (line 35) silently materialises above the fold. A screen-reader user is told nothing at all; a sighted trader focused on the lower half of a tall page (the cockpit is a two-column grid plus the account panel, so the rail is scrolled out of view) also gets no signal. The whole point of that banner — that real money is currently unprotected — is missed until they happen to scroll back up. Same for the STALE-data banner at line 18 and the no-broker-backstop banner at line 47.
- **Fix:** Add `role="alert"` (implicit `aria-live="assertive"`) to the danger banners (unguarded positions) and `role="status"` / `aria-live="polite"` to the degraded and no-backstop banners, so each is announced on appearance. Consider also making the rail `sticky` or mirroring a count into the sticky CommandBar so a scrolled-down trader still sees it.

### frontend/src/components/live/cockpit/BrokerConnect.jsx:105

- **STATUS:** UNVERIFIED
- **Defect:** The Upstox chip self-polls outside the shared provider and swallows every error with `.catch(() => {})`, keeping the last-good status object forever, so a green "connected" dot can outlive the actual data-feed session indefinitely with no staleness marker.
- **Failure:** Unlike every money slice (which feeds `health.degraded` and the "as of … STALE" stamp), this fetch has no error state, no lastSuccess, and no membership in `SLICE_LABEL`. If `/upstox/status` starts 500-ing or the network drops, the chip keeps rendering `bg-success` + "connected" from a reading that may be hours old. The trader concludes the market-data feed is healthy and leaves deployments running; only the separate FeedHealthBanner (which requires ≥1 ACTIVE deployment and a specific `feedHealth.state`) might contradict it.
- **Fix:** Move Upstox status into `LiveDataProvider` alongside the other slices so it carries an error and `lastSuccess`, and grey/mark the chip when its last successful read is stale.

### frontend/src/components/live/cockpit/BrokerConnect.jsx:55

- **STATUS:** UNVERIFIED
- **Defect:** The chip button that toggles the Reconnect/Disconnect popover has no `aria-expanded`, no `aria-haspopup`, and no `aria-controls`, and opening the popover does not move focus into it.
- **Failure:** A screen-reader user activates the Flattrade chip to disconnect the broker. Nothing is announced — the button reports no expanded state — so the user believes the click did nothing and presses it again, which toggles the popover closed. Even when it is open, focus remains on the chip, so the Reconnect/Disconnect buttons (lines 74–75) are only reachable by continuing to Tab forward, and the Escape handler at line 28 closes the popover without returning focus anywhere predictable. Contrast with LiveDeploymentStrip.jsx:302, which does set `aria-expanded` correctly on its disclosure button.
- **Fix:** Add `aria-expanded={open}` and `aria-haspopup="true"` to the button at line 55, give the popover div an `id` referenced by `aria-controls`, and focus the first action button in the popover when `open` flips true (restoring focus to the chip on close).

### frontend/src/components/live/cockpit/BrokerConnect.jsx:34

- **STATUS:** UNVERIFIED
- **Defect:** `connected` ignores `status.regenerate_after_6am`, so the Flattrade chip can show a green "connected" dot for a token the backend already treats as unusable — a regression against LiveBanner.jsx:16, the component BrokerConnect replaced.
- **Failure:** `expired` (from `expires_at`) and `regenerate_after_6am` (from `issued_at` vs today's 06:00 IST cutoff) are computed independently in backend/app/live/flattrade_token.py:252-278, and `regenerate_after_6am` fails CLOSED to true on an unparseable `issued_at` while `expired` stays false when `expires_at` is absent or parses fine (line 256, `if expires_at_str:`). backend/app/routers/deployments.py:117 blocks live execution on either flag. So a token doc written without `expires_at`, or with a malformed `issued_at`, renders as a green "Flattrade · exec · connected" chip while every broker call 400s. The trader concludes execution is healthy and that the guard can transmit exits; it cannot.
- **Fix:** Mirror LiveBanner: `const expired = !!(status?.expired || status?.regenerate_after_6am)` and show "token expired — login needed" for either.

### frontend/src/components/live/cockpit/CommandBar.jsx:45

- **STATUS:** FIXED — Relabelled "Kill switch ↓", quiet styling, scroll-margin-top on target (3007f7d).
- **Defect:** The command bar's "Kill" control is a danger-red anchor with a Zap icon that only scrolls to `#kill-switch`; it is visually indistinguishable from a fire button, and its navigate-only nature is disclosed solely in a hover `title`.
- **Failure:** Verified: it does NOT duplicate or fake the kill logic — it targets `<div id="kill-switch">` (LiveCockpit.jsx:133) wrapping the real `KillSwitchPanel`, and `api.liveKillSwitch()` is called only from KillSwitchPanel.jsx:134. The defect is affordance: in a fast market a trader hits the red Kill in the persistent bar, the page jumps, nothing else happens, and they must find the panel, click "FLATTEN EVERYTHING…", type KILL and hit KILL — four more actions they did not expect. On touch/keyboard there is no hover tooltip at all, so the control reads as "already fired".
- **Fix:** Relabel to "Kill switch ↓" / "Go to kill" with a chevron, or make it a button that scrolls AND opens the typed-confirm step in one action; also add `scroll-margin-top` to the target so the sticky bar doesn't overlap the panel header.

### frontend/src/components/live/cockpit/CommandBar.jsx:46

- **STATUS:** UNVERIFIED
- **Defect:** The `Kill` shortcut is a fragment anchor to `#kill-switch`, but the target `<div id="kill-switch">` (LiveCockpit.jsx:133) has no `tabIndex={-1}` so focus never moves there, and neither it nor the page defines `scroll-margin-top`, so the sticky `top-0` CommandBar overlays the panel it just scrolled to.
- **Failure:** In a panic the trader clicks `⚡ Kill`. The page scrolls the kill-switch card flush to viewport top, where the sticky command bar (CommandBar.jsx:28, ~44px tall and `flex-wrap`, so 80px+ once the brand, pill, two broker chips, Kill and Configure wrap at laptop widths) sits directly on top of it — hiding the `KILL SWITCH` heading and the open-position/working-order counts (KillSwitchPanel.jsx:150–160), and on a wrapped bar most of the `FLATTEN EVERYTHING…` button at KillSwitchPanel.jsx:171. The trader has to scroll back up a little before they can click the thing the shortcut was supposed to deliver them to. A keyboard user is worse off: activating the anchor leaves focus on the Kill link in the command bar, so the very next Tab goes to Configure, not to the kill button — they must tab through the entire left column and half the right column to reach it.
- **Fix:** Add `tabIndex={-1}` and `className="scroll-mt-24"` to the `#kill-switch` wrapper in LiveCockpit.jsx:133 (or set a global `scroll-padding-top` on `html`), and make the Kill control a button that calls `el.scrollIntoView()` followed by `el.focus()` so keyboard focus lands on the panel.

### frontend/src/components/live/cockpit/ConfigDrawer.jsx:41

- **STATUS:** FIXED — Backdrop/kill concern superseded: drawer is inert+aria-hidden when closed (91fa367).
- **Defect:** The drawer's full-viewport `fixed inset-0 z-40` backdrop sits above the sticky CommandBar (`z-20`) and the always-on KillSwitchPanel (no z-index), so while the drawer is open no kill or broker control is clickable and the drawer itself contains no kill control.
- **Failure:** Trader opens Configure to stop a misbehaving deployment, the market moves against them, and they reach for the kill switch. The first click anywhere outside the drawer only dismisses it (the backdrop's `onClick={onClose}`); they must click again to reach the panel, then run the typed-KILL flow. In a real-money emergency the design guarantee "the kill switch is always one click away" is broken exactly when the trader is operating deployment controls — the most likely moment to need it.
- **Fix:** Either raise the CommandBar above the backdrop (and keep its Kill control clickable while the drawer is open), or place a kill entry point inside the drawer header.

### frontend/src/components/live/cockpit/ConfigDrawer.jsx:41

- **STATUS:** UNVERIFIED
- **Defect:** The drawer backdrop is `fixed inset-0 z-40` while the CommandBar is `sticky top-0 z-20` and the kill panel is unlayered, so while the config drawer is open both the Kill anchor and the real FLATTEN control are behind the overlay and unclickable.
- **Failure:** Trader is in the drawer adjusting the overall SL when the underlying gaps against an open position. They click the red "Kill" pill in the command bar (CommandBar.jsx:45-51) — the overlay intercepts it and the click merely closes the drawer; nothing scrolls, nothing arms. They must click again to reach the panel, then again to open the confirm, then type KILL. (The Kill control itself is correctly wired: it is an `href="#kill-switch"` anchor to LiveCockpit.jsx:133's `<div id="kill-switch">` wrapping the one real KillSwitchPanel → `api.liveKillSwitch` — it does not duplicate or fake the kill.)
- **Fix:** Raise the CommandBar above the backdrop (z-50) and keep the Kill anchor click-through, or render a kill affordance inside the drawer header; alternatively have the anchor close the drawer and scroll in one action.

### frontend/src/components/live/cockpit/ConfigDrawer.jsx:32

- **STATUS:** UNVERIFIED
- **Defect:** The drawer's Escape handler is unconditional while open, so Escape dismisses the drawer even when a nested overlay owns the key — closing the child and the drawer in one keystroke.
- **Failure:** Trader opens Configure → clicks "Enable live execution" on a deployment, opening DeployToLivePanel's Radix Dialog (a typed-ENABLE consent flow). They press Esc to back out of the dialog. Radix dismisses the dialog *and* this listener fires (Radix's DismissableLayer does not stop native keydown propagation), so the whole drawer slides shut — the trader must reopen it and re-find the row. The same happens with OverallSettingsPanel's SettingChip popovers (which register their own Escape handler at OverallSettingsPanel.jsx:185) and with the BrokerConnect popovers.
- **Fix:** Track nested-overlay state (or check `event.defaultPrevented` / a shared "topmost layer" flag) before closing, or gate on `document.querySelector('[data-radix-dialog-content]') == null` so Escape only reaches the drawer when nothing is stacked on it.

### frontend/src/components/live/cockpit/ConfigDrawer.jsx:46

- **STATUS:** UNVERIFIED
- **Defect:** The closed drawer stays mounted and merely translated off-screen with no `inert` / `aria-hidden` / `visibility:hidden`, so every control inside it remains in the tab order and keyboard-activatable while invisible.
- **Failure:** With the drawer closed, the trader tabs through the page. After the last visible control, focus moves into the off-screen aside and lands on real mutating controls — "Stop ALL live", per-deployment Disable/Stop, GTT Cancel, Overall-controls Save. Pressing Enter on the invisible "Stop ALL live" button pops a `window.confirm` with no visible originating UI; one more Enter squares every open paper trade, pauses every deployment and disables every live deployment. Screen-reader users get the entire drawer read out as part of the page. (Related: activating an off-screen SettingChip portals its popover to `document.body` with a fixed position clamped back on-screen — a floating panel with no visible parent.)
- **Fix:** Add `inert` (or `aria-hidden="true"` + `pointer-events-none` + `visibility:hidden` after the transition) to the `<aside>` when `!open`, and restore focus to the Configure button on close.

### frontend/src/components/live/cockpit/MarketAnalysis.jsx:197

- **STATUS:** UNVERIFIED
- **Defect:** The ATM (nearest-to-spot) strike row in the option chain is identified exclusively by a `bg-sky-500/10` background tint — no text marker, no font weight change, no `aria-current` — and that tint is a 1.105:1 luminance step against the light-theme white card background.
- **Failure:** A trader on the White theme scans the ATM ± 2 chain to pick a strike. The highlighted row is indistinguishable from its four neighbours (1.105:1 — roughly a 1% brightness difference), so they read CE LTP off the wrong strike and enter the Quick Trade ticket with an OTM strike they believed was ATM. A colourblind trader on the dark theme, and any screen-reader user (the tint carries no semantics at all), hit the same problem regardless of theme — the chain is announced as five identical unlabelled rows.
- **Fix:** Add a non-colour marker to the nearest row: an `ATM` text badge in the Strike cell, `font-bold`/left border-accent, and `aria-current="true"` plus a visually-hidden "ATM" string, keeping the tint only as reinforcement. Also raise the tint to a theme token so it survives the light theme.

### frontend/src/components/live/cockpit/MarketAnalysis.jsx:199

- **STATUS:** UNVERIFIED
- **Defect:** Decision-critical numeric values are painted with `text-dimmer` (`--text-3` #6E7A8E), which measures 4.18:1 on `--bg-1` and 3.64:1 on `--bg-3` in the dark theme — below the 4.5:1 AA minimum for the 10–12px sizes used.
- **Failure:** On the default dark theme, the option chain's CE OI and PE OI columns (lines 199 and 203) — the inputs a trader uses to read where writers are positioned — render at 4.18:1 in 12px mono, so they fade into the card whenever the room is bright or the monitor is dimmed, and the trader misreads or skips the OI column entirely. Worse, in the Order book tab the order STATUS badge (liveHelpers.js:212, `text-dimmer text-[10px]` on `bg-bg-3`) measures 3.64:1 at 10px: the trader glances at the account panel to confirm whether an exit order is COMPLETE, OPEN or REJECTED and cannot read it — while `text-dim` (`--text-2`) used two cells over measures 8.69:1 and is comfortably legible.
- **Fix:** Promote these specific values from `text-dimmer` to `text-dim` (`--text-2` #AAB4C5, 8.69:1) — OI cells at MarketAnalysis.jsx:199/203 and the status badge text at liveHelpers.js:212 — or darken/lighten `--text-3` in index.css:60 to clear 4.5:1 on `--bg-3`. Keep `text-dimmer` for genuine micro-labels only.

### frontend/src/components/live/cockpit/MarketPulse.jsx:131

- **STATUS:** UNVERIFIED
- **Defect:** The S/R range bar's price-position marker is a 2px `bg-sky-400` line hardcoded to the dark palette; against the light-theme `bg-bg-2` (#EEF3F8) it measures 1.92:1, below the 3:1 minimum for non-text UI components, and the underlying `position_in_range` value is never rendered as text.
- **Failure:** A trader on the White theme looks at Market Pulse to judge where spot sits between support and resistance. The sky-blue tick on the pale grey track is effectively invisible (1.92:1), and because the numeric position is not printed anywhere — only the S / pivot / R prices at lines 141–143 — there is no fallback. The trader cannot tell whether price is pinned to resistance or sitting mid-range, which is the entire purpose of the widget. The same widget is also unreadable to a screen-reader user: the bar has no `role="meter"`/`progressbar`, no `aria-valuenow`, and no text alternative.
- **Fix:** Use a theme token (`bg-info`, which is #0B66C3 in light theme) or add a contrasting outline to the marker, widen it to ≥3px, print `{Math.round(posPct)}% of range` as text next to the S/pivot/R row, and give the track `role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={posPct} aria-label="Spot position between support and resistance"`.

### frontend/src/components/live/cockpit/MarketPulse.jsx:93

- **STATUS:** UNVERIFIED
- **Defect:** `--color-warning` is defined as #BE123C in the light theme (index.css:107) — a crimson, not an amber — so every warning-vs-danger colour distinction in the cockpit collapses into two nearly identical reds when the White theme is active.
- **Failure:** A trader on the White theme reads the 5-segment regime meter, where the lit segment is the ONLY per-segment signal: buckets 0–1 use `bg-danger` (#C62828) and bucket 2 (Choppy) uses `bg-warning` (#BE123C). In a 6px-tall bar those two reds are indistinguishable, so a choppy regime and a bearish regime look identical. The same collapse hits the RiskKpis Guard tile (RiskKpis.jsx:30–32): `ARMED` renders `tone="danger"` (#C62828) and `DRY-RUN` renders `tone="warn"` (#BE123C), so the at-a-glance colour cue for "will the guard actually place exit orders?" is gone and the trader must read the small caps text. It also affects the `bg-warning` used-margin segment in AccountTabs.jsx:167, which reads as an alarm even at 5% utilisation.
- **Fix:** Change `--color-warning` in the light theme block (index.css:107) to an actual amber/ochre with sufficient contrast (e.g. #92500E or reuse `--color-amber: #9A6500` already defined at index.css:110) so warning and danger stay hue-separated in both themes. The regime meter should additionally carry the bucket name as text on the lit segment rather than relying on position alone.

### frontend/src/components/live/cockpit/MarketPulse.jsx:44

- **STATUS:** UNVERIFIED
- **Defect:** The analysis panels carry no as-of timestamp or staleness marker and their poll error is deliberately excluded from the degraded banner, so a frozen regime/trend/option-chain reads exactly like a live one.
- **Failure:** `/market/analysis` starts failing at 11:00. usePoll.js:41-43 keeps the last successful payload forever ("a failed call NEVER clears data"), and LiveDataProvider.jsx:133-140 deliberately keeps `marketAnalysis` out of the money-slice error set, so no degraded banner fires. MarketPulse keeps rendering the 11:00 regime meter, trend arrows and confidence bar — labelled "deterministic" (line 78) — and MarketAnalysis keeps rendering the 11:00 PCR, max-pain and option chain (used to eyeball strikes for a Quick Trade). At 13:00 the trader places a real-money order off a two-hour-old chain. The broker slices got exactly this treatment via `fmtAsOf` in LiveCockpit.jsx:107-116; the analysis panels did not.
- **Fix:** Add an "as of HH:MM:SS" stamp (from the provider's lastSuccess for the analysis slice) to both panels and grey/badge them once the payload is older than a small multiple of the 10s cadence.

### frontend/src/components/live/cockpit/RiskKpis.jsx:30

- **STATUS:** FIXED — Same fix as RiskKpis:21.
- **Defect:** The Guard KPI uses `!!guard?.armed`, which fails to the DANGEROUS direction on a partial payload and labels the not-armed state "DRY-RUN", directly contradicting GuardPanel's deliberate `status?.armed !== false` default and its "Guard unreachable" wording.
- **Failure:** GuardPanel.jsx:164-168 documents the rule: an absent `armed` must read as armed because "`!!undefined` would render 'Dry-run · logs only' over positions that are in fact being auto-exited for real — the fail-DANGEROUS direction." RiskKpis line 21 does exactly that `!!`. With any payload variant lacking `armed` (older backend, proxy/partial response), the same screen shows "Guard: DRY-RUN" (amber) in the KPI grid and "Auto-exit live" (red) in GuardPanel six inches below. Worse, "DRY-RUN" implies a safe simulation, whereas the only real cause of not-armed today is that the guard cannot reach the broker — i.e. positions are UNPROTECTED, the opposite of what "dry-run" suggests.
- **Fix:** Use `guard?.armed !== false` and relabel the false state "UNREACHABLE" with danger tone, matching GuardPanel.

### frontend/src/components/live/cockpit/RiskKpis.jsx:37

- **STATUS:** FIXED — Day Stop wired to real caps + today realised; honest empty states (3007f7d).
- **Defect:** The "Day Stop" KPI is hardcoded to `value="—"` with `sub="per-deployment"`, so a risk tile in the live risk grid can never report anything.
- **Failure:** The daily loss cap is a required, enforced live gate (DeployToLivePanel.jsx:140-142 refuses to enable live without a positive `daily_loss_cap`). A trader scanning the risk grid sees Day Stop "—" beside real values (Day P&L, Avail Margin) and reads it as "no day stop configured / not near it", when in fact a ₹4,000 cap may be one bad trade from halting every live deployment. A permanently blank tile in a risk grid is worse than no tile.
- **Fix:** Populate it from the deployment caps + today's realised P&L (both already in context via `deployments` / `deployLive[].today.realized_pnl`), or remove the tile until it is wired.

### frontend/src/components/live/liveHelpers.js:107

- **STATUS:** UNVERIFIED
- **Defect:** PositionsBlotter and the RiskKpis tiles treat a null slice as "loading" forever, so a broker read that has never succeeded shows a permanent "Loading positions…" / "…" instead of naming the failure — the exact bug AccountTabs.jsx:63 was fixed for.
- **Failure:** Trader opens the page with an expired Flattrade session (the normal out-of-session state — every broker route 400s). `positions` stays null, so the always-on Open Positions card renders "Loading positions…" indefinitely (line 107-113) and the Day P&L / Open Pos tiles render "…" (RiskKpis.jsx:27,29). Neither says the read failed; `errors.positions` is available in context and unused by both. The Holdings and Funds tabs got the `Unavailable` treatment for this exact case (AccountTabs.jsx:39-52,63); the always-on core did not, so the panel a trader actually watches is the one that lies.
- **Fix:** Pass `errors.positions`/`errors.limits`/`errors.orders` into PositionsBlotter and RiskKpis and render the same `Unavailable` state AccountTabs uses when the slice is null AND erroring.


## LOW

### C:/Users/haroo/OneDrive/Documents/New project/Emergent-AlphaForge/frontend/src/components/live/LiveDeploymentStrip.jsx:99

- **STATUS:** UNVERIFIED
- **Defect:** The 'entry refused' chip forces `whitespace-nowrap` on a variable-length, human-expanded refusal reason inside the config drawer's ~374px row, and DrawerSection's `overflow-hidden` (ConfigDrawer.jsx:20) clips the tail instead of wrapping it.
- **Failure:** A live deployment stops placing orders and the backend reports `ref_premium_unavailable`; entryErrorLabel expands it to 'no fresh option tick to capture the reference premium'. Prefixed with 'entry refused: ' that is ~67 characters ≈ 400px at text-[10px], inside a drawer row whose usable width is ~374px (460px drawer − 32 p-4 − 24 DrawerSection p-3 − 24 row px-3 − borders), and narrower still when the drawer falls back to 94vw. `whitespace-nowrap` blocks wrapping, and the section's overflow-hidden clips the last words with no scrollbar. The trader sees a truncated explanation of why their live strategy is silently not trading; the full text is only in the `title` tooltip.
- **Fix:** Drop `whitespace-nowrap` from the chip at LiveDeploymentStrip.jsx:99 (the row is already `flex-wrap`) or add `max-w-full truncate` so it ellipsises visibly instead of being clipped by the ancestor.

### C:/Users/haroo/OneDrive/Documents/New project/Emergent-AlphaForge/frontend/src/components/live/cockpit/AccountTabs.jsx:130

- **STATUS:** FIXED — Loading-vs-unavailable states added for Funds/Holdings/Orders (3007f7d).
- **Defect:** The tab strip is a plain `flex` row with no `flex-wrap` and no `overflow-x-auto`, sitting inside a card with `overflow-hidden` (line 129), so once the four tabs' min-content exceeds the card width the last tab is clipped away with no scroll affordance.
- **Failure:** Trader narrows the browser window (or docks it beside a broker terminal) so page-content drops under ~400px. The four buttons ('Funds & Margin', 'Holdings', 'Order book', 'Trade book') at `px-4 py-2.5 text-xs` cannot shrink below their min-content (~77+87+100+100 ≈ 364px plus gaps); the row overflows the card and `overflow-hidden` clips it. The 'Trade book' tab — the only route to LiveBlotter and LiveTradeStats — becomes invisible and unclickable, and no horizontal scrollbar appears to reach it.
- **Fix:** Add `overflow-x-auto` (plus `shrink-0` on the buttons) to the tab row at AccountTabs.jsx:130, or `flex-wrap` so the tabs stack instead of being clipped.

### C:/Users/haroo/OneDrive/Documents/New project/Emergent-AlphaForge/frontend/src/components/live/cockpit/MarketPulse.jsx:130

- **STATUS:** UNVERIFIED
- **Defect:** The S/R range marker is an `absolute w-0.5` span positioned with `left: ${posPct}%` and no `-translate-x-1/2` inside an `overflow-hidden` track, so at position_in_range = 1.0 the entire 2px marker lands outside the clip box and disappears.
- **Failure:** Spot prints at the day's/session's resistance so the backend returns `levels.position_in_range = 1.0`. posPct clamps to 100, the marker is placed with its LEFT edge at the track's right padding edge, and the parent's `overflow-hidden` (line 128) clips all 2px of it. The trader sees an empty range bar — visually indistinguishable from a broken/unavailable reading — at precisely the moment the pulse should be screaming 'at resistance'. The same class of marker in MarketHeader.jsx:290 gets this right with `-translate-x-1/2`.
- **Fix:** Add `-translate-x-1/2` (and keep the clamp) to the marker span at MarketPulse.jsx:130-133, mirroring MarketHeader.jsx:290.

### frontend/src/components/live/FeedHealthBanner.jsx:26

- **STATUS:** UNVERIFIED
- **Defect:** Both banner actions swallow failures: `connect()` has an empty catch and no else-branch when `login_url` is absent, and `restart()` catches with a bare comment.
- **Failure:** Active deployments exist and the feed is NEEDS_LOGIN. The trader clicks "Connect Upstox". `/upstox/auth/start` 500s because the Upstox credentials aren't configured (broker.py:219). The button flips to "Opening…", then back to "Connect Upstox" with no message; the banner stays. The trader clicks it repeatedly with no idea the credentials are missing. Same for "Restart feed" when `/live-feed/restart` fails.
- **Fix:** Surface the error — `toast.error(getApiErrorMessage(e, ...))` in both catches, plus an explicit error when no login URL is returned (BrokerConnect's `redirectToAuth` already does this at line 97).

### frontend/src/components/live/LiveCockpit.jsx:65

- **STATUS:** UNVERIFIED
- **Defect:** `authMsg` is set once on the OAuth redirect and never cleared — no timeout, no dismiss control, and no invalidation when the connection state later changes.
- **Failure:** Trader logs in to Flattrade in the morning; the green "Flattrade login successful — connected." banner appears in the AlertRail. Hours later the token expires (or they hit Disconnect). The banner is still on screen asserting "connected" while the Flattrade chip shows "token expired" and the account tabs 400 — two contradictory statements about the one thing that determines whether an order can be placed.
- **Fix:** Auto-clear `authMsg` after ~10s (`setTimeout` in the effect, cleared on unmount) or add a dismiss ✕, and clear it whenever `status.connected` flips false.

### frontend/src/components/live/LiveCockpit.jsx:142

- **STATUS:** UNVERIFIED
- **Defect:** The staleness stamp covers only `positions`/`limits`; the market-analysis slice is deliberately excluded from `health.degraded` (LiveDataProvider.jsx:68) yet `usePoll` keeps its last-good data, so a frozen regime/PCR/max-pain reads as current with no cue.
- **Failure:** The `/market/analysis` poll starts failing at 11:00 (backend restart, feed drop). `marketAnalysis` retains the 11:00 payload forever, so MarketPulse keeps showing "Strong / confidence 78% / Daily ▲" and MarketAnalysis keeps showing an 11:00 PCR, max-pain and ATM chain at 14:30. Nothing on either panel marks them stale — the only as-of stamp on the page is labelled "Broker account" and is driven by `lastSuccess.positions ?? lastSuccess.limits`.
- **Fix:** Expose `lastSuccess` for the analysis poll and render a per-panel "as of HH:MM:SS" (dimmed/amber past ~60s) in the MarketPulse and MarketAnalysis headers, or blank the tiles when `errors.marketAnalysis` has persisted for more than a few cycles.

### frontend/src/components/live/cockpit/CommandBar.jsx:46

- **STATUS:** UNVERIFIED
- **Defect:** The Kill anchor scrolls `#kill-switch` to the top of the page-content scroller, where the CommandBar's own `sticky top-0` bar overlays it — the kill panel's header and its open/working counts land underneath the bar.
- **Failure:** Trader clicks Kill during a fast market. The scroll container (`Layout.jsx:106`, `overflow-y-auto`) brings `#kill-switch` (LiveCockpit.jsx:161) flush to its top edge, but the sticky CommandBar (line 28, ~40px, z-20) sits on that same edge, so the panel's title row and the "N open · N working" blast-radius readout are hidden — the trader sees a headless red box and has to scroll up to confirm what will be flattened.
- **Fix:** Add `scroll-mt-16` (or `style={{scrollMarginTop: 56}}`) to the `#kill-switch` wrapper.

### frontend/src/components/live/cockpit/DeploymentSummary.jsx:33

- **STATUS:** UNVERIFIED
- **Defect:** The summary renders at most four deployment rows with no "+N more" indicator, and any deployment whose `mode` field is missing falls through to a neutral grey "Paper" pill.
- **Failure:** Trader has six non-archived deployments and the one in live mode is fifth in list order. `rows.slice(0, 4)` shows four grey "Paper" pills and the button; nothing indicates two rows are hidden. The header badge does say "1 live · 5 paper", but the trader cannot see WHICH strategy is live without opening the drawer. Compounding this, `statusPill` (line 8-14) returns the neutral "Paper" pill as its default branch, so a deployment document missing `mode` is labelled paper — the fail-dangerous direction for a mode readout.
- **Fix:** Sort live-mode deployments to the top before slicing, add a "+N more" row, and make the unknown-mode case render an explicit "mode unknown" pill rather than defaulting to "Paper".

### frontend/src/components/live/cockpit/MarketPulse.jsx:78

- **STATUS:** UNVERIFIED
- **Defect:** The market-intelligence panels carry a "deterministic" badge and no as-of/staleness indicator, while `marketAnalysis` is deliberately excluded from the `health.degraded` money-slice set and `usePoll` retains last-good data on error.
- **Failure:** LiveDataProvider.jsx:66-68 documents the exclusion as intentional ("a failure here is NON-money"), but the panel is the top-left element of a manual-trading cockpit that also hosts a one-click real-money ticket. If `/market/analysis` starts failing at 10:00, MarketPulse keeps showing the 10:00 regime, confidence bar and S/R levels, and MarketAnalysis keeps showing a frozen option chain and PCR, both labelled "deterministic" with no timestamp. A trader sizes a real option buy off a chain that stopped updating hours ago.
- **Fix:** Expose `lastSuccess` for the analysis slice and stamp both panels with "as of HH:MM:SS", dimming/badging them when the reading is older than a few poll intervals.

### frontend/src/components/live/cockpit/MarketPulse.jsx:32

- **STATUS:** UNVERIFIED
- **Defect:** `TrendGlyph` and `structureGlyph` emit bare `▲` / `▼` / `▬` / `—` characters with no accessible text, so the four multi-timeframe trend cells have no name beyond their timeframe label.
- **Failure:** A screen-reader user reads the Market Pulse card and hears "Intraday", "Daily", "Weekly", "Monthly" with either silence or an inconsistent symbol reading ("black up-pointing triangle" on VoiceOver, nothing at all on NVDA with default symbol verbosity) in between — so the entire multi-timeframe trend read, which is the main reason the card exists, is unavailable. Unlike the structure row at line 84, where `structure.label` supplies the wording, these cells have no textual equivalent anywhere.
- **Fix:** Return `<span aria-hidden="true">▲</span><span className="sr-only">up</span>` (and equivalents for down/flat/unknown) from `TrendGlyph`, and apply the same pattern to the `structureGlyph` character at MarketPulse.jsx:83.

### frontend/src/components/live/cockpit/QuickTrade.jsx:18

- **STATUS:** UNVERIFIED
- **Defect:** `disabled={false}` is hardcoded, so the real-money order ticket is fully interactive even when the Flattrade session is disconnected or expired, despite `status` being available one component away.
- **Failure:** The Flattrade token has expired (chip amber, account tabs showing "Broker session is not active"). The trader fills the ticket, previews, clicks "Place order — REAL MONEY", and confirms in the danger dialog. Only then does `handlePlaceConfirmed` arm LIVE_TEST, fail, and stand back down — the trader gets a generic failure after a full two-step real-money confirmation instead of a disabled ticket that says "broker not connected".
- **Fix:** Pass `disabled={!(status?.connected && !status?.expired)}` down from LiveCockpit and render a short reason line in the SectionCard badge.

### frontend/src/components/live/cockpit/RiskKpis.jsx:37

- **STATUS:** UNVERIFIED
- **Defect:** The "Day Stop" risk KPI is hardcoded to "—" and never reads any data, so a configured per-deployment daily loss cap is indistinguishable from none at all.
- **Failure:** Trader sets a ₹4,000 daily loss cap when enabling live execution (DeployToLivePanel.jsx:357-380, where it is a required field), then glances at the risk grid mid-session to check how much of it is left. The tile renders a permanent "—" next to five tiles that do carry real broker numbers, so it reads as "no day stop configured" or "data missing" — and there is no other day-stop readout anywhere on the cockpit.
- **Fix:** Either wire the tile to the deployment day-stop from the live-status payload, or remove it until it has a data source; do not ship a permanently-blank tile inside a grid of live values.

### frontend/src/components/live/liveHelpers.js:226

- **STATUS:** FIXED — ReconcileChip renders "reconcile: NO DATA" when the slice errors (3007f7d).
- **Defect:** `ReconcileChip` returns `null` when `reconcile` is falsy and renders an unqualified green "Reconciled ✓" from last-good data when the poll is failing, so "never loaded / erroring" and "broker book matches the guard registry" are visually the same or better than the truth.
- **Failure:** `GET /live-broker/reconcile` raises HTTP 400 when the Flattrade client can't be built (live_broker.py:681-692) and `usePoll` never clears `data` on error, so after a token expiry the "Open Positions" header keeps its emerald "Reconciled ✓" chip from the last good read. Simultaneously `LiveCockpit`'s `unguardedPositions` derivation (line 62) freezes, so the UNGUARDED-positions danger banner cannot appear for a position opened after the failure. The trader reads a green reconciled badge over a frozen positions table and concludes everything is watched.
- **Fix:** Render an explicit grey "reconcile: NO DATA" chip when `reconcile == null`, and grey/amber the "Reconciled ✓" chip whenever `errors.reconcile` is set.



## DATA-NULL-SAFETY (dimension completed inline 2026-07-25)

The auditor for this dimension never ran — both workflow attempts were killed by
the usage limit. Completed by hand against the authoritative Noren Limits spec
(`docs/Resources/flattrade-pi-api/endpoints/25-limits.md`).

### liveHelpers.js — deriveCash()

- **STATUS:** FIXED — `cash ?? net` only; the used-margin fallback removed.
- **Defect:** fell back to `limits.marginusedtoday` for the "Available Cash" /
  "Avail Margin" KPI. Per the Noren spec `cash` is "Cash Margin **available**"
  while `marginused` is "Total margin / fund **used** today" — the fallback is a
  SEMANTIC INVERSION, and `marginusedtoday` is not even a documented field.
- **Failure:** on any payload lacking `cash`/`net`, the money KPI reports the
  amount already CONSUMED as if it were spendable; a trader sizing a position off
  it over-commits. Absent data must render "—", never the opposite number.

### AccountTabs.jsx — usedMargin

- **STATUS:** FIXED — uses the documented total `marginused` only.
- **Defect:** fell back to `premium` / `span`, which are COMPONENTS of margin
  used, not the total.
- **Failure:** understates margin used and makes the utilisation bar read far
  lower than reality (a partial figure presented as the total). Now renders "—"
  when the total is absent.

### Swept and found CLEAN (no defect)

- Falsy-vs-nullish: the only `||` uses on numerics are bar widths and additive
  sums where 0 is harmless; `MetricCard` renders a genuine 0 (only
  null/undefined/"" become "–"), so no legitimate zero is swallowed.
- KPI counts vs the tables they label: both use the shared `isOpenPosition` /
  `isWorkingOrder` predicates, so they cannot disagree.
- Utilisation math cannot divide by zero (`total ? ... : null`).
- `deriveDayPnl` sums unrealised + realised per position with a `pnl` fallback —
  no double count.
- Stale-as-fresh is covered by the as-of stamp + degraded banner, and the
  reconcile chip now reports NO DATA when its slice errors.
