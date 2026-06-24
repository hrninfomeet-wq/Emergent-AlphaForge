# Positions Book

**Category:** Orders & Trades · **Type:** REST · **Method:** POST  
**Path:** `/PositionBook` · **URL:** `https://piconnect.flattrade.in/PiConnectAPI/PositionBook`  
**Source:** PDF pages 27-29

## Summary
Retrieves the user's Positions Book via a POST call. On success it returns a JSON array of position objects (one per contract) covering day buy/sell, carry-forward, open quantities, net position, LTP, realized PnL and unrealized MTOM; on failure it returns a single JSON object.

## Request — Query Parameters
| Parameter | Required | Possible values | Description |
|---|---|---|---|
| jData* | yes |  | Should send json object with fields in below list. |
| jKey* | yes |  | Key Obtained on login success. |

## Request — jData JSON fields
| Field | Required | Possible values | Description |
|---|---|---|---|
| uid* | yes |  | Logged in User Id. |
| actid* | yes |  | Account Id of logged in user. |

## Sample request
```bash
curl --location 'https://BaseURL/PositionBook' \
--header 'Content-Type: application/json' \
--data 'jData={
    "uid": "FZ00000",
    "actid": "FZ00000"
}&jKey=GHUDWU53H32MTHPA536Q32WR'
```

## Response fields
| Field | Possible values | Description |
|---|---|---|
| stat | Ok / Not_Ok | Position book success or failure indication. |
| exch |  | Exchange Segment. |
| tsym |  | Trading symbol / contract. |
| token |  | Contract token. |
| uid |  | User Id. |
| actid |  | Account Id. |
| prd |  | Product name to be shown. |
| netqty |  | Net Position quantity. |
| netavgprc |  | Net position average price. |
| daybuyqty |  | Day Buy Quantity. |
| daysellqty |  | Day Sell Quantity. |
| daybuyavgprc |  | Day Buy average price. |
| daysellavgprc |  | Day buy average price. (per doc; appears to be Day Sell average price) |
| daybuyamt |  | Day Buy Amount. |
| daysellamt |  | Day Sell Amount. |
| cfbuyqty |  | Carry Forward Buy Quantity. |
| cforgavgprc |  | Original Avg Price. |
| cfsellqty |  | Carry Forward Sell Quantity. |
| cfbuyavgprc |  | Carry Forward Buy average price. |
| cfsellavgprc |  | Carry Forward Buy average price. (per doc; appears to be Carry Forward Sell average price) |
| cfbuyamt |  | Carry Forward Buy Amount. |
| cfsellamt |  | Carry Forward Sell Amount. |
| totsellavgprc |  | Total Sell Avg Price. |
| lp |  | LTP. |
| rpnl |  | RealizedPNL. |
| urmtom |  | UnrealizedMTOM. (Can be recalculated in LTP update: = netqty * (lp from web socket - netavgprc) * prcftr) |
| bep |  | Break even price. |
| openbuyqty |  | (no description given in doc) Open Buy Quantity. |
| opensellqty |  | (no description given in doc) Open Sell Quantity. |
| openbuyamt |  | (no description given in doc) Open Buy Amount. |
| opensellamt |  | (no description given in doc) Open Sell Amount. |
| openbuyavgprc |  | (no description given in doc) Open Buy average price. |
| opensellavgprc |  | (no description given in doc) Open Sell average price. |
| mult |  | (no description given in doc) Multiplier. |
| pp |  | (no description given in doc) Price precision. |
| prcftr |  | gn*pn/(gd*pd). (Price factor) |
| ti |  | Tick size. |
| ls |  | Lot size. |
| instname |  | Instrument Name. |
| request_time |  | This will be present only in a failure response. (In the dedicated failure-response table on page 29 its description is given as: Response received time.) |
| emsg |  | Error message. (Present only in a failure response.) |

## Sample success response
```json
[
{
"stat":"Ok",
"uid":"POORNA",
"actid":"POORNA",
"exch":"NSE",
"tsym":"ACC-EQ",
"prarr":"C",
"pp":"2",
"ls":"1",
"ti":"5.00",
"mult":"1",
"prcftr":"1.000000",
"daybuyqty":"2",
"daysellqty":"2",
"daybuyamt":"2610.00",
"daybuyavgprc":"1305.00",
"daysellamt":"2610.00",
"daysellavgprc":"1305.00",
"cfbuyqty":"0",
"cfsellqty":"0",
"cfbuyamt":"0.00",
"cfbuyavgprc":"0.00",
"cfsellamt":"0.00",
"cfsellavgprc":"0.00",
"openbuyqty":"0",
"opensellqty":"23",
"openbuyamt":"0.00",
"openbuyavgprc":"0.00",
"opensellamt":"30015.00",
"opensellavgprc":"1305.00",
"netqty":"0",
"netavgprc":"0.00",
"lp":"0.00",
"urmtom":"0.00",
"rpnl":"0.00",
"cforgavgprc":"0.00"
}
]
```

## Sample failure response
```json
{
"stat":"Not_Ok",
"request_time":"14:14:11 26-05-2020",
"emsg":"Error Occurred : 5 \"no data\""
}
```

## Notes
Success response is a JSON ARRAY of objects (one per contract); failure response is a single JSON object. The base URL is `https://piconnect.flattrade.in/PiConnectAPI`; the curl example uses a `BaseURL` placeholder. Query params (jData, jKey) are sent as a url-encoded form body separated by `&`, with jData being the JSON object. `urmtom` can be recalculated on LTP updates as: `netqty * (lp from web socket - netavgprc) * prcftr`. `prcftr = gn*pn/(gd*pd)`. `request_time` and `emsg` appear only in a failure response.

The source has TWO separate failure tables: (1) the success response-fields table lists `request_time` with the description "This will be present only in a failure response."; (2) a dedicated failure-response table (page 29) lists `stat` (Not_Ok, "Position book request failure indication."), `request_time` ("Response received time.") and `emsg` ("Error message.").

DOC INCONSISTENCY: `daysellavgprc` is described as "Day buy average price" and `cfsellavgprc` as "Carry Forward Buy average price" in the source table — these descriptions appear to be copy-paste errors (should read Day Sell / Carry Forward Sell average price respectively). The sample success response also contains a `prarr` field which is not documented in the response-fields table, while several documented fields (`token`, `totsellavgprc`, `bep`, `instname`) do not appear in the sample. The fields `openbuyqty`, `opensellqty`, `openbuyamt`, `opensellamt`, `openbuyavgprc`, `opensellavgprc`, `mult`, and `pp` have BLANK description cells in the source table (page 28); their meanings here are inferred from their names.
