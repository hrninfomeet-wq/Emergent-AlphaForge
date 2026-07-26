# Four real-money safety fixes — implementation plan (2026-07-25)

> **STATUS 2026-07-25:** Items **2, 3 and 4 are IMPLEMENTED and pushed**
> (`8519ae4`, `596d190`, `041459f`; suite 3,610/0, frontend compiles, 3 contract
> tests added). **Item 1 is deferred by the user to live-market time** — it needs a
> real transmit to exercise honestly, so it is the one fix that should be built and
> validated while the market is open.

> Deferred from the Live Cockpit page audit. Findings register:
> `docs/live-cockpit-audit-2026-07-25.md`. Each item below was re-verified against
> the code before planning (no speculative work).

**Common invariants:** no new broker-mutating endpoints; no change to the
executor chokepoint; every change gets host tests; verify in Chrome against the
running app; commit per item so a usage-limit interrupt never loses work.

**Suggested order = descending money-risk.** Items 1 and 2 can strand or destroy
real protection; 3 misleads about protection that is already degraded; 4
corrupts saved config.

---

## Item 1 — "Transmission unconfirmed" on a failed place (HIGHEST RISK)

**Where:** `frontend/src/components/live/LiveOrderTicket.jsx:352-354`

**Verified defect:** the `catch` around `api.approveOrder(...)` treats EVERY
throw as "Place failed". A transport failure (timeout, proxy drop, browser
offline) can occur AFTER the order reached Flattrade. The message says the place
failed, and because `placedOk` stays false the `finally` block also fires a
best-effort stand-down — reinforcing "nothing happened".

**Failure scenario:** trader confirms a real-money entry; the response is lost in
transit but the order fills at the broker. The UI says "Place failed", so they
place it again → **duplicate live position**, unhedged and unintended.

**Fix:**
1. Distinguish *rejected* from *unconfirmed*. A structured API error (4xx with a
   verdict body) is a genuine rejection — keep today's wording. A network/timeout
   error (`!e.response`, `ECONNABORTED`, 502/503/504) is UNCONFIRMED.
2. On unconfirmed: render a distinct amber panel — "TRANSMISSION UNCONFIRMED —
   this order may already be live at the broker. Check the Order book before
   retrying." Keep `previewResult` cleared and the Place button DISABLED so it
   cannot be re-fired from stale state.
3. Offer one action: "Refresh order book" → `refetch.all()`; re-enable placing
   only after a successful broker read.
4. Do NOT auto stand-down on an unconfirmed place (today it does): standing down
   while an order may be live is misleading. Leave mode untouched and say so.

**Tests:** `tests/test_live_order_ticket_ui.py` (source-contract, mirroring the
repo's grep-the-JSX pattern): the unconfirmed branch exists, is distinct from the
rejected branch, disables re-place, and does not call `setLiveMode` on a
network-class error.

**Verify:** in Chrome, throttle/abort the approve request (DevTools offline) and
confirm the amber unconfirmed state, disabled Place, and working refresh.

---

## Item 2 — Confirm before cancelling the PC-down backstop

**Where:** `frontend/src/components/live/GttBook.jsx` (`GttRow`, cancel button)

**Verified defect:** one unconfirmed click calls `api.cancelGtt(al_id, kind)`,
removing the resting broker OCO — the ONLY protection that survives the app or PC
dying. It is styled like the benign "Refresh" button sitting directly above it.

**Failure scenario:** trader means to Refresh, hits Cancel on the adjacent row;
the position silently loses its broker-side catastrophe exit. If the machine then
sleeps or crashes, the position is completely unprotected.

**Fix:**
1. Two-step inline confirm on the row (the pattern already used for
   BrokerConnect's Disconnect): first click arms, second confirms, auto-resets
   after ~5s or on blur.
2. The confirm names the consequence and the symbol: "Remove the PC-down backstop
   for NIFTY24200CE? The position keeps only the software guard, which needs this
   app running."
3. Danger tokens on Cancel; keep Refresh neutral so they are never confusable.
4. Disable the row's Cancel while `busy`.

**Tests:** source-contract test that the cancel path requires a confirm state and
that the confirm copy names the symbol; existing GttBook tests must stay green.

---

## Item 3 — Flag positions re-attached with DEFAULT stops after a restart

**Where:** `frontend/src/components/live/GuardPanel.jsx` (`GuardRow`) — verified:
the file contains NO reference to `source` or `rehydrated_count`.

**Verified defect:** the backend emits `source` per guarded position and
`rehydrated_count` on the payload precisely so the UI can distinguish a position
re-attached after a restart (which carries a DEEP-DEFAULT catastrophe stop, the
trader's original levels having been lost) from one still carrying its real
stop/target. Nothing reads them, so the two render identically.

**Failure scenario:** after a restart the trader sees a guarded position with a
stop far from where they set it, believes their original protection is intact,
and sizes/holds accordingly.

**Fix:**
1. Per-row amber badge when `pos.source === "rehydrated"`: "DEFAULT STOP — original
   levels lost on restart; re-set your stop/target."
2. Header count from `status.rehydrated_count` when > 0.
3. Surface it once in `AlertRail` when any rehydrated position exists, so it is
   visible without opening the guard panel.

**Tests:** source-contract test pinning the badge + the AlertRail branch; a
behavioural test is unnecessary (pure display of an existing field).

---

## Item 4 — Never present a failed settings load as the saved config

**Where:** `frontend/src/components/live/OverallSettingsPanel.jsx:377-389`

**Verified defect:** on a failed GET the `.catch` sets `defaultConfig()`
(everything disabled) into BOTH `config` and `loaded`. Because `loaded` is also
the Reset baseline, the panel now presents an all-disabled config as if it were
the saved truth — visually identical to a genuinely-off configuration — and Save
writes that back, silently wiping the real basket SL/target/trailing.

**Failure scenario:** the settings GET 404s/times out; the trader opens the panel,
sees SL/Target/Trail "off", adjusts one field and hits Save → their real overall
risk controls are overwritten with disabled ones.

**Fix:**
1. Track `loadFailed` separately from `loadNote`. On failure do NOT populate
   `loaded` with defaults (leave it null).
2. Render an explicit error state above the form: "Could not read your saved
   overall controls — the values below are NOT your live configuration."
3. Disable **Save** while `loadFailed` (Reset stays available), with a Retry that
   re-runs the GET and clears the state on success.
4. Keep today's behaviour for a genuine 404 "not configured yet" IF the backend
   distinguishes it; otherwise treat every failure as unknown (fail closed).

**Tests:** source-contract test that Save is gated on the failure flag and that
the warning copy exists. If the panel gets a behavioural harness later, add: GET
rejects → Save disabled → Retry succeeds → Save enabled.

---

## Sequencing & effort

| Item | Risk if left | Effort | Depends on |
|---|---|---|---|
| 1 Transmission unconfirmed | Duplicate live position | ~half day | — |
| 2 Backstop cancel confirm | Position loses PC-down protection | ~1–2 h | — |
| 3 Rehydrated-stop badge | False sense of protection | ~1–2 h | — |
| 4 Settings load-failure | Silent wipe of risk controls | ~2–3 h | — |

All four are independent; each ships and commits on its own. Items 2 and 3 are
the cheapest real-safety wins and could land first if time is short.
