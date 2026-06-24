# Order Book

**Category:** Orders & Trades · **Type:** REST · **Method:** POST  
**Path:** `/OrderBook` · **URL:** `https://piconnect.flattrade.in/PiConnectAPI/OrderBook`  
**Source:** PDF pages 17-20

## Summary
Retrieves the user's order book (all orders placed for the trading day) via a POST call. The request sends a jData JSON object (uid required, optional prd) plus the login jKey, and on success returns a JSON array of order objects with full per-order detail (symbol, price, qty, status, validity, etc.).

## Request — Query Parameters
| Parameter | Required | Possible values | Description |
|---|---|---|---|
| jData* | yes | | Should send json object with fields in below list. |
| jKey* | yes | | Key Obtained on login success. |

## Request — jData JSON fields
| Field | Required | Possible values | Description |
|---|---|---|---|
| uid* | yes | | Logged in User Id. |
| prd | no | H / M / ... | Product name. |

## Sample request
```bash
curl --location 'https://BaseURL/OrderBook' \
--header 'Content-Type: application/json' \
--data 'jData={
    "uid": "FZ00000"
}“&jKey=GHUDWU53H32MTHPA536Q32WR'
```

## Response fields
| Field | Possible values | Description |
|---|---|---|
| stat | Ok or Not_Ok | Order book success or failure indication. |
| exch | | Exchange Segment. |
| tsym | | Trading symbol / contract on which order is placed. |
| norenordno | | Noren Order Number. |
| prc* | | Order Price [If prc is junk value other than numbers] "Order price cannot be zero". |
| qty* | | Order Quantity [If qty is junk value other than numbers]. |
| prd | | Display product alias name, using prarr returned in user details. |
| status | | Order status. |
| trantype* | B / S | B -> BUY, S -> SELL [transtype should be 'B' or 'S' else reject]. |
| prctyp | LMT | Price type. |
| fillshares | | Total Traded Quantity of this order. |
| avgprc | | Average trade price of total traded quantity. |
| rejreason | | If order is rejected, reason in text form. |
| exchordid | | Exchange Order Number. |
| cancelqty | | Canceled quantity for order which is in status cancelled. |
| remarks | | Any message Entered during order entry. |
| dscqty* | | Order disclosed quantity [If dscqty is junk value other than numbers]. |
| trgprc | | Order trigger price. |
| ret* | DAY / EOS / IOC | Order validity [ret should be DAY / EOS / IOC else reject]. |
| uid | | |
| actid | | |
| bpprc | | Book Profit Price applicable only if product is selected as B (Bracket order). |
| blprc | | Book loss Price applicable only if product is selected as H and B (High Leverage and Bracket order). |
| trailprc | | Trailing Price applicable only if product is selected as H and B (High Leverage and Bracket order). |
| amo* | Yes | The message "Invalid AMO" will be displayed if the "amo" field is not sent with a "Yes" value. If amo is not required, do not send this field. |
| pp | | Price precision. |
| ti | | Tick size. |
| ls | | Lot size. |
| token | | Contract Token. |
| orddttm | | |
| ordenttm | | |
| extm | | |
| snoordt | | 0 for profit leg and 1 for stoploss leg. |
| snonum | | This field will be present for product H and B; and only if it is profit/sl order. |
| dname | | Broker specific contract display name. |
| rorgqty | | To be used in get margin from modify window. |
| rorgprc | | To be used in get margin from modify window. |
| orgtrgprc | | To be used in get margin from modify window, for H/B products only. |
| orgblprc | | To be used in get margin from modify window, for H/B products only. |
| algo_name | | Algo Name. |
| C | CUST_FIRM_C | |

## Sample success response
```json
[
{
“stat” : “Ok”,
“exch” : “NSE” ,
“tsym” : “ACC-EQ” ,
“norenordno” : “20062500000001223”,
“prc” : “127230”,
“qty” : “100”,
“prd” : “C”,
“status”: “Open”,
“trantype” : “B”,
“prctyp” : ”LMT”,
“fillshares” : “0”,
“avgprc” : “0”,
“exchordid” : “250620000000343421”,
“uid” : “VIDYA”,
“actid” : “CLIENT1”,
“ret” : “DAY”,
“amo” : “Yes”
},
{
“stat” : “Ok”,
“exch” : “NSE” ,
“tsym” : “ABB-EQ” ,
“norenordno” : “20062500000002543”,
“prc” : “127830”,
“qty” : “50”,
“prd” : “C”,
“status”: “REJECT”,
“trantype” : “B”,
“prctyp” : ”LMT”,
“fillshares” : “0”,
“avgprc” : “0”,
“rejreason” : “Insufficient funds”
“uid” : “VIDYA”,
“actid” : “CLIENT1”,
“ret” : “DAY”,
“amo” : “No”
}
]
```

## Sample failure response
```json
{
"stat":"Not_Ok",
"emsg":"Session Expired : Invalid Session Key"
}
```

## Notes
- Two response shapes are documented: on success the body is a JSON **array** of order objects (not a single object); on failure it is a single JSON object with `stat=Not_Ok` plus `request_time` and `emsg` (Error message).
- The curl example uses a placeholder host `https://BaseURL/OrderBook`; the real production URL is `https://piconnect.flattrade.in/PiConnectAPI/OrderBook`.
- The source curl has a stray smart-quote typo: `}“&jKey=` — the `}` should be followed by a normal straight-quote `'` closing the `--data` string before `&jKey` (reproduced verbatim above).
- The request body has only two fields: `uid` (mandatory, shown as `uid*`) and `prd` (optional, possible values `H / M / ...`).
- Several response fields' mandatory asterisks (`prc*`, `qty*`, `trantype*`, `dscqty*`, `ret*`, `amo*`) are carried over from the order-placement field table; in this read-only order-book response they indicate fields normally present rather than caller-supplied requirements.
- Conditional response fields: `bpprc` only when product=B (Bracket); `blprc`/`trailprc` only when product=H or B (High Leverage / Bracket); `snonum` and `orgtrgprc`/`orgblprc` only for H/B products.
- `amo`: an "Invalid AMO" message results if `Yes` is not sent — omit the field entirely when AMO is not required.
- The trailing row `C / CUST_FIRM_C` at the top of page 20 is an isolated key/value pair with no description; its role (a literal field named `C`) is ambiguous but transcribed as shown.
- The sample success JSON uses curly/smart quotes (`“ ”`) rather than straight ASCII quotes, and the second object is missing a comma after the `rejreason` line (both reproduced verbatim from the source).
