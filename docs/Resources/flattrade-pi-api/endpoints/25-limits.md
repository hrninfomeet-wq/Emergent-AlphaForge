# Limits

**Category:** Holdings & Limits · **Type:** REST · **Method:** POST  
**Path:** `/Limits` · **URL:** `https://piconnect.flattrade.in/PiConnectAPI/Limits`  
**Source:** PDF pages 42-47

## Summary
Retrieves the logged-in account's trading limits, margin and fund details via a POST call. Returns cash margin available, payin/payout, margin used and its detailed segment-wise breakup (span, exposure, premium, var-elm, brokerage, realized/unrealized PNL, collateral, etc.) across Equity/Derivative/FX/Commodity segments and product types.

## Request — Query Parameters
| Parameter | Required | Possible values | Description |
|---|---|---|---|
| jData* | yes | Should send json object with fields in below list | JSON object containing the request body fields (uid, actid). Sent URL-encoded as `jData={...}`. |
| jKey* | yes |  | Key Obtained on login success. |

## Request — jData JSON fields
| Field | Required | Possible values | Description |
|---|---|---|---|
| uid* | yes |  | Logged in User Id |
| actid* | yes |  | Account id of the logged in user. |

## Sample request
```bash
curl --location 'https://BaseURL/Limits' \
--header 'Content-Type: application/json' \
--data 'jData={
    "uid": "FZ00000",    
    "actid": "FZ00000"    
}&jKey=GHUDWU53H32MTHPA536Q32WR'
```

