# Product Conversion

**Category:** Orders & Trades · **Type:** REST · **Method:** POST  
**Path:** `/ProductConversion` · **URL:** `https://piconnect.flattrade.in/PiConnectAPI/ProductConversion`  
**Source:** PDF pages 29-30

## Summary
Converts an existing position from one product type to another (e.g. intraday to delivery / carry-forward, or vice versa) via a POST call. Returns a simple Ok / Not_Ok status indicating conversion success or failure.

## Request — Query Parameters
| Parameter | Required | Possible values | Description |
|---|---|---|---|
| jData* | yes |  | Should send json object with fields in below list. |
| jKey* | yes |  | Key Obtained on login success. |

## Request — jData JSON fields
| Field | Required | Possible values | Description |
|---|---|---|---|
| exch* | yes |  | Exchange |
| tsym* | yes |  | Unique id of contract on which order was placed. Can't be modified, must be the same as that of original order. (use url encoding to avoid special char error for symbols like M&M) |
| qty* | yes |  | Quantity to be converted [If qty is junk value other than numbers]. |
| uid* | yes |  | User id of the logged in user. |
| actid* | yes |  | Account id |
| prd* | yes |  | Product to which the user wants to convert position. |
| prevprd* | yes |  | Original product of the position. |
| trantype* | yes | B / S | B -> BUY, S -> SELL [transtype should be 'B' or 'S' else reject]. |
| postype* | yes | Day / CF | Converting Day or Carry forward position |
| ordersource | no | API | For Logging |

## Sample request
```bash
curl --location 'https://BaseURL/ProductConversion' \
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
    "prevprd": "C", 
    "postype": "Day"
}&jKey=GHUDWU53H32MTHPA536Q32WR'
```

## Response fields
| Field | Possible values | Description |
|---|---|---|
| stat | Ok or Not_Ok | Position conversion success or failure indication. |
| emsg |  | This will be present only if Position conversion fails. |

## Sample success response
```json
{
"request_time":"10:52:12 02-06-2020",
"stat":"Ok"
}
```

## Sample failure response
```json
{
"stat":"Not_Ok",
"emsg":"Invalid Input :  Invalid Position Type"
}
```

## Notes
Section spans PDF pages 29-30; ends where "PLACE GTT ORDER" begins. The documented jData-field table lists exch, tsym, qty, uid, actid, prd, prevprd, trantype, postype, ordersource — it does NOT list `prc` or `prctyp`, even though both appear in the curl example; those two extra example fields are not part of the field spec. The `prd` and `prevprd` "Possible value" cells are blank in the doc (free-text product codes such as H/C/I are implied by the example but not enumerated). Only `ordersource` is optional (no trailing `*`); all other jData fields are required.
