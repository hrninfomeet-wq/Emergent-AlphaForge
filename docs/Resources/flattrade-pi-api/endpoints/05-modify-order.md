# Modify Order

**Category:** Orders & Trades · **Type:** REST · **Method:** POST  
**Path:** `/ModifyOrder` · **URL:** `https://piconnect.flattrade.in/PiConnectAPI/ModifyOrder`  
**Source:** PDF pages 10-11

## Summary
Modifies an existing (open/pending) order placed via the Flattrade Noren OMS. A POST call carries a `jData` JSON object (identifying the order by `norenordno` and `tsym`) plus the `jKey` session token, and can change price type, price, quantity, retention, trigger price, and bracket/high-leverage prices.

## Request — Query Parameters
| Parameter | Required | Possible values | Description |
|---|---|---|---|
| jData* | yes |  | Should send json object with fields in below list. |
| jKey* | yes |  | Key Obtained on login success. |

## Request — jData JSON fields
| Field | Required | Possible values | Description |
|---|---|---|---|
| exch* | yes |  | Exchange |
| norenordno* | yes |  | Noren order number, which needs to be modified |
| prctyp | no | LMT / SL-LMT | This can be modified |
| prc* | yes |  | Modified / New price [If prc is junk value other than numbers] "Order price cannot be zero". |
| qty* | yes |  | Modified / New Quantity. Quantity to Fill / Order Qty - This is the total qty to be filled for the order. Its Open Qty/Pending Qty plus Filled Shares (cumulative for the order) for the order. * Please do not send only the pending qty in this field [If qty is junk value other than numbers]. |
| tsym* | yes |  | Unque id of contract on which order was placed. Can't be modified, must be the same as that of original order. (use url encoding to avoid special char error for symbols like M&M) |
| ret* | yes | DAY / EOS / IOC | Retention type [ret should be DAY / EOS / IOC else reject] |
| trgprc | no |  | New trigger price in case of SL-LMT |
| uid* | yes |  | User id of the logged in user. |
| bpprc | no |  | Book Profit Price applicable only if product is selected as B (Bracket order ) |
| blprc | no |  | Book loss Price applicable only if product is selected as H and B (High Leverage and Bracket order ) |
| trailprc | no |  | Trailing Price applicable only if product is selected as H and B (High Leverage and Bracket order ) |

## Sample request
```bash
curl --location 'https://BaseURL/ModifyOrder' \
--header 'Content-Type: application/json' \
--data 'jData={
         "uid": "FZ00000",
         "actid": "FZ00000",
         "exch": "NSE",
         "tsym": "ACC-EQ",
         "qty": "50",
         "prc": "1400",         
         "prctyp": "LMT",
         "ret": "DAY", 
         "norenordno": "123456789"
     }&jKey=GHUDWU53H32MTHPA536Q32WR'
```

## Response fields
| Field | Possible values | Description |
|---|---|---|
| stat | Ok or Not_Ok | Modify order success or failure indication. |
| result |  | Noren Order number of the order modified. |
| request_time |  | Response received time |
| emsg |  | This will be present only if Order modification fails |

## Sample success response
```json
{
"request_time":"14:14:08 26-05-2020",
"stat":"Ok",
"result":"20052600000103"
}
```

## Sample failure response
```json
{
"request_time":"16:03:29 28-05-2020",
"stat":"Not_Ok",
"emsg":"Rejected : ORA:Order not found"
}
```

## Notes
- Base URL in the live endpoint is `https://piconnect.flattrade.in/PiConnectAPI`; the curl sample uses a placeholder `https://BaseURL/ModifyOrder`. Substitute the real base URL.
- `jData` is sent as a URL-style parameter: `--data 'jData={...}&jKey=...'` (`jData` and `jKey` concatenated with `&`), Content-Type `application/json`.
- `tsym` (contract id) CANNOT be modified and must exactly match the original order; URL-encode symbols containing special characters (e.g. M&M) to avoid special-char errors.
- `qty` must be the TOTAL order quantity (Open/Pending Qty + Filled Shares, cumulative), NOT just the pending qty.
- `prc` cannot be zero; `prc` and `qty` junk (non-numeric) values are rejected.
- `ret` must be one of DAY / EOS / IOC or the order is rejected.
- `prctyp` can be modified to LMT or SL-LMT; `trgprc` is only relevant for SL-LMT price type.
- `bpprc` applies only when product = B (Bracket); `blprc` and `trailprc` apply only when product = H or B (High Leverage / Bracket).
- `request_time` timestamps are in `HH:MM:SS DD-MM-YYYY` format (IST).
- The curl sample additionally includes an `actid` (account id) field not listed in the Json Fields table.
