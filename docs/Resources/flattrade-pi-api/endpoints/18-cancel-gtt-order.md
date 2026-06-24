# Cancel GTT Order

**Category:** Orders & Trades · **Type:** REST · **Method:** POST  
**Path:** `/CancelGTTOrder` · **URL:** `https://piconnect.flattrade.in/PiConnectAPI/CancelGTTOrder`  
**Source:** PDF pages 33-34

## Summary
Cancels an existing GTT (Good Till Triggered) alert/order via a POST call. The request supplies the user id (uid) and the alert id (al_id) of the GTT order to cancel; a successful response echoes the al_id with a request_time and a delete-success status.

## Request — Query Parameters
| Parameter | Required | Possible values | Description |
|---|---|---|---|
| jData* | yes |  | Should send json object with fields in below list. |
| jKey* | yes |  | Key Obtained on login success. |

## Request — jData JSON fields
| Field | Required | Possible values | Description |
|---|---|---|---|
| uid | no |  | User id of the logged in user |
| al_id | no |  | Alert Id |

## Sample request
```bash
curl --location 'https://BaseURL/CancelGTTOrder' \
--header 'Content-Type: application/json' \
--data 'jData={
    "uid": "FZ00000",    
    "al_id": "21041500000013"   
}&jKey=GHUDWU53H32MTHPA536Q32WR'
```

## Response fields
| Field | Possible values | Description |
|---|---|---|
| stat |  | GTT order success or failure indication. On success the sample shows "Oi delete success"; on failure "Not_Ok". |
| request_time |  | This will be present only in a successful response. |
| al_id |  | Alert Id (echoed back; the success sample JSON key is "Al_id"). |
| emsg |  | This will be present only in case of errors. That is : 1) Invalid Input  2) Session Expired |

## Sample success response
```json
[
{
"request_time":"12:20:01 15-04-2021",
"stat":"Oi delete success",
"Al_id":"21041500000013"
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
- Real base URL is `https://piconnect.flattrade.in/PiConnectAPI`; the curl sample uses a placeholder `https://BaseURL/CancelGTTOrder`.
- The POST body is form-style: `jData={...JSON...}` and `jKey=...` joined with `&` (not a pure JSON body), despite the `Content-Type: application/json` header in the curl sample.
- Neither uid nor al_id is marked mandatory in the doc (no trailing `*`); only the jData and jKey request parameters are starred. In practice al_id identifies which GTT order to cancel, so it is effectively required.
- Success response is a JSON array; failure response is a JSON object.
- Response key casing differs from the field table: the success sample uses `"Al_id"` (capital A) while the doc field list calls it al_id.
- emsg appears only on error and conveys either 1) Invalid Input or 2) Session Expired.
