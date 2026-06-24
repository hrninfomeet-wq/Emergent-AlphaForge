# Modify OCO Order

**Category:** Orders & Trades · **Type:** REST · **Method:** POST  
**Path:** `/ModifyOCOOrder` · **URL:** `https://piconnect.flattrade.in/PiConnectAPI/ModifyOCOOrder`  
**Source:** PDF pages 38-40

## Summary
Modifies an existing OCO (One-Cancels-the-Other) / alert order. A POST call to `/ModifyOCOOrder` with `jData` (a JSON object containing the order/alert fields plus nested `oivariable` and `place_order_params` arrays) and `jKey` returns `"OI replaced"` with the alert id on success.

## Request — Query Parameters
| Parameter | Required | Possible values | Description |
|---|---|---|---|
| jData* | yes |  | Should send json object with fields in below list. |
| jKey* | yes |  | Key Obtained on login success. |

## Request — jData JSON fields
| Field | Required | Possible values | Description |
|---|---|---|---|
| uid* | yes |  | User id of the logged in user. |
| tsym* | yes |  | Unique id of contract on which order to be placed. (use url encoding to avoid special char error for symbols like M&M) |
| exch* | yes |  | Exchange |
| validity* | yes | DAY or GTT | Validity |
| ai_t* | yes |  | Alert type. (Doc text and page image read `ai_t*`; likely a typo for `al_t`, the alert-type counterpart to `al_id`.) |
| al_id* | yes |  | Alert id |
| exchsym | no |  | Exchange symbol |
| oivariable | no |  | Array Object, details given below (see OIVARIABLE FORMAT). |
| place_order_params | no |  | Array Object, details given below (see PLACE_ORDER_PARAMS FORMAT). |
| oivariable.d* | yes |  | OIVARIABLE FORMAT field: Data to be compared with LTP. |
| oivariable.var_name* | yes | x or y | OIVARIABLE FORMAT field: Variable Name. |
| place_order_params.tsym* | yes |  | PLACE_ORDER_PARAMS FORMAT field: Trading symbol of the scrip (contract). |
| place_order_params.exch* | yes |  | PLACE_ORDER_PARAMS FORMAT field: Exchange. |
| place_order_params.trantype* | yes | B / S | PLACE_ORDER_PARAMS FORMAT field: B -> BUY, S -> SELL [transtype should be 'B' or 'S' else reject]. |
| place_order_params.prctyp* | yes |  | PLACE_ORDER_PARAMS FORMAT field: Price Type. |
| place_order_params.prd* | yes |  | PLACE_ORDER_PARAMS FORMAT field: Product. |
| place_order_params.ret* | yes | DAY / EOS / IOC | PLACE_ORDER_PARAMS FORMAT field: Retention type [ret should be DAY / EOS / IOC else reject]. |
| place_order_params.actid* | yes |  | PLACE_ORDER_PARAMS FORMAT field: Acct Id. |
| place_order_params.uid* | yes |  | PLACE_ORDER_PARAMS FORMAT field: User Id. |
| place_order_params.ordersource | no | MOB / WEB / TT | PLACE_ORDER_PARAMS FORMAT field: Used to generate exchange info fields. |
| place_order_params.remarks | no |  | PLACE_ORDER_PARAMS FORMAT field: Any tag by user to mark order. |
| place_order_params.qty* | yes |  | PLACE_ORDER_PARAMS FORMAT field: Order Quantity [If qty is junk value other than numbers]. |
| place_order_params.prc* | yes |  | PLACE_ORDER_PARAMS FORMAT field: Order Price [If prc is junk value other than numbers] "Order price cannot be zero". |
| place_order_params.trgprc | no |  | PLACE_ORDER_PARAMS FORMAT field: New trigger price in case of SL-LMT. |

## Sample request
```bash
curl --location 'https://BaseURL/ModifyOCOOrder' \
--header 'Content-Type: application/json' \
--data 'jData={
    "uid": "FZ00000",    
    "exch": "NSE",
    "tsym": "ACC-EQ",
    "validity": "DAY"
}&jKey=GHUDWU53H32MTHPA536Q32WR'
```

## Response fields
| Field | Possible values | Description |
|---|---|---|
| stat | OI replaced / Invalid Oi | OCO order success or failure indication. i. "stat":"OI replaced" - incase of success ii. "stat":"Invalid Oi" - incase of failure. |
| request_time |  | This will be present only in a successful response. |
| al_id |  | Alert Id |
| emsg |  | This will be present only in case of errors. That is: 1) Invalid Input 2) Session Expired. |

## Sample success response
```json
{
"request_time":"11:14:52 11-10-2021",
"stat":"OI replaced",
"al_id":"21101100000001"
}
```

## Sample failure response
```json
{
"stat":"Not_Ok",
"emsg":"Session Expired : Invalid Session Key"
}
```

## Notes
REST POST. Authoritative URL: `https://piconnect.flattrade.in/PiConnectAPI/ModifyOCOOrder`. The jData schema is hierarchical: `oivariable` and `place_order_params` are nested array objects whose sub-fields are documented in the OIVARIABLE FORMAT (page 38) and PLACE_ORDER_PARAMS FORMAT (page 39) tables; here they are flattened with `oivariable.`/`place_order_params.` prefixes to disambiguate. The field `ai_t*` is recorded verbatim from the page image/text but is almost certainly a typo for `al_t` (alert type), the counterpart to `al_id`. The failure-response `stat` value in the sample (`Not_Ok`) differs from the documented failure indicator (`Invalid Oi`); both come straight from the source page.
