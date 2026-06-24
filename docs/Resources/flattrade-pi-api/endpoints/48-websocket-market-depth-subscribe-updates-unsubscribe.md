# WebSocket Market Depth (Subscribe/Updates/Unsubscribe)

**Category:** WebSocket · **Type:** WebSocket · **Method:** WebSocket  
**Path:** `/PiConnectWSAPI/` · **URL:** `wss://piconnect.flattrade.in/PiConnectWSAPI/`  
**Source:** PDF pages 72-77

## Summary
WebSocket message frames for the Flattrade Pi market-depth feed: send a `d` (Subscribe Depth) frame to subscribe to one or more scrips, receive a `dk` acknowledgement followed by `df` depth-feed updates (full 5-level order book — best buy/sell prices, quantities and orders, plus OHLC, circuit limits, OI and feed time), and send a `ud` (Unsubscribe Depth) frame, which is acknowledged with a `udk` frame.

## Request — jData JSON fields
| Field | Required | Possible values | Description |
|---|---|---|---|
| t | no | d / ud | Message-type / task field. `d` = Subscribe Depth request frame (`'d'` represents depth subscription); `ud` = Unsubscribe Depth request frame (`'ud'` represents Unsubscribe depth). |
| k | no | NSE\|22#BSE\|508123 | One or more scrip list for subscription (`t='d'`) or unsubscription (`t='ud'`). Each scrip is `EXCHANGE\|TOKEN`, multiple scrips joined with `#`. Example: `NSE\|22#BSE\|508123`. |

## Sample request
```bash
# Subscribe Depth:
curl --location 'wss://piconnect.flattrade.in/PiConnectWSAPI/' 
--data '{
    "t": "d",  
    "k": "NSE|22#BSE|508123#NSE|10#BSE|2879"
}'

# Unsubscribe Depth:
curl --location 'wss://piconnect.flattrade.in/PiConnectWSAPI/' 
--data '{
    "t": "ud",  
    "k": "NSE|22#BSE|508123#NSE|10#BSE|2879"
}'
```

## Response fields
| Field | Possible values | Description |
|---|---|---|
| t | dk / df / udk | `dk` = depth subscription acknowledgement; `df` = depth feed (subscription update); `udk` = unsubscribe depth acknowledgement. |
| e | NSE, BSE, NFO | Exchange name. |
| tk | 22 | Scrip Token. |
| lp | LTP | LTP (Last Traded Price). |
| pc |  | Percentage change. |
| v | volume | volume. |
| o |  | Open price. |
| h |  | High price. |
| l |  | Low price. |
| c |  | Previous Close price. |
| cp |  | Close price. |
| ap |  | Average trade price. |
| ltt |  | Last trade time. |
| ltq |  | Last trade quantity. |
| tbq |  | Total Buy Quantity. |
| tsq |  | Total Sell Quantity. |
| bq1 |  | Best Buy Quantity 1. |
| bq2 |  | Best Buy Quantity 2. |
| bq3 |  | Best Buy Quantity 3. |
| bq4 |  | Best Buy Quantity 4. |
| bq5 |  | Best Buy Quantity 5. |
| bp1 |  | Best Buy Price 1. |
| bp2 |  | Best Buy Price 2. |
| bp3 |  | Best Buy Price 3. |
| bp4 |  | Best Buy Price 4. |
| bp5 |  | Best Buy Price 5. |
| bo1 |  | Best Buy Orders 1. |
| bo2 |  | Best Buy Orders 2. |
| bo3 |  | Best Buy Orders 3. |
| bo4 |  | Best Buy Orders 4. |
| bo5 |  | Best Buy Orders 5. |
| sq1 |  | Best Sell Quantity 1. |
| sq2 |  | Best Sell Quantity 2. |
| sq3 |  | Best Sell Quantity 3. |
| sq4 |  | Best Sell Quantity 4. |
| sq5 |  | Best Sell Quantity 5. |
| sp1 |  | Best Sell Price 1. |
| sp2 |  | Best Sell Price 2. |
| sp3 |  | Best Sell Price 3. |
| sp4 |  | Best Sell Price 4. |
| sp5 |  | Best Sell Price 5. |
| so1 |  | Best Sell Orders 1. |
| so2 |  | Best Sell Orders 2. |
| so3 |  | Best Sell Orders 3. |
| so4 |  | Best Sell Orders 4. |
| so5 |  | Best Sell Orders 5. |
| lc |  | Lower Circuit Limit. |
| uc |  | Upper Circuit Limit. |
| 52h |  | 52 week high low in other exchanges, Life time high low in mcx. |
| 52l |  | 52 week high low in other exchanges, Life time high low in mcx. |
| oi |  | Open interest. |
| poi |  | Previous day closing Open Interest. |
| toi |  | Total open interest for underlying. |
| ft |  | Feed time. |
| ue |  | (LPP) Exchange high range. Appears only in the depth-feed update (`df`) field list, not the acknowledgement list. |
| le |  | (LPP) Exchange Low range. Appears only in the depth-feed update (`df`) field list, not the acknowledgement list. |
| k | NSE\|22#BSE\|508123 | Echoed scrip list in the `udk` unsubscribe acknowledgement (one or more scrips for unsubscription, e.g. `NSE\|22#BSE\|508123`). |

