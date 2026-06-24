# Get Payout Report

**Category:** Funds · **Type:** REST · **Method:** POST  
**Path:** `/GetPayoutReport` · **URL:** `https://piconnect.flattrade.in/PiConnectAPI/GetPayoutReport`  
**Source:** PDF pages 66-67

## Summary
Retrieves the Funds Payout (withdrawal) report for a user's account over a date range. POST call returning a JSON array of payout transaction records, each with a transaction status and a (negative) amount.

## Request — Query Parameters
| Parameter | Required | Possible values | Description |
|---|---|---|---|
| jData* | yes |  | Should send json object with fields in below list. |
| jKey* | yes |  | Key Obtained on login success. |

## Request — jData JSON fields
| Field | Required | Possible values | Description |
|---|---|---|---|
| actid* | yes |  | Login users account ID |
| from_date* | yes |  | From date |
| to_date* | yes |  | To date |

## Sample request
```bash
curl --location 'https://BaseURL/GetPayoutReport' \
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
| trans_ref_num |  | transaction reference number (number which defines each transaction) |
| tran_status | WITHDRAW_ST_COMPLETE_STR | This is used to indicate the status of transaction |
| amt |  | Amount |
| emsg |  | Error message; present in a failure response (e.g. 'Session Expired : Invalid Session Key'). Not listed in the response-details table but appears in the sample failure response. |

## Sample success response
```json
[
{
"stat":"Ok",
"actid":"GURURAJ",
"trans_ref_num":"20211270000002",
"tran_status":"Complete",
"amt":"-1000.00"
},
{
"stat":"Ok",
"actid":"GURURAJ",
"trans_ref_num":"20211270000003",
"tran_status":"Complete",
"amt":"-100.00"
},
{
"stat":"Ok",
"actid":"GURURAJ",
"trans_ref_num":"20211270000004",
"tran_status":"Complete",
"amt":"-1000.00"
},
{
"stat":"Ok",
"actid":"GURURAJ",
"trans_ref_num":"20211270000005",
"tran_status":"Complete",
"amt":"-100.00"
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
- Documented BaseURL is `https://piconnect.flattrade.in/PiConnectAPI`; the curl example uses a placeholder `https://BaseURL/GetPayoutReport`.
- Request is sent as a combined data string of two params: `jData` (the JSON object, with `from_date`/`to_date` allowed to be empty strings as shown) and `jKey` appended after the jData JSON, separated by `&` — exactly as in the curl `--data` string. Content-Type in the curl example is `application/json`.
- The success response is a JSON **array** of payout transaction objects (not a single object).
- A payout is a withdrawal, so the sample amounts are **negative** numeric strings (e.g. `-1000.00`, `-100.00`); the `trans_ref_num` values are in the `20211270000xxx` series.
- In the sample success response the `tran_status` value is the literal string `Complete`, while the response-details table lists the enum-like possible value `WITHDRAW_ST_COMPLETE_STR` (documentation-only).
- Page-split caveat: the success array opens with `[` at the bottom of the page-66 right column and continues into the page-67 right column. The first array element (`trans_ref_num` `20211270000001`) falls in the page-66 right-column gap and is not legibly captured in the source text; the sample above reproduces the array as captured (elements `20211270000002`–`20211270000005`).
