# Get Time Price Data (Chart Data)

**Category:** Market Info · **Type:** REST · **Method:** POST  
**Path:** `/TPSeries` · **URL:** `https://piconnect.flattrade.in/PiConnectAPI/TPSeries`  
**Source:** PDF pages 51-53

## Summary
Returns time-price (intraday chart) candle series for a token over a time window. POST to `/TPSeries` with a `jData` JSON object (uid, exch, token, st, et, intrv) plus the `jKey` session key; the response is a JSON array of per-interval OHLC/vwap/volume/OI records, newest interval first.

## Request — Query Parameters
| Parameter | Required | Possible values | Description |
|---|---|---|---|
| jData* | yes | | Should send json object with fields in below list |
| jKey* | yes | | Key Obtained on login success. |

## Request — jData JSON fields
| Field | Required | Possible values | Description |
|---|---|---|---|
| uid* | yes | | Logged in User Id |
| exch* | yes | | Exchange |
| token* | yes | | |
| st | no | | Start time (seconds since 1 jan 1970) |
| et | no | | End Time (seconds since 1 jan 1970) |
| intrv | no | 1 / 3 / 5 / 10 / 15 / 30 / 60 / 120 | chart intervals |

## Sample request
```bash
curl --location 'https://BaseURL/TPSeries' \
--header 'Content-Type: application/json' \
--data 'jData={
    "uid": "FZ00000",    
    "exch": "NSE", 
    "token": "23456", 
    "st": "12315", 
    "et": "4874564", 
    "intrv": "1"
}&jKey=GHUDWU53H32MTHPA536Q32WR'
```

## Response fields
| Field | Possible values | Description |
|---|---|---|
| stat | Ok / Not_Ok | TPData success indication (in the success object) / TPData failure indication (in the failure object). |
| time | DD/MM/CCYY hh:mm:ss | Timestamp of the interval. |
| into | | Interval open |
| inth | | Interval high |
| intl | | Interval low |
| intc | | Interval close |
| intvwap | | Interval vwap |
| intv | | Interval volume |
| v | | volume |
| intoi | | Interval io change |
| oi | | oi |
| emsg | | This will be present only in case of errors. |

## Sample success response
```json
[
{
"stat":"Ok",
"time":"02-06-2020 15:46:23",
"into":"0.00",
"inth":"0.00",
"intl":"0.00",
"intc":"0.00",
"intvwap":"0.00",
"intv":"0",
"intoi":"0",
"v":"980515",
"oi":"128702"
},
{
"stat":"Ok",
"time":"02-06-2020 15:45:23",
"into":"0.00",
"inth":"0.00",
"intl":"0.00",
"intc":"0.00",
"intvwap":"0.00",
"intv":"0",
"intoi":"0",
"v":"980515",
"oi":"128702"
},
{
"stat":"Ok",
"time":"02-06-2020 15:44:23",
"into":"0.00",
"inth":"0.00",
"intl":"0.00",
"intc":"0.00",
"intvwap":"0.00",
"intv":"0",
"intoi":"0",
"v":"980515",
"oi":"128702"
},
{
"stat":"Ok",
"time":"02-06-2020 15:43:23",
"into":"1287.00",
"inth":"1287.00",
"intl":"0.00",
"intc":"1287.00",
"intvwap":"128702.00",
"intv":"4",
"intoi":"128702",
"v":"980515",
"oi":"128702"
},
{
"stat":"Ok",
"time":"02-06-2020 15:42:23",
"into":"0.00",
"inth":"0.00",
"intl":"0.00",
"intc":"0.00",
"intvwap":"0.00",
"intv":"0",
"intoi":"0",
"v":"980511",
"oi":"128702"
}
]
```

## Sample failure response
```json
{
"stat":"Not_Ok",
"emsg":"Session Expired : Invalid Session Key"
}
```

## Notes
- URL in the doc is `https://piconnect.flattrade.in/PiConnectAPI/TPSeries`; the curl example uses a `https://BaseURL/TPSeries` placeholder.
- Request is sent as form/query-style data: `jData={...}&jKey=...` in the POST body (Content-Type: application/json header per the curl, but jData and jKey are concatenated as a query string, not a pure JSON body).
- `st` (start time) and `et` (end time) are epoch seconds (seconds since 1 Jan 1970).
- `intrv` (chart interval) accepts only the discrete values 1 / 3 / 5 / 10 / 15 / 30 / 60 / 120 (minutes).
- Response is a JSON ARRAY of per-interval objects, newest interval first (descending time).
- Per-record field order in the success sample is stat, time, into, inth, intl, intc, intvwap, intv, intoi, v, oi (note intv/intoi come before v/oi).
- Success records carry `stat:"Ok"`; failure returns a single object `stat:"Not_Ok"` with `emsg`.
- The page-51 top fragment shows a different failure string `Invalid Input : Missing uid or exch or bskt or tb or crt`; its field list (bskt/tb/crt) is copy-paste leftover from another endpoint and is NOT accurate for TPSeries. The canonical TPSeries failure example is the page-52 `Session Expired : Invalid Session Key`.
- jData fields marked `*` in the doc (uid, exch, token) are mandatory; st, et, intrv are shown without `*` (optional in the table). The token description cell is blank in the doc.
