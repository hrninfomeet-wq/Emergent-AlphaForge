# Login Flow & Session Token

**Category:** Setup · **Type:** Info · **Method:** POST  
**Path:** `/trade/apitoken` · **URL:** `https://authapi.flattrade.in/trade/apitoken`  
**Source:** PDF pages 5-7

## Summary
Explains how to obtain the session access token (jKey/token) used by all trading APIs. APIKey + APISecret are exchanged via a browser-based authentication flow: open the Authorization URL `https://auth.flattrade.in/?app_key=APIKEY`, log in with Client ID (UCC)/password/PAN-DOB, receive a one-time `request_code` on the registered redirect URL, then POST `api_key`/`request_code`/`api_secret` to `https://authapi.flattrade.in/trade/apitoken` to validate the code and receive the token.

## Request — JSON body fields
| Field | Required | Possible values | Description |
|---|---|---|---|
| api_key | no | | The public API key (APIKey) allocated to you. Example value in doc: `xcvvwegfhgh4454646`. |
| request_code | no | | A one-time code obtained during the login flow (returned to the registered redirect URL as `request_code`). Its lifetime is only a few minutes and it is meant to be exchanged for a token immediately after obtaining. Example value in doc: `xxdfddfdfdsfdsf84okkdlfelfdfdfd345fsf`. |
| api_secret | no | | SHA-256 hash of (api_key + request_token + api_secret) per the doc's note. Example value in doc: `sdfdsfsdfdsfXXXXXXX`. |

## Sample request
```bash
Call to https://authapi.flattrade.in/trade/apitoken in POST method to validate request_code and get the token

{
"api_key":"xcvvwegfhgh4454646",
"request_code":"xxdfddfdfdsfdsf84okkdlfelfdfdfd345fsf",
"api_secret":"sdfdsfsdfdsfXXXXXXX"
}
```

## Response fields
| Field | Possible values | Description |
|---|---|---|
| token | | The session access token (used as jKey in subsequent API calls). Example: `dsfdsf84okkdlfelfdfdfd3454545454ssdfsf`. Valid for 24 hours; tokens are cleared between 5-6 AM, regenerate after 6 AM. |
| client | | The client/account code (UCC). Example: `CCODE123`. |
| status | Ok | Request status. Example success value: `Ok`. |
| emsg | | Error message; empty string on success. |

## Sample success response
```json
{
"token":"dsfdsf84okkdlfelfdfdfd3454545454ssdfsf",
"client":"CCODE123",
"status":"Ok",
"emsg":""
}
```

## Notes
**Token Generation Steps:**

1. Open the Authorization URL `https://auth.flattrade.in/?app_key=APIKEY` in a browser, replacing `APIKEY` with the Apikey allocated to you (from the earlier API-key generation step 4).
2. Enter your Client id (UCC), Password, PAN/DOB and submit.
3. After you are authorized in the authentication portal, the screen redirects to your URL with `request_token` in the form `https://yourRedirectURL.com/?request_code=requestCodeValue`.
4. Call `POST https://authapi.flattrade.in/trade/apitoken` with `api_key`/`request_code`/`api_secret` to validate the code and get the token.
5. Use the returned token in appropriate end points to get more details of the user.

**Caveats:**

- Redirect URL is pre-registered with Flattrade against each API Key; if you have different redirect URLs for PROD and TESTING, each environment should use a different registered API Key.
- `request_code` is a one-time code whose lifetime is only a few minutes and must be exchanged for a token immediately.
- `api_secret` is the SHA-256 hash of (api_key + request_token + api_secret) per the doc's NOTE.
- The token will be returned ONLY if the request originates from the registered private static IP for the API key.
- The browser-based authentication step is always required (even for GUI or console programs) to create the access token.
- Access token has 24-hour validity (generate once per day); tokens are cleared between 5-6 AM each morning so regenerate after 6 AM; once generated the token can be stored to bypass authentication for subsequent connects.
- The base sample (from the page's right-column code block) is: `BaseUrl - https://piconnect.flattrade.in/PiConnectAPI` ; `ClientId - FT0000` ; `jKey - GHUDWU53H32MTHPA536Q32WR` (jKey is the generated token; the PiConnect base URL is distinct from the `authapi` host used to mint the token).
- The doc's NOTE also references a separate document for a detailed walkthrough on handling the security key parameter.
