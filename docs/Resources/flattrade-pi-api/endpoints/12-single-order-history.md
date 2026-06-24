# Single Order History

**Category:** Orders & Trades · **Type:** REST · **Method:** POST  
**Path:** `/SingleOrdHist` · **URL:** `https://piconnect.flattrade.in/PiConnectAPI/SingleOrdHist`  
**Source:** PDF pages 22-25

## Summary
Retrieves the full status history (every report/event) of a single order identified by its Noren order number. Returns a JSON array of objects, one per order state transition (e.g. PendingNew, NewAck, OPEN, COMPLETE).

## Request — Query Parameters
| Parameter | Required | Possible values | Description |
|---|---|---|---|
| jData* | yes |  | Should send json object with fields in below list (the jData JSON fields). URL-encoded JSON payload. |
| jKey* | yes |  | Key Obtained on login success. |

## Request — jData JSON fields
| Field | Required | Possible values | Description |
|---|---|---|---|
| uid* | yes |  | Logged in User Id. |
| norenordno* | yes |  | Noren Order Number. |

## Sample request
```bash
curl --location 'https://BaseURL/SingleOrdHist' \
--header 'Content-Type: application/json' \
--data 'jData={"uid":"FZ00000", 
"norenordno":"20121300065716"}&jKey=GHUDWU53H32MTHPA536Q32WR'
```

## Response fields
| Field | Possible values | Description |
|---|---|---|
| stat | Ok or Not_Ok | Order book success or failure indication. |
| exch |  | Exchange Segment. |
| tsym |  | Trading symbol / contract on which order is placed. |
| norenordno |  | Noren Order Number. |
| prc* |  | Order Price [If prc is junk value other than numbers] "Order price cannot be zero". |
| qty* |  | Order Quantity [If qty is junk value other than numbers]. |
| prd |  | Display product alias name, using prarr returned in user details. |
| status |  | Order status. |
| rpt |  | Report Type (fill/complete etc). |
| trantype* | B / S | B -> BUY, S -> SELL [transtype should be 'B' or 'S' else reject]. |
| prctyp | LMT | Price type. |
| fillshares |  | Total Traded Quantity of this order. |
| avgprc |  | Average trade price of total traded quantity. |
| rejreason |  | If order is rejected, reason in text form. |
| exchordid |  | Exchange Order Number. |
| cancelqty |  | Canceled quantity for order which is in status cancelled. |
| remarks |  | Any message Entered during order entry. |
| dscqty* |  | Order disclosed quantity [If dscqty is junk value other than numbers]. |
| trgprc |  | Order trigger price. |
| ret* | DAY / EOS / IOC | Order validity [ret should be DAY / EOS / IOC else reject]. |
| uid |  | (User Id; Description cell blank in doc.) |
| actid |  | (Account Id; Description cell blank in doc.) |
| bpprc |  | Book Profit Price applicable only if product is selected as B (Bracket order). |
| blprc |  | Book loss Price applicable only if product is selected as H and B (High Leverage and Bracket order). |
| trailprc |  | Trailing Price applicable only if product is selected as H and B (High Leverage and Bracket order). |
| amo* | Yes | The message "Invalid AMO" will be displayed if the "amo" field is not sent with a "Yes" value. If amo is not required, do not send this field. |
| pp |  | Price precision. |
| ti |  | Tick size. |
| ls |  | Lot size. |
| token |  | Contract Token. |
| orddttm |  | (Order date-time; Description cell blank in doc.) |
| ordenttm |  | (Order entry time; Description cell blank in doc.) |
| extm |  | (Exchange time; Description cell blank in doc.) |

