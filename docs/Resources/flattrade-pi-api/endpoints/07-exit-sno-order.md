# Exit SNO Order

**Category:** Orders & Trades · **Type:** REST · **Method:** POST  
**Path:** `/ExitSNOOrder` · **URL:** `https://piconnect.flattrade.in/PiConnectAPI/ExitSNOOrder`  
**Source:** PDF pages 12-13

## Summary
Exits an SNO (special / second-leg) order via a POST call. Applicable only to Cover Order (H) and Bracket Order (B) products, identified by the user id, product type and Noren order number.

## Request — Query Parameters
| Parameter | Required | Possible values | Description |
|---|---|---|---|
| jData* | yes |  | Should send json object with fields in below list (the Exit SNO Order json body). |
| jKey* | yes |  | Key Obtained on login success. |

## Request — jData JSON fields
| Field | Required | Possible values | Description |
|---|---|---|---|
| norenordno* | yes |  | Noren order number, which needs to be modified. |
| prd* | yes | H / B | Allowed for only H and B products (Cover order and bracket order). |
| uid* | yes |  | User id of the logged in user. |

## Sample request
```bash
curl --location 'https://BaseURL/ExitSNOOrder' \
--header 'Content-Type: application/json' \
--data 'jData={
    "uid": "FZ00000",    
    "prd": "H",
    "norenordno": "123456789"
}&jKey=GHUDWU53H32MTHPA536Q32WR'
```

## Response fields
| Field | Possible values | Description |
|---|---|---|
| stat | Ok or Not_Ok | Cancel order success or failure indication. |
| dmsg |  | Display message, (will be present only in case of success). |
| request_time |  | Response received time |
| emsg |  | This will be present only if Order cancelation fails |

## Notes
- The jData body is sent as a query/form parameter alongside jKey (`jData={...}&jKey=...`), with `Content-Type: application/json`.
- The `prd` field is restricted to H (Cover Order) and B (Bracket Order) products only.
- The `BaseURL` in the curl example corresponds to the documented base host `https://piconnect.flattrade.in/PiConnectAPI`.
- The doc shows **no** JSON sample response for this endpoint (neither success nor failure). The `Not_Ok` / "Order not found to Cancel" JSON near this section in the PDF belongs to the preceding Cancel-order endpoint, not to Exit SNO Order.
- Response fields use `dmsg` (display message on success) rather than the `result` field used by the preceding Cancel-order section.
