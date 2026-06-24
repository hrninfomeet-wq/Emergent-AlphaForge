# Funds Payout Request

**Category:** Funds · **Type:** REST · **Method:** POST  
**Path:** `/FundsPayOutReq` · **URL:** `https://piconnect.flattrade.in/PiConnectAPI/FundsPayOutReq`  
**Source:** PDF pages 65-66

## Summary
Submits a funds payout (withdrawal) request for a logged-in user's account. POST to `/FundsPayOutReq` with a `jData` JSON body (uid, actid, payout, optional remarks) plus the session `jKey`; returns the transaction status, request time, and transaction id.

## Request — Query Parameters
| Parameter | Required | Possible values | Description |
|---|---|---|---|
| jData* | yes |  | Should send json object with fields in below list. |
| jKey* | yes |  | Key Obtained on login success. |

## Request — jData JSON fields
| Field | Required | Possible values | Description |
|---|---|---|---|
| uid* | yes |  | User id of the logged in user. |
| actid* | yes |  | Login users account ID. |
| payout* | yes |  | Payout amount. |
| remarks | no |  | Any message Entered during order entry. |

## Sample request
```bash
curl --location 'https://BaseURL/FundsPayOutReq' \
--header 'Content-Type: application/json' \
--data 'jData={    
    "actid": "FZ00000", 
    "actid": "FZ00000",
    "payout": ""
}&jKey=GHUDWU53H32MTHPA536Q32WR'
```

## Response fields
| Field | Possible values | Description |
|---|---|---|
| stat |  | Tran status (transaction status). |
| request_time |  | This will be present only in a successful response. |
| trn_id |  | Tran id (transaction id). The response table row for this field is garbled in the source; the success sample uses the key `trn_id`. |

## Sample success response
```json
{
"request_time":"15:52:27 10-05-2021",
"trn_id":"20211300000030",
"stat":"W"
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
- The curl sample uses placeholder `https://BaseURL/`; the real base URL is `https://piconnect.flattrade.in/PiConnectAPI` (full endpoint `https://piconnect.flattrade.in/PiConnectAPI/FundsPayOutReq`).
- `jData` and `jKey` are passed as URL-encoded form data joined by `&` in the `--data` body (`jData={...}&jKey=...`), not as separate query string params.
- The curl sample's `jData` is malformed in the source PDF: it lists `actid` twice and omits both `uid` (a mandatory field per the table) and `remarks`; `payout` is shown empty. Treat it as illustrative only — per the field table the body should contain `uid`, `actid`, `payout` (all mandatory) and optional `remarks`.
- Response-field naming is inconsistent: the source response table calls the id row "Tran id" while the success JSON uses the key `trn_id`. The table prose says `request_time` is present only in a successful response.
- `stat` value in the success sample is `"W"` (likely a pending/withdrawal-requested state); failure responses carry `stat:"Not_Ok"` plus an `emsg` error message (`emsg` is not documented in the response table).
- All "Possible value" cells in the request/json/response tables are blank in the source (no enums provided).