## Response fields
| Field | Possible values | Description |
|---|---|---|
| stat | Ok or Not_Ok | Limits request success or failure indication. |
| actid |  | Account id |
| prd |  | Product name |
| seg | CM / FO / FX | Segment |
| exch |  | Exchange |
| cash |  | Cash Margin available (Cash Primary Field) |
| payin |  | Total Amount transferred using Payins today (Cash Primary Field) |
| payout |  | Total amount requested for withdrawal today (Cash Primary Field) |
| brkcollamt |  | Prevalued Collateral Amount (Cash Additional Field) |
| unclearedcash |  | Uncleared Cash (Payin through cheques) (Cash Additional Field) |
| daycash |  | Additional leverage amount / Amount added to handle system errors - by broker. (Cash Additional Field) |
| marginused |  | Total margin / fund used today (Margin Utilized) |
| mtomcurper |  | Mtom current percentage (Margin Utilized) |
| cbu |  | CAC Buy used (Margin Used component) |
| csc |  | CAC Sell Credits (Margin Used component) |
| rpnl |  | Current realized PNL (Margin Used component) |
| unmtom |  | Current unrealized mtom (Margin Used component) |
| marprt |  | Covered Product margins |
| span |  | Span used |
| expo |  | Exposure margin |
| premium |  | Premium used |
| varelm |  | Var Elm Margin |
| grexpo |  | Gross Exposure |
| greexpo_d |  | Gross Exposure derivative |
| scripbskmar |  | Scrip basket margin |
| addscripbskmrg |  | Additional scrip basket margin |
| brokerage |  | Brokerage amount |
| collateral |  | Collateral calculated based on uploaded holdings |
| cash_coll |  | Cash Collateral |
| grcoll |  | Valuation of uploaded holding pre haircut |
| turnoverlmt |  | (Additional Risk Limits) — no description given in doc |
| pendordvallmt |  | (Additional Risk Limits) — no description given in doc |
| turnover |  | Turnover (Additional Risk Indicator) |
| pendordval |  | Pending Order value (Additional Risk Indicator) |
| rzpnl_e_i |  | Current realized PNL (Equity Intraday) |
| rzpnl_e_m |  | Current realized PNL (Equity Margin) |
| rzpnl_e_c |  | Current realized PNL (Equity Cash n Carry) |
| rzpnl_d_i |  | Current realized PNL (Derivative Intraday) |
| rzpnl_d_m |  | Current realized PNL (Derivative Margin) |
| rzpnl_f_i |  | Current realized PNL (FX Intraday) |
| rzpnl_f_m |  | Current realized PNL (FX Margin) |
| rzpnl_c_i |  | Current realized PNL (Commodity Intraday) |
| rzpnl_c_m |  | Current realized PNL (Commodity Margin) |
| uzpnl_e_i |  | Current unrealized MTOM (Equity Intraday) |
| uzpnl_e_m |  | Current unrealized MTOM (Equity Margin) |
| uzpnl_e_c |  | Current unrealized MTOM (Equity Cash n Carry) |
| uzpnl_d_i |  | Current unrealized MTOM (Derivative Intraday) |
| uzpnl_d_m |  | Current unrealized MTOM (Derivative Margin) |
| uzpnl_f_i |  | Current unrealized MTOM (FX Intraday) |
| uzpnl_f_m |  | Current unrealized MTOM (FX Margin) |
| uzpnl_c_i |  | Current unrealized MTOM (Commodity Intraday) |
| uzpnl_c_m |  | Current unrealized MTOM (Commodity Margin) |
| span_d_i |  | Span Margin (Derivative Intraday) |
| span_d_m |  | Span Margin (Derivative Margin) |
| span_f_i |  | Span Margin (FX Intraday) |
| span_f_m |  | Span Margin (FX Margin) |
| span_c_i |  | Span Margin (Commodity Intraday) |
| span_c_m |  | Span Margin (Commodity Margin) |
| expo_d_i |  | Exposure Margin (Derivative Intraday) |
| expo_d_m |  | Exposure Margin (Derivative Margin) |
| expo_f_i |  | Exposure Margin (FX Intraday) |
| expo_f_m |  | Exposure Margin (FX Margin) |
| expo_c_i |  | Exposure Margin (Commodity Intraday) |
| expo_c_m |  | Exposure Margin (Commodity Margin) |
| premium_d_i |  | Option premium (Derivative Intraday) |
| premium_d_m |  | Option premium (Derivative Margin) |
| premium_f_i |  | Option premium (FX Intraday) |
| premium_f_m |  | Option premium (FX Margin) |
| premium_c_i |  | Option premium (Commodity Intraday) |
| premium_c_m |  | Option premium (Commodity Margin) |
| varelm_e_i |  | Var Elm (Equity Intraday) |
| varelm_e_m |  | Var Elm (Equity Margin) |
| varelm_e_c |  | Var Elm (Equity Cash n Carry) |
| marprt_e_h |  | Covered Product margins (Equity High leverage) |
| marprt_e_b |  | Covered Product margins (Equity Bracket Order) |
| marprt_d_h |  | Covered Product margins (Derivative High leverage) |
| marprt_d_b |  | Covered Product margins (Derivative Bracket Order) |
| marprt_f_h |  | Covered Product margins (FX High leverage) |
| marprt_f_b |  | Covered Product margins (FX Bracket Order) |
| marprt_c_h |  | Covered Product margins (Commodity High leverage) |
| marprt_c_b |  | Covered Product margins (Commodity Bracket Order) |
| scripbskmar_e_i |  | Scrip basket margin (Equity Intraday) |
| scripbskmar_e_m |  | Scrip basket margin (Equity Margin) |
| scripbskmar_e_c |  | Scrip basket margin (Equity Cash n Carry) |
| addscripbskmrg_d_i |  | Additional scrip basket margin (Derivative Intraday) |
| addscripbskmrg_d_m |  | Additional scrip basket margin (Derivative Margin) |
| addscripbskmrg_f_i |  | Additional scrip basket margin (FX Intraday) |
| addscripbskmrg_f_m |  | Additional scrip basket margin (FX Margin) |
| addscripbskmrg_c_i |  | Additional scrip basket margin (Commodity Intraday) |
| addscripbskmrg_c_m |  | Additional scrip basket margin (Commodity Margin) |
| brkage_e_i |  | Brokerage (Equity Intraday) |
| brkage_e_m |  | Brokerage (Equity Margin) |
| brkage_e_c |  | Brokerage (Equity CAC) |
| brkage_e_h |  | Brokerage (Equity High Leverage) |
| brkage_e_b |  | Brokerage (Equity Bracket Order) |
| brkage_d_i |  | Brokerage (Derivative Intraday) |
| brkage_d_m |  | Brokerage (Derivative Margin) |
| brkage_d_h |  | Brokerage (Derivative High Leverage) |
| brkage_d_b |  | Brokerage (Derivative Bracket Order) |
| brkage_f_i |  | Brokerage (FX Intraday) |
| brkage_f_m |  | Brokerage (FX Margin) |
| brkage_f_h |  | Brokerage (FX High Leverage) |
| brkage_f_b |  | Brokerage (FX Bracket Order) |
| brkage_c_i |  | Brokerage (Commodity Intraday) |
| brkage_c_m |  | Brokerage (Commodity Margin) |
| brkage_c_h |  | Brokerage (Commodity High Leverage) |
| brkage_c_b |  | Brokerage (Commodity Bracket Order) |
| mr_fx_u |  | MR fx used |
| mr_sell |  | MR sell credit |
| mr_t1sell |  | MR t1 sell credit |
| mr_eqt_a |  | MR equity allocated |
| mr_der_a |  | MR derivatives allocated |
| mr_fx_a |  | MR fx allocated |
| mr_com_a |  | MR commodity allocated |
| request_time |  | request_time |
| emsg |  | This will be present only in a failure response. |

