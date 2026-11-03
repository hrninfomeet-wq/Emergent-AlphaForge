# Chart upgrade: broker capability gate

Checked 2026-11-03. Scope: official public documentation and repository inspection only. No broker calls, authentication, orders, account changes, or execution-code changes.

**Decision:** retain Upstox market data and Flattrade execution; chart actions must reuse Conditional Quick Trade. Do not advertise native derivatives GTT/OCO, market orders, or broker-hosted trailing protection as verified capabilities.

## Checks and evidence

The critical question is what the execution broker actually permits. A wrong capability display could drive a money-moving decision. Checks: (1) current official restriction announcement; (2) independent official support article plus current Pi schema. Account acceptance remains untested.

| Capability | Evidence and UI consequence |
|---|---|
| Execution order types | Confirmed: Flattrade API v2 permits LMT and SL-LMT; CO/BO are prohibited (checked [v2 announcement](https://flattrade.in/kosh/flattrade-api-implementation-of-retail-algo-standards-api-version-2-launch/) and [regulatory support answer](https://flattrade.in/support/knowledge-base/are-all-types-of-orders-permitted-by-apis-in-the-new-regulatory-framework/)). Offer limit and stop-limit terminology only. The [Pi Place/Modify schema](https://pi.flattrade.in/docs) agrees on price types, but still contains legacy CO/BO fields; their presence does not override the explicit prohibition. |
| Network/session | Confirmed: place, modify and cancel require the registered static IP; daily OAuth continues (checked v2 announcement). Browser chart gestures must route through the existing backend execution owner, never become direct broker requests. |
| Native GTT | Confirmed: Pi documents GTT endpoints, also present in the [decoded endpoint](../Resources/flattrade-pi-api/endpoints/16-place-gtt-order.md). Confirmed: the currently published [GTT policy](https://flattrade.in/forms/GTC_GTT%20Policy.pdf), sections 3(d/e), restricts the facility to NSE/BSE cash CNC, while the [older Trading Products article](https://flattrade.in/support/knowledge-base/trading-products-flattrade/) includes F&O. The sources conflict; an endpoint name is not proof of segment availability. Keep derivatives native GTT unavailable/unverified in capability UI. |
| Native OCO | Confirmed: the same policy describes sell-side OCO within GTT; it does not establish derivatives API OCO availability (checked policy scope and OCO clause). Label Conditional Quick Trade target/stop coordination as software-managed; do not call it a broker bracket or guaranteed native OCO. |
| Order streaming | Confirmed: current Pi documentation and the [decoded order feed](../Resources/flattrade-pi-api/endpoints/49-websocket-order-update-subscribe-updates-unsubscribe.md) specify `o` subscription, `om` events and no subscription acknowledgement. Events include order identity, status and fills. Silence cannot prove subscription failure or an empty account. |
| Position streaming | Confirmed: current Pi documentation and the [decoded position feed](../Resources/flattrade-pi-api/endpoints/50-websocket-position-update-subscribe-updates-unsubscribe.md) describe `p`, `pk`, `pm`; availability depends on startup `-position_update`. The request table says `uid`, while its sample uses `actid`. Do not equate documented frames with enabled account support. Retain snapshot reconciliation and freshness indicators. |

Confirmed: current Pi migration guidance uses `PiConnectWSAPI`, connection task `a`, and `accesstoken` (checked current [Pi documentation](https://pi.flattrade.in/docs), final migration section). This is documentation evidence, not verification of the running client's transport.

## Preserve the existing owners

Confirmed: the repository assigns primary data to Upstox and live execution to Flattrade ([HANDOFF](../HANDOFF.md)); `backend/app/chart_stream.py` consumes the existing Upstox connection, and `backend/app/conditional/broker.py` imports the existing conditional executor. Official Upstox [historical candles](https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/) and [market feed](https://upstox.com/developer/api-documentation/v3/get-market-data-feed) document the required data services. Keeping Upstox data-only preserves this implemented boundary; this gate does not propose another execution broker.

Confirmed: Conditional Quick Trade's [implementation record](../quick-trade-audit/IMPLEMENTATION.md) identifies software conditions, separate paper/live adapters and a broker-stop verification gate. Its [rollout record](../quick-trade-audit/ROLLOUT.md) leaves behavioural/live acceptance open. Reusing its API/preview/status lifecycle does not certify those unresolved checks. Chart execution scope must not exceed that engine's supported instruments and controls merely because chart search supports more instruments.

## Unknowns and risks

- I don't know whether this account accepts a protective SL-LMT for each target product. To find out: obtain broker clarification and separately authorized acceptance/readback evidence. Meanwhile: Confirmed: only documented capability was checked here. If acceptance is assumed incorrectly, displayed protection can be absent.
- I don't know whether native derivatives GTT/OCO or position streaming is enabled for this account. To find out: obtain current written broker clarification; resolve the position-field inconsistency before an authorized read-only transport check. Meanwhile: Confirmed: public sources conflict or qualify availability. If assumed enabled, conditions or overlays can misrepresent protection/account state.
- Critical risk: Likely: stop-limit execution can remain pending after a gap because a trigger does not remove its limit constraint. The UI must describe planned stop loss separately from full premium exposure and costs; a false maximum-loss promise could encourage excessive position sizing.
- Likely: software protection can fail during host, feed, credential or network outages because its evaluation and transmission depend on those services. Preserve the engine's protection/freshness disclosures and unresolved state; do not present a chart line as broker acceptance.

Lesson: distinguish schema presence, documented permission, and account acceptance. Confirmed approach: cross-check current broker rules against both schema and policy. Dead end to avoid: treating legacy marketing or product enums as API authorization. Research gate complete; no later-phase implementation or live acceptance was attempted.
