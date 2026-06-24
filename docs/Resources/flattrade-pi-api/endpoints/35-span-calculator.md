# Span Calculator

**Category:** Market Info · **Type:** REST · **Method:** POST  
**Path:** `/SpanCalc` · **URL:** `https://piconnect.flattrade.in/PiConnectAPI/SpanCalc`  
**Source:** PDF pages 58-59

## Summary
Span Calculator computes the span and exposure margin for a basket of derivative positions. You POST a jData JSON object containing an account id and an array of position objects (each describing exchange / instrument / symbol / expiry / option-type / strike and buy/sell/net quantities); the response returns span and exposure margin values, plus variants that ignore the buyqty/sellqty open quantities.

## Request — Query Parameters
| Parameter | Required | Possible values | Description |
|---|---|---|---|
| jData* | yes |  | Should send json object with fields in below list. |

## Request — jData JSON fields
| Field | Required | Possible values | Description |
|---|---|---|---|
| actid* | yes |  | Any Account id, preferably actual account id if sending from post login screen. |
| pos* | yes | Array of json objects | Array of json objects. (object fields given in below table.) |
| exch | no | NFO, CDS, MCX ... | Exchange (field of each object in the pos array). |
| instname | no | FUTSTK, FUTIDX, OPTSTK, FUTCUR... | Instrument name (field of each object in the pos array). |
| symname | no | USDINR, ACC, ABB, NIFTY.. | Symbol name (field of each object in the pos array). |
| expd | no | 2020-10-29 | YYYY-MM-DD format (field of each object in the pos array). |
| optt | no | CE, PE | Option Type (field of each object in the pos array). |
| strprc | no | 11900.00, 71.0025 | Strike price (field of each object in the pos array). |
| buyqty | no |  | Buy Open Quantity (field of each object in the pos array). |
| sellqty | no |  | Sell Open Quantity (field of each object in the pos array). |
| netqty | no |  | Net traded quantity (field of each object in the pos array). |

## Sample request
```bash
curl --location 'https://BaseURL/SpanCalc' \
--header 'Content-Type: application/json' \
--data 'jData={
    "actid": "FZ00000", 
    "pos": [
        {
            "exch": "NFO",
            "instname": "OPTSTK",
            "symname": "ACC",
            "expd": "2020-10-29",
            "optt": "CE",
            "strprc": "11900.00",
            "buyqty": "0",
            "sellqty": "0",
            "netqty": "100"
        }
    ]       
}&jKey=GHUDWU53H32MTHPA536Q32WR'
```

## Response fields
| Field | Possible values | Description |
|---|---|---|
| stat | Ok or Not_Ok | Market watch success or failure indication. |
| span | Span value | Span value. |
| expo | Exposure margin | Exposure margin. |
| span_trade | Span value ignoring input fields buyqty, sellqty | Span value ignoring input fields buyqty, sellqty. |
| expo_trade | Exposure margin ignoring input fields buyqty, sellqty | Exposure margin ignoring input fields buyqty, sellqty. |

## Notes
- The documented URL is `https://piconnect.flattrade.in/PiConnectAPI/SpanCalc`; the sample curl uses a placeholder host `https://BaseURL/SpanCalc`.
- The request is sent as a form-encoded body `jData={...}&jKey=...` with `Content-Type: application/json` (per the doc's curl). `jKey` is the session/API key appended to the request body in the sample; it is NOT listed as a documented query parameter (the only documented query parameter is `jData*`).
- `actid` and `pos` are the only asterisked (mandatory) jData fields. The per-position object sub-fields (exch, instname, symname, expd, optt, strprc, buyqty, sellqty, netqty) carry no asterisk in the doc.
- The section spans two pages: title, URL, query params, `actid`/`pos`, and the first pos sub-field (`exch`) are on page 58; the remaining pos sub-fields and the response details are on page 59.
- No dedicated sample success/failure JSON response is provided for SpanCalc in the source. The JSON visible in the page-58 right column (`{"stat":"Ok","norentm":"02-05-1975 08:48:52","msgtyp":"Admin Message","dmsg":"Test Msg All Message Recovery2"}`) belongs to the preceding section, not Span Calculator, and was excluded.
