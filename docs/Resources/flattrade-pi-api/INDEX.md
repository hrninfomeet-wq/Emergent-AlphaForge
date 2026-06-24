# Flattrade *pi* API — endpoint index

58 sections decoded from the source PDF. ✅ = vision-verified (cross-checked against the page render). ⚠️ = raw text slice pending verification.

| # | Endpoint | Category | Type | Method | Path | Pages | Status |
|---|---|---|---|---|---|---|---|
| 1 | [Introduction & App Registration](endpoints/01-introduction-app-registration.md) | Setup | Info |  | `/` | 1-4 | ✅ verified |
| 2 | [Login Flow & Session Token](endpoints/02-login-flow-session-token.md) | Setup | Info | POST | `/trade/apitoken` | 5-7 | ✅ verified |
| 3 | [Postman Collections](endpoints/03-postman-collections.md) | Setup | Info |  | `/` | 7 | ✅ verified |
| 4 | [Place Order](endpoints/04-place-order.md) | Orders & Trades | REST | POST | `/PlaceOrder` | 7-9 | ✅ verified |
| 5 | [Modify Order](endpoints/05-modify-order.md) | Orders & Trades | REST | POST | `/ModifyOrder` | 10-11 | ✅ verified |
| 6 | [Cancel Order](endpoints/06-cancel-order.md) | Orders & Trades | REST | POST | `/CancelOrder` | 11-12 | ✅ verified |
| 7 | [Exit SNO Order](endpoints/07-exit-sno-order.md) | Orders & Trades | REST | POST | `/ExitSNOOrder` | 12-13 | ✅ verified |
| 8 | [Order Margin](endpoints/08-order-margin.md) | Orders & Trades | REST | POST | `/GetOrderMargin` | 13-15 | ✅ verified |
| 9 | [Basket Margin](endpoints/09-basket-margin.md) | Orders & Trades | REST | POST | `/GetBasketMargin` | 15-17 | ✅ verified |
| 10 | [Order Book](endpoints/10-order-book.md) | Orders & Trades | REST | POST | `/OrderBook` | 17-20 | ✅ verified |
| 11 | [Multi Leg Order Book](endpoints/11-multi-leg-order-book.md) | Orders & Trades | REST | POST | `/MultiLegOrderBook` | 20-22 | ✅ verified |
| 12 | [Single Order History](endpoints/12-single-order-history.md) | Orders & Trades | REST | POST | `/SingleOrdHist` | 22-25 | ✅ verified |
| 13 | [Trade Book](endpoints/13-trade-book.md) | Orders & Trades | REST | POST | `/TradeBook` | 25-27 | ✅ verified |
| 14 | [Positions Book](endpoints/14-positions-book.md) | Orders & Trades | REST | POST | `/PositionBook` | 27-29 | ✅ verified |
| 15 | [Product Conversion](endpoints/15-product-conversion.md) | Orders & Trades | REST | POST | `/ProductConversion` | 29-30 | ✅ verified |
| 16 | [Place GTT Order](endpoints/16-place-gtt-order.md) | Orders & Trades | REST | POST | `/PlaceGTTOrder` | 30-31 | ✅ verified |
| 17 | [Modify GTT Order](endpoints/17-modify-gtt-order.md) | Orders & Trades | REST | POST | `/ModifyGTTOrder` | 31-33 | ✅ verified |
| 18 | [Cancel GTT Order](endpoints/18-cancel-gtt-order.md) | Orders & Trades | REST | POST | `/CancelGTTOrder` | 33-34 | ✅ verified |
| 19 | [Get Pending GTT Order](endpoints/19-get-pending-gtt-order.md) | Orders & Trades | REST | POST | `/GetPendingGTTOrder` | 33-35 | ✅ verified |
| 20 | [Get Enabled GTTs](endpoints/20-get-enabled-gtts.md) | Orders & Trades | REST | POST | `/GetEnabledGTTs` | 35-36 | ✅ verified |
| 21 | [Place OCO Order](endpoints/21-place-oco-order.md) | Orders & Trades | REST | POST | `/PlaceOCOOrder` | 36-38 | ✅ verified |
| 22 | [Modify OCO Order](endpoints/22-modify-oco-order.md) | Orders & Trades | REST | POST | `/ModifyOCOOrder` | 38-40 | ✅ verified |
| 23 | [Cancel OCO Order](endpoints/23-cancel-oco-order.md) | Orders & Trades | REST | POST | `/CancelOCOOrder` | 40-41 | ✅ verified |
| 24 | [Holdings](endpoints/24-holdings.md) | Holdings & Limits | REST | POST | `/Holdings` | 40-42 | ✅ verified |
| 25 | [Limits](endpoints/25-limits.md) | Holdings & Limits | REST | POST | `/Limits` | 42-47 | ✅ verified |
| 26 | [Get Index List](endpoints/26-get-index-list.md) | Market Info | REST | POST | `/GetIndexList` | 47-48 | ✅ verified |
| 27 | [Get Top List Names](endpoints/27-get-top-list-names.md) | Market Info | REST | POST | `/TopListName` | 48-50 | ✅ verified |
| 28 | [Get Top List](endpoints/28-get-top-list.md) | Market Info | REST | POST | `/TopList` | 50-51 | ✅ verified |
| 29 | [Get Time Price Data](endpoints/29-get-time-price-data.md) | Market Info | REST | POST | `/TPSeries` | 51-53 | ✅ verified |
| 30 | [Get EOD Chart Data](endpoints/30-get-eod-chart-data.md) | Market Info | REST | POST | `/EODChartData` | 53-54 | ✅ verified |
| 31 | [Get Option Chain](endpoints/31-get-option-chain.md) | Market Info | REST | POST | `/GetOptionChain` | 53-55 | ✅ verified |
| 32 | [Get Option Greek](endpoints/32-get-option-greek.md) | Market Info | REST | POST | `/GetOptionGreek` | 55-56 | ✅ verified |
| 33 | [Get Exchange Msg](endpoints/33-get-exchange-msg.md) | Market Info | REST | POST | `/ExchMsg` | 56-57 | ✅ verified |
| 34 | [Get Broker Msg](endpoints/34-get-broker-msg.md) | Market Info | REST | POST | `/GetBrokerMsg` | 57-58 | ✅ verified |
| 35 | [Span Calculator](endpoints/35-span-calculator.md) | Market Info | REST | POST | `/SpanCalc` | 58-59 | ✅ verified |
| 36 | [Set Alert](endpoints/36-set-alert.md) | Alerts | REST | POST | `/SetAlert` | 59-60 | ✅ verified |
| 37 | [Cancel Alert](endpoints/37-cancel-alert.md) | Alerts | REST | POST | `/CancelAlert` | 60-61 | ✅ verified |
| 38 | [Modify Alert](endpoints/38-modify-alert.md) | Alerts | REST | POST | `/ModifyAlert` | 61-62 | ✅ verified |
| 39 | [Get Pending Alert](endpoints/39-get-pending-alert.md) | Alerts | REST | POST | `/GetPendingAlert` | 62-63 | ✅ verified |
| 40 | [Get Enabled Alert Types](endpoints/40-get-enabled-alert-types.md) | Alerts | REST | POST | `/GetEnabledAlertTypes` | 63-64 | ✅ verified |
| 41 | [Funds](endpoints/41-funds.md) | Funds | REST | POST | `/GetMaxPayoutAmount` | 64-65 | ✅ verified |
| 42 | [Funds Payout Request](endpoints/42-funds-payout-request.md) | Funds | REST | POST | `/FundsPayOutReq` | 65-66 | ✅ verified |
| 43 | [Get Payin Report](endpoints/43-get-payin-report.md) | Funds | REST | POST | `/GetPayinReport` | 65-66 | ✅ verified |
| 44 | [Get Payout Report](endpoints/44-get-payout-report.md) | Funds | REST | POST | `/GetPayoutReport` | 66-67 | ✅ verified |
| 45 | [Cancel Payout](endpoints/45-cancel-payout.md) | Funds | REST | POST | `/CancelPayout` | 67-68 | ✅ verified |
| 46 | [WebSocket General Guidelines & Connect](endpoints/46-websocket-general-guidelines-connect.md) | WebSocket | WebSocket | WebSocket | `/PiConnectWSAPI/` | 68-69 | ✅ verified |
| 47 | [WebSocket Touchline (Subscribe/Updates/Unsubscribe)](endpoints/47-websocket-touchline-subscribe-updates-unsubscribe.md) | WebSocket | WebSocket | WebSocket | `/PiConnectWSAPI/` | 69-72 | ✅ verified |
| 48 | [WebSocket Market Depth (Subscribe/Updates/Unsubscribe)](endpoints/48-websocket-market-depth-subscribe-updates-unsubscribe.md) | WebSocket | WebSocket | WebSocket | `/PiConnectWSAPI/` | 72-77 | ✅ verified |
| 49 | [WebSocket Order Update (Subscribe/Updates/Unsubscribe)](endpoints/49-websocket-order-update-subscribe-updates-unsubscribe.md) | WebSocket | WebSocket | WebSocket | `/PiConnectWSAPI/` | 77-80 | ✅ verified |
| 50 | [WebSocket Position Update (Subscribe/Updates/Unsubscribe)](endpoints/50-websocket-position-update-subscribe-updates-unsubscribe.md) | WebSocket | WebSocket | WebSocket | `/PiConnectWSAPI/` | 80-83 | ✅ verified |
| 51 | [WebSocket Heartbeat](endpoints/51-websocket-heartbeat.md) | WebSocket | WebSocket | WebSocket | `/PiConnectWSAPI/` | 83 | ✅ verified |
| 52 | [User Details](endpoints/52-user-details.md) | Account & Reference | REST | POST | `/UserDetails` | 83-85 | ✅ verified |
| 53 | [Search Scrips](endpoints/53-search-scrips.md) | Account & Reference | REST | POST | `/SearchScrip` | 85-86 | ✅ verified |
| 54 | [Get Quotes](endpoints/54-get-quotes.md) | Account & Reference | REST | POST | `/GetQuotes` | 86-89 | ✅ verified |
| 55 | [Postback / Webhook](endpoints/55-postback-webhook.md) | Account & Reference | Info | POST (push from Flattrade to your endpoint) | `(user-configured postback/webhook URL endpoint)` | 89-91 | ✅ verified |
| 56 | [Scrip Master](endpoints/56-scrip-master.md) | Account & Reference | Info |  |  | 91-92 | ✅ verified |
| 57 | [API Rate Limits](endpoints/57-api-rate-limits.md) | Account & Reference | Info |  |  | 92 | ✅ verified |
| 58 | [Change Log](endpoints/58-change-log.md) | Account & Reference | Info |  |  | 92-93 | ✅ verified |

## Machine-readable catalog
`catalog.json` holds the structured spec (params, required flags, possible values, response fields, sample request/response) for every verified endpoint.

## Full decoded text
`reference/full-text.md` — all 93 pages, boilerplate stripped.
