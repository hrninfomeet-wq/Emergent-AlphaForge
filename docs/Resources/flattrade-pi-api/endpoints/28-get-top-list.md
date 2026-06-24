# Get Top List

**Category:** Market Info · **Type:** REST · **Method:** POST  
**Path:** `/TopList` · **URL:** `https://piconnect.flattrade.in/PiConnectAPI/TopList`  
**Source:** PDF pages 50-51

## Summary
Returns the top or bottom contracts for a given exchange and basket, ranked by a chosen criteria. A POST call carrying jData (JSON request fields) and jKey (session token) as URL-form data, responding with an array containing a status object whose `values` is the list of top/bottom contract objects.

## Request — Query Parameters
| Parameter | Required | Possible values | Description |
|---|---|---|---|
| jData* | yes |  | Should send json object with fields in below list. |
| jKey* | yes |  | Key Obtained on login success. |

## Request — jData JSON fields
| Field | Required | Possible values | Description |
|---|---|---|---|
| uid* | yes |  | Logged in User Id |
| exch* | yes |  | Exchange |
| tb* | yes | T or B | Top or Bottom |
| bskt* | yes | bskt |  |
| crt* | yes | criteria |  |

## Sample request
```bash
curl --location 'https://BaseURL/TopList' \
--header 'Content-Type: application/json' \
--data 'jData={
    "uid": "FZ00000",    
    "exch": "NSE", 
    "tb": "T", 
    "bskt": "NSEALL", 
    "crt": "LTP"
}&jKey=GHUDWU53H32MTHPA536Q32WR'
```

## Response fields
| Field | Possible values | Description |
|---|---|---|
| stat | Ok or Not_Ok | TopList success or failure indication. |
| values |  | Array of top / bottom contracts object |
| request_time |  | This will be present only in a successful response. |
| emsg |  | This will be present only in case of errors. |
| tsym |  | Trading symbol (field within each top/bottom contracts object in the values array). |
| lp |  | LTP (within each contracts object). |
| c |  | Previous Close price (within each contracts object). |
| v |  | volume (within each contracts object). |
| value |  | Total traded value (within each contracts object). |
| oi |  | Open interest (within each contracts object). |
| pc |  | LTP percentage change (within each contracts object). |

## Sample success response
```json
[
{
"stat":"Ok",
"request_time":"15:44:45 03-06-2020",
"values":[
{
"tsym":"AIRAN-EQ",
"lp":"950.00",
"c":"915.00",
"v":"42705",
"value":"40185405.00",
"oi":"0",
"Pc":"3.83"
},
{
"tsym":"SHRENIK-EQ",
"lp":"1850.00",
"c":"1785.00",
"v":"206846",
"value":"368806418.00",
"oi":"0",
"Pc":"3.64”
},
{
"tsym":"REMSONSIND-EQ",
"lp":"6000.00",
"c":"5795.00",
"v":"3948",
"value":"22752324.00",
"Oi":"0",
"pc":"3.54"
},
{
"tsym":"AXISNIFTY-EQ",
"lp":"106700.00",
"c":"103301.00",
"v":"422",
"value":"43825544.00",
"oi":"0",
"Pc":"3.29"
}
]
}
]
```

## Sample failure response
```json
{
"stat":"Not_Ok",
"emsg":"Invalid Input : Missing uid or exch or bskt or tb or crt"
}
```

## Notes
- The curl example uses a placeholder host `https://BaseURL/TopList`; the real base URL is `https://piconnect.flattrade.in/PiConnectAPI/` so the actual endpoint is `https://piconnect.flattrade.in/PiConnectAPI/TopList`.
- Both jData and jKey are sent together as a single url-form `--data` payload joined by `&` (`jData={...}&jKey=...`), not as separate JSON body fields.
- All five jData fields (uid, exch, tb, bskt, crt) are mandatory (marked `*`); the failure response confirms missing any of them yields `Invalid Input : Missing uid or exch or bskt or tb or crt`.
- tb takes `T` (Top) or `B` (Bottom). In the sample, bskt=`NSEALL` and crt=`LTP` are concrete example values from the curl; the doc's "Possible value" cells for bskt and crt merely repeat `bskt` and `criteria` (no enumerated allowed values), and their Description cells are blank in the page-50 image.
- request_time format is `HH:MM:SS dd-mm-yyyy` (e.g. `15:44:45 03-06-2020`).
- The success response is a JSON array whose single element is a status object containing stat, request_time, and values; each element of `values` contains tsym, lp, c, v, value, oi, pc (per the page-51 TOP/BOTTOM CONTRACTS OBJECT field table).
- VERBATIM-source quirks reproduced (do NOT silently "correct"): the doc inconsistently capitalizes the field keys in the success JSON (`Pc` vs `pc` and `Oi` vs `oi` appear mixed), and one entry ends with a curly closing quote `3.64”` instead of a straight quote. The canonical field names per the field table are lowercase `pc` and `oi`.
