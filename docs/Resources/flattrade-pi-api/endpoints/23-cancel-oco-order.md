# Cancel OCO Order

**Category:** Orders & Trades · **Type:** REST · **Method:** POST  
**Path:** `/CancelOCOOrder` · **URL:** `https://piconnect.flattrade.in/PiConnectAPI/CancelOCOOrder`  
**Source:** PDF pages 40-41

## Summary
Cancels an existing OCO (One-Cancels-Other) order/alert via a POST call. The request identifies the OCO order by its alert id (al_id), and the response indicates success or failure.

## Request — Query Parameters
| Parameter | Required | Possible values | Description |
|---|---|---|---|
| jData* | yes |  | Should send json object with fields in below list |
| jKey* | yes |  | Key Obtained on login success. |

## Request — jData JSON fields
| Field | Required | Possible values | Description |
|---|---|---|---|
| uid | no |  | User id of the logged in user. |
| al_id* | yes |  | Alert Id |

## Sample request
```bash
curl --location 'https://BaseURL/CancelOCOOrder' \
--header 'Content-Type: application/json' \
--data 'jData={
    "uid": "FZ00000",    
    "al_id": "21083000000040"
}&jKey=GHUDWU53H32MTHPA536Q32WR'
```

## Response fields
| Field | Possible values | Description |
|---|---|---|
| stat |  | OCO order success or failure indication. |
| request_time |  | This will be present only in a successful response. |
| al_id |  | Alert Id |
| emsg |  | This will be present only in case of errors. That is : 1) Invalid Input 2) Session Expired |

## Sample success response
```json
{
"request_time":"17:41:02 30-08-2021",
"stat":"Oi delete success"
,"al_id":"21083000000040"
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
The curl example uses `BaseURL` as a placeholder; the actual host is `https://piconnect.flattrade.in/PiConnectAPI` (full endpoint `.../CancelOCOOrder`). The body is sent as form data of the shape `jData={...}&jKey=...`. In the request JSON, only `al_id` is mandatory (trailing `*`); `uid` is optional. On success, `stat` returns a free-text value (e.g. "Oi delete success") rather than the usual "Ok", and `request_time` + `al_id` are echoed back; on failure `stat` is "Not_Ok" with an `emsg` describing the error (Invalid Input or Session Expired). The "Possible value" column is blank for every field in this section. The entire section is contained on page 40 (page 41 begins the Holdings section).
