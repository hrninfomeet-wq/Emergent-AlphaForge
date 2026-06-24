# Get Option Chain

**Category:** Market Info · **Type:** REST · **Method:** POST  
**Path:** `/GetOptionChain` · **URL:** `https://piconnect.flattrade.in/PiConnectAPI/GetOptionChain`  
**Source:** PDF pages 53-55

## Summary
Returns the option chain for the underlying of a given option/future trading symbol. You POST a `jData` JSON object (`uid`, `exch`, `tsym`, `strprc`, `cnt`) plus a `jKey`, and receive an array of contract objects spanning `cnt` strikes on each side of the supplied mid price for both PUT and CALL.

## Request — Query Parameters
| Parameter | Required | Possible values | Description |
|---|---|---|---|
| jData* | yes |  | Should send json object with fields in below list |
| jKey* | yes |  | Key Obtained on login success. |

## Request — jData JSON fields
| Field | Required | Possible values | Description |
|---|---|---|---|
| uid* | yes |  | Logged in User Id |
| tsym* | yes |  | Trading symbol of any of the option or future. Option chain for that underlying will be returned. (use url encoding to avoid special char error for symbols like M&M) |
| exch* | yes |  | Exchange (UI need to check if exchange in NFO / CDS / MCX / or any other exchange which has options, if not don't allow) |
| strprc* | yes |  | Mid price for option chain selection |
| cnt* | yes |  | Number of strike to return on one side of the mid price for PUT and CALL. (example cnt is 4, total 16 contracts will be returned, if cnt is is 5 total 20 contract will be returned) |

## Sample request
```bash
curl --location 'https://BaseURL/GetOptionChain' \
--header 'Content-Type: application/json' \
--data 'jData={
    "uid": "FZ00000",    
    "exch": "NSE", 
    "tsym": "ACC-EQ",
    "strprc": "2567", 
    "cnt": "5"    
}&jKey=GHUDWU53H32MTHPA536Q32WR'
```

## Response fields
| Field | Possible values | Description |
|---|---|---|
| stat | Ok or Not_Ok | Market watch success or failure indication. |
| values |  | Array of json objects. (object fields given in below table) |
| emsg |  | This will be present only in case of errors. That is : 1) Invalid Input 2) Session Expired |
| values[].exch | NSE, BSE, NFO ... | Exchange |
| values[].tsym |  | Trading symbol of the scrip (contract) |
| values[].token |  | Token of the scrip (contract) |
| values[].optt |  | Option Type |
| values[].strprc |  | Strike price |
| values[].pp |  | Price precision |
| values[].ti |  | Tick size |
| values[].ls |  | Lot size |

## Notes
- The response is a top-level object with `stat`, `values` (array of contract objects), and `emsg`. The per-contract fields (`exch`, `tsym`, `token`, `optt`, `strprc`, `pp`, `ti`, `ls`) live inside each object of the `values` array ("Json Fields of object in values Array" table on page 55).
- `cnt` is per-side AND applied to both PUT and CALL: `cnt=4` returns 16 contracts total; `cnt=5` returns 20 contracts total.
- URL-encode `tsym` to avoid special-char errors for symbols like M&M.
- `exch` must be an exchange that has options (NFO / CDS / MCX, etc.); the doc says the UI should reject exchanges without options.
- `strprc` supplies the mid price around which the chain is centered.
- `emsg` is present only on errors: 1) Invalid Input, 2) Session Expired.
- The doc shows no Sample Success Response / Sample Failure Response block for this endpoint — only the curl request. The JSON immediately following on page 55 ("request_time" / "cal_price" / etc.) belongs to the next section, Get Option Greek.
- `https://BaseURL/GetOptionChain` in the curl is a placeholder; the real base URL is `https://piconnect.flattrade.in/PiConnectAPI`.
