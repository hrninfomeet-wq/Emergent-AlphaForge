# Get Payin Report

**Category:** Funds · **Type:** REST · **Method:** POST  
**Path:** `/GetPayinReport` · **URL:** `https://piconnect.flattrade.in/PiConnectAPI/GetPayinReport`  
**Source:** PDF pages 65-66

## Summary
Returns the pay-in (add-fund) report for an account over a date range. A POST call whose jData JSON body carries the account id and a from/to date window; the success response is a JSON array of transaction records, each with a transaction reference number, status and amount.

## Request — Query Parameters
| Parameter | Required | Possible values | Description |
|---|---|---|---|
| jData* | yes |  | Should send json object with fields in below list. |
| jKey* | yes |  | Key Obtained on login success. |

## Request — jData JSON fields
| Field | Required | Possible values | Description |
|---|---|---|---|
| actid* | yes |  | Login users account ID. |
| from_date* | yes |  | From date. |
| to_date* | yes |  | To date. |

## Sample request
```bash
curl --location 'https://BaseURL/GetPayinReport' \
--header 'Content-Type: application/json' \
--data 'jData={    
    "actid": "FZ00000", 
    "from_date": "",
    "to_date": ""
}&jKey=GHUDWU53H32MTHPA536Q32WR'
```

## Response fields
| Field | Possible values | Description |
|---|---|---|
| stat |  | success or failure indication. |
| actid |  | This will be present only in a successful response. |
| trans_ref_num |  | transaction reference number (number which defines each transaction). |
| tran_status | ADD_FUND_ST_COMPLETE_STR | This is used to indicate the status of transaction. |
| amt |  | Amount. |
| emsg |  | Error message; present in a failure response (e.g. session expired). Not listed in the doc's response table but appears in the failure sample. |

## Sample success response
```json
[
{
"stat":"Ok",
"actid":"GURURAJ",
"trans_ref_num":"20211250000001",
"tran_status":"Complete",
"amt":"10000.00"
},
{
"stat":"Ok",
"actid":"GURURAJ",
"trans_ref_num":"20211250000002",
"tran_status":"Complete",
"amt":"10000.00"
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
- The success response is a JSON ARRAY of transaction objects (one per pay-in), not a single object.
- The doc's response-table "Possible value" column lists `ADD_FUND_ST_COMPLETE_STR` for `tran_status` (the value is flattened across three OCR rows — `ADD_FUND_S` / `T_COMPLETE` / `_STR` — but reads as one token on the page image), while the sample success body shows `tran_status` as the literal string `"Complete"`. Treat `ADD_FUND_ST_COMPLETE_STR` as the documented status constant; real payloads may use a short form like `"Complete"`.
- `stat` possible values are inferred from the samples (`"Ok"` on success, `"Not_Ok"` on failure); the doc text only says "success or failure indication".
- `from_date` / `to_date` are mandatory, but their exact format is not given in the doc (the sample sends them empty `""`).
- `jKey` (session token from login) is passed as a query parameter alongside `jData`, not inside the jData JSON.
- The request jData carries only `actid` / `from_date` / `to_date` — there is no `uid` field here (unlike the neighbouring FundsPayOutReq).
- `BaseURL` in the curl example resolves to `https://piconnect.flattrade.in/PiConnectAPI`.
