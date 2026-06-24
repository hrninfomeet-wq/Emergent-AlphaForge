# Order Margin

**Category:** Orders & Trades · **Type:** REST · **Method:** POST  
**Path:** `/GetOrderMargin` · **URL:** `https://piconnect.flattrade.in/PiConnectAPI/GetOrderMargin`  
**Source:** PDF pages 13-15

## Summary
Calculates the margin required for a single order before placement. A POST call sends a jData JSON object (order details plus optional modify/bracket fields) and the login jKey, and returns the cash available and margin used for the order.

## Request — Query Parameters
| Parameter | Required | Possible values | Description |
|---|---|---|---|
| jData* | yes |  | Should send json object with fields in below list. |
| jKey* | yes |  | Key Obtained on login success. |

## Request — jData JSON fields
| Field | Required | Possible values | Description |
|---|---|---|---|
| uid | yes |  | Logged in User Id |
| actid | yes |  | Login users account ID |
| exch | yes | NSE / NFO / BSE / MCX | Exchange (Select from 'exarr' Array provided in User Details response) |
| tsym | yes |  | Unique id of contract on which order to be placed. (use url encoding to avoid special char error for symbols like M&M) |
| qty | yes |  | Order Quantity [If qty is junk value other than numbers]. |
| prc | yes |  | Order Price [If prc is junk value other than numbers] "Order price cannot be zero". |
| trgprc | no |  | Only to be sent in case of SL / SL-M order. |
| prd | yes | C / M / H | Product name (Select from 'prarr' Array provided in User Details response, and if same is allowed for selected, exchange. Show product display name, for user to select, and send corresponding prd in API call) |
| trantype | yes | B / S | B -> BUY, S -> SELL [transtype should be 'B' or 'S' else reject]. |
| prctyp | yes | LMT / SL-LMT |  |
| blprc | no |  | Book loss Price applicable only if product is selected as H and B (High Leverage and Bracket order ) |
| rorgqty | no |  | Optional field. Application only for modify order, open order quantity |
| fillshares | no |  | Optional field. Application only for modify order, quantity already filled. |
| rorgprc | no |  | Optional field. Application only for modify order, open order price |
| orgtrgprc | no |  | Optional field. Application only for modify order, open order trigger price |
| norenordno | no |  | Optional field. Application only for H or B order modification |
| snonum | no |  | Optional field. Application only for H or B order modification |

## Sample request
```bash
curl --location 'https://BaseURL/GetOrderMargin' \
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
    "norenordno": "123456789"
}&jKey=GHUDWU53H32MTHPA536Q32WR'
```

## Response fields
| Field | Possible values | Description |
|---|---|---|
| stat | Ok or Not_Ok | Place order success or failure indication. |
| request_time |  | Response received time |
| remarks |  | This field will be available only on success. |
| cash |  | Total credits available for order |
| marginused |  | Total margin used. |
| emsg |  | This will be present only if Order placement fails |

## Notes
- Request is sent form-style: jData and jKey are concatenated in the body (`jData={...}&jKey=...`), as shown in the curl example, with `Content-Type: application/json` header. The curl uses a placeholder host `https://BaseURL/GetOrderMargin`; the real base URL is `https://piconnect.flattrade.in/PiConnectAPI`.
- jKey is the token obtained on login success.
- `tsym` must be URL-encoded to avoid special-character errors for symbols like M&M.
- `exch` must be one of the values in the 'exarr' array from the User Details response (NSE / NFO / BSE / MCX listed).
- `prd` must be picked from the 'prarr' array from User Details (only products allowed for the selected exchange); displayed values are C / M / H.
- `trantype` must be exactly 'B' or 'S' else the request is rejected.
- `prc` cannot be zero ("Order price cannot be zero").
- `trgprc` is only sent for SL / SL-M orders.
- The source gives no description text for `prctyp`, only the enum LMT / SL-LMT.
- `blprc` applies only when product = H and B (High Leverage and Bracket order).
- `rorgqty`, `fillshares`, `rorgprc`, `orgtrgprc` apply only to modify-order scenarios; `norenordno` and `snonum` apply only to H or B order modification.
- No sample success/failure JSON response body is provided in the source.
