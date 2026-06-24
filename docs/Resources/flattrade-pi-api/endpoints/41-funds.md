# Get Max Payout Amount

**Category:** Funds · **Type:** REST · **Method:** POST  
**Path:** `/GetMaxPayoutAmount` · **URL:** `https://piconnect.flattrade.in/PiConnectAPI/GetMaxPayoutAmount`  
**Source:** PDF pages 64-65

## Summary
Funds endpoint to retrieve the maximum payout amount available for a logged-in user's account. A POST call with jData (uid, actid) and jKey returns the account id and the maximum payout amount.

## Request — Query Parameters
| Parameter | Required | Possible values | Description |
|---|---|---|---|
| jData* | yes |  | Should send json object with fields in below list |
| jKey* | yes |  | Key Obtained on login success. |

## Request — jData JSON fields
| Field | Required | Possible values | Description |
|---|---|---|---|
| uid* | yes |  | User id of the logged in user. |
| actid* | yes |  | Login users account ID |

## Sample request
```bash
curl --location 'https://BaseURL/GetMaxPayoutAmount' \
--header 'Content-Type: application/json' \
--data 'jData={
    "uid": "FZ00000",  
    "actid": "FZ00000" 
}&jKey=GHUDWU53H32MTHPA536Q32WR'
```

## Response fields
| Field | Possible values | Description |
|---|---|---|
| stat |  | success or failure indication. |
| request_time |  | This will be present only in a successful response. |
| actid |  | Account id |
| payout |  | Maximum payout amount |

## Sample success response
```json
{
"request_time":"15:52:26 10-05-2021",
"stat":"Ok",
"actid":"C-GURURAJ",
"payout":"21200.20"
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
The "FUNDS" section begins on PDF page 64 with the GET MAX PAYOUT AMOUNT endpoint only. The subsequent ALL-CAPS titles (FUNDS PAYOUT REQUEST, GET PAYIN REPORT) on page 65 are separate endpoints outside this focal section and were excluded. Both jData fields (uid, actid) are mandatory (trailing '*'). The jData JSON is sent URL-form-encoded alongside jKey (jData={...}&jKey=...) per the Noren/PiConnect convention. In the cleaned-text success response a stray page number "112" appears between "request_time" and "stat" (an OCR/pagination artifact); it was removed from the reconstructed sample since it is not part of the JSON (the page image confirms a clean JSON object). No "Possible value" enum cells were populated for any request or response field in the source table.
