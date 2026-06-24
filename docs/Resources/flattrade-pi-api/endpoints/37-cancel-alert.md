# Cancel Alert

**Category:** Alerts · **Type:** REST · **Method:** POST  
**Path:** `/CancelAlert` · **URL:** `https://piconnect.flattrade.in/PiConnectAPI/CancelAlert`  
**Source:** PDF pages 60-61

## Summary
Cancels a previously placed price alert. Send a POST request with a `jData` JSON object identifying the alert (`al_id`) plus the `jKey` session token; a successful response returns the alert id and a cancel/delete status indication.

## Request — Query Parameters
| Parameter | Required | Possible values | Description |
|---|---|---|---|
| jData* | yes | | Should send json object with fields in below list |
| jKey* | yes | | Key Obtained on login success. |

## Request — jData JSON fields
| Field | Required | Possible values | Description |
|---|---|---|---|
| uid | no | | User id of the logged in user. |
| al_id* | yes | | Alert Id |

## Sample request
```bash
curl --location 'https://BaseURL/CancelAlert' \
--header 'Content-Type: application/json' \
--data 'jData={
    "uid": "FZ00000", 
    "actid": "FZ00000"
}&jKey=GHUDWU53H32MTHPA536Q32WR'
```

## Response fields
| Field | Possible values | Description |
|---|---|---|
| stat | | alert success or failure indication. |
| request_time | | This will be present only in a successful response. |
| al_id | | Alert Id |
| emsg | | This will be present only in case of errors. That is : 1) Invalid Input 2) Session Expired |

## Sample success response
```json
[
{
"request_time":"15:03:33 08-04-2021",
"stat":"Oi delete success",
"al_id":"21040800000008"
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
- The curl example uses a placeholder `BaseURL`; the real base is `https://piconnect.flattrade.in/PiConnectAPI`, so the full endpoint is `.../CancelAlert`.
- Request is form-style: `jData` is a JSON object and `jKey` is appended as a separate body/query parameter (`jData={...}&jKey=...`).
- DOC INCONSISTENCY: the documented jData fields are `uid` (optional) and `al_id*` (mandatory), but the sample curl `--data` body instead contains `"uid"` and `"actid"` and does NOT include `al_id` — this looks like a copy-paste artifact from another endpoint. To cancel an alert you must supply `al_id`.
- `request_time` format is `HH:MM:SS DD-MM-YYYY`.
- In the PDF the JSON sample renders the `al_id` key/value with curly/smart quotes (“ ”) — a PDF artifact; real responses use standard JSON double quotes (transcribed here as straight quotes).
