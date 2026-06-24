# Multi Leg Order Book

**Category:** Orders & Trades · **Type:** REST · **Method:** POST  
**Path:** `/MultiLegOrderBook` · **URL:** `https://piconnect.flattrade.in/PiConnectAPI/MultiLegOrderBook`  
**Source:** PDF pages 20-22

## Summary
Retrieves the Multi Leg Order Book via a POST call. On success returns a JSON array of order objects (covering single, two-leg / 2L and three-leg / 3L orders) with per-leg trading symbols, quantities, prices, traded quantities and average trade prices; on failure returns a JSON object with `stat`, `request_time` and `emsg`.

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
curl --location 'https://BaseURL/MultiLegOrderBook' \
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
| uid | | (Blank description in the source.) |
| actid | | (Blank description in the source.) |
| bpprc | | Book Profit Price applicable only if product is selected as B (Bracket order). |
| blprc | | Book loss Price applicable only if product is selected as H and B (High Leverage and Bracket order). |
| trailprc | | Trailing Price applicable only if product is selected as H and B (High Leverage and Bracket order). |
| amo* | Yes | The message "Invalid AMO" will be displayed if the "amo" field is not sent with a "Yes" value. If amo is not required, do not send this field. |
| pp | | Price precision. |
| ti | | Tick size. |
| ls | | Lot size. |
| tsym2 | | Trading symbol of second leg, mandatory for price type 2L and 3L. |
| trantype2 | | Transaction type of second leg, mandatory for price type 2L and 3L. |
| qty2 | | Quantity for second leg, mandatory for price type 2L and 3L. |
| prc2 | | Price for second leg, mandatory for price type 2L and 3L. |
| tsym3 | | Trading symbol of third leg, mandatory for price type 3L. |
| trantype3 | | Transaction type of third leg, mandatory for price type 3L. |
| qty3 | | Quantity for third leg, mandatory for price type 3L. |
| prc3 | | Price for third leg, mandatory for price type 3L. |
| fillshares2 | | Total Traded Quantity of 2nd Leg. |
| avgprc2 | | Average trade price of total traded quantity for 2nd leg. |
| fillshares3 | | Total Traded Quantity of 3rd Leg. |
| avgprc3 | | Average trade price of total traded quantity for 3rd leg. |
| stat | Not_Ok | (Failure case) Order book failure indication. |
| request_time | | (Failure case) Response received time. |
| emsg | | (Failure case) Error message. |

## Sample success response
Response data will be in json Array of objects with the success fields above. No verbatim JSON example block is shown in the document; the success response is a JSON array of objects (e.g. `stat` = "Ok").

## Sample failure response
Response data will be in json format with below fields in case of failure (no verbatim JSON example block is provided in the document):

- `stat` = Not_Ok — Order book failure indication.
- `request_time` — Response received time.
- `emsg` — Error message.

## Notes
- The base URL in the curl example is a placeholder (`https://BaseURL/MultiLegOrderBook`); the real endpoint is `https://piconnect.flattrade.in/PiConnectAPI/MultiLegOrderBook`.
- The request is sent as a form-style POST: `jData` is a JSON object and `jKey` is appended as a separate parameter (`jData={...}&jKey=...`).
- The curl `--data` block in the source contains a stray smart/curly double-quote after the closing brace (`}“&jKey=...`) — an OCR/encoding artifact. A correct payload would close with a normal quote: `}'&jKey=...`.
- The only documented request body (jData) fields are `uid*` (required) and `prd` (optional, `H / M / ...`).
- In the response/order-book field table, the fields marked with a trailing `*` are: `prc*`, `qty*`, `trantype*`, `dscqty*`, `ret*`, `amo*`.
- `amo`: must be sent with value "Yes" or the API returns "Invalid AMO"; if AMO is not required, omit the field entirely.
- Multi-leg fields `tsym2`/`trantype2`/`qty2`/`prc2` (plus `fillshares2`/`avgprc2`) are mandatory for price type 2L and 3L; `tsym3`/`trantype3`/`qty3`/`prc3` (plus `fillshares3`/`avgprc3`) are mandatory for price type 3L.
- `bpprc` applies only for product B (Bracket); `blprc` and `trailprc` apply for products H (High Leverage) and B (Bracket).
- The response fields `uid` and `actid` appear in the source table with blank possible-value and blank description cells.
- `uid` is documented both as a request jData body field and as a response field.
