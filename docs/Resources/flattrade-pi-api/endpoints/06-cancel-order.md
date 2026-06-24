# Cancel Order

**Category:** Orders & Trades · **Type:** REST · **Method:** POST  
**Path:** `/CancelOrder` · **URL:** `https://piconnect.flattrade.in/PiConnectAPI/CancelOrder`  
**Source:** PDF pages 11-12

## Summary
Cancels a previously placed (open/pending) order. Make a POST call to the CancelOrder endpoint with the Noren order number; the response indicates success/failure and returns the order number of the cancelled order.

## Request — Query Parameters
| Parameter | Required | Possible values | Description |
|---|---|---|---|
| jData* | yes |  | Should send json object with fields in below list (the jData JSON body containing norenordno and uid). |
| jKey* | yes |  | Key Obtained on login success. |

## Request — jData JSON fields
| Field | Required | Possible values | Description |
|---|---|---|---|
| norenordno* | yes |  | Noren order number, which needs to be modified. (Doc text reuses the "which needs to be modified" phrasing from the Modify section; for this endpoint it identifies the order to be cancelled.) |
| uid* | yes |  | User id of the logged in user. |

## Sample request
```bash
curl --location 'https://BaseURL/CancelOrder' \
--header 'Content-Type: application/json' \
--data 'jData={
         "uid": "FZ00000",
         "norenordno": "123456789"
     }&jKey=GHUDWU53H32MTHPA536Q32WR'
```

## Response fields
| Field | Possible values | Description |
|---|---|---|
| stat | Ok or Not_Ok | Cancel order success or failure indication. |
| result |  | Noren Order number of the canceled order. |
| request_time |  | Response received time. |
| emsg |  | This will be present only if Order cancelation fails. |

## Sample success response
```json
{
"request_time":"14:14:10 26-05-2020",
"stat":"Ok",
"result":"20052600000103"
}
```

## Sample failure response
```json
{
"request_time":"16:01:48 28-05-2020",
"stat":"Not_Ok",
"emsg":"Rejected : ORA:Order not found to Cancel"
}
```

## Notes
- The curl sample uses `https://BaseURL/CancelOrder` as a placeholder; the real base URL is `https://piconnect.flattrade.in/PiConnectAPI` (so the full endpoint is `https://piconnect.flattrade.in/PiConnectAPI/CancelOrder`).
- The request payload is sent as a URL-encoded form body of the shape `jData={...json...}&jKey=...` with Content-Type `application/json` (per the doc's curl), not as a pure JSON body.
- Only two jData fields exist for Cancel Order: `norenordno*` and `uid*`, both mandatory. The jData table has no "Possible value" entries for either field.
- In the doc's curl the `uid` field appears before `norenordno` inside the jData object.
- `request_time` values are local timestamps in `HH:MM:SS DD-MM-YYYY` format (e.g. `14:14:10 26-05-2020`), not epoch.
- The description for `norenordno` in the doc literally says "which needs to be modified" (carried over from the Modify section); for this endpoint it identifies the order to cancel.
