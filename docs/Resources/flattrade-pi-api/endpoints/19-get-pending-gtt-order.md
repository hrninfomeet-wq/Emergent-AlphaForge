# Get Pending GTT Order

**Category:** Orders & Trades · **Type:** REST · **Method:** POST  
**Path:** `/GetPendingGTTOrder` · **URL:** `https://piconnect.flattrade.in/PiConnectAPI/GetPendingGTTOrder`  
**Source:** PDF pages 33-35

## Summary
Retrieves the list of pending GTT (Good Till Triggered) alert orders for the logged-in user. POST call with jData (JSON object) and jKey (login session key) as request parameters; returns an array of pending GTT order objects.

## Request — Query Parameters
| Parameter | Required | Possible values | Description |
|---|---|---|---|
| jData* | yes |  | Should send json object with fields in below list |
| jKey* | yes |  | Key Obtained on login success. |

## Request — jData JSON fields
| Field | Required | Possible values | Description |
|---|---|---|---|
| uid* | yes |  | User id of the logged in user |

## Sample request
```bash
curl --location 'https://BaseURL/GetPendingGTTOrder' \
--header 'Content-Type: application/json' \
--data 'jData={
    "uid": "FZ00000"   
}&jKey=GHUDWU53H32MTHPA536Q32WR'
```

## Response fields
| Field | Possible values | Description |
|---|---|---|
| stat |  | alert success or failure indication. |
| ai_t |  | Alert type |
| al_id |  | Alert Id |
| tsym |  | Trading symbol |
| exch |  | Exchange Segment |
| token |  | Contract token |
| remarks |  | Any message Entered during order entry. |
| validity | DAY or GTT | Validity |
| d |  | Data to be compared with LTP |
| trantype* | B / S | B -> BUY, S -> SELL [transtype should be 'B' or 'S' else reject]. |
| prctyp | LMT / SL-LMT / DS / 2L / 3L |  |
| prd | C / M / H | Product name |
| ret* | DAY / EOS / IOC | Retention type [ret should be DAY / EOS / IOC else reject] |
| actid |  | Login users account ID |
| qty* |  | Order Quantity [If qty is junk value other than numbers]. |
| prc* |  | Order Price [If prc is junk value other than numbers] "Order price cannot be zero". |
| emsg |  | This will be present only in case of errors. That is : 1) Invalid Input 2) Session Expired |

## Sample success response
```json
[
{
"stat":"Ok",
"ai_t":"LTP_A",
"Al_id":"21041500000002",
"tsym":"ACC-EQ",
"exch":"NSE",
"Token":"22",
"Remarks":"test",
"validity":"DAY",
"actid":"MOHINI",
"trantype":"B",
"prctyp":"LMT",
"Qty":1,
"Prc":"1305.00",
"C":"C",
"prd":"C",
"ordersource":"API",
"d":"1900.00",
"oivariable":[
  {
    "var_name": "x",
    "d": "5645"
  }
 ]
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
Request parameters jData and jKey are sent as URL form-encoded data (jData=...&jKey=...), where jData is the JSON object. The production base URL is `https://piconnect.flattrade.in/PiConnectAPI/` (the curl sample uses a `BaseURL` placeholder). The only request json_field is uid (mandatory).

Field-to-possible-value bindings verified against the page-34 image: validity = DAY or GTT; trantype* = B / S; prctyp = LMT / SL-LMT / DS / 2L / 3L (the possible-value cell wraps across two text lines but is one cell); prd = C / M / H; ret* = DAY / EOS / IOC. The response-fields table spans pages 34-35 (qty*, prc*, emsg continue on page 35). The prctyp response row has a possible-value cell but no description text in the table.

The sample success JSON uses mixed-case keys (Al_id, Token, Remarks, Qty, Prc) that differ from the lowercase table field names (al_id, token, remarks, qty, prc), and includes extra keys not in the field table (Qty, Prc, C, ordersource, oivariable) plus a nested oivariable array of {var_name, d}. The doc lists ret in the response-fields table but it does not appear in the sample success response. The mandatory markers on trantype*, ret*, qty*, prc* appear carried over from the order-entry/GTT-placement field definitions even though this is a read operation.
