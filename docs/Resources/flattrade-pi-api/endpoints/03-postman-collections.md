# Postman Collections

**Category:** Setup · **Type:** Info · **Method:** —  
**Path:** `/` · **URL:** `https://piconnect.flattrade.in/PiConnectAPI`  
**Source:** PDF pages 7-7

## Summary
Informational setup note pointing users to a downloadable Postman collection for testing the Flattrade pi APIs. To test an API in Postman you must first define the required variable fields — the base URL, clientid, and jKey — on the API specification.

## Sample request
```
Sample:
 BaseUrl - https://piconnect.flattrade.in/PiConnectAPI
 ClientId - FT0000
 jKey - GHUDWU53H32MTHPA536Q32WR
```

## Notes
- This is an informational / setup section only; it defines no request method, path, query parameter, jData field, or response payload.
- The page shows a download affordance — a download icon next to the link text "To Download the Postman Collections". The actual download URL is not present in the rendered/decoded text (only the icon is shown).
- Verbatim page instruction: "To test the API in Postman, you need to define the required variable fields such as the base URL, clientid, jkey on the API specification." (The body text writes the key as lowercase "jkey"; the Sample block on the right of the page writes it as "jKey".)
- The right-hand Sample block lists example values bound to this note: BaseUrl `https://piconnect.flattrade.in/PiConnectAPI`, ClientId `FT0000`, jKey `GHUDWU53H32MTHPA536Q32WR`. ClientId `FT0000` and the jKey value are placeholders/examples, not real credentials.
- Inferred (not stated in this note itself): the jKey is the session token obtained from the auth/apitoken flow described in the preceding section (steps 4–5 on this same page); that token is returned only if the request originates from the registered private static IP for the API key.
