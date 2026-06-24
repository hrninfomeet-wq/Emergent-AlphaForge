# API Rate Limits

**Category:** Account & Reference · **Type:** Info · **Method:** —  
**Path:** `—` · **URL:** `—`  
**Source:** PDF pages 92-92

## Summary
Documents the throttling limits for Flattrade pi API calls. The general "API RATE LIMIT" allows 40 requests per second and 200 requests per minute. A separate, stricter "ORDER API RATE LIMIT" applies to order-related endpoints at 10 requests per second and 40 requests per minute.

## Notes
The focal "API RATE LIMIT" table has two columns — "Time Frame" and "Rate Limit" — with two rows:

| Time Frame | Rate Limit |
|---|---|
| Per Second | 40 |
| Per Minute | 200 |

The page also shows an adjacent, distinct section "ORDER API RATE LIMIT" (a separate ALL-CAPS title) which applies a tighter limit specifically to order APIs. Same two-column / two-row layout:

| Time Frame | Rate Limit |
|---|---|
| Per Second | 10 |
| Per Minute | 40 |

The page additionally contains unrelated neighbouring content — a "Scrip Groups" download list at the top and the start of "CHANGE LOG" / "API & WEBSOCKET CONFIGURATION UPDATE" at the bottom — which is NOT part of the rate-limit section and is ignored here.

This is an Info section: there is no HTTP method, path, URL, request parameters, jData fields, response fields, or sample request/response blocks.
