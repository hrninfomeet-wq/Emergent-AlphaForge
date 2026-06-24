# WebSocket Order Update (Subscribe/Updates/Unsubscribe)

**Category:** WebSocket · **Type:** WebSocket · **Method:** WebSocket  
**Path:** `/PiConnectWSAPI/` · **URL:** `wss://piconnect.flattrade.in/PiConnectWSAPI/`  
**Source:** PDF pages 77-80

## Summary
WebSocket order-update feed over the Flattrade PiConnect socket. The client subscribes by sending a frame with `t="o"` plus the account id; the server then pushes order-update messages (`t="om"`) for every order event (Fill, Rejected, Canceled, etc.) on that account. There is NO acknowledgement frame for the subscribe request itself. Unsubscribe by sending a frame with `t="uo"`, which the server acknowledges with `t="uok"`.

## Request — jData JSON fields
| Field | Required | Possible values | Description |
|---|---|---|---|
| t | yes | o (subscribe) / uo (unsubscribe) | Task type. Send `o` to subscribe to order updates (`'o'` represents order update subscription task); send `uo` to unsubscribe (`'uo'` represents Unsubscribe Order update). |
| actid | no |  | Account id based on which order updated to be sent. Sent in the subscribe (`t="o"`) frame. |

## Sample request
```bash
# Subscribe Order Update

curl --location 'wss://piconnect.flattrade.in/PiConnectWSAPI/' 
--data '{
    "t": "o",  
    "actid": "FZ00000"
}'

# Unsubscribe Order Update

curl --location 'wss://piconnect.flattrade.in/PiConnectWSAPI/' 
--data '{
    "t": "uo"    
}'
```

## Response fields
| Field | Possible values | Description |
|---|---|---|
| t | om / uok | Message type. `om` represents the order-update feed push message (the doc text labels it "touchline feed" verbatim, which is a copy-paste artifact); `uok` represents the Unsubscribe Order update acknowledgement. NOTE: there is no acknowledgement for the order-update subscription (`t="o"`) request itself. |
| norenordno |  | Noren Order Number. |
| uid |  | User Id. |
| actid |  | Account ID. |
| exch |  | Exchange. |
| tsym |  | Trading symbol. |
| qty |  | Order Quantity. |
| prc |  | Order Price. |
| pcode |  | Product. |
| status | New / Replaced / Complete / Rejected etc | Order status (New, Replaced, Complete, Rejected etc). |
| reporttype | Fill / Rejected / Canceled | Order event for which this message is sent out. (Fill, Rejected, Canceled) |
| trantype | buy / sell | Order transaction type, buy or sell. |
| prctyp | LMT / SL-LMT | Order price type (LMT, SL-LMT). |
| ret | DAY / EOS / IOC... | Order Retention type [DAY / EOS / IOC...]. |
| fillshares |  | Total Filled shares for this order. |
| avgprc |  | Average fill price. |
| fltm |  | Fill Time (present only when reporttype is Fill). |
| flid |  | Fill ID (present only when reporttype is Fill). |
| flqty |  | Fill Qty (present only when reporttype is Fill). |
| flprc |  | Fill Price (present only when reporttype is Fill). |
| rejreason |  | Order rejection reason, if rejected. |
| exchordid |  | Exchange Order ID. |
| cancelqty |  | Canceled quantity, in case of canceled order. |
| remarks |  | User added tag, while placing order. |
| dscqty |  | Disclosed quantity. |
| trgprc |  | Trigger price for SL orders. |
| snonum |  | This will be present for child orders in case of cover and bracket orders, if present needs to be sent during exit. |
| snoordt |  | This will be present for child orders in case of cover and bracket orders, it will indicate whether the order is profit or stoploss. |
| blprc |  | This will be present for cover and bracket parent order. This is the differential stop loss trigger price to be entered. |
| bpprc |  | This will be present for bracket parent order. This is the differential profit price to be entered. |
| trailprc |  | This will be present for cover and bracket parent order. This is required if trailing ticks is to be enabled. |
| exch_tm | dd-mm-YYYY hh:MM:ss | This will have the exchange update time. Format: dd-mm-YYYY hh:MM:ss. |
| amo | Yes | This field will be present if the order is After Market Order. Data will be "Yes". |
| tm |  | Timestamp. |
| ntm |  | Nano Timestamp. |
| kidid |  | Kid Id. |
| sno_fillid |  | BO Sequence Id. |
| rejby |  | If an order is rejected, it will indicate from where it got rejected. |
| dname |  | Broker specific contract display name, present only if applicable. |
| handlinst | DMA / TOUCH / WO | DMA/TOUCH/WO. |
| ordentm |  | Order entry time. |
| uidc |  | UI_DEV_CODE. |
| os |  | Order Source. |
| ai |  | Algo Id. |

## Notes
This is a WebSocket (`wss://`) endpoint over the PiConnect socket, not REST; the doc illustrates the frames as JSON sent over the socket via a `curl --data` example. The order-update section spans PDF pages 77-79 (page 80 begins a separate "Subscribe Position Update" endpoint).

IMPORTANT: there is NO subscription acknowledgement frame for the order-update subscription (`t="o"`) — the server simply starts pushing `t="om"` messages. By contrast, the unsubscribe (`t="uo"`) IS acknowledged with `t="uok"`. The subscribe frame carries `actid` (account id); the unsubscribe frame carries only `t`.

The om-feed `t` row description reads "'om' represents touchline feed" verbatim in the source — a doc copy-paste artifact (it is the order-update feed). No field in either the page images or the cleaned text carries a trailing `*`, so NO field is doc-marked mandatory; the required flag on `t` is inferred (the frame is meaningless without it) and `actid` is required only for the subscribe frame.

Several order-update (om) fields are conditional: `fltm`/`flid`/`flqty`/`flprc` are present only when reporttype is Fill; `cancelqty` appears only for canceled orders; `trgprc` only for SL orders; `snonum`/`snoordt` only for cover/bracket child orders; `blprc`/`trailprc` for cover & bracket parent orders; `bpprc` for bracket parent order; `amo` present only for After Market Orders (value "Yes"); `dname` present only if applicable. `exch_tm` time format is dd-mm-YYYY hh:MM:ss.

The reporttype "Canceled" and status spellings ("Canceled", "Replaced", "Complete") are reproduced verbatim from the doc. No sample success/failure JSON response bodies are provided in the doc for this WebSocket section (only the curl request frames are shown).
