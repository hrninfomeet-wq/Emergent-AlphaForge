# Basket Margin

**Category:** Orders & Trades · **Type:** REST · **Method:** POST  
**Path:** `/GetBasketMargin` · **URL:** `https://piconnect.flattrade.in/PiConnectAPI/GetBasketMargin`  
**Source:** PDF pages 15-17

## Summary
Returns the basket margin for a set of orders. A POST call (jData + jKey) that computes the total margin used and the margin used after the trade for a primary order plus an optional array (basketlists) of additional order objects.

## Request — Query Parameters
| Parameter | Required | Possible values | Description |
|---|---|---|---|
| jData* | yes |  | Should send json object with fields in below list. |
| jKey* | yes |  | Key Obtained on login success. |

## Request — jData JSON fields
| Field | Required | Possible values | Description |
|---|---|---|---|
| uid* | yes |  | Logged in User Id |
| actid* | yes |  | Login users account ID |
| exch* | yes | NSE / NFO / BSE / MCX | Exchange (Select from 'exarr' Array provided in User Details response) |
| tsym* | yes |  | Unique id of contract on which order to be placed. (use url encoding to avoid special char error for symbols like M&M) |
| qty* | yes |  | Order Quantity [If qty is junk value other than numbers]. |
| prc* | yes |  | Order Price [If prc is junk value other than numbers] "Order price cannot be zero". |
| trgprc | no |  | Only to be sent in case of SL / SL-M order. |
| prd* | yes | C / M / H | Product name (Select from 'prarr' Array provided in User Details response, and if same is allowed for selected, exchange. Show product display name, for user to select, and send corresponding prd in API call) |
| trantype* | yes | B / S | B -> BUY, S -> SELL [transtype should be 'B' or 'S' else reject]. |
| prctyp* | yes | LMT / SL-LMT | Order price type. |
| blprc | no |  | Book loss Price applicable only if product is selected as H and B (High Leverage and Bracket order) |
| rorgqty | no |  | Optional field. Application only for modify order, open order quantity |
| fillshares | no |  | Optional field. Application only for modify order, quantity already filled. |
| rorgprc | no |  | Optional field. Application only for modify order, open order price |
| orgtrgprc | no |  | Optional field. Application only for modify order, open order trigger price |
| norenordno | no |  | Optional field. Application only for H or B order modification |
| snonum | no |  | Optional field. Application only for H or B order modification |
| basketlists | no |  | Optional field. Array of json objects. (object fields given in below table) |
| basketlists[].exch* | yes | NSE / NFO / BSE / MCX | (Array object field) Exchange (Select from 'exarr' Array provided in User Details response) |
| basketlists[].tsym* | yes |  | (Array object field) Unique id of contract on which order to be placed. (use url encoding to avoid special char error for symbols like M&M) |
| basketlists[].qty* | yes |  | (Array object field) Order Quantity [If qty is junk value other than numbers]. |
| basketlists[].prc* | yes |  | (Array object field) Order Price [If prc is junk value other than numbers] "Order price cannot be zero". |
| basketlists[].trgprc | no |  | (Array object field) Only to be sent in case of SL / SL-M order. |
| basketlists[].prd* | yes | C / M / H | (Array object field) Product name (Select from 'prarr' Array provided in User Details response, and if same is allowed for selected, exchange. Show product display name, for user to select, and send corresponding prd in API call) |
| basketlists[].trantype* | yes | B / S | (Array object field) B -> BUY, S -> SELL [transtype should be 'B' or 'S' else reject]. |
| basketlists[].prctyp* | yes | LMT / SL-LMT | (Array object field) Order price type. |
| basketlists[].introp_key | no |  | (Array object field) Optional field. |
| basketlists[].introp_exch | no |  | (Array object field) Optional field. |

## Sample request
```bash
curl --location 'https://BaseURL/GetBasketMargin' \
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
| remarks |  | This field will contain rejection reason. |
| marginused |  | Total margin used. |
| marginusedtrade |  | Margin used after trade. |
| emsg |  | This will be present only if Order placement fails |

## Notes
Request is sent as jData (a JSON object) plus jKey, Content-Type application/json. Base URL in production is https://piconnect.flattrade.in/PiConnectAPI (the curl sample uses a 'BaseURL' placeholder). url-encode symbols containing special characters such as M&M to avoid errors. 'blprc' (Book loss Price) applies only when product = H and B (High Leverage and Bracket order). The fields rorgqty, fillshares, rorgprc, orgtrgprc are only for modify-order use; norenordno/snonum apply only for H or B order modification. 'basketlists' is an optional array of additional order objects (each with its own exch/tsym/qty/prc/trgprc/prd/trantype/prctyp plus optional introp_key/introp_exch). No sample success/failure JSON response body is shown in the doc; only the response field table is provided. prctyp possible values are LMT / SL-LMT (no market order type listed, consistent with Flattrade's limit/SL-limit-only constraint).
