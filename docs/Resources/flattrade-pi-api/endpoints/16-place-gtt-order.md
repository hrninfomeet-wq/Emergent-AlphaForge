# Place GTT Order

**Category:** Orders & Trades · **Type:** REST · **Method:** POST  
**Path:** `/PlaceGTTOrder` · **URL:** `https://piconnect.flattrade.in/PiConnectAPI/PlaceGTTOrder`  
**Source:** PDF pages 30-31

## Summary
Places a GTT (Good Till Triggered) alert/order via a POST call. The request sends a jData JSON object (order/alert parameters) plus the login jKey; on success it returns an alert id (al_id / Al_id) and a request_time.

## Request — Query Parameters
| Parameter | Required | Possible values | Description |
|---|---|---|---|
| jData* | yes |  | Should send json object with fields in below list. |
| jKey* | yes |  | Key Obtained on login success. |

## Request — jData JSON fields
| Field | Required | Possible values | Description |
|---|---|---|---|
| uid* | yes |  | User id of the logged in user. |
| tsym* | yes |  | Trading symbol. |
| exch* | yes |  | Exchange Segment. |
| ai_t* | yes |  | Alert Type. |
| validity* | yes | DAY or GTT | Validity. |
| d | no |  | Data to be compared with LTP. |
| remarks* | yes |  | Any message Entered during order entry. |
| trantype* | yes | B / S | B -> BUY, S -> SELL [transtype should be 'B' or 'S' else reject]. |
| prctyp* | yes | LMT / SL-LMT / DS / 2L / 3L |  |
| prd* | yes | C / M / H | Product name. |
| ret* | yes | DAY / EOS / IOC | Retention type [ret should be DAY / EOS / IOC else reject]. |
| actid* | yes |  | Login users account ID. |
| qty* | yes |  | Order Quantity [If qty is junk value other than numbers]. |
| prc* | yes |  | Order Price [If prc is junk value other than numbers] "Order price cannot be zero". |
| dscqty* | yes |  | Disclosed quantity (Max 10% for NSE, and 50% for MCX) [If dscqty is junk value other than numbers]. |

## Sample request
```bash
curl --location 'https://BaseURL/PlaceGTTOrder' \
--header 'Content-Type: application/json' \
--data 'jData={
    "uid": "FZ00000",    
    "exch": "NSE",
    "tsym": "ACC-EQ",
    "validity": "DAY", 
    "qty": "50",
    "prc": "1400",
    "prd": "H",
    "trantype": "B",
    "prctyp": "LMT",
    "prevprd": "C", 
    "ret": "DAY", 
    "dscqty": "10"
}&jKey=GHUDWU53H32MTHPA536Q32WR'
```

## Response fields
| Field | Possible values | Description |
|---|---|---|
| stat |  | GTT order success or failure indication. |
| request_time |  | This will be present only in a successful response. |
| al_id |  | Alert Id (returned as "Al_id" in the sample success response). |
| emsg |  | This will be present only in case of errors. That is : 1) Invalid Input 2) Session Expired. |

## Sample success response
```json
[
{
"request_time":"10:02:06 15-04-2021",
"stat":"Oi created",
"Al_id":"21041500000010"
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
- jData and jKey are sent as URL/form parameters (`jData={...}&jKey=...`), with the JSON object passed as the value of jData. The curl uses a placeholder `BaseURL`; the real base URL is `https://piconnect.flattrade.in/PiConnectAPI/`.
- The json fields table spans pages 30-31: `uid`/`tsym`/`exch`/`ai_t` are on page 30; `validity`/`d`/`remarks`/`trantype`/`prctyp`/`prd`/`ret`/`actid`/`qty`/`prc`/`dscqty` are on page 31.
- `d` (Data to be compared with LTP) is the only optional json field (no `*` in the source table); all other listed fields are marked required.
- `prctyp` has only a Possible value cell (LMT / SL-LMT / DS / 2L / 3L) and **no** description text in the source table.
- `ai_t` (Alert Type) has no Possible value cell in the source table.
- `prd` (product) possible values: C / M / H. `ret` (retention) must be DAY / EOS / IOC else rejected. `trantype` must be 'B' or 'S' else rejected. Disclosed quantity (`dscqty`) max is 10% for NSE and 50% for MCX.
- The sample curl body includes an extra field `"prevprd": "C"` that is **not** in the documented json_fields table for Place GTT Order (`prevprd` belongs to Product Conversion on the previous page) — reproduced verbatim from the source and flagged as a likely copy/paste artifact, not added to json_fields.
- The success `stat` reads `"Oi created"` verbatim (appears to be a typo/OCR artifact for an order/alert-created message); on success `request_time` and `al_id` are present; on error `emsg` is present (Invalid Input or Session Expired).
- Alert-id casing differs between the response-fields table (`al_id`) and the sample success JSON (`"Al_id"`).
