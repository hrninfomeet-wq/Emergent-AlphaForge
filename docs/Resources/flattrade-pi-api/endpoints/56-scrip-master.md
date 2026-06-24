# Scrip Master

**Category:** Account & Reference · **Type:** Info · **Method:** —  
**Path:** `—` · **URL:** `—`  
**Source:** PDF pages 91-92

## Summary
Scrip Master is a reference resource that provides downloadable scrip (symbol) master files for each exchange segment. Each "Scrip Group" row offers a DOWNLOAD link to fetch the full symbol list for that segment, used to map tradingsymbols/tokens for order placement and market-data subscriptions.

## Notes
This is an informational/reference section, not a REST or WebSocket endpoint — there is no request/response body, jData/jKey params, or JSON sample. It presents a static list of "Scrip Groups", each with a "DOWNLOAD" button/link to retrieve that segment's scrip master file.

The available Scrip Groups (segments), in the order shown on the page image, each with its own DOWNLOAD link:

| # | Scrip Group |
|---|---|
| 1 | NSE - Equity |
| 2 | NSE - Equity Derivatives |
| 3 | NSE - Index Derivatives |
| 4 | NSE - Currency Derivatives |
| 5 | MCX - Commodity |
| 6 | BSE - Equity |
| 7 | BSE - Index Derivatives |
| 8 | BSE - Equity Derivatives |

**Text-ordering caveat:** the cleaned page-92 text flattens the table and lists the BSE rows slightly out of order (a stray "DOWNLOAD" line appears before "BSE - Equity", and the DOWNLOAD lines for "BSE - Index Derivatives" / "BSE - Equity Derivatives" appear transposed). The page IMAGE is authoritative and shows the eight segments in the order above, each with its own DOWNLOAD link.

The section title "SCRIP MASTER" appears at the bottom of page 91; the actual content (Scrip Groups table) is on page 92. The section ends where "ORDER API RATE LIMIT" begins (also page 92).

No actual download URLs, file format (CSV/TXT/ZIP), update frequency, or column schemas for the downloaded scrip files are documented in this section.