## Sample success response
```json
{
"request_time":"18:07:31 29-05-2020",
"stat":"Ok",
"cash":"1500000000000000.00",
"payin":"0.00",
"payout":"0.00",
"brkcollamt":"0.00",
"unclearedcash":"0.00",
"daycash":"0.00",
"turnoverlmt":"50000000000000.00",
"pendordvallmt":"2000000000000000.00",
"turnover":"3915000.00",
"pendordval":"2871000.00",
"marginused":"3945540.00",
"mtomcurper":"0.00",
"urmtom":"30540.00",
"grexpo":"3915000.00",
"uzpnl_e_i":"15270.00",
"uzpnl_e_m":"61080.00",
"uzpnl_e_c":"-45810.00"
}
```

## Sample failure response
```json
{
"stat":"Not_Ok",
"emsg":"Server Timeout :  "
}
```

## Notes
- Request body is sent URL-encoded as `jData={...}&jKey=...` (form-style), not as a raw JSON body. Both query params `jData*` and `jKey*` are mandatory; both jData fields `uid*` and `actid*` are mandatory.
- The `cash` value in the sample success response is an extremely large placeholder (`"1500000000000000.00"`).
- The response is segment/product-wise: `seg` is one of CM / FO / FX, and many fields carry suffixes — `_e/_d/_f/_c` for Equity/Derivative/FX/Commodity and `_i/_m/_c/_h/_b` for Intraday/Margin/Cash-n-Carry/High-leverage/Bracket-order.
- Response fields `turnoverlmt` and `pendordvallmt` are listed under "Additional Risk Limits" with no description in the doc (empty cells).
- The sample success response uses key `urmtom` (unrealized mtom) whereas the response-field table lists `unmtom` for "Current unrealized mtom" — an apparent name inconsistency between sample and field table, preserved verbatim.
- The doc groups response fields into sections: identity (stat/actid/prd/seg/exch); Cash Primary; Cash Additional; Margin Utilized; Margin Used components (cbu/csc/rpnl/unmtom); a top-level margin/collateral block; Additional Risk Limits; Additional Risk Indicators; the Margin-used detailed breakup fields (rzpnl_*/uzpnl_*/span_*/expo_*/premium_*/varelm_*/marprt_*/scripbskmar_*/addscripbskmrg_*/brkage_*/mr_*); then `request_time` and `emsg`.
