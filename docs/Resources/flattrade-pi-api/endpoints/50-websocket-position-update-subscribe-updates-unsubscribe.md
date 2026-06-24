# WebSocket Position Update (Subscribe/Updates/Unsubscribe)

**Category:** WebSocket · **Type:** WebSocket · **Method:** WebSocket  
**Path:** `/PiConnectWSAPI/` · **URL:** `wss://piconnect.flattrade.in/PiConnectWSAPI/`  
**Source:** PDF pages 80-83

## Summary
WebSocket message frames to subscribe to, receive, and unsubscribe from position updates over the Flattrade pi WebSocket (`wss://piconnect.flattrade.in/PiConnectWSAPI/`). The client sends a subscribe frame (`t=p`), receives an acknowledgement (`t=pk`) plus a stream of position-update frames (`t=pm`) that each carry the full position fields including a `child_orders` array, and later sends an unsubscribe frame (`t=up`) which is acknowledged with `t=upk`.

## Request — jData JSON fields
These are the frames the client sends over the open WebSocket (there are no REST query parameters).

| Field | Required | Possible values | Description |
|---|---|---|---|
| t | no | p | Subscribe Position Update REQUEST frame task type. 'p' represents position subscription. |
| uid | no |  | Subscribe Position Update REQUEST: User id. (Note: the verbatim subscribe curl sample uses the key `actid` with value `FZ00000` rather than `uid`.) |
| t | no | up | Unsubscribe Position Update REQUEST: 'up' represents Unsubscribe Position update. |

## Sample request
```bash
curl --location 'wss://piconnect.flattrade.in/PiConnectWSAPI/' 
--data '{
    "t": "p",  
    "actid": "FZ00000"
}'
```

Unsubscribe frame:

```bash
curl --location 'wss://piconnect.flattrade.in/PiConnectWSAPI/' 
--data '{
    "t": "up"    
}'
```

## Response fields

### Subscribe acknowledgement (RESPONSE)
| Field | Possible values | Description |
|---|---|---|
| t | pk | 'pk' represents position subscription (acknowledgement). On successful connection establishment, position updates will be received if it is made available in the startup (`-position_update`). |
| uid |  | User id. |

### Position update frame (POSITION UPDATE SUBSCRIPTION UPDATES)
| Field | Possible values | Description |
|---|---|---|
| t | pm | 'pm' represents position update. |
| exch |  | Exchange segment. |
| token |  | Contract token. |
| uid |  | User id. |
| actid |  | Account id. |
| prd |  | Product name to be shown. |
| daybuyqty |  | Day Buy Quantity. |
| daysellqty |  | Day Sell Quantity. |
| daybuyamt |  | Day Buy Amount. |
| daysellamt |  | Day Sell Amount. |
| cfbuyqty |  | Carry Forward Buy Quantity. |
| cfsellqty |  | Carry Forward Sell Quantity. |
| cfbuyamt |  | Carry Forward Buy Amount. |
| cfsellamt |  | Carry Forward Sell Amount. |
| openbuyqty |  | Open Buy Quantity. |
| opensellqty |  | Open Sell Quantity. |
| openbuyamt |  | Open Buy Amount. |
| opensellamt |  | Open Sell Amount. |
| instname |  | Instrument Name. |
| upload_prc |  | Upload Price. |
| buyavgprc |  | Buy Average Price [(daybuyamt + cfbuyamt) / (daybuyqty + cfbuyqty)]. |
| sellavgprc |  | Sell Average Price [(daysellamt + cfsellamt) / (daysellqty + cfsellqty)]. |
| rpnl |  | Realized Panel (Realized P&L). |
| netqty |  | Net Quantity [daybuyqty + cfbuyqty - daysellqty - cfsellqty]. |
| totbuyamt |  | Total Buy Amount. |
| totsellamt |  | Total Sell Amount. |
| totbuyavgprc |  | Total Buy Avg Price. |
| totsellavgprc |  | Total Sell Avg Price. |
| child_orders |  | Array Object, details given below (see CHILD_ORDERS FORMAT). |

#### child_orders[] (CHILD_ORDERS FORMAT)
| Field | Possible values | Description |
|---|---|---|
| exch |  | Exchange segment. |
| token |  | Contract token. |
| tsym |  | Trading symbol/contract. |
| daybuyqty |  | Day Buy Quantity. |
| daysellqty |  | Day Sell Quantity. |
| daybuyamt |  | Day Buy Amount. |
| daysellamt |  | Day Sell Amount. |
| cfbuyqty |  | CF Buy Quantity. |
| cfsellqty |  | CF Sell Quantity. |
| cfbuyamt |  | CF Buy Amount. |
| cfsellamt |  | CF Sell Amount. |
| openbuyqty |  | Open Buy Quantity. |
| opensellqty |  | Open Sell Quantity. |
| openbuyamt |  | Open Buy Amount. |
| opensellamt |  | Open Sell Amount. |
| rpnl |  | Realized Panel (Realized P&L). |
| netqty |  | Net Quantity [daybuyqty + cfbuyqty - daysellqty - cfsellqty]. |
| upload_prc |  | Upload Price. |
| totbuyamt |  | Total Buy Amount. |
| totsellamt |  | Total Sell Amount. |
| totbuyavgprc |  | Total Buy Avg Price. |
| totsellavgprc |  | Total Sell Avg Price. |
| buyavgprc |  | Buy Average Price. |
| sellavgprc |  | Sell Average Price. |

### Unsubscribe acknowledgement (RESPONSE)
| Field | Possible values | Description |
|---|---|---|
| t | upk | 'upk' represents Unsubscribe Position update acknowledgement. |

## Notes
This is a WebSocket message-frame flow, not a REST endpoint; all frames use the same socket `wss://piconnect.flattrade.in/PiConnectWSAPI/`. The `curl` examples in the doc are illustrative only — the JSON frame is what is sent over the open WebSocket.

Flow:
1. Subscribe REQUEST frame `{"t":"p", uid}`
2. Acknowledgement frame `{"t":"pk", uid}`
3. Stream of position-update frames `{"t":"pm", ...full position fields...}` where `child_orders` is an array of per-contract objects (CHILD_ORDERS FORMAT)
4. Unsubscribe: send `{"t":"up"}` → acknowledgement `{"t":"upk"}`

Position updates are received on connection establishment only if made available in the startup with the `-position_update` flag.

Inconsistency: the Subscribe field table lists `uid` (User id), but the verbatim subscribe curl sample uses the key `actid` with value `FZ00000` instead of `uid`.

Field-name quirk: `rpnl` is described verbatim in the doc as "Realized Panel" (i.e. Realized P&L).

No sample success/failure JSON response is shown for the Position Update frames. The HEARTBEAT (`t=h` / `t=hk`) and USER DETAILS (POST to `/PiConnectAPI/UserDetails`) sections that begin on page 83 are SEPARATE sections and are NOT part of this Position Update section; the page-83 Sample Success Response belongs to USER DETAILS, not to Position Update.
