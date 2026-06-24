# Cancel Payout

**Category:** Funds · **Type:** REST · **Method:** POST  
**Path:** `/CancelPayout` · **URL:** `https://piconnect.flattrade.in/PiConnectAPI/CancelPayout`  
**Source:** PDF pages 67-68

## Summary
Cancels a previously requested payout (fund withdrawal) for the given account. A POST call is made to `/CancelPayout` with a jData JSON object identifying the user, account and the transaction reference number of the payout to cancel, plus the jKey session token.

## Request — Query Parameters
| Parameter | Required | Possible values | Description |
|---|---|---|---|
| jData* | yes |  | Should send json object with fields in below list. |
| jKey* | yes |  | Key Obtained on login success. |

## Request — jData JSON fields
| Field | Required | Possible values | Description |
|---|---|---|---|
| actid* | yes |  | Login users account ID |
| uid* | yes |  | User id of the logged in user. |
| trans_ref_num* | yes |  | transaction reference number (number which defines each transaction) |
| brkname | no |  | Broker name |

## Sample request
```bash
curl --location 'https://BaseURL/CancelPayout' \
--header 'Content-Type: application/json' \
--data 'jData={    
    "uid": "FZ00000",
    "actid": "FZ00000",
    "trans_ref_num": ""    
}&jKey=GHUDWU53H32MTHPA536Q32WR'
```

## Response fields
| Field | Possible values | Description |
|---|---|---|
| stat |  | success or failure indication. |
| actid |  | This will be present only in a successful response. |
| tran_status |  | This is used to indicate the status of transaction |
| request_time |  | This will be present only in a successful response. |

## Sample success response
```json
{
"request_time":"18:59:25 12-05-2021",
"stat":"Ok",
"actid":"GURURAJ",
"tran_status":"88"
}
```

## Sample failure response
```json
{
"stat":"Not_Ok",
"request_time":"18:58:47 12-05-2021",
"emsg":"Error Occurred : -103 20211300000033 is Already Canceled"
}
```

## Notes
- Mandatory fields (marked with * in the doc): `actid`, `uid`, `trans_ref_num`. `brkname` is optional (no asterisk).
- The jData object is sent URL/form style as `jData={...}&jKey=...` in the request body, matching the curl example; the Content-Type header in the sample is `application/json`.
- Replace `BaseURL` in the sample curl with the real host `https://piconnect.flattrade.in/PiConnectAPI` when calling `/CancelPayout`.
- `trans_ref_num` is the transaction reference number that uniquely defines each payout transaction (e.g. `20211270000002`); it identifies which payout to cancel. (The sample curl leaves it as an empty string as a placeholder.)
- In a successful response, `request_time`, `actid` and `tran_status` are present; `tran_status` is a numeric status code (e.g. `"88"`).
- Failure case observed: attempting to cancel an already-canceled payout returns `stat=Not_Ok` with `emsg` `'Error Occurred : -103 <ref> is Already Canceled'`. A session-key problem returns `'Session Expired : Invalid Session Key'`.
