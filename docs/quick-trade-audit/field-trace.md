# Quick Trade field trace — 2026-10-25

Confirmed: this is a source/isolated-execution audit of the dirty working tree, not a certification of running-image parity or live broker behaviour. No broker order, mode change, database write, or application source edit was made by this audit worker.

## Actual execution chain

`cockpit/QuickTrade.jsx:24` mounts `LiveOrderTicket` with `disabled=false`. The supplied `mode` prop does not choose paper execution. `LiveOrderTicket.jsx:309-312` sends the ticket to preview; `:336` creates an approval; final confirmation at `:383` sets `LIVE_TEST`, then `:392` redeems the approval. This is real execution, not a paper-mode ticket.

Backend: `routers/live_broker.py:1197-1210` request schema → `:1213-1300` broker SearchScrip adapter + `validate_and_build` + margin preview → `:1331-1365` approval → `:1389-1447` consume/recheck → `:2142-2235` manual placement → `live/executor.py:335-348` **build_intent** (not validate_and_build) → `:404` transmit/arm. Approval comparison checks resolved identity, quantity, price, product and type again before transmission. `live/manual_ticket.py:9-24` permits only one-lot BUY/MIS/LIMIT with absent/default 0.5% buffer. Other tickets can preview but cannot be approved.

## Every editable field

All rows are Confirmed against the cited code. File prefix for UI is `frontend/src/components/live/LiveOrderTicket.jsx`; builder is `backend/app/live/order_builder.py`; router is `backend/app/routers/live_broker.py`.

| UI label | UI default / units | Sent value and validation | Effective broker / guard behaviour | Evidence |
|---|---|---|---|---|
| Underlying | NIFTY; enum NIFTY/BANKNIFTY/SENSEX | `underlying`; normalized uppercase in router; resolver allow-list | Resolves NFO for NIFTY/BANKNIFTY, BFO for SENSEX; ultimately `exch`, `tsym` | UI 32,54,504-522,269; router 1225,1233-1238; flattrade_symbol.py 63-67 |
| Strike | blank; underlying index strike points | `strike: Number(strike)`; required nonempty UI; resolver requires positive strike and exact contract match | Embedded in matched broker `tsym`; no separate strike field | UI 55,270,291-297,525-542; flattrade_symbol.py 359-365,420-426 |
| ATM button | no stored rule | Calls ATM suggestion immediately; copies returned strike, expiry and premium into fields | Fixed chosen contract is previewed/placed; no trigger-time resolution | UI 181-198 |
| Type | CE | `option_type`; CE/PE select; mapped to resolver `side`; resolver checks CE/PE | Matched broker `tsym` determines CE/PE | UI 56,271,565-593; router 1436; flattrade_symbol.py 367-369 |
| Side | Buy / B; Sell visible disabled | `side`; builder allows B/S but manual capability + executor only B | `trantype=B`; no short entry from this panel | UI 58,272,595-635; manual_ticket.py 12; executor.py 302-303 |
| Expiry (YYYY-MM-DD) | blank; calendar date | `expiry_date`; UI nonempty/date input; resolver ISO parse plus exact SearchScrip expiry match | Selected contract's `tsym`; no separate expiry field | UI 57,273,638-653; flattrade_symbol.py 192-215,413-418 |
| Order Type | LIMIT; options LIMIT and SL-LMT from rules | `order_type`; MARKET removed in UI and denied by rules; SL-LMT preview only | Live path always `prctyp=LMT`; preview SL-LMT adds `trgprc` | UI 59,132,275,656-671; builder 375,465-483; manual_ticket.py 15; executor.py 339 |
| Product | MIS; NRML also selectable | `product`; preview rules allow MIS/NRML; approval only MIS | Live `prd=I`; preview NRML maps `prd=M` | UI 60,276,674-689; builder 378; manual_ticket.py 14 |
| Lots | 1; whole lots | `lots:Number(lots)`; UI finite positive integer; API int; preview positive int and client-sent fat-finger cap 50; approval only 1 | Actual `qty` is 1 × broker SearchScrip `ls`, serialized as string; preview splits at static freeze cap | UI 49,61,274,285,291-297,692-707; builder 522-545; executor.py 340,384 |
| Ref LTP (premium ₹) | blank; rupees per option unit (premium points), NOT spot | `ref_ltp:parseFloat(refLtp)`; UI finite/nonempty, builder finite positive. Editable manually; Fetch can use live tick or last candle. No fresh-quote timestamp binds the reference to placement | Used to compute broker `prc`, margin estimate, and initial guard stop/target. It is NOT the observed entry fill | UI 62,218-250,277,710-755; builder 256-269,280-288; router 367-381 |
| Band % | 3%; percentage deviation from **entered reference premium** | `band_pct`; finite nonnegative preserved; blank/negative/nonfinite becomes 5%; safety check requires finite ≥0 | Not sent as broker field. Entry cross buffer is fixed 0.5%; effective buffer=min(0.5, band). BUY price rounded up to tick; final price checked against band | UI 50,63,281-287,764-778; builder 277-288,343; safety.py 72-101 |
| Stop % (guard), or Stop % (SL trigger + guard) | 50%; percentage of **reference premium**, not spot and initially not fill | `levels.stop_pct`; finite >0 preserved; zero/negative/blank/nonfinite becomes 50. No upper bound; backend `levels` is untyped dict | Live LIMIT carries no stop field. App guard starts at max(₹0.05, ref×(1-stop/100)). For preview SL-LMT only, this becomes broker `trgprc`; manual SL-LMT placement is denied | UI 64-66,260-265,781-799; router 364-381; builder 587-606; execution_policy.py 45-84 |
| Target % (optional) | blank; percentage gain from **reference premium** | Finite >0 adds `levels.target_pct`; zero/negative/blank/nonfinite omitted | No broker target/OCO field; app monitor target=ref×(1+target/100) | UI 67,262-264,801-816; router 371; execution_policy.py 64-71 |