## Sample success response
```json
Sample Message :
{
"t": "df",
"e": "NSE",
"tk": "22",
"o": "1166.00",
"h": "1179.00",
"l": "1145.35",
"c": "1152.65",
"ap": "1159.74",
"v": "819881",
"tbq": "120952",
"tsq": "131730",
"bp1": "1156.00",
"sp1": "1156.50",
"bp2": "1155.80",
"sp2": "1156.55",
"bp3": "1155.75",
"sp3": "1156.65",
"bp4": "1155.70",
"sp4": "1156.70",
"bp5": "1155.65",
"sp5": "1156.75",
"bq1": "4",
"sq1": "10",
"bq2": "67",
"sq2": "63",
"bq3": "83",
"sq3": "1",
"bq4": "139",
"sq4": "53",
"bq5": "393",
"sq5": "94"
}
```

## Notes
- This is a WebSocket endpoint (`wss://`), not REST. The curl examples use `--location` with the `wss://` URL and a JSON `--data` frame purely to illustrate the message payload sent over the socket; the connection must be a live authenticated WebSocket session (see the WebSocket connect/authentication section). A valid touchline/connect handshake must precede these frames.
- Scrip list format in `k`: `EXCHANGE|TOKEN`, multiple scrips joined with `#`, e.g. `NSE|22#BSE|508123#NSE|10#BSE|2879`. Exchange values seen include NSE, BSE, NFO.
- Acknowledgement count: the number of acknowledgements for a single subscription equals the number of scrips listed in the key (k) field (one `dk` per scrip).
- Message-type contract: send `t='d'` to subscribe -> receive `t='dk'` acknowledgement -> then receive a stream of `t='df'` depth-feed updates; send `t='ud'` to unsubscribe -> receive `t='udk'` acknowledgement.
- The SUBSCRIPTION DEPTH ACKNOWLEDGEMENT field list (pages 72-74, `t='dk'`) and the DEPTH SUBSCRIPTION UPDATES field list (pages 74-76, `t='df'`) carry the same field set EXCEPT that `ue`/`le` ('(LPP) Exchange high/low range') appear ONLY in the depth-feed-update (`df`) list on page 76, not in the acknowledgement list (which ends at `ft`).
- Depth feed provides 5 levels each side: best buy price/quantity/orders bp1-bp5 / bq1-bq5 / bo1-bo5 and best sell price/quantity/orders sp1-sp5 / sq1-sq5 / so1-so5.
- `52h` / `52l` field meaning is exchange-dependent: '52 week high/low' on most exchanges, but 'Life time high/low' on MCX.
- Updates are sparse/delta: a given `df` frame may carry only the fields that changed (the sample message omits several documented fields such as `ltt`, `ltq`, `lc`, `uc`, `oi`, `ft`).
- No explicit mandatory `*` markers appear on any request field in this section, so `t` and `k` are recorded as required=no; in practice both are needed for a valid subscribe/unsubscribe frame.
- No failure/error response sample is shown for this section.
