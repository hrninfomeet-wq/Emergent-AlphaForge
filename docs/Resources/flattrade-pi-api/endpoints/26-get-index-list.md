# Get Index List

**Category:** Market Info · **Type:** REST · **Method:** POST  
**Path:** `/GetIndexList` · **URL:** `https://piconnect.flattrade.in/PiConnectAPI/GetIndexList`  
**Source:** PDF pages 47-48

## Summary
Retrieves the list of indices for a given exchange. A POST call returns an array of "Basket, Criteria pair" objects, each containing the index name (idxname) and the token used to subscribe to it.

## Request — Query Parameters
| Parameter | Required | Possible values | Description |
|---|---|---|---|
| jData* | yes |  | Should send json object with fields in below list |
| jKey* | yes |  | Key Obtained on login success. |

## Request — jData JSON fields
| Field | Required | Possible values | Description |
|---|---|---|---|
| uid* | yes |  | Logged in User Id |
| exch* | yes |  | Exchange |

## Sample request
```bash
curl --location 'https://BaseURL/GetIndexList' \
--header 'Content-Type: application/json' \
--data 'jData={
    "uid": "FZ00000",    
    "exch": "NSE"
}&jKey=GHUDWU53H32MTHPA536Q32WR'
```

## Response fields
| Field | Possible values | Description |
|---|---|---|
| stat | Ok or Not_Ok | Limits request success or failure indication. |
| values | Array Of Basket, Criteria pair. | Array of Basket, Criteria pair objects (each with idxname and token). |
| request_time |  | This will be present only in a successful response. |
| emsg |  | This will be present only in case of errors. |
| idxname |  | Basket, Criteria pair object field: Index Name |
| token |  | Basket, Criteria pair object field: Index token used to subscribe |

## Sample success response
```json
{
"request_time": "20:12:29 13-12-2020",
"values": [
{
"idxname": "HangSeng BeES-NAV",
"token": "26016"
},
{
"idxname": "India VIX",
"token": "26017"
},
{
"idxname": "Nifty 50",
"token": "26000"
},
{
"idxname": "Nifty IT",
"token": "26008"
},
{
"idxname": "Nifty Next 50",
"token": "26013"
},
{
"idxname": "Nifty Bank",
"token": "26009"
},
{
"idxname": "Nifty 500",
"token": "26004"
},
{
"idxname": "Nifty 100",
"token": "26012"
},
{
"idxname": "Nifty Midcap 50",
"token": "26014"
},
{
"idxname": "Nifty Realty",
"token": "26018"
},
]
}
```

## Notes
The "values" response field is an array of "Basket, Criteria pair" objects; each object has two fields: idxname (Index Name) and token (Index token used to subscribe). The curl example in the source uses a placeholder host `https://BaseURL/GetIndexList`, but the documented real endpoint is `https://piconnect.flattrade.in/PiConnectAPI/GetIndexList`. Request payload is sent as form-style data: `jData={json}&jKey={token}`. A successful response includes `request_time` (format "HH:MM:SS DD-MM-YYYY", e.g. "20:12:29 13-12-2020"); `emsg` appears only on errors. The sample success JSON in the source (titled "Sample Output:") shows only the values array and request_time (no top-level `stat` field) and ends with a trailing comma after the last array element — preserved verbatim.
