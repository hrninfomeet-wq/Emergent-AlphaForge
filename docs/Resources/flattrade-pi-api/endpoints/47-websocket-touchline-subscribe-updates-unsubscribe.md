# WebSocket Touchline (Subscribe/Updates/Unsubscribe)

**Category:** WebSocket · **Type:** WebSocket · **Method:** WebSocket  
**Path:** `/PiConnectWSAPI/` · **URL:** `wss://piconnect.flattrade.in/PiConnectWSAPI/`  
**Source:** PDF pages 69-72

## Summary
WebSocket message frames for Touchline market-data streaming over the Flattrade Pi connect socket. The client sends a Subscribe Touchline frame (`t=t`) or Unsubscribe Touchline frame (`t=u`) with a `#`-delimited scrip list in the `k` field; the server replies with a touchline acknowledgement (`t=tk`) per scrip, then pushes touchline feed updates (`t=tf`) as ticks arrive, and an unsubscribe acknowledgement (`t=uk`) on unsubscription.

## Request — jData JSON fields
| Field | Required | Possible values | Description |
|---|---|---|---|
| t | no | t (subscribe) / u (unsubscribe) | Touchline request task type. Set to `t` to subscribe to touchline ("'t' represents touchline task"). For unsubscription this same field is set to `u` ("'u' represents Unsubscribe Touchline"). |
| k | no | e.g. NSE\|22#BSE\|508123#NSE\|NIFTY | One or more scriplist for subscription/unsubscription, `#`-delimited as Exchange\|Token. Subscribe example: `NSE\|22#BSE\|508123#NSE\|NIFTY`. Unsubscribe example: `NSE\|22#BSE\|508123`. |

## Sample request
```bash
curl --location 'wss://piconnect.flattrade.in/PiConnectWSAPI/' 
--data '{
    "t": "t",
    "k": "NSE|22#BSE|508123#NSE|10#BSE|2879"
}'
```

Unsubscribe (verbatim from page 71 — the doc places the ack value `uk`, not `u`, in this request body):
```bash
curl --location 'wss://piconnect.flattrade.in/PiConnectWSAPI/' 
--data '{
    "t": "uk",
    "k": "NSE|22#BSE|508123#NSE|10#BSE|2879"
}'
```

## Response fields
| Field | Possible values | Description |
|---|---|---|
| t | tk | Touchline subscription acknowledgement frame. "'tk' represents touchline acknowledgement". The number of acknowledgement frames for a single subscription equals the number of scrips listed in the `k` field. |
| e | NSE, BSE, NFO .. | Exchange name |
| tk | 22 | Scrip Token |
| pp | 2 for NSE, BSE / 4 for CDS USDINR | Price precision |
| ts | | Trading Symbol |
| ti | | Tick size |
| ls | | Lot size |
| lp | | LTP (Last Traded Price) |
| pc | | Percentage change |
| v | | volume |
| o | | Open price |
| h | | High price |
| l | | Low price |
| c | | Close price |
| ap | | Average trade price |
| oi | | Open interest |
| poi | | Previous day closing Open Interest |
| toi | | Total open interest for underlying |
| bq1 | | Best Buy Quantity 1 |
| bp1 | | Best Buy Price 1 |
| sq1 | | Best Sell Quantity 1 |
| sp1 | | Best Sell Price 1 |
| ft | | Feed time |
| ord_msg | | Order message (appears in the Subscribe acknowledgement frame; NOT part of the touchline feed-update field set) |
| t (feed update) | tf | Touchline subscription update / feed frame. "'tf' represents touchline feed". Per the doc: "Accept [Except] for t, e, and tk other fields may / may not be present" — only changed fields are sent. Update field set: e, tk, lp, pc, v, o, h, l, c, ap, oi, poi, toi, bq1, bp1, sq1, sp1, ft. |
| t (unsubscribe ack) | uk | Unsubscribe Touchline response frame. "'uk' represents Unsubscribe Touchline acknowledgement". The unsubscribe response also echoes the `k` field (one or more scriplist for unsubscription, e.g. `NSE\|22#BSE\|508123`). |

## Notes
This is a WebSocket message-frame API, not a REST call; the endpoint is `wss://piconnect.flattrade.in/PiConnectWSAPI/` and the doc illustrates each frame with a `curl --location` example whose `--data` payload is the JSON frame to send.

A connect/auth frame must already be established before subscribing — the connect acknowledgement (`t=ak` with `uid` and `s=Ok` or `Not_Ok`) and its request fields (`uid`/`actid`/`source`/`accesstoken`) belong to the preceding CONNECT section at the top of page 69, not to Touchline.

The `k` field is a `#`-delimited list of `Exchange|Token` entries; the number of acknowledgement frames returned equals the number of scrips listed in `k`.

Touchline feed/update frames (`t=tf`) are partial: only `t`, `e`, and `tk` are guaranteed present; all other fields (lp, pc, v, o, h, l, c, ap, oi, poi, toi, bq1, bp1, sq1, sp1, ft) may or may not appear depending on what changed. The doc wording is "Accept for t, e, and tk other fields may / may not be present" — `Accept` is the doc's rendering of `Except`.

Unsubscribe uses the same socket with `t=u` (request) and yields `t=uk` (response), echoing the `k` scriplist. Note: the doc's verbatim unsubscribe curl example on page 71 actually shows `"t": "uk"` in the request body (i.e. it uses the acknowledgement value, not `u`); reproduced verbatim above.

`pp` (price precision) = 2 for NSE/BSE and 4 for CDS USDINR.

The Subscribe Touchline acknowledgement carries one extra field (`ord_msg`, Order message) that is NOT listed in the touchline feed-update field set.

No verbatim success-response JSON is provided for the Touchline frames in this section — only the curl request examples. The only full sample JSON message on these pages (the `df` message on page 72) belongs to SUBSCRIBE DEPTH (out of scope) and is therefore deliberately excluded here.
