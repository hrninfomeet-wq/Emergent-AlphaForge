# Place Order

**Category:** Orders & Trades · **Type:** REST · **Method:** POST  
**Path:** `/PlaceOrder` · **URL:** `https://piconnect.flattrade.in/PiConnectAPI/PlaceOrder`  
**Source:** PDF pages 7-9

## Summary
Places a new order to the OMS via a POST call. The request carries a `jData` JSON object (order details) plus the session `jKey`; on success it returns the assigned Noren order number (`norenordno`).

## Request — Query Parameters
| Parameter | Required | Possible values | Description |
|---|---|---|---|
| jData* | yes |  | Should send json object with fields in the jData JSON fields list below. |
| jKey* | yes |  | Key obtained on login success. |

## Request — jData JSON fields
| Field | Required | Possible values | Description |
|---|---|---|---|
| uid* | yes |  | Logged in User Id. |
| actid* | yes |  | Login users account ID. |
| exch* | yes | NSE / NFO / BSE / MCX | Exchange (Select from 'exarr' Array provided in User Details response). |
| tsym* | yes |  | Unique id of contract on which order to be placed. (use url encoding to avoid special char error for symbols like M&M) |
| qty* | yes |  | Order Quantity [If qty is junk value other than numbers]. |
| prc* | yes |  | Order Price [If prc is junk value other than numbers] "Order price cannot be zero". |
| trgprc | no |  | Only to be sent in case of SL / SL-M order. |
| dscqty* | yes |  | Disclosed quantity (Max 10% for NSE, and 50% for MCX) [If dscqty is junk value other than numbers]. |
| prd* | yes | C - CNC / M - NRML / H - CO / B - BO / I - MIS / F - MTF | Product name (Select from 'prarr' Array provided in User Details response, and if same is allowed for selected, exchange. Show product display name, for user to select, and send corresponding prd in API call). |
| trantype* | yes | B / S | B -> BUY, S -> SELL [transtype should be 'B' or 'S' else reject]. |
| prctyp* | yes | LMT / SL-LMT | Price type. |
| ret* | yes | DAY / EOS / IOC | Retention type [ret should be DAY / EOS / IOC else reject]. |
| remarks | no |  | Any tag by user to mark order. |
| ordersource | no | API | Used to generate exchange info fields. |
| bpprc | no |  | Book Profit Price applicable only if product is selected as B (Bracket order). |
| blprc | no |  | Book loss Price applicable only if product is selected as H and B (High Leverage and Bracket order). |
| trailprc | no |  | Trailing Price applicable only if product is selected as H and B (High Leverage and Bracket order). |
| amo* | yes | Yes | The message "Invalid AMO" will be displayed if the "amo" field is not sent with a "Yes" value. If amo is not required, do not send this field. |
| tsym2 | no |  | Trading symbol of second leg, mandatory for price type 2L and 3L (use url encoding to avoid special char error for symbols like M&M). |
| trantype2 | no |  | Transaction type of second leg, mandatory for price type 2L and 3L. |
| qty2 | no |  | Quantity for second leg, mandatory for price type 2L and 3L. |
| prc2 | no |  | Price for second leg, mandatory for price type 2L and 3L. |
| tsym3 | no |  | Trading symbol of third leg, mandatory for price type 3L (use url encoding to avoid special char error for symbols like M&M). |
| trantype3 | no |  | Transaction type of third leg, mandatory for price type 3L. |
| qty3 | no |  | Quantity for third leg, mandatory for price type 3L. |
| prc3 | no |  | Price for third leg, mandatory for price type 3L. |

## Sample request
```bash
curl --location 'https://BaseURL/PlaceOrder' \
--header 'Content-Type: application/json' \
--data 'jData={
    "uid": "FZ00000",
    "actid": "FZ00000",
    "exch": "NSE",
    "tsym": "ACC-EQ",
    "qty": "50",
    "prc": "1400",
    "prd": "H",
    "trantype": "B",
    "prctyp": "LMT",
    "ret": "DAY"
}&jKey=GHUDWU53H32MTHPA536Q32WR'
```

## Response fields
| Field | Possible values | Description |
|---|---|---|
| stat | Ok or Not_Ok | Place order success or failure indication. |
| request_time |  | Response received time. |
| norenordno |  | It will be present only on successful Order placement to OMS. |

## Sample success response
```json
{
"request_time": "10:48:03 20-05-2020",
"stat": "Ok",
"norenordno": "20052000000017"
}
```

## Sample failure response
```json
{
"stat": "Not_Ok",
"request_time": "20:40:01 19-05-2020",
"emsg": "Error Occurred : 2 \"invalid input\""
}
```

## Notes
- BaseUrl in samples = `https://piconnect.flattrade.in/PiConnectAPI` ; ClientId example FT0000 ; jKey example GHUDWU53H32MTHPA536Q32WR. The curl uses a placeholder `https://BaseURL/PlaceOrder`.
- Request is form/query style: `jData` (the JSON order object) and `jKey` are joined with `&` in the body/query, not a pure JSON body.
- URL-encode trading symbols containing special characters (e.g. M&M) in `tsym`/`tsym2`/`tsym3` to avoid errors.
- `trgprc` only to be sent for SL / SL-M orders.
- `dscqty` (disclosed qty) max is 10% of qty for NSE and 50% for MCX.
- `prd` values map: C=CNC, M=NRML, H=CO (Cover Order / High Leverage), B=BO (Bracket Order), I=MIS, F=MTF. Select a valid `prd` from the 'prarr' array in User Details for the chosen exchange.
- `exch` must be chosen from the 'exarr' array in the User Details response.
- Bracket/Cover-order extras: `bpprc` (Book Profit) for product B; `blprc` (Book Loss) and `trailprc` (Trailing) for products H and B.
- `amo`: send `"amo":"Yes"` for after-market orders; if not an AMO, omit the field entirely (sending it wrong yields "Invalid AMO").
- Multi-leg legs (`tsym2`/`trantype2`/`qty2`/`prc2` and `tsym3`/`trantype3`/`qty3`/`prc3`) are mandatory for price types 2L and 3L respectively.
- `request_time` in responses is a formatted timestamp string `"HH:MM:SS DD-MM-YYYY"` (e.g. 10:48:03 20-05-2020), not epoch.
- `ordersource` should be set to API to generate exchange info fields.
- The RESPONSE DETAILS table documents only `stat`, `request_time` and `norenordno`; `emsg` is returned on failure (see sample failure response) but is not listed in that table.
