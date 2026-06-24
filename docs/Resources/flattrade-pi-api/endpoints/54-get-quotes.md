# Get Quotes

**Category:** Account & Reference · **Type:** REST · **Method:** POST  
**Path:** `/GetQuotes` · **URL:** `https://piconnect.flattrade.in/PiConnectAPI/GetQuotes`  
**Source:** PDF pages 86-89

## Summary
Get Quotes returns a full market-data snapshot (LTP, OHLC, volume, 5-level market depth bid/ask price/quantity/orders, circuit limits, contract metadata) for a single scrip identified by exchange and contract token. It is a POST call carrying jData (a JSON object) and jKey (the login session key) as query parameters.

## Request — Query Parameters
| Parameter | Required | Possible values | Description |
|---|---|---|---|
| jData* | yes | | Should send json object with fields in below list. |
| jKey* | yes | | Key Obtained on login success. |

## Request — jData JSON fields
| Field | Required | Possible values | Description |
|---|---|---|---|
| uid* | yes | | Logged in User Id |
| exch | no | | Exchange |
| token | no | | Contract Token |

## Sample request
```bash
curl \
jData={"uid":"FZ00000", "exch":"NSE",
"token":"22"}&jKey=GHUDWU53H32MTHPA536Q32WR
```

## Response fields
| Field | Possible values | Description |
|---|---|---|
| stat | Ok or Not_Ok | Watch list update success or failure indication. |
| request_time | | It will be present only in a successful response. |
| exch | NSE, BSE, NFO ... | Exchange |
| tsym | | Trading Symbol |
| cname | | Company Name |
| symname | | Symbol Name |
| seg | | Segment |
| instname | | Intrument Name |
| isin | | ISIN |
| pp | | Price precision |
| ls | | Lot Size |
| ti | | Tick Size |
| mult | | Multiplier |
| uc | | Upper circuit limit |
| lc | | Lower circuit limit |
| prcftr_d | | Price factor ((GN / GD) * (PN/PD)) |
| token | | Token |
| lp | | LTP |
| h | | Day High Price |
| l | | Day Low Price |
| v | | Volume |
| ltq | | Last trade quantity |
| ltt | | Last trade time |
| ltd | dd-mm-yy | Last Trade Date |
| bp1 | | Best Buy Price 1 |
| sp1 | | Best Sell Price 1 |
| bp2 | | Best Buy Price 2 |
| sp2 | | Best Sell Price 2 |
| bp3 | | Best Buy Price 3 |
| sp3 | | Best Sell Price 3 |
| bp4 | | Best Buy Price 4 |
| sp4 | | Best Sell Price 4 |
| bp5 | | Best Buy Price 5 |
| sp5 | | Best Sell Price 5 |
| bq1 | | Best Buy Quantity 1 |
| sq1 | | Best Sell Quantity 1 |
| bq2 | | Best Buy Quantity 2 |
| sq2 | | Best Sell Quantity 2 |
| bq3 | | Best Buy Quantity 3 |
| sq3 | | Best Sell Quantity 3 |
| bq4 | | Best Buy Quantity 4 |
| sq4 | | Best Sell Quantity 4 |
| bq5 | | Best Buy Quantity 5 |
| sq5 | | Best Sell Quantity 5 |
| bo1 | | Best Buy Orders 1 |
| so1 | | Best Sell Orders 1 |
| bo2 | | Best Buy Orders 2 |
| so2 | | Best Sell Orders 2 |
| bo3 | | Best Buy Orders 3 |
| so3 | | Best Sell Orders 3 |
| bo4 | | Best Buy Orders 4 |
| so4 | | Best Sell Orders 4 |
| bo5 | | Best Buy Orders 5 |
| so5 | | Best Sell Orders 5 |
| und_exch | | Underlying Exch seg |
| und_tk | | Underlying Token |
| ord_msg | | Order Message |
| sptprc | | Spot Price [ # ] |
| issuecap | | issue capital |
| e_date | | end date |

## Sample success response
```json
{
"request_time":"12:05:21 18-05-2021",
"stat":"Ok"
,"exch":"NSE",
"tsym":"ACC-EQ",
"cname":"ACC LIMITED",
"symname":"ACC",
"seg":"EQT",
"instname":"EQ",
"isin":"INE012A01025",
"pp":"2",
"ls":"1",
"ti":"0.05",
"mult":"1",
"uc":"2093.95",
"lc":"1713.25",
"prcftr_d":"(1 / 1 ) * (1 / 1)",
"token":"22",
"lp":"0.00",
"h":"0.00",
"l":"0.00",
"v":"0",
"ltq":"0",
"ltt":"05:30:00",
"bp1":"2000.00",
"sp1":"0.00",
"bp2":"0.00",
"sp2":"0.00",
"bp3":"0.00",
"sp3":"0.00",
"bp4":"0.00",
"sp4":"0.00",
"bp5":"0.00",
"sp5":"0.00",
"bq1":"2",
"sq1":"0",
"bq2":"0",
"sq2":"0",
"bq3":"0",
"sq3":"0",
"bq4":"0",
"sq4":"0",
"bq5":"0",
"sq5":"0",
"bo1":"2",
"so1":"0",
"bo2":"0",
"so2":"0",
"bo3":"0",
"so3":"0",
"bo4":"0",
"so4":"0",
"bo5":"0",
"So5":"0"
}
```

## Sample failure response
```json
{
"stat":"Not_Ok",
"request_time":"10:50:54 10-12-2020",
"emsg":"Error Occurred : 5 \"no data\""
}
```

## Notes
- Endpoint path is `/GetQuotes`; full URL `https://piconnect.flattrade.in/PiConnectAPI/GetQuotes`. The doc intro text on page 87 mistakenly reads "To get place order you need to make a POST call to the following url" (copy/paste artifact from the order docs) but the URL and operation are Get Quotes.
- Request is a POST; `jData` and `jKey` are passed as URL/query parameters and are mandatory (marked with `*`). Inside `jData` only `uid` is mandatory (marked with `*`); `exch` and `token` are not starred in the doc but are required to identify the scrip and appear in the curl example.
- The success response sample shows `ltt` as a time string (`"05:30:00"`); a separate `ltd` field (format dd-mm-yy) carries Last Trade Date.
- `prcftr_d` is the price factor expressed as `((GN / GD) * (PN/PD))`; the sample renders it as `"(1 / 1 ) * (1 / 1)"` (note the stray space).
- Market depth is 5 levels deep: `bp/sp` = best buy/sell price, `bq/sq` = quantity, `bo/so` = orders, levels 1-5. `sptprc` (Spot Price) is annotated "[ # ]" in the doc. `und_exch` (Underlying Exch seg) and `und_tk` (Underlying Token) are returned for derivative contracts.
- Doc artifacts faithfully preserved: the response `stat` description is copied from the watch-list section ("Watch list update success or failure indication"); `instname` is spelled "Intrument Name"; and the success-response sample capitalizes the last key as `"So5"` though the table lists it as `so5`.
- The Get Quotes failure response is the page-88 sample. The page-86 `{"stat":"Not_Ok","emsg":"No Data : "}` block (plus the preceding `values[]` array of exch/token/tsym objects) belongs to the PRECEDING watchlist/search-scrip section, not Get Quotes.
