# Get EOD Chart Data

**Category:** Market Info · **Type:** REST · **Method:** POST  
**Path:** `/EODChartData` · **URL:** `https://piconnect.flattrade.in/PiConnectAPI/EODChartData`  
**Source:** PDF pages 53-54

## Summary
Retrieves end-of-day (EOD) historical OHLC + volume chart data for a given symbol over a from/to date range. POST call returning a JSON array of daily candle objects.

## Request — Query Parameters
| Parameter | Required | Possible values | Description |
|---|---|---|---|
| jData* | yes | | Should send json object with fields in below list. |
| jKey* | yes | | Key Obtained on login success. |

## Request — jData JSON fields
| Field | Required | Possible values | Description |
|---|---|---|---|
| sym* | yes | | Symbol name |
| from* | yes | | From date |
| to* | yes | | To date |

## Sample request
```bash
curl --location 'https://BaseURL/EODChartData' \
--header 'Content-Type: application/json' \
--data 'jData={
    "sym": "NSE:RELIANCE-EQ",
    "from": "1624838400",
    "to": "1663718400"}&jKey=GHUDWU53H32MTHPA536Q32WR'
```

## Response fields
| Field | Possible values | Description |
|---|---|---|
| time | DD/MM/CCYY hh:mm:ss |  |
| into | | Interval open |
| inth | | Interval high |
| intl | | Interval low |
| intc | | Interval close |
| ssboe | Date,Seconds in 1970 format |  |
| intv | | Interval volume |

## Sample success response
```json
[
"{
"time":"21-SEP-2022",
"into":"2496.75",
"inth":"2533.00",
"intl":"2495.00",
"intc":"2509.75",
"ssboe":"1663718400",
"intv":"4249172.00"
}",
"{
"time":"15-SEP-2022",
"into":"2583.00",
"inth":"2603.55",
"intl":"2556.75",
"intc":"2562.70",
"ssboe":"1663200000",
"intv":"4783723.00"
}",
"{
"time":"28-JUN-2021",
"into":"2122.00",
"inth":"2126.50",
"intl":"2081.00",
"intc":"2086.00",
"ssboe":"1624838400",
"intv":"9357852.00"
}"
]
```

## Notes
- Despite being a chart-data fetch, it is a POST (not GET); body is form-style: `jData={...json...}&jKey=...` with `Content-Type: application/json`.
- The doc leaves the "Possible value" cells for the jData fields sym/from/to empty (descriptions only: Symbol name / From date / To date); the curl sample shows `sym` as EXCHANGE:SYMBOL-SERIES (e.g. `NSE:RELIANCE-EQ`) and `from`/`to` as epoch-seconds (Unix timestamps), e.g. 1624838400 = 28-JUN-2021, 1663718400 = 21-SEP-2022.
- Response is a JSON ARRAY whose elements are JSON objects rendered as strings (each candle is a stringified JSON object); the sample is ordered most-recent-first.
- The response-table `time` possible-value reads "DD/MM/CCYY hh:mm:ss" but the actual sample `time` values render as "21-SEP-2022" style; the `ssboe` field carries the epoch-seconds timestamp.
- The curl uses placeholder host `BaseURL`; the real base URL is `https://piconnect.flattrade.in/PiConnectAPI` (full endpoint `https://piconnect.flattrade.in/PiConnectAPI/EODChartData`).
- No sample failure response is shown for this endpoint in the doc.
