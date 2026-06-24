# User Details

**Category:** Account & Reference · **Type:** REST · **Method:** POST  
**Path:** `/UserDetails` · **URL:** `https://piconnect.flattrade.in/PiConnectAPI/UserDetails`  
**Source:** PDF pages 83-85

## Summary
Retrieves the logged-in user's account details: enabled exchanges (`exarr`), enabled price/order types (`orarr`), enabled products as an array of Product Obj (`prarr`), broker id, branch id, email, account id, mobile number, user privilege (always `INVESTOR`), and access type. POST call with `jData` (JSON containing `uid`) and `jKey` (session token).

## Request — Query Parameters
| Parameter | Required | Possible values | Description |
|---|---|---|---|
| jData* | yes | | Should send json object with fields in below list |
| jKey* | yes | | Key Obtained on login success. |

## Request — jData JSON fields
| Field | Required | Possible values | Description |
|---|---|---|---|
| uid* | yes | | Logged in User Id |

## Sample request
```bash
curl --location 'https://BaseURL/UserDetails' \
--header 'Content-Type: application/json' \
--data 'jData={    
    "uid": "FZ00000"    
}&jKey=GHUDWU53H32MTHPA536Q32WR'
```

## Response fields
| Field | Possible values | Description |
|---|---|---|
| stat | Ok or Not_Ok | User details success or failure indication. |
| exarr | | Json array of strings with enabled exchange names |
| orarr | | Json array of strings with enabled price types for user |
| prarr | | Json array of Product Obj with enabled products, as defined below. |
| brkname | | Broker id |
| brnchid | | Branch id |
| email | | |
| actid | | |
| m_num | | Mobile Number |
| uprev | | Always it will be an INVESTOR, other types of user not allowed to login using this API. |
| access_type | | Access Type |
| request_time | | It will be present only in a successful response. |
| emsg | | This will be present only in case of errors. |
| prd | | Product Obj field (PRODUCT OBJ FORMAT): Product name |
| s_prdt_ali | | Product Obj field (PRODUCT OBJ FORMAT): Product display name |
| exch | | Product Obj field (PRODUCT OBJ FORMAT): Json array of strings with enabled, allowed exchange names |

## Sample success response
```json
{
"request_time": "20:20:04 19-05-2020",
"prarr": [
{ "prd":"C",
"s_prdt_ali" : "Delivery",
"exch" : ["NSE", "BSE"]
},
{ "prd":"I",
"s_prdt_ali" : "Intraday",
"exch" : ["NSE", "BSE", "NFO"]
},
, { "prd":"H",
"s_prdt_ali" : "High Leverage",
"exch" : ["NSE", "BSE", "NFO"]
},
{ "prd":"B",
"s_prdt_ali" : "Bracket Order",
"exch" : ["NSE", "BSE", "NFO"]
}
],
"exarr": [
"NSE",
"NFO"
],
"orarr": [
"LMT",
"SL-LMT",
"DS",
"2L",
"3L",
"4L"
],
"brkname": "VIDYA",
"brnchid": "VIDDU",
"email": "gururaj@gmail.com",
"actid": "GURURAJ",
"uprev": "INVESTOR",
"stat": "Ok"
}
```

## Sample failure response
```json
{
"stat": "Not_Ok",
"emsg": "Session Expired : Invalid Session Key"
}
```

## Notes
- The doc shows the curl example with the placeholder host `https://BaseURL/UserDetails`; the actual base URL per the prose is `https://piconnect.flattrade.in/PiConnectAPI/UserDetails`.
- `jData` is sent as a URL-style form parameter: `jData={...json...}&jKey=...` in the request body (a `Content-Type: application/json` header is shown even though the body is form-encoded `jData=`/`jKey=`).
- `prarr` is an array of Product Obj entries; each Product Obj has fields `prd` / `s_prdt_ali` / `exch`, documented separately under PRODUCT OBJ FORMAT (page 85): `prd` = Product name, `s_prdt_ali` = Product display name, `exch` = Json array of enabled/allowed exchange names. Sample products: C=Delivery, I=Intraday, H=High Leverage, B=Bracket Order.
- `orarr` sample enumerates price/order types: LMT, SL-LMT, DS, 2L, 3L, 4L.
- `uprev` is always `INVESTOR`; other user types cannot log in via this API. (The doc table leaves `uprev`'s Possible-value cell blank; `INVESTOR` appears only in its description and in the sample.)
- The doc table leaves the Description cell BLANK for the `email` and `actid` response fields (reproduced as empty); their meaning is inferred only from the sample.
- `request_time` appears only on success; `emsg` appears only on errors. `access_type` and `m_num` are documented but do not appear in the sample success response.
- The success-response JSON in the source has a stray leading comma before the third `prarr` entry (`, { "prd":"H"`) — reproduced verbatim; it is a doc artifact, not valid JSON. The source also renders the `prarr` block with curly/smart quotes; straight quotes are used here for consistency.
