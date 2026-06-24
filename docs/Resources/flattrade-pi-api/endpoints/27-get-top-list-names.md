# Get Top List Names

**Category:** Market Info · **Type:** REST · **Method:** POST  
**Path:** `/TopListName` · **URL:** `https://piconnect.flattrade.in/PiConnectAPI/TopListName`  
**Source:** PDF pages 48-50

## Summary
Returns the list of available Top List basket/criteria pairs (e.g. NSEBL/VOLUME, NSEEQ/LTP, NSEALL/VALUE) for a given exchange. The returned bskt + crt pairs are the inputs to the Get Top List (TopList) request.

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
curl --location 'https://BaseURL/TopListName' \
--header 'Content-Type: application/json' \
--data 'jData={
    "uid": "FZ00000",    
    "exch": "NSE"
}&jKey=GHUDWU53H32MTHPA536Q32WR'
```

## Response fields
| Field | Possible values | Description |
|---|---|---|
| stat | Ok or Not_Ok | TopListNames success or failure indication. |
| values |  | Array Of Basket, Criteria pair. |
| request_time |  | This will be present only in a successful response. |
| emsg |  | This will be present only in case of errors. |
| bskt |  | Basket name (field inside each Basket, Criteria pair object in the values array). |
| crt |  | criteria (field inside each Basket, Criteria pair object in the values array). |

## Sample success response
```json
{
"request_time":"13:08:22 03-06-2020",
"values":[
{
"bskt":"NSEBL",
"crt":"VOLUME"
},
{
"bskt":"NSEBL",
"crt":"LTP"
},
{
"bskt":"NSEBL",
"crt":"VALUE"
},
{
"bskt":"NSEEQ",
"crt":"VOLUME"
},
{
"bskt":"NSEEQ",
"crt":"LTP"
},
{
"bskt":"NSEEQ",
"crt":"VALUE”
},
{
"bskt":"NSEALL",
"crt":"VOLUME"
},
{
"bskt":"NSEALL",
"crt":"LTP"
},
{
"bskt":"NSEALL",
"crt":"VALUE"
}
]
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
- The jData JSON is url-style appended to jKey in the request body: `jData={...}&jKey=...`; Content-Type is application/json.
- The curl example uses a placeholder host `https://BaseURL/TopListName`; the real base URL is `https://piconnect.flattrade.in/PiConnectAPI` (so the full endpoint is `.../PiConnectAPI/TopListName`).
- Each element of the `values` array is a "Basket, Criteria pair object" with fields `bskt` (basket name) and `crt` (criteria). These pairs are the inputs to the Get Top List (TopList) endpoint.
- `request_time` format is `HH:MM:SS DD-MM-YYYY` (e.g. `13:08:22 03-06-2020`).
- Source artifact: in the success response one `crt` value is shown with a curly closing quote (`"VALUE”`) instead of a straight quote — preserved verbatim above; the intended value is `"VALUE"`.
- Source layout note: page 48's "GET TOP LIST NAMES" title begins at the bottom of the page, and the "Sample Output" block above it (idxname/token pairs, e.g. Nifty 50/26000) belongs to the neighbouring Get Index List response, NOT this endpoint. Page 49 is authoritative for this section's response shape (bskt/crt fields + labelled Sample Success/Failure Response blocks).
