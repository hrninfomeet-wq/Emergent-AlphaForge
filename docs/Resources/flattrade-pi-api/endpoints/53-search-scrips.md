# Search Scrips

**Category:** Account & Reference · **Type:** REST · **Method:** POST  
**Path:** `/SearchScrip` · **URL:** `https://piconnect.flattrade.in/PiConnectAPI/SearchScrip`  
**Source:** PDF pages 85-86

## Summary
Search Scrips returns a list of matching scrips (contracts) for a given search text, optionally scoped to a chosen exchange. It is a POST call whose jData JSON body carries the logged-in user id, the search text and an optional exchange, returning each match's exchange, trading symbol, token, and contract precision/tick/lot details.

## Request — Query Parameters
| Parameter | Required | Possible values | Description |
|---|---|---|---|
| jData* | yes |  | Should send json object with fields in below list (the JSON body containing uid, stext, exch). |
| jKey* | yes |  | Key Obtained on login success. |

## Request — jData JSON fields
| Field | Required | Possible values | Description |
|---|---|---|---|
| uid* | yes |  | Logged in User Id |
| stext* | yes |  | Search Text |
| exch | no |  | Exchange (Select from 'exarr' Array provided in User Details response) |

## Sample request
```bash
curl --location 'https://BaseURL/SearchScrip' \
--header 'Content-Type: application/json' \
--data 'jData={    
    "uid": "FZ00000", 
    "stext": "NIFTY", 
    "exch": "NSE"
}&jKey=GHUDWU53H32MTHPA536Q32WR'
```

## Response fields
| Field | Possible values | Description |
|---|---|---|
| stat | Ok or Not_Ok | Market watch success or failure indication. |
| values |  | Array of json objects. (object fields given in below table) |
| emsg |  | This will be present only in case of errors. That is : 1) Invalid Input 2) Session Expired |
| exch | NSE, BSE, NFO ... | Exchange (field within each object of the 'values' array) |
| tsym |  | Trading symbol of the scrip (contract) (field within each object of the 'values' array) |
| token |  | Token of the scrip (contract) (field within each object of the 'values' array) |
| pp |  | Price precision (field within each object of the 'values' array) |
| ti |  | Tick size (field within each object of the 'values' array) |
| ls |  | Lot size (field within each object of the 'values' array) |

## Sample success response
```json
{
"stat": "Ok",
"values": [
{
"exch": "NSE",
"token": "18069",
"tsym": "REL100NAV-EQ"
},
{
"exch": "NSE",
"token": "24225",
"tsym": "RELAXO-EQ"
},
{
"exch": "NSE",
"token": "4327",
"tsym": "RELAXOFOOT-EQ"
},
{
"exch": "NSE",
"token": "18068",
"tsym": "RELBANKNAV-EQ"
},
{
"exch": "NSE",
"token": "2882",
"tsym": "RELCAPITAL-EQ"
},
{
"exch": "NSE",
"token": "18070",
"tsym": "RELCONSNAV-EQ"
},
{
"exch": "NSE",
"token": "18071",
"tsym": "RELDIVNAV-EQ"
},
{
"exch": "NSE",
"token": "18072",
"tsym": "RELGOLDNAV-EQ"
},
{
"exch": "NSE",
"token": "2885",
"tsym": "RELIANCE-EQ"
},
{
"exch": "NSE",
"token": "15068",
"tsym": "RELIGARE-EQ"
},
{
"exch": "NSE",
"token": "553",
"tsym": "RELINFRA-EQ"
},
{
"exch": "NSE",
"token": "18074",
"tsym": "RELNV20NAV-EQ"
}
]
}
```

## Sample failure response
```json
{
"stat":"Not_Ok",
"emsg":"No Data : "
}
```

## Notes
- The documented production base URL is `https://piconnect.flattrade.in/PiConnectAPI/SearchScrip`; the curl sample uses the placeholder `https://BaseURL/SearchScrip`, which must be substituted with the real base URL.
- Request is form-style: jData carries the JSON object and jKey is appended in the same body separated by '&' (jData={...}&jKey=...). Content-Type is application/json per the sample.
- The exch field in jData is optional; when provided it must be selected from the 'exarr' array in the User Details response.
- In the success response, each element of the 'values' array shown in the sample contains exch, token and tsym; the response-field table also documents pp (price precision), ti (tick size) and ls (lot size) as object fields, though these do not appear in the provided sample objects.
- emsg appears only on errors (e.g. Invalid Input or Session Expired); the failure sample returns stat=Not_Ok with emsg 'No Data : '.
- The cleaned source text misspells the jData field-group header as 'feilds' (page 85); this is a typo in the source PDF, not an actual field name.