## Sample success response
```json
[
{
"stat": "Ok","norenordno": "20121300065716",
"uid": "DEMO1",
"actid": "DEMO1",
"exch": "NSE",
"tsym": "ACCELYA-EQ",
"qty": "180",
"trantype": "B",
"prctyp": "LMT",
"ret": "DAY",
"token": "7053",
"pp": "2",
"ls": "1",
"ti": "0.05",
"prc": "800.00",
"avgprc": "800.00",
"dscqty": "0",
"prd": "M",
"status": "COMPLETE",
"rpt": "Fill",
"fillshares": "180",
"norentm": "19:59:32 13-12-2020",
"exch_tm": "00:00:00 01-01-1980",
"remarks": "WC TEST Order",
"exchordid": "6858"
},
{
"stat": "Ok",
"norenordno": "20121300065716",
"uid": "DEMO1",
"actid": "DEMO1",
"exch": "NSE",
"tsym": "ACCELYA-EQ",
"qty": "180",
"trantype": "B",
"prctyp": "LMT",
"ret": "DAY",
"token": "7053",
"pp": "2",
"ls": "1",
"ti": "0.05",
"prc": "800.00",
"dscqty": "0",
"prd": "M",
"status": "OPEN",
"rpt": "New",
"norentm": "19:59:32 13-12-2020",
"exch_tm": "00:00:00 01-01-1980",
"remarks": "WC TEST Order",
"exchordid": "6858"
},
{
"stat": "Ok",
"norenordno": "20121300065716",
"uid": "DEMO1",
"actid": "DEMO1",
"exch": "NSE",
"tsym": "ACCELYA-EQ",
"qty": "180",
"trantype": "B",
"prctyp": "LMT",
"ret": "DAY",
"token": "7053",
"pp": "2",
"ls": "1",
"ti": "0.05",
"prc": "800.00",
"dscqty": "0",
"prd": "M",
"status": "PENDING",
"rpt": "PendingNew",
"norentm": "19:59:32 13-12-2020",
"remarks": "WC TEST Order"
},
{
"stat": "Ok",
"norenordno": "20121300065716",
"uid": "DEMO1",
"actid": "DEMO1",
"exch": "NSE",
"tsym": "ACCELYA-EQ",
"qty": "180",
"trantype": "B",
"prctyp": "LMT",
"ret": "DAY",
"token": "7053",
"pp": "2",
"ls": "1",
"ti": "0.05",
"prc": "800.00",
"prd": "M",
"status": "PENDING",
"rpt": "NewAck",
"norentm": "19:59:32 13-12-2020",
"remarks": "WC TEST Order"
}
]
```

## Sample failure response
```json
{
"stat": "Not_Ok",
"request_time": <Response received time>,
"emsg": <Error message>
}
```

## Notes
- Returns a JSON **array** (not a single object) — one element per order-history report/event for the given `norenordno`. The sample shows the lifecycle most-recent-first: COMPLETE/Fill, OPEN/New, PENDING/PendingNew, PENDING/NewAck.
- Request jData fields (`uid`, `norenordno`) are both mandatory.
- **Doc inconsistency (asterisks):** in the response field tables the trailing `*` markers appear on `prc`, `qty`, `trantype`, `dscqty`, `ret`, and `amo`. These are carried over verbatim from the order-placement request schema and are not meaningful for a read-only history response; they are reproduced here as documented but should not be read as "required to send".
- **Conditional response fields:** `bpprc` only when product = B (Bracket); `blprc` and `trailprc` only when product = H and B (High Leverage and Bracket); `amo` only echoed when sent as `Yes`.
- **Doc inconsistency (sample vs table):** the response-field table lists `orddttm` / `ordenttm` / `extm` (blank descriptions), but the JSON sample instead carries `norentm` and `exch_tm` and never shows `orddttm`/`ordenttm`/`extm`. The fields `uid`, `actid`, `orddttm`, `ordenttm`, `extm` all have blank Description cells in the source.
- **Failure response** is a JSON object (not an array): `stat` = `Not_Ok`, `request_time` (Response received time), `emsg` (Error message). No literal failure JSON block is printed in the source — the sample above is reconstructed from the failure field table (angle-bracket placeholders preserved).
- `https://BaseURL/` in the curl is the doc's placeholder for the real base host `https://piconnect.flattrade.in/PiConnectAPI`.
