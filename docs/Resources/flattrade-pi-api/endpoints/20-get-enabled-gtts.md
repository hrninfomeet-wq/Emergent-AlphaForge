# Get Enabled GTTs

**Category:** Orders & Trades · **Type:** REST · **Method:** POST  
**Path:** `/GetEnabledGTTs` · **URL:** `https://piconnect.flattrade.in/PiConnectAPI/GetEnabledGTTs`  
**Source:** PDF pages 35-36

## Summary
Retrieves the list of GTT (Good Till Triggered) alert types enabled for the logged-in user. A POST call carrying the user's `uid` returns an array of supported alert types (`ai_ts`), each element exposing an `ai_t` code such as `ATP` or `LTP`.

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
curl --location 'https://BaseURL/GetEnabledGTTs' \
--header 'Content-Type: application/json' \
--data 'jData={
    "uid": "FZ00000"   
}&jKey=GHUDWU53H32MTHPA536Q32WR'
```

## Response fields
| Field | Possible values | Description |
|---|---|---|
| stat | Ok / Not_Ok | GTT order success or failure indication. |
| request_time |  | This will be present only in a successful response. |
| ai_ts |  | Array of alert types. Each element is an object with an `ai_t` field (e.g. `{"ai_t":"ATP"}`, `{"ai_t":"LTP"}`). |
| emsg |  | Error message present only in case of failure (e.g. "Session Expired : Invalid Session Key"). |

## Sample success response
```json
{
"stat":"Ok",
"request_time":"04062021121503",
"ai_ts":
[
{"ai_t":"ATP"},
{"ai_t":"LTP"}
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
Despite the GET-sounding name, this is a POST request. Parameters are sent as URL-encoded form data: `jData` (a JSON object) and `jKey`, joined with `&` (`jData={...}&jKey=...`), not as a pure JSON body. The curl example shows `https://BaseURL/GetEnabledGTTs` as a placeholder; the real base URL is `https://piconnect.flattrade.in/PiConnectAPI`. `request_time` in the success sample is a timestamp string in DDMMYYYYHHMMSS format (e.g. `04062021121503`). `ai_ts` is an array of objects, each carrying a single `ai_t` alert-type code (e.g. ATP, LTP). `jKey` is the session token obtained at login.

Provenance: the RESPONSE DETAILS table on page 35 lists only `stat`, `request_time`, and `ai_ts` (no Possible-value cells). `stat`'s possible values (Ok / Not_Ok) are inferred from the success/failure samples, not an explicit table cell. `emsg` is NOT in the response table; it is documented here from the sample failure response. The section is fully contained on page 35 and ends where PLACE OCO ORDER begins on page 36.
