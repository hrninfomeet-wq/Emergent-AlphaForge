# Change Log

**Category:** Account & Reference · **Type:** Info · **Method:** —  
**Path:** `—` · **URL:** `—`  
**Source:** PDF pages 92-93

## Summary
Change Log for the "API & WebSocket Configuration Update" release, which introduces breaking changes to the Flattrade *pi* REST and WebSocket endpoints: the REST Base URL, the WebSocket URL, and the socket-connection initialization payload were all updated. Clients must update their configuration as described.

## Notes

This is an Info-type section (a change log / migration notice), not a callable endpoint — there are no request query parameters, no jData JSON body fields, and no response fields. The title "CHANGE LOG" with its subtitle "API & WEBSOCKET CONFIGURATION UPDATE" appears at the bottom of page 92; all detail is on page 93 (the final page of the document, page 93 of 93).

### Breaking changes (3)

**1. Base URL endpoint change** — The Base URL for all REST API requests changed from the old URL endpoint `"PiConnectTP"` to the new URL endpoint `"PiConnectAPI"`. All existing REST endpoints (Holdings, Orders, Positions, etc.) must now be accessed using the updated Base URL.

- OLD: `https://piconnect.flattrade.in/PiConnectTP/`
- NEW: `https://piconnect.flattrade.in/PiConnectAPI/`

**2. WebSocket URL endpoint change** — The WebSocket connection URL changed from the old URL endpoint `"PiConnectWSTp"` to the new URL endpoint `"PiConnectWSAPI"`. Connections made to the old WebSocket URL will be rejected.

- OLD: `wss://piconnect.flattrade.in/PiConnectWSTp/`
- NEW: `wss://piconnect.flattrade.in/PiConnectWSAPI/`

**3. Socket connection payload change** — The socket connection initialization payload has been updated:

- Connection-type field value changed: `"t": "c"` → `"t": "a"`
- Auth token field renamed: `"susertoken"` → `"accesstoken"` (the token value itself is unchanged)

### Summary of Changes

| Item | Change | Description |
|---|---|---|
| Base URL | Endpoint | REST API base endpoint updated |
| WebSocket URL | Endpoint | WebSocket endpoint updated |
| Payload Field | `"t": "c"` → `"t": "a"` | Socket connection type updated |
| Auth Field | `"susertoken"` → `"accesstoken"` | Authentication token field renamed |

### Action Required

- Update Base URL configuration in the application
- Update WebSocket connection URL
- Use the updated socket connection payload

### Socket connection payload (verbatim)

Previous Payload:

```json
{
  "t": "c",
  "uid": "FZ00000",
  "actid": "FZ00000",
  "source": "API",
  "susertoken": "GHUDWU53H32MTHPA536Q32WR"
}
```

Updated Payload:

```json
{
  "t": "a",
  "uid": "FZ00000",
  "actid": "FZ00000",
  "source": "API",
  "accesstoken": "GHUDWU53H32MTHPA536Q32WR"
}
```

These two JSON blocks are illustrative socket-init payloads (not an HTTP response); they are the only verbatim JSON samples on the page.
