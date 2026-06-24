# Trade Book

**Category:** Orders & Trades · **Type:** REST · **Method:** POST  
**Path:** `/TradeBook` · **URL:** `https://piconnect.flattrade.in/PiConnectAPI/TradeBook`  
**Source:** PDF pages 25-27

## Summary
Retrieves the user's Trade Book via a POST call. The request sends a jData JSON object (uid, actid) plus the jKey login token; on success the response is a JSON array of trade/order objects (one per fill).

## Request — Query Parameters
| Parameter | Required | Possible values | Description |
|---|---|---|---|
| jData* | yes |  | Should send json object with fields in below list. |
| jKey* | yes |  | Key Obtained on login success. |

## Request — jData JSON fields
| Field | Required | Possible values | Description |
|---|---|---|---|
| uid* | yes |  | Logged in User Id |
| actid* | yes |  | Account Id of logged in user |

## Sample request
```bash
curl --location 'https://BaseURL/TradeBook' \
--header 'Content-Type: application/json' \
--data 'jData={
    "uid": "FZ00000",
    "actid": "FZ00000",
}&jKey=GHUDWU53H32MTHPA536Q32WR'
```

## Response fields
| Field | Possible values | Description |
|---|---|---|
| stat | Ok or Not_Ok | Order book success or failure indication. |
| exch |  | Exchange Segment |
| tsym |  | Trading symbol / contract on which order is placed. |
| norenordno |  | Noren Order Number |
| qty |  | Order Quantity [If qty is junk value other than numbers]. |
| prd |  | Display product alias name, using prarr returned in user details. |
| trantype | B / S | B -> BUY, S -> SELL [transtype should be 'B' or 'S' else reject]. |
| prctyp | LMT | Price type |
| fillshares |  | Total Traded Quantity of this order |
| avgprc |  | Average trade price of total traded quantity |
| exchordid |  | Exchange Order Number |
| remarks |  | Any message Entered during order entry. |
| ret | DAY / EOS / IOC | Order validity [ret should be DAY / EOS / IOC else reject] |
| uid |  |  |
| actid |  |  |
| pp |  | Price precision |
| ti |  | Tick size |
| ls |  | Lot size |
| cstFrm |  | Custom Firm |
| fltm |  | Fill Time |
| flid |  | Fill ID |
| flqty |  | Fill Qty |
| flprc |  | Fill Price |
| ordersource |  | Order Source |
| token |  | Token |

In case of failure the response is a single JSON object with the following fields:

| Field | Possible values | Description |
|---|---|---|
| stat | Not_Ok | Order book failure indication. |
| request_time |  | Response received time. |
| emsg |  | Error message |

## Sample success response
```json
[
{
"stat": "Ok",
"norenordno": "20121300065715",
"uid": "GURURAJ",
"actid": "GURURAJ",
"exch": "NSE",
"prctyp": "LMT",
"ret": "DAY",
"prd": "M",
"flid": "102",
"fltm": "01-01-1980 00:00:00",
"trantype": "S",
"tsym": "ACCELYA-EQ",
"qty": "180",
"token": "7053",
"fillshares": "180",
"flqty": "180",
"pp": "2",
"ls": "1",
"ti": "0.05",
"prc": "800.00",
"flprc": "800.00",
"norentm": "19:59:32 13-12-2020",
"exch_tm": "00:00:00 01-01-1980",
"remarks": "WC TEST Order",
"exchordid": "6857"
},
{
"stat": "Ok",
"norenordno": "20121300065716",
"uid": "GURURAJ",
"actid": "GURURAJ",
"exch": "NSE",
"prctyp": "LMT",
"ret": "DAY",
"prd": "M",
"flid": "101",
"fltm": "01-01-1980 00:00:00",
"trantype": "B",
"tsym": "ACCELYA-EQ",
"qty": "180",
"token": "7053",
"fillshares": "180",
"flqty": "180",
"pp": "2",
"ls": "1",
"ti": "0.05",
"prc": "800.00",
"flprc": "800.00",
"norentm": "19:59:32 13-12-2020",
"exch_tm": "00:00:00 01-01-1980",
"remarks": "WC TEST Order",
"exchordid": "6858"
}
]
```

## Sample failure response
```json
{
"stat": "Not_Ok",
"request_time": "Response received time.",
"emsg": "Error message"
}
```

## Notes
- Base URL is `https://piconnect.flattrade.in/PiConnectAPI`; the curl sample uses the placeholder `https://BaseURL/TradeBook`.
- Request body is form-style: `jData={...JSON...}&jKey=...` with `Content-Type: application/json` (as shown verbatim in the doc).
- The sample request's jData has a trailing comma after the `"actid"` line (invalid strict JSON) — reproduced verbatim from the doc.
- Success response is a JSON ARRAY of trade objects (one per fill/order); each object's `stat` is `Ok`. Failure response is a single JSON object with `stat="Not_Ok"`.
- The failure JSON block is NOT printed literally in the source; it is reconstructed from the documented failure-fields table (stat/request_time/emsg). Values shown are the doc's description placeholders, not real data.
- In the response field table the cells for `uid` and `actid` are blank (no possible-value or description) in the Trade Book section — kept blank to stay faithful to the source (the "User Id" / "Account Id" wording from the Positions Book table is not carried over here).
- In the response field table, `qty`, `trantype`, and `ret` carry a trailing `*` in the image. The `*` is a required-input marker carried over from the order-entry field table; in this response context it is informational only.
- The sample success objects include keys not present in the documented response field table: `prc`, `norentm`, `exch_tm`.
- Page 27 begins the next section (POSITIONS BOOK); the Trade Book content itself spans pages 25-26. (Page 25 top also carries the tail of the previous Order Book section's failure-field table.)
