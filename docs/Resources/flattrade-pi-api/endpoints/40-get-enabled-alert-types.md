# Get Enabled Alert Types

**Category:** Alerts · **Type:** REST · **Method:** POST  
**Path:** `/GetEnabledAlertTypes` · **URL:** `https://piconnect.flattrade.in/PiConnectAPI/GetEnabledAlertTypes`  
**Source:** PDF pages 63-64

## Summary
Returns the list of alert types (e.g. ATP, LTP, Perc. Change) that are enabled for the logged-in user. POST call requiring `jData` (a JSON object whose only field is `uid`) and `jKey` as request parameters.

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
curl --location 'https://BaseURL/GetEnabledAlertTypes' \
--header 'Content-Type: application/json' \
--data 'jData={
    "uid": "FZ00000"   
}&jKey=GHUDWU53H32MTHPA536Q32WR'
```

## Response fields
| Field | Possible values | Description |
|---|---|---|
| stat |  | alert success or failure indication. |
| request_time |  | This will be present only in a successful response. |
| ai_ts |  | Array of alert types |

## Sample success response
```json
{
"stat":"Ok",
"request_time":"04062021121503",
"ai_ts":
[
{"ai_t":"ATP"},
{"ai_t":"LTP"},
{"ai_t":"Perc. Change"}
]
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
The documented full URL host is `piconnect.flattrade.in` (the curl example uses a `BaseURL` placeholder which resolves to `https://piconnect.flattrade.in/PiConnectAPI`). The request body is sent as form data: `jData` (a JSON object, the only field being `uid`) plus `jKey`, joined with `&`. The `ai_ts` response is an array of objects each containing an `ai_t` field (the alert type code, e.g. ATP / LTP / Perc. Change); the response-fields table lists `ai_ts` as "Array of alert types" but the actual element key is `ai_t`, as seen in the success sample. `request_time` is only present on success. On failure `stat` is `Not_Ok` and an `emsg` field carries the error reason. The "Possible value" column is blank for every request parameter, jData field, and response field in the source tables.
