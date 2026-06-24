# Modify GTT Order

**Category:** Orders & Trades · **Type:** REST · **Method:** POST  
**Path:** `/ModifyGTTOrder` · **URL:** `https://piconnect.flattrade.in/PiConnectAPI/ModifyGTTOrder`  
**Source:** PDF pages 31-33

## Summary
Modifies an existing GTT (Good Till Triggered) alert/order. Make a POST call to `https://piconnect.flattrade.in/PiConnectAPI/ModifyGTTOrder` with a `jData` JSON body (and `jKey` from login) to update the order parameters the alert will fire. On success it returns a JSON array with the alert id and a replacement status; on error it returns a JSON object with `stat` and `emsg`.

## Request — Query Parameters
> Note: the Modify GTT Order section does not print an explicit REQUEST DETAILS parameter table. `jData`/`jKey` are documented here from the curl example body (`jData={...}&jKey=...`).

| Parameter | Required | Possible values | Description |
|---|---|---|---|
| jData* | yes |  | Should send a JSON object with the fields in the jData list below (the modify request payload). |
| jKey* | yes |  | Key obtained on login success. |

## Request — jData JSON fields
| Field | Required | Possible values | Description |
|---|---|---|---|
| uid* | yes |  | User id of the logged in user. |
| tsym* | yes |  | Trading symbol. |
| exch* | yes |  | Exchange Segment. |
| ai_t* | yes |  | Alert Type, should be original alert type, can't be modified. |
| al_id | no |  | Alert Id. |
| validity* | yes | DAY or GTT | Validity. |
| d | no |  | Data to be compared with LTP. |
| remarks* | yes |  | Any message Entered during order entry. |
| trantype* | yes | B / S | B -> BUY, S -> SELL [transtype should be 'B' or 'S' else reject]. |
| prctyp* | yes | LMT / SL-LMT / DS / 2L / 3L | Price type / order type. (Doc has only a possible-value cell for this field; no description text.) |
| prd* | yes | C / M / H | Product name. |
| ret* | yes | DAY / EOS / IOC | Retention type [ret should be DAY / EOS / IOC else reject]. |
| actid* | yes |  | Login users account ID. |
| qty* | yes |  | Order Quantity [If qty is junk value other than numbers]. |
| prc* | yes |  | Order Price [If prc is junk value other than numbers] "Order price cannot be zero". |
| dscqty* | yes |  | Disclosed quantity (Max 10% for NSE, and 50% for MCX) [If dscqty is junk value other than numbers]. |

## Sample request
```bash
curl --location 'https://BaseURL/ModifyGTTOrder' \
--header 'Content-Type: application/json' \
--data 'jData={
    "uid": "FZ00000",    
    "actid": "FZ00000",    
    "exch": "NSE",
    "tsym": "ACC-EQ",
    "validity": "DAY", 
    "qty": "50",
    "prc": "1400",
    "prd": "H",
    "trantype": "B",
    "prctyp": "LMT",
    "prevprd": "C", 
    "ret": "DAY", 
    "dscqty": "10"
}&jKey=GHUDWU53H32MTHPA536Q32WR'
```

## Response fields
| Field | Possible values | Description |
|---|---|---|
| stat |  | GTT order success or failure indication. (Success sample shows "Oi Replacedt"; failure sample shows "Not_Ok".) |
| request_time |  | This will be present only in a successful response. |
| al_id |  | Alert Id. (Returned in the sample JSON as "Al_id".) |
| emsg |  | This will be present only in case of errors. That is : 1) Invalid Input 2) Session Expired. |

## Sample success response
```json
[
{
"request_time":"12:15:18 15-04-2021",
"stat":"Oi Replacedt",
"Al_id":"21041500000008"
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
Mandatory fields are marked with a trailing '*' in the doc; in the jData table all fields except `al_id` and `d` carry the asterisk. `ai_t` must be the ORIGINAL alert type and cannot be modified. `trantype` rejects anything other than 'B' or 'S'; `ret` rejects anything other than DAY/EOS/IOC. `prc` cannot be zero. `dscqty` (disclosed qty) max is 10% for NSE and 50% for MCX. `jKey` is the session key obtained on login. The success response is a JSON array; the failure response is a JSON object.

Caveats carried verbatim from the source: the success `stat` value reads "Oi Replacedt" (an apparent typo for "Oi Replaced"); the response field is documented as `al_id` but appears as `Al_id` (capitalized) in the sample JSON; the curl `--data` body includes a `prevprd` key that is NOT in the documented jData field table, and omits several documented fields (`ai_t`, `al_id`, `remarks`, `d`) — left verbatim. The curl uses the placeholder host `https://BaseURL/ModifyGTTOrder`; the real endpoint is `https://piconnect.flattrade.in/PiConnectAPI/ModifyGTTOrder`.

Layout note: the section spans pages 31-33. Page 31 left column carries the tail of the preceding Place GTT Order field/response table then the MODIFY GTT ORDER title + endpoint URL, while its right column carries the tail of the preceding sample (`"Al_id":"21041500000010"`) + a failure response + the curl opening line. Page 32 carries the full jData field table + RESPONSE DETAILS (left) and the curl `--data` body + both sample responses (right). Page 33 carries only the tail of the `emsg` description ("2) Session Expired") before the next section (Cancel GTT Order). The PDF does not print a dedicated REQUEST DETAILS parameter table for Modify GTT Order (unlike the sibling Cancel GTT Order section).
