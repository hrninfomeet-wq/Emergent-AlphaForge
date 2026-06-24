# WebSocket General Guidelines & Connect

**Category:** WebSocket · **Type:** WebSocket · **Method:** WebSocket  
**Path:** `/PiConnectWSAPI/` · **URL:** `wss://piconnect.flattrade.in/PiConnectWSAPI/`  
**Source:** PDF pages 68-69

## Summary
Flattrade's WebSocket API endpoint (`wss://piconnect.flattrade.in/PiConnectWSAPI/`) for streaming data. As soon as the socket connection is established, the client must send a CONNECT frame (`t="a"`) carrying the User id, account id and login session token to authenticate the stream; the server replies with a connect acknowledgement frame (`t="ak"`) whose `s` field is `Ok` on success or `Not_Ok` on an invalid user id or session id.

## Request — jData JSON fields
| Field | Required | Possible values | Description |
|---|---|---|---|
| t | no | a | 'a' represents connect task. |
| uid | no |  | User ID. |
| actid | no |  | Account id. |
| source | no | API | Source should be same as login request. |
| accesstoken | no |  | User Session Token. |

## Sample request
```bash
curl --location 'wss://piconnect.flattrade.in/PiConnectWSAPI/' 
--data '

{
    "t": "a",
    "uid": "FZ00000",
    "actid": "FZ00000",
    "source": "API", 
    "accesstoken": "GHUDWU53H32MTHPA536Q32WR"
}
'
```

## Response fields
| Field | Possible values | Description |
|---|---|---|
| t | ak | 'ak' represents connect acknowledgement. |
| uid |  | User ID. |
| s | Ok or Not_Ok (in case of invalid user id or session id) | Connection status. Ok or Not_Ok (in case of invalid user id or session id). |

## Notes
- This is the WebSocket entry point. Connect to `wss://piconnect.flattrade.in/PiConnectWSAPI/` over a WebSocket (not plain HTTP, despite the doc showing a curl example).
- GENERAL GUIDELINES (verbatim from page 68): (1) As soon as connection is done, a connection request should be sent with User id and login session id. (2) All input and output messages will be in json format.
- The CONNECT request frame uses `t="a"` (connect task); the acknowledgement frame uses `t="ak"`. A successful connection returns `s="Ok"`; an invalid user id or session id returns `s="Not_Ok"`.
- `source` should be the same value used in the login request (shown as `"API"`).
- `accesstoken` is the User Session Token obtained from login.
- No field in the CONNECT request table is marked with a trailing `*`, so per the doc's asterisk convention none are flagged mandatory (uid/actid/source/accesstoken are nonetheless required in practice to authenticate the stream).
- No dedicated success/failure JSON sample is shown for the CONNECT frame itself (only the curl request block and the field-level acknowledgement table). The sample JSON in page 68's right column (`"actid":"GURURAJ"`,`"tran_status":"88"` and the `"Not_Ok"` / `-103 ... is Already Canceled` failure) belongs to the PRECEDING section, NOT to CONNECT, so it is omitted here.
- The CONNECT section spans the bottom of page 68 (heading, guidelines, `t` field) and the top of page 69 (uid/actid/source/accesstoken request fields + the RESPONSE table). It is immediately followed by SUBSCRIBE TOUCHLINE (`t="t"`) on page 69, which is a separate section.
