# Get Broker Msg

**Category:** Market Info · **Type:** REST · **Method:** POST  
**Path:** `/GetBrokerMsg` · **URL:** `https://piconnect.flattrade.in/PiConnectAPI/GetBrokerMsg`  
**Source:** PDF pages 57-58

## Summary
Retrieves broker/admin (Noren) messages for the logged-in user via a POST call. On success the response is a JSON array of message objects, each carrying a status, Noren time, message type, and the message body.

## Request — Query Parameters
| Parameter | Required | Possible values | Description |
|---|---|---|---|
| jData* | yes | Should send json object with fields in below list | URL-encoded JSON object containing the request body fields listed in the jData JSON fields table. |
| jKey* | yes | Key Obtained on login success. | Session key obtained on login success. |

## Request — jData JSON fields
| Field | Required | Possible values | Description |
|---|---|---|---|
| uid* | yes | | Logged in User Id |

## Sample request
```bash
curl --location 'https://BaseURL/GetBrokerMsg' \
--header 'Content-Type: application/json' \
--data 'jData={
    "uid": "FZ00000"      
}&jKey=GHUDWU53H32MTHPA536Q32WR'
```

## Response fields
| Field | Possible values | Description |
|---|---|---|
| stat | Ok | Broker Msg success or failure indication. |
| dmsg | | This will be present only in case of success. Number of days to expiry will be present in same. |
| norentm | | Noren Time |
| msgtyp | Admin Message | Message type. Not listed in the doc's success field table; appears only in the sample response objects. |

## Sample success response
```json
[
{
"stat": "Ok",
"norentm": "02-05-1975 08:48:52",
"msgtyp": "Admin Message",
"dmsg": "Test Msg All Message Recovery2"
},
{
"stat": "Ok",
"norentm": "02-05-1975 08:48:52",
"msgtyp": "Admin Message",
"dmsg": "Test Msg All Message Recovery2"
}
]
```

## Notes
- Despite "get" in the name, this is a POST call. Parameters are passed as `jData` (URL-encoded JSON) plus `jKey` in the request body, using the same `jData=...&jKey=...` convention as other Flattrade pi endpoints.
- In the curl example the URL is shown as the placeholder `https://BaseURL/GetBrokerMsg`; the actual base URL is `https://piconnect.flattrade.in/PiConnectAPI`.
- The jData JSON fields table for this endpoint (page 58) lists exactly one field, `uid*`. The `exch` field visible on page 57 belongs to the PREVIOUS endpoint (Exchange Message), not Get Broker Msg.
- The success response is a JSON ARRAY of message objects (not a single object); each object carries `stat`/`norentm`/`msgtyp`/`dmsg`. The sample spans pages 57-58 with two such objects.
- The doc's success response-details table lists only `stat`, `dmsg`, and `norentm`; `msgtyp` is not in that table but appears in every sample response object.
- The `dmsg` description ("Number of days to expiry will be present in same.") reads as boilerplate carried over from another section; for this endpoint `dmsg` holds the broker/admin message body.
- No explicit failure-response sample or failure field table is shown for this endpoint in the source pages.