Hidden/noneditable values: `fat_finger_cap=50` for previews, `buffer_pct=0.5`; manual route pins cap=1, buffer=0.5. Broker payload also carries `ordersource=API`, account identity, `dscqty=0`, `ret=DAY`, and generated `remarks`/client identity (`broker_protocol.py:78-89`). UI quantity, estimated premium, lot/freeze/tick/cadence chips and payoff chart are derived displays, not extra order inputs. No time window, trailing, profit-lock, underlying condition, explicit paper selector, or forced-square time input exists.

## Behaviour executed in isolation

Command: `.venv/Scripts/python.exe -m pytest tests/test_manual_ticket_contract.py tests/test_live_validate_and_build.py -q -p no:cacheprovider`

Observed: **59 passed in 0.89s**. These exercise unsupported approval blocks, resolved-field drift refusal, freeze splitting, tick prices and source-free numerical builder calls. They do not certify broker acceptance.

Additional Python execution imported `validate_and_build`, `build_monitor_state`, and the existing canned SearchScrip fixture. Output:

```text
ref=200, band=3: LMT BUY MIS NFO qty=65 prc=201, no trigger; all verdicts passed.
ref=200, band=0: LMT BUY MIS NFO qty=65 prc=200, no trigger; all verdicts passed.
ref=200, band=0.2: rejected price_band: price 200.4000 is 0.20% off reference 200.0000 (limit 0.2%).
ref=200, stop=50,target=10: initial_stop=100, stop_level=100, target_level=220.
ref=200, stop=100,target=10: initial_stop=0.05, target_level=220.
ref=200, stop=150,target=10: initial_stop=0.05, target_level=220.
BUY SL-LMT preview, ref=200,stop=50: trgprc=100, prc=100.05; approval capability=false.
```

Arithmetic independently checked from the definition: 200×(1+0.5/100)=201; 200×(1−50/100)=100; 200×(1+10/100)=220. The band=0.2 failure is binary-float comparison at `safety.py:96-97`, not a broker rejection.

## Findings for parent review

| Severity | Finding / evidence | Proposed correction |
|---|---|---|
| Sev-1 design dependency | Initial stop and target derive from editable/stale ref, not fill; guard later records `daybuyavgprc` and changes `state.entry` for trailing, while deliberately retaining absolute stop/target (`live_position_guard.py:821-851`). Actual scope for severe harm depends on stale reference and fill gap; builder trace proves levels use ref. | Conditional engine must persist order-specific fill VWAP and quantity; initialize protection from actual fills while preserving any tighter active stop. Do not use aggregate daybuyavgprc as an order-specific fill when multiple entries share a contract. Parent should reproduce full guard path before any patch. |
| Sev-2 | BUY SL-LMT preview creates trigger below reference using a sell-stop formula; actual placement blocked. Executed ref200/stop50 gives trigger100, limit100.05. | Remove unsupported entry type or define separate buy-stop-entry threshold semantics; retain capability gate. |
| Sev-2 | Exact band boundary can falsely reject; executed 200.40 vs 200 with 0.2% band fails. | Compare monetary boundaries using decimal/tick arithmetic; add exact-boundary regression. |
| Sev-2 | Stop input ≥100% accepted and becomes ₹0.05; UI no maximum. Executed 100% and150% both yield floor. Blank/zero becomes 50%. | Explicitly validate bounded long-premium stop and display normalized value before any approval. |
| Sev-2 | Static UI rules are not instrument-master-derived: exchange/segment, freeze quantity and lot-display constants in flattrade_symbol.py:63-105. Actual order lot is broker `ls`; tick silently defaults if missing/invalid (`:268-288`). | Use versioned master metadata with freshness and fail closed when required metadata absent; distinguish display estimates from resolved quantity. |
| Sev-3 | Quick Trade badge says “real money · 1-click” though current code requires preview, prepare and final confirmation (`QuickTrade.jsx:16`; `LiveOrderTicket.jsx:979-1015`). | Rename badge to supervised live entry. |

Confirmed: ref price is not independently refreshed in `place_live_test_order` (read function 233-418); its only broker read before submission is limits, with symbol search performed by the route. Thus Band does not constrain deviation from a current independent market quote.

Not verified: rendered running UI, live SearchScrip metadata, live broker SL acceptance, production market-hours behaviour, complete simulated tick lifecycle, running source parity. These belong to the parent's integration/safety audit.
