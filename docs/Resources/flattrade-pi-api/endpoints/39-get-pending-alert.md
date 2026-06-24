# Get Pending Alert

**Category:** Alerts · **Type:** REST · **Method:** POST  
**Path:** `/GetPendingAlert` · **URL:** `https://piconnect.flattrade.in/PiConnectAPI/GetPendingAlert`  
**Source:** PDF pages 62-63

## Summary
Retrieves the list of pending (active) price alerts for the logged-in user via a POST call. Each returned alert contains its type, id, trading symbol, exchange, token, remarks, validity, and the data value compared against the LTP.

## Request — Query Parameters
| Parameter | Required | Possible values | Description |
|---|---|---|---|
| jData* | yes |  | Should send json object with fields in below list |
| jKey* | yes |  | Key Obtained on login success. |

## Request — jData JSON fields
| Field | Required | Possible values | Description |
|---|---|---|---|
| uid* | yes |  | User id of the logged in user. |

## Sample request
```bash
curl --location 'https://BaseURL/GetPendingAlert' \
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
| emsg |  | This will be present only in case of errors. That is : 1) Invalid Input 2) Session Expired |

## Sample success response
```json
[
{
"Stat":"ok",
“ai_t”:”LTP_A”,
“al_id”:”21040800000008”,
“tsym”:”ACC-EQ”,
“exch”:”NSE”
“token”:”22”,
“remarks”:”test”,
“validity”:”DAY”,
“d”:”95000.00”
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
Per the doc table, the success response is a JSON ARRAY (list) of alert objects, while the failure response is a single object. The curl example uses a placeholder host `https://BaseURL/GetPendingAlert` — the real base URL is `https://piconnect.flattrade.in/PiConnectAPI`. The request body is x-www-form-urlencoded style: `jData` (the JSON object) and `jKey` joined with `&`. The success-response JSON in the doc is reproduced verbatim with the source's defects: it uses smart/curly quotes (e.g. “ai_t”), the first key is `"Stat"` (capital S) although the field table documents it as `stat`, and the `"exch":"NSE"` line is missing its trailing comma. Response field `ai_t` is the alert type; `d` is the data/price value compared against LTP.
