# Quick Trade broker and instrument evidence — 2026-10-25

Confirmed: AlphaForge uses Flattrade for execution and Upstox for primary ticks/contracts (checked `backend/app/runtime.py:82`, `:235-241`, `backend/app/upstox_client.py:713-726`; no broker write or authentication action was performed).

## Official API rules

| Topic | Evidence-backed result | Official source |
|---|---|---|
| Flattrade entry and protective order types | Confirmed: API v2 permits LMT and SL-LMT only, prohibits CO/BO. Market and SL-M must not be offered as available Flattrade alternatives. Documentation permission is not proof of this account accepting a resting protective order. | [Flattrade v2 announcement](https://flattrade.in/kosh/flattrade-api-implementation-of-retail-algo-standards-api-version-2-launch/), restriction list; [Pi docs](https://pi.flattrade.in/docs), PlaceOrder |
| Flattrade static IP/session | Confirmed: place/modify/cancel require registered static IP; primary IP mandatory, secondary optional, updates once per calendar week. OAuth token generation remains daily. | Same Flattrade v2 announcement, Static IP and Version 2 Adoption sections |
| Flattrade rate budgets | Confirmed: order API 10 requests/second and 40/minute; general API 40/second and 200/minute (checked current indexed official Pi documentation and local decoded reference `docs/Resources/flattrade-pi-api/reference/full-text.md:10239-10263`). These are shared budgets, not per conditional order. | [Pi docs](https://pi.flattrade.in/docs), Order API Rate Limit / API Rate Limit |
| NSE options SL-M | Confirmed: Upstox's exchange-specific notice states SL-M discontinued for index/stock options; SL-L remains available. Generic order enum is not a segment capability. | [Upstox options stop notice](https://upstox.com/announcements/risk-management/update-on-stop-loss-orders-for-options-contracts/) |
| BSE options SL-M | Confirmed: Upstox's BSE notice states SL-M discontinued in BSE equities, futures, options and currencies; SL-L remains available. | [Upstox BSE notice](https://upstox.com/announcements/updates/important-update-bse-halts-stop-loss-market-sl-m-orders/) |
| Upstox generic schema / market protection | Confirmed: V3 schema lists MARKET/LIMIT/SL/SL-M, but market_protection=0 is documented as rejected for API MARKET; default -1 applies automatic protection, custom values 1–25 are percentages. This does not override segment prohibitions or prove option SL-M acceptance. V3 supports freeze-based auto-slicing. | [Upstox Place Order V3](https://upstox.com/developer/api-documentation/v3/place-order/), request-body table |
| Upstox static IP | Confirmed: official deployment notice requires a registered static IP for API order placement and states raw market orders are prohibited, with MPP enabled. AlphaForge does not currently use Upstox for execution. | [Upstox regulatory deployment announcement](https://community.upstox.com/t/important-update-regulatory-changes-for-api-and-algo-trading-are-now-live/14874), staff opening post |
| Upstox session | Confirmed: standard access token expires at the next 03:30 AM daily boundary; the document examples explicitly handle tokens obtained before that boundary. | [Upstox Get Token](https://upstox.com/developer/api-documentation/get-token/) |
| Upstox rate budgets | Confirmed: regular order APIs combined (place/modify/cancel/multi/GTT) 10/second, 500/minute, 2,000/30 minutes; registered algorithms 50/second with same longer windows. Standard read APIs 50/second, 500/minute, 2,000/30 minutes per API/user. | [Upstox Rate Limits](https://upstox.com/developer/api-documentation/rate-limiting/) |

The Pi page's direct web open returned its JavaScript shell; its current official indexed content supplied the rate table, independently matched to the locally decoded reference. The BSE circular page could not be fetched by the web tool; the official Upstox BSE announcement above is the readable primary broker source. No third-party summaries are used as capability evidence.

## Current public master sample and application lookup

Confirmed: public Upstox NSE/BSE compressed JSON master files were downloaded read-only and parsed this session. Response Last-Modified for both was `Sun, 25 Oct 2026 00:01:29 GMT`. Selected samples and aggregated values are saved in [upstox-public-master-sample.json](upstox-public-master-sample.json). This is public Upstox metadata, **not verified** current Flattrade account SearchScrip/contract acceptance.

| Underlying | Upstox underlying / option segment | Flattrade resolver mapping | Public master lot units | Raw master tick_size | Public freeze units | Application lookup |
|---|---|---|---:|---:|---:|---|
| NIFTY 50 | `NSE_INDEX\|Nifty 50` / `NSE_FO` | `NFO`, symname `NIFTY` | 65 | 5.0 | 3510 | SearchScrip identity matching |
| BANK NIFTY | `NSE_INDEX\|Nifty Bank` / `NSE_FO` | `NFO`, symname `BANKNIFTY` | 30 | 5.0 | 1440 | SearchScrip identity matching |
| SENSEX | `BSE_INDEX\|SENSEX` / `BSE_FO` | `BFO`, symname `BSXOPT` | 20 | 5.0 | 1000 | SearchScrip identity matching |

Confirmed: these values were observed in the parsed files, not recalled from exchange constants. Raw Upstox `tick_size=5.0` is preserved as supplied; its rupee-unit conversion was **not verified** in this subtask and must not be silently treated as ₹5.00 or fed into Flattrade price rounding. Flattrade execution instead reads SearchScrip `ti` (see below). [Upstox instrument documentation](https://upstox.com/developer/api-documentation/instruments/) documents option segment, expiry, lot size, freeze quantity, tick size, strike and stable instrument key; exchange tokens may be reused after expiry.

Confirmed source behaviour:

- `backend/app/instruments.py:8-10` maps index keys; `:29-31` still hardcodes strike steps and fallback lot sizes (BANKNIFTY fallback 35 differs from the public sample 30).
- `backend/app/live/flattrade_symbol.py:62-65` maps execution segment/symname. `:378-439` queries underlying + strike, requires matching symbol-name, CE/PE, expiry and display-name strike, and rejects zero/multiple matches. `:444-474` uses positive broker `ls` authoritatively and reads broker `ti`.
- `backend/app/live/flattrade_symbol.py:265-289` defaults missing/bad/nonpositive `ti` to 0.05 rather than failing closed; no current Flattrade `ti` was queried in this subtask. NaN/Infinity validation also needs review because this helper tests only `<= 0` after float conversion.
- `backend/app/live/flattrade_symbol.py:90-105` hardcodes freeze quantities, segment, tick fallback, lot fallback, supported price types and expiry cadence. The observed public freeze values agree today; that does not make this a dynamic metadata path.
- `backend/app/live/order_builder.py:517-540` uses resolved broker lot/tick but splits quantity using the hardcoded rules freeze. Whole-lot splitting exists; dynamic freeze ingestion does not.
- `backend/app/upstox_client.py:683-697` normalization keeps expiry/strike/lot/segment but drops master tick/freeze fields; a missing lot falls back to `UNDERLYING_META`. `:713-726` fetches the current option-contract endpoint, not the public BOD file sampled above.

## Verdict and required design consequences

Confirmed: documented stop type for all requested instruments on the existing execution broker is **SL-LMT**, not SL-M. **Not verified:** actual accepted protective stop, margin treatment, trigger-pending readback, current Flattrade lot/tick metadata, LPP behaviour, and fill/cancel behaviour on this account. No live order was placed to claim any of those.

Likely: a broker SL-LMT can remain unfilled after a price gap because the triggered order retains its limit; therefore a displayed stop-based loss is a planned loss, not a guaranteed maximum. For a long option, the hard capital-at-risk figure must include full premium exposure and costs where no guaranteed execution exists.

The parent audit must determine who currently owns protection from actual guard paths and simulated fills; documentation alone cannot certify the existing Quick Trade stop. A conditional engine must persist the exact contract identity and fresh metadata, enforce shared order budgets (including emergency exits), reject missing/ambiguous metadata, reconcile acceptance/fills, and distinguish broker permission from a proven resting stop. An in-process watchdog cannot flatten while the same backend is down; an expired broker session can prevent flattening even while the watchdog runs.

Risks: if generic enum support is mistaken for segment permission, protective orders can reject and leave exposure. If hardcoded metadata changes, the engine can choose invalid quantity/prices or reject exits. If flatten is presented as guaranteed during session/backend loss, the user can rely on protection that cannot transmit.

Scope complete: official documentation and local metadata paths checked; only these evidence artifacts were written. Live broker behaviour remains **not verified**.
