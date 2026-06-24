# WebSocket Heartbeat

**Category:** WebSocket · **Type:** WebSocket · **Method:** WebSocket  
**Path:** `/PiConnectWSAPI/` · **URL:** `wss://piconnect.flattrade.in/PiConnectWSAPI/`  
**Source:** PDF pages 83-83

## Summary
Heartbeat message frame used to keep the WebSocket connection alive. The client sends a frame with `t="h"` every 30 seconds and the server replies with a heartbeat acknowledgment frame (`t="hk"`) that also carries a timestamp in seconds.

## Request — jData JSON fields
| Field | Required | Possible values | Description |
|---|---|---|---|
| t | no | h | Task type for the WebSocket request frame. 'h' represents the Heartbeat task. Sent by the client to keep the connection alive. |

## Sample request
```bash
curl --location 'wss://piconnect.flattrade.in/PiConnectWSAPI/' 
--data '{
    "t": "h"
}'
```

## Response fields
| Field | Possible values | Description |
|---|---|---|
| t | hk | Task type of the response frame. 'hk' represents Heartbeat acknowledgment. |
| hk |  | Timestamp in seconds (heartbeat acknowledgment timestamp). [Source prints the typo "timmstamps in seconds".] |

## Notes
This is a WebSocket message frame, not a REST endpoint: the connection is over `wss://` (not `https://`), and the documented "curl" example illustrates the JSON frame payload rather than a literal runnable HTTP call. The heartbeat frame `{"t":"h"}` must be sent every 30 seconds to keep the connection alive; the server acknowledges with a frame whose `t="hk"`.

The RESPONSE table has two rows: `t` (Possible value `hk`) and `hk` (a timestamp in seconds). On the page, the "timmstamps in seconds" text sits in the Description column of the `hk` row, and that row's Possible-value cell is empty.

No query parameters (no `jData`/`jKey`) — the heartbeat is a pure WebSocket frame. The page does not print any verbatim success-response or failure-response JSON block for the heartbeat acknowledgment, so no sample response blocks are shown here.

Source typos preserved for reference: "actknowledgment" (acknowledgment) and "timmstamps" (timestamp).

Page note: page 83 opens with the tail of the preceding "Unsubscribe Position update" section (`t=upk`, excluded) and ends by starting the next "USER DETAILS" section (also excluded).
