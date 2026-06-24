# Place OCO Order

**Category:** Orders & Trades · **Type:** REST · **Method:** POST  
**Path:** `/PlaceOCOOrder` · **URL:** `https://piconnect.flattrade.in/PiConnectAPI/PlaceOCOOrder`  
**Source:** PDF pages 36-38

## Summary
Places a One-Cancels-the-Other (OCO) order by POSTing a jData JSON object (plus the login jKey) to the PlaceOCOOrder endpoint. The OCO order ties two full order legs (place_order_params and place_order_params_leg2) together with an oivariable array whose values are compared against the LTP; on success the response returns the created alert/OI id (al_id).

## Request — Query Parameters
| Parameter | Required | Possible values | Description |
|---|---|---|---|
| jData | yes |  | Should send json object with fields in below list. |
| jKey | yes |  | Key Obtained on login success. |

## Request — jData JSON fields
| Field | Required | Possible values | Description |
|---|---|---|---|
| uid | yes |  | User id of the logged in user. |
| tsym | yes |  | Unique id of contract on which order to be placed. (use url encoding to avoid special char error for symbols like M&M) |
| exch | yes |  | Exchange |
| validity | yes | DAY or GTT | Validity |
| ai_t | yes |  | Alert type |
| exchsym | no |  | Exchange symbol |
| oivariable | no |  | Array Object, details given below (see OIVARIABLE FORMAT). |
| place_order_params | yes |  | List of place order Params fields (the first / leg-1 order parameters; see PLACE_ORDER_PARAMS FORMAT). |
| place_order_params_leg2 | yes |  | List of Place order params fields for leg2 (the second order parameters; see PLACE_ORDER_PARAMS FORMAT). |
| oivariable.d | yes |  | OIVARIABLE FORMAT field: Data to be compared with LTP. |
| oivariable.var_name | yes | x or y | OIVARIABLE FORMAT field: Variable Name. |
| place_order_params.tsym | yes |  | PLACE_ORDER_PARAMS field: Trading symbol of the scrip (contract). |
| place_order_params.exch | yes |  | PLACE_ORDER_PARAMS field: Exchange. |
| place_order_params.trantype | yes | B / S | PLACE_ORDER_PARAMS field: B -> BUY, S -> SELL [transtype should be 'B' or 'S' else reject]. |
| place_order_params.prctyp | yes |  | PLACE_ORDER_PARAMS field: Price Type. |
| place_order_params.prd | yes |  | PLACE_ORDER_PARAMS field: Product. |
| place_order_params.ret | yes | DAY / EOS / IOC | PLACE_ORDER_PARAMS field: Retention type [ret should be DAY / EOS / IOC else reject]. |
| place_order_params.actid | yes |  | PLACE_ORDER_PARAMS field: Acct Id. |
| place_order_params.uid | yes |  | PLACE_ORDER_PARAMS field: User Id. |
| place_order_params.ordersource | no | MOB / WEB / TT | PLACE_ORDER_PARAMS field: Used to generate exchange info fields. |
| place_order_params.remarks | no |  | PLACE_ORDER_PARAMS field: Any tag by user to mark order. |
| place_order_params.qty | yes |  | PLACE_ORDER_PARAMS field: Order Quantity [If qty is junk value other than numbers]. |
| place_order_params.prc | yes |  | PLACE_ORDER_PARAMS field: Order Price [If prc is junk value other than numbers] "Order price cannot be zero". |
| place_order_params.trgprc | no |  | PLACE_ORDER_PARAMS field: New trigger price in case of SL-LMT. |

## Sample request
```bash
curl --location 'https://BaseURL/PlaceOCOOrder' \
--header 'Content-Type: application/json' \
--data 'jData={
    "uid": "FZ00000",
    "ai_t": "LMT_BOS_O",
    "remarks": "admn",
    "validity": "GTT",
    "tsym": "ACC-EQ",
    "exch": "NSE",
    "oivariable": [
        {
            "d": "20000",
            "var_name": "x"
        },
        {
            "d": "30000",
            "var_name": "y"
        }
    ],
    "place_order_params": {
        "tsym": "ACC-EQ",
        "exch": "NSE",
        "trantype": "B",
        "prctyp": "LMT",
        "prd": "C",
        "ret": "DAY",
        "actid": "FZ00000",
        "uid": "FZ00000",
        "ordersource": "WEB",
        "qty": "1",
        "prc": "200"
    },
    "place_order_params_leg2": {
        "tsym": "ACC-EQ",
        "exch": "NSE",
        "trantype": "S",
        "prctyp": "LMT",
        "prd": "C",
        "ret": "DAY",
        "actid": "FZ00000",
        "uid": "FZ00000",
        "ordersource": "WEB",
        "qty": "1",
        "prc": "200"
    }
}&jKey=652c99c82d7edcd4f472869786074c90bd27dfd0c68635c2e53db0ed08cbea0f'
```

## Response fields
| Field | Possible values | Description |
|---|---|---|
| stat | OI created / Not_Ok | OCO orders success or failure indication. |
| request_time |  | This will be present only in a successful response. |
| al_id |  | Alert Id. |
| emsg |  | This will be present only in case of errors. That is: 1) Invalid Input 2) Session Expired. |

## Sample success response
```json
{
"request_time":"18:56:26 08-10-2021",
"stat":"OI created",
"al_id":"21100800000009"
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
- BaseURL in the curl resolves to https://piconnect.flattrade.in/PiConnectAPI, so the full URL is https://piconnect.flattrade.in/PiConnectAPI/PlaceOCOOrder.
- jData and jKey are sent as a url-encoded form body (jData={...}&jKey=...), not raw JSON, despite the Content-Type: application/json header.
- tsym: use URL encoding to avoid special-char errors for symbols like M&M.
- An OCO order requires BOTH legs: place_order_params (leg 1) and place_order_params_leg2 (leg 2), each a full PLACE_ORDER_PARAMS object.
- oivariable is an array of {d, var_name} where var_name is x or y and d is the value compared against LTP (the alert trigger boundaries).
- trantype must be exactly 'B' or 'S' else reject; ret must be DAY / EOS / IOC else reject.
- prc (order price) cannot be zero; qty/prc are rejected if junk (non-numeric) values.
- trgprc is the new trigger price, used in case of SL-LMT price type.
- On success stat is "OI created" and al_id is the created alert/OI id; request_time appears only on success. On failure stat is "Not_Ok" and emsg carries the reason (Invalid Input or Session Expired).
- The very next section (Modify OCO Order, POST /PiConnectAPI/ModifyOCOOrder) begins on page 38 and is out of scope for this extraction.
