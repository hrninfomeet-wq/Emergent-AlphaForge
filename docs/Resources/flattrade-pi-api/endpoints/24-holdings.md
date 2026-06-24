# Holdings

**Category:** Holdings & Limits · **Type:** REST · **Method:** POST  
**Path:** `/Holdings` · **URL:** `https://piconnect.flattrade.in/PiConnectAPI/Holdings`  
**Source:** PDF pages 40-42

## Summary
Retrieves the user's holdings (long-term DP/demat positions) for a given product. A POST call returning, on success, a JSON array of holding objects, each containing an `exch_tsym` array plus quantity and uploaded-price fields.

## Request — Query Parameters
| Parameter | Required | Possible values | Description |
|---|---|---|---|
| jData* | yes |  | Should send json object with fields in below list. |
| jKey* | yes |  | Key Obtained on login success. |

## Request — jData JSON fields
| Field | Required | Possible values | Description |
|---|---|---|---|
| uid* | yes |  | Logged in User Id |
| actid* | yes |  | Account id of the logged in user. |
| prd* | yes |  | Product name |

## Sample request
```bash
curl --location 'https://BaseURL/Holdings' \
--header 'Content-Type: application/json' \
--data 'jData={
    "uid": "FZ00000",    
    "actid": "FZ00000", 
    "prd": "C"
}&jKey=GHUDWU53H32MTHPA536Q32WR'
```

## Response fields
| Field | Possible values | Description |
|---|---|---|
| stat | Ok or Not_Ok | Holding request success or failure indication. |
| exch_tsym |  | Array of objects exch_tsym objects as defined below. |
| holdqty |  | Holding quantity |
| dpqty |  | DP Holding quantity |
| npoadqty |  | Non Poa display quantity |
| colqty |  | Collateral quantity |
| benqty |  | Beneficiary quantity |
| unplgdqty |  | Unpledged quantity |
| brkcolqty |  | Broker Collateral |
| btstqty |  | BTST quantity |
| btstcolqty |  | BTST Collateral quantity |
| usedqty |  | Holding used today |
| upldprc |  | Average price uploaded along with holdings |
| exch_tsym.exch | NSE, BSE, NFO ... | Exchange (field of the exch_tsym object in the values array). |
| exch_tsym.tsym |  | Trading symbol of the scrip (contract). |
| exch_tsym.token |  | Token of the scrip (contract). |
| exch_tsym.pp |  | Price precision. |
| exch_tsym.ti |  | Tick size. |
| exch_tsym.ls |  | Lot size. |
| request_time |  | Response received time (present in the failure response). |
| emsg |  | Error message (present in the failure response). |

## Sample success response
```json
[
{
"stat":"Ok",
"exch_tsym":[
{
"exch":"NSE",
"token":"13",
"tsym":"ABB-EQ"
}
],
"holdqty":"2000000",
"colqty":"200",
"btstqty":"0",
"btstcolqty":"0",
"usedqty":"0",
"upldprc" : "1800.00"
},
{
"stat":"Ok",
"exch_tsym":[
{
"exch":"NSE",
"token":"22",
"tsym":"ACC-EQ"
}
],
"holdqty":"2000000",
"colqty":"200",
"btstqty":"0",
"btstcolqty":"0",
"usedqty":"0",
"upldprc" : "1400.00"
}
]
```

## Sample failure response
```json
{
"stat":"Not_Ok",
"emsg":"Invalid Input : Missing uid or actid or prd."
}
```

## Notes
On success the response is a JSON **array** of holding objects (not a single object); each object has its own `stat` and `exch_tsym` array. The failure response is a single JSON object with `stat=Not_Ok` plus `request_time` and `emsg`.

The doc (page 42) gives two derived formulas:
- **Valuation** = btstqty + holdqty + brkcolqty + unplgdqty + benqty + Max(npoadqty, dpqty) − usedqty
- **Salable** = btstqty + holdqty + unplgdqty + benqty + dpqty − usedqty

The `exch_tsym` sub-object fields (`exch`, `tsym`, `token`, `pp`, `ti`, `ls`) are documented on page 42 under "Exch_tsym object". The curl uses the placeholder host `https://BaseURL/Holdings`; the real base URL is `https://piconnect.flattrade.in/PiConnectAPI`. The sample uses product `prd="C"` (CNC/cash).

The success-response sample only shows a subset of the documented fields; `dpqty`, `npoadqty`, `benqty`, `unplgdqty`, `brkcolqty` are documented but absent from the sample, and the `exch_tsym` sample objects omit `pp`/`ti`/`ls`. The failure-table `stat` description on page 42 reads "Position book request failure indication." — a doc copy-paste carryover from the position-book section, not Holdings-specific.
