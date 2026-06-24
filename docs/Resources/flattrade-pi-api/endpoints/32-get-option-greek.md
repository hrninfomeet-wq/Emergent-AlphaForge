# Get Option Greek

**Category:** Market Info · **Type:** REST · **Method:** POST  
**Path:** `/GetOptionGreek` · **URL:** `https://piconnect.flattrade.in/PiConnectAPI/GetOptionGreek`  
**Source:** PDF pages 55-56

## Summary
Calculates option Greeks (call/put price, delta, gamma, theta, rho, vega) for a given option contract via a POST call. Inputs include expiry date, strike price, spot price, interest rate, volatility and option type; the response returns both call and put values for each Greek.

## Request — Query Parameters
| Parameter | Required | Possible values | Description |
|---|---|---|---|
| jData* | yes |  | Should send json object with fields in below list |
| jKey* | yes |  | Key Obtained on login success. |

## Request — jData JSON fields
| Field | Required | Possible values | Description |
|---|---|---|---|
| exd | no |  | Expiry Date |
| strprc | no |  | Strike Price |
| sptprc | no |  | Spot Price |
| int_rate | no |  | Init Rate |
| volatility | no |  | Volatility |
| optt | no |  | Option Type |

## Sample request
```bash
curl --location 'https://BaseURL/GetOptionGreek' \
--header 'Content-Type: application/json' \
--data 'jData={
    "exd": "2021-07-28",    
    "strprc": "2567", 
    "sptprc": "2668",
    "int_rate": "0.05", 
    "volatility": "0.2", 
    "optt": "C"
}&jKey=GHUDWU53H32MTHPA536Q32WR'
```

## Response fields
| Field | Possible values | Description |
|---|---|---|
| stat |  | Success or failure indication. |
| request_time |  | This will be present only in a successful response. |
| cal_price |  | Cal Price |
| put_price |  | Put Price |
| cal_delta |  | Cal Delta |
| put_delta |  | Put Delta |
| cal_gamma |  | Cal Gamma |
| put_gamma |  | Put Gamma |
| cal_theta |  | Cal Theta |
| put_theta |  | Put Theta |
| cal_rho |  | Cal Rho |
| put_rho |  | Put Rho |
| cal_vego |  | Cal Vego |
| put_vego |  | Put Vego |

## Sample success response
```json
{
"request_time":"17:22:58 28-07-2021",
"stat":"OK",
"cal_price":"1441",
"put_price":"0.417071",
"cal_delta":"0.997304",
"put_delta":"-0.002696",
"cal_gamma":"0.000001",
"put_gamma":"0.000001",
"cal_theta":"-31.535015",
"put_theta":"-31.401346",
"cal_rho":"0.000119",
"put_rho":"-0.016590",
"cal_vego":"0.006307",
"put_vego":"0.006307"
}
```

## Sample failure response
```json
{
"stat":"Not_Ok",
"emsg":"Invalid Input : jData is Missing."
}
```

## Notes
Section spans page 55 (title GET OPTION GREEK through the success response) into page 56 (RESPONSE DETAILS table + sample failure response), ending where EXCH MSG begins on page 56. The "Possible value" column is empty for every jData field and response field in the source table (no enums exist in the doc); `stat`'s OK / Not_Ok values are inferred from the sample responses. Field descriptions are the terse one-word labels exactly as printed in the PDF (e.g. `int_rate` = "Init Rate", `cal_vego`/`put_vego` = "Cal Vego"/"Put Vego"). The sample failure response is the generic "jData is Missing" error rendered in the page-56 right column, not a Greek-specific failure.
