# Get Exchange Msg

**Category:** Market Info · **Type:** REST · **Method:** POST  
**Path:** `/ExchMsg` · **URL:** `https://piconnect.flattrade.in/PiConnectAPI/ExchMsg`  
**Source:** PDF pages 56-57

## Summary
Retrieves the exchange message for a given exchange. Send a POST call with a jData JSON object (uid, exch) and the jKey session key obtained on login success.

## Request — Query Parameters
| Parameter | Required | Possible values | Description |
|---|---|---|---|
| jData* | yes |  | Should send json object with fields in below list. |
| jKey* | yes |  | Key Obtained on login success. |

## Request — jData JSON fields
| Field | Required | Possible values | Description |
|---|---|---|---|
| uid* | yes |  | Logged in User Id |
| exch | no |  | Exchange (Select from 'exarr' Array provided in User Details response) |

## Sample request
```bash
curl --location 'https://BaseURL/ExchMsg' \
--header 'Content-Type: application/json' \
--data 'jData={"uid":"FZ00000","exch":"NSE"}&jKey=GHUDWU53H32MTHPA536Q32WR'
```

## Response fields
| Field | Possible values | Description |
|---|---|---|
| stat | Ok (success) / Not_Ok (failure) | On success: "Whi Exch Msg success or failure indication." (value 'Ok'). On failure: "Order book failure indication." (value 'Not_Ok'). |
| exchmsg |  | It will be present only in a successful response. |
| exchtm |  | Exchange Time |
| request_time |  | Response received time. (present in a failure response) |
| emsg |  | Error message (present in a failure response). |

## Sample failure response
```json
{
"stat":"Not_Ok",
"emsg":"Invalid Input : jData is Missing."
}
```

## Notes
- Title appears as "EXCH MSG" in the doc — this is the "Get Exchange Msg" endpoint.
- The section spans the bottom of page 56 (title, URL, query params, curl example, Sample Failure Response) and the top of page 57 (jData fields table + success/failure response tables); it ends where "GET BROKER MSG" begins. The `cal_price/put_price/...` table at the top of page 56 belongs to the preceding Option Greek section, not this one.
- In the live curl the host `BaseURL` is a placeholder for the real base host `piconnect.flattrade.in`. The body is sent as a url-encoded query string of the form `jData={...}&jKey=...`, not a raw JSON body.
- `exch` is NOT marked required in the source (no trailing `*`); only `uid` is required. Its value must be one of the exchanges in the `exarr` array returned by the User Details response.
- The source splits the response into a success table (`stat`='Ok', `exchmsg`, `exchtm`) and a failure table (`stat`='Not_Ok', `request_time`, `emsg`). The success-response `stat` description reads verbatim "Whi Exch Msg success or failure indication." (apparent typo for "Which/Whether"); the failure-response `stat` description reuses generic order-book wording.
- No explicit Sample Success Response JSON is shown for this section in the source (the page-57 Sample Success block belongs to the following GET BROKER MSG section).
