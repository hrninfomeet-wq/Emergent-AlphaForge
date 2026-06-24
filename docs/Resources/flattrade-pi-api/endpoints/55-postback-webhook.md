# Postback / Webhook

**Category:** Account & Reference · **Type:** Info · **Method:** POST (push from Flattrade to your endpoint)  
**Path:** `(user-configured postback/webhook URL endpoint)` · **URL:** `(your registered postback URL endpoint)`  
**Source:** PDF pages 89-91

## Summary
Describes the order-update postback (webhook) that Flattrade pushes to your configured endpoint: you receive order updates for orders placed through the API. Each update is delivered as a JSON payload describing the current state/event of the order (new, fill, rejected, cancelled, etc.), including a SHA256 checksum so you can verify the message is genuinely from Flattrade and not a third party.

## Response fields
| Field | Possible values | Description |
|---|---|---|
| norenordno | | Noren Order Number |
| uid | | User Id |
| actid | | Account ID |
| exch | | Exchange |
| tsym | | Trading symbol |
| qty* | | Order Quantity [If qty is junk value other than numbers]. |
| prc* | | Order Price [If prc is junk value other than numbers] "Order price cannot be zero". |
| prd | | Product |
| status | | Order status (New, Replaced, Complete, Rejected etc) |
| reporttype | | Order event for which this message is sent out. (Fill, Rejected, Canceled) |
| trantype* | B / S | B -> BUY, S -> SELL [transtype should be 'B' or 'S' else reject]. |
| prctyp | | Order price type (LMT, SL-LMT) |
| ret* | DAY / EOS / IOC | Order Retention type [ret should be DAY / EOS / IOC else reject]. |
| fillshares | | Total Filled shares for this order |
| avgprc | | Average fill price |
| fltm | | Fill Time (present only when reporttype is Fill) |
| flid | | Fill ID (present only when reporttype is Fill) |
| flqty | | Fill Qty (present only when reporttype is Fill) |
| flprc | | Fill Price (present only when reporttype is Fill) |
| rejreason | | Order rejection reason, if rejected |
| exchordid | | Exchange Order ID |
| cancelqty | | Canceled quantity, in case of canceled order |
| remarks | | User added tag, while placing order |
| dscqty* | | Disclosed quantity [If dscqty is junk value other than numbers]. |
| trgprc | | Trigger price for SL orders |
| snonum | | This will be present for child orders in case of cover and bracket orders, if present needs to be sent during exit |
| snoordt | | This will be present for child orders in case of cover and bracket orders, it will indicate whether the order is profit or stoploss |
| blprc | | This will be present for cover and bracket parent order. This is the differential stop loss trigger price to be entered. |
| bpprc | | This will be present for bracket parent order. This is the differential profit price to be entered. |
| trailprc | | This will be present for cover and bracket parent order. This is required if trailing ticks is to be enabled. |
| exch_tm | | This will have the exchange update time. Format: dd-mm-YYYY hh:MM:SS |
| amo* | Yes | The message "Invalid AMO" will be displayed if the "amo" field is not sent with a "Yes" value. If amo is not required, do not send this field. |
| tm | | TimeStamp |
| kidid | | Kid Id |
| sno_fillid | | BO Sequence Id |
| checksum | | sha256 [ noren_order_num + noren_time_stamp + vendor_key ] CheckSum. (Make sure checksum matches to avoid any third party sending false order updates to your url endpoint) |

## Sample success response
```json
{
    "norenordno":"23010500000376",
    "kidid":"1",
    "uid":"ASHWATHINV123",
    "actid":"ASHWATHINV",
    "exch":"NSE","tsym":"ACC-EQ",
    "qty":"1",
    "rorgqty":"0",
    "ipaddr":"117.248.82.174",
    "ordenttm":"1672921211",
    "sno_fillid":"",
    "trantype":"B",
    "prctyp":"LMT",
    "ret":"DAY",
    "amo":"Yes",
    "token":"22",
    "prc":"2500.00",
    "pcode":"C",
    "remarks":"",
    "status":"OPEN",
    "rpt":"New",
    "ls":"1",
    "ti":"0.05",
    "rprc":"2500.00",
    "dscqty":"0",
    "norentm":"17:50:11 05-01-2023",
    "checksum":"619521a541ff3e634ecb02147f0cb77e822ea415c9b79259cd5e40592a73b810"
  }
```

## Notes
- This is an Info section: it documents an inbound webhook/postback, not a callable REST endpoint. Flattrade sends order updates to your endpoint for orders placed through the API. The HTTP method and exact URL are not printed in the doc — Flattrade POSTs the JSON payload to the user-registered postback URL endpoint (method/url inferred).
- Only three fields have a dedicated "Possible value" column cell in the page image: trantype* (B / S), ret* (DAY / EOS / IOC), and amo* (Yes). The enums shown for status (New, Replaced, Complete, Rejected etc), reporttype (Fill, Rejected, Canceled), and prctyp (LMT, SL-LMT) appear only inline in their Description text, not in the Possible value column.
- Required fields (trailing '*' in the table): qty*, prc*, trantype*, ret*, dscqty*, amo*.
- Validation/reject rules embedded in field descriptions: qty/prc/dscqty must be numeric (junk non-number values are flagged); prc cannot be zero; trantype must be 'B' or 'S' else reject; ret must be DAY/EOS/IOC else reject; amo must be sent with value 'Yes' (omit the field entirely if AMO not required, else "Invalid AMO").
- SECURITY: verify each postback with the checksum = sha256 of (noren_order_num + noren_time_stamp + vendor_key). The doc explicitly warns to confirm the checksum matches to avoid third parties sending false order updates to your URL endpoint.
- Fill-only fields (present only when reporttype is Fill): fltm, flid, flqty, flprc.
- Cover/Bracket order fields: snonum, snoordt (child orders); blprc (differential stop-loss trigger, CO/BO parent), bpprc (differential profit price, BO parent), trailprc (trailing ticks, CO/BO parent). snonum if present must be sent during exit.
- exch_tm format: dd-mm-YYYY hh:MM:SS.
- The sample payload contains extra fields not in the field table (rorgqty, ipaddr, ordenttm "1672921211" = Unix epoch seconds, token, pcode, rpt "New", ls = lot size, ti = tick size, rprc, norentm "17:50:11 05-01-2023").
- No explicit failure/error JSON sample is given for the postback (this is a push notification, not a request/response endpoint).
- Section spans PDF pages 89-91; page 89 also continues a preceding section's field table (bo2..e_date — NOT part of Postback/Webhook), and page 91 ends where the next section SCRIP MASTER begins.
