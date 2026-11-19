# Conditional Quick Trade UI checkpoint

Confirmed: implementation source has been saved in `frontend/src/components/live/ConditionalOrderPanel.jsx`, `frontend/src/lib/conditionalApi.js`, and `frontend/src/components/live/cockpit/QuickTrade.jsx`. The existing manual live ticket remains in its own tab; conditional orders default to paper.

The panel implements master-backed expiry/direct-strike selectors; ATM/ITM/OTM trigger rules; optional spot cross/level and premium conditions; minutes/until validity; stop/target, trailing and profit-lock controls; forced square-off; entry timeout/reprices; stale-feed policy; mode-specific kill/reset; server events, alerts and actual arm reference. Live preview requires a maximum entry premium, and live arm requires the explicit confirmation checkbox. Editing invalidates the preview. Mutations are not retried, and the consumed preview is discarded before awaiting arm.

Endpoint contract used: `GET /conditional-orders`, `GET /conditional-orders/metadata?underlying=`, `POST /conditional-orders/preview` (spec), `/arm` ({preview_id,confirm_live}), `/{id}/cancel`, `/kill` ({mode}), `/reset` ({mode}), all using the existing apiClient `/api` base. Parent owns backend matching.

The preview reference is explicitly indicative. The server recaptures at arm, and order.reference is displayed as the actual fixed reference. Confirmation distinguishes the maximum entry premium budget from planned stop loss and full premium exposure plus costs. Software-only protection warning remains prominent.

`ConditionalOrderPanel.test.jsx` contains mocked-API coverage for paper default/request units, live premium cap/confirmation, invalidated preview, combined conditions/trailing, arm error/no retry, and mode-specific kill. **Not run:** testing was deferred on the parent's latest instruction to prioritize implementation. No browser or live actions were performed.

Core lesson: use server confirmation identity and display the actual captured reference; never silently preserve a preview after an edit. Confirmed approach: separate API helper avoids modifying the pre-existing dirty API module. Dead ends avoided: treating indicative preview data as the fixed arm reference, omitting a debit cap for dynamic live strikes, or allowing a retry of an uncertain mutation.
