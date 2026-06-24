# Modify Alert

**Category:** Alerts · **Type:** REST · **Method:** POST  
**Path:** `/ModifyAlert` · **URL:** `https://piconnect.flattrade.in/PiConnectAPI/ModifyAlert`  
**Source:** PDF pages 61-62

## Summary
Modifies an existing pending price alert. Send a POST request with a jData JSON body (the original alert parameters with the changed values) and the session jKey; returns a success/failure status with the alert id.

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
| ai_t* | yes |  | Alert Type, should be original alert type, can't be modified. |
| al_id | no |  | Alert Id. |
| validity* | yes | DAY or GTT | Validity. |
| d | no |  | Data to be compared with LTP. |
| remarks* | yes |  | Any message Entered during order entry. |

## Sample request
```bash
curl --location 'https://BaseURL/ModifyAlert' \
--header 'Content-Type: application/json' \
--data 'jData={
    "uid": "FZ00000", 
    "actid": "FZ00000", 
    "exch": "NSE",
    "tsym": "ACC-EQ",
    "ai_t": "", 
    "validity": "DAY", 
    "remarks": ""
}&jKey=GHUDWU53H32MTHPA536Q32WR'
```

## Response fields
| Field | Possible values | Description |
|---|---|---|
| stat |  | alert success or failure indication. |
| request_time |  | This will be present only in a successful response. |
| al_id |  | Alert Id. |
| emsg |  | This will be present only in case of errors. That is : 1) Invalid Input 2) Session Expired |

## Sample success response
```json
[
{
"request_time":"16:36:42 08-04-2021",
"stat":"Oi Replaced",
“al_id”:”21040800000013”
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
- The curl example uses `https://BaseURL/ModifyAlert` as a placeholder; the real endpoint is `https://piconnect.flattrade.in/PiConnectAPI/ModifyAlert`.
- The jData payload is sent as a form-encoded body in the form `jData={...}&jKey=...` (not a raw JSON body), even though `Content-Type` is `application/json` in the doc's example.
- `ai_t` (Alert Type) must be the ORIGINAL alert type — it cannot be changed by a modify call.
- The jData field table spans pages 61-62: `uid*`/`tsym*`/`exch*`/`ai_t*`/`al_id` on page 61, then `validity*`/`d`/`remarks*` on page 62. `validity` Possible value = "DAY or GTT".
- Required fields (trailing `*` in the table): uid, tsym, exch, ai_t, validity, remarks. NOT required (no `*`): al_id, d.
- The sample curl shows an extra `actid` field not listed in the field table; the field table lists uid/tsym/exch/ai_t/al_id/validity/d/remarks.
- Success response is a JSON array; failure response is a JSON object. Success `stat` is "Oi Replaced".
- The cleaned-text JSON uses curly/smart quotes (e.g. “al_id”) in places — reproduced verbatim from the source.
