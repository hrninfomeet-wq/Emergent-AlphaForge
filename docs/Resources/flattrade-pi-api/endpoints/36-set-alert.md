# Set Alert

**Category:** Alerts · **Type:** REST · **Method:** POST  
**Path:** `/SetAlert` · **URL:** `https://piconnect.flattrade.in/PiConnectAPI/SetAlert`  
**Source:** PDF pages 59-60

## Summary
Sets a price alert for a trading symbol. Make a POST call with a jData JSON object describing the symbol, alert type, comparison value and validity; returns the created alert id on success.

## Request — Query Parameters
| Parameter | Required | Possible values | Description |
|---|---|---|---|
| jData* | yes |  | Should send json object with fields in below list |
| jKey* | yes |  | Key Obtained on login success. |

## Request — jData JSON fields
| Field | Required | Possible values | Description |
|---|---|---|---|
| uid* | yes |  | User id of the logged in user. |
| tsym* | yes |  | Trading symbol |
| exch* | yes |  | Exchange Segment |
| ai_t* | yes |  | Alert Type |
| validity* | yes | DAY or GTT | Validity |
| d | no |  | Data to be compared with LTP |
| remarks* | yes |  | Any message Entered during order entry. |

## Sample request
```bash
curl --location 'https://BaseURL/SetAlert' \
--header 'Content-Type: application/json' \
--data 'jData={
    "uid": "FZ00000", 
    "exch": "NSE",
    "tsym": "ACC-EQ",
    "ai_t": "",
    "validity": "DAY", 
    "remarks": ""
}&jKey=GHUDWU53H32MTHPA536Q32WR'
```

## Response fields
| Field | Possible values | Description |
|---|---|---|
| stat |  | alert success or failure indication. |
| request_time |  | This will be present only in a successful response. |
| al_id |  | Alert Id |
| emsg |  | This will be present only in case of errors. That is : 1) Invalid Input 2) Session Expired |

## Sample success response
```json
[
{
"request_time":"11:22:26 08-04-2021",
"stat":"Oi created",
“al_id”:”21040800000004”
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
The full request is sent as a form-urlencoded query-style string with two parts joined by '&': jData={...json...} and jKey=<session key>. The success response is a JSON ARRAY containing a single object (unlike the failure response, which is a bare object). The curl shows 'https://BaseURL/SetAlert' as a placeholder; the real base URL is https://piconnect.flattrade.in/PiConnectAPI. Field 'd' (data to be compared with LTP) is the only non-mandatory jData field; all others (uid, tsym, exch, ai_t, validity, remarks) are mandatory per the trailing '*'. validity possible values are 'DAY or GTT'. emsg appears only on error and signals Invalid Input or Session Expired. The success response uses curly/smart quotes around al_id verbatim in the source PDF.
