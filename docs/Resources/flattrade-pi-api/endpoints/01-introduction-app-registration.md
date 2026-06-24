# Introduction & App Registration

**Category:** Setup · **Type:** Info · **Method:** —  
**Path:** `/` · **URL:** `https://piconnect.flattrade.in/PiConnectAPI`  
**Source:** PDF pages 1-4

## Summary
Pi is a collection of REST APIs (with WebSocket streaming) for building a modern stock-market investment and trading platform — execute orders in real time across equities, commodities and currency, and stream live market data over WebSockets. Before using the APIs you must register your App on the Flattrade Wall to obtain an apiKey and apiSecret; this section walks through both registration flows (less-than vs. more-than 10 orders/second).

## Registration form fields (web portal — not API body fields)

### IP Configuration (both flows: 3.1.2 / 3.2.1)
| Field | Required | Possible values | Description |
|---|---|---|---|
| Primary IP Address | yes | An IP address | IP for API request. |
| Secondry IP Address | no | An IP address | IP for API request (optional). |

### URL Configuration (both flows: 3.1.3 / 3.2.2)
| Field | Required | Possible values | Description |
|---|---|---|---|
| App Name | yes | Text | Your App Name. |
| App ShortName | yes | Text | Short Name of your APP. |
| Redirect URL | yes | A URL | URL to which we need to redirect after successful login authentication. Note: Code to generate the token will be sent as parameter to this URL. |
| Postback URL | no | A URL | URL to which you will be reciving order updates for the orders placed through API. |
| Description | no | Text | Short description about your app. |

### Strategy / Segment (high-volume flow only: 3.2.3)
| Field | Required | Possible values | Description |
|---|---|---|---|
| Strategy | yes | Text | Enter your strategy for your API key. |
| Segment | yes | A segment | Select the segment for your API key. |
| File | yes | A file | Upload file for the selected segment. |

## Notes
- This is an informational / setup section. It documents the manual web-portal app-registration process, not a callable REST endpoint, so there is no method/path, no jData/jKey query parameters, no jData JSON body, and no JSON request/response samples.
- **Base URL** for all Pi Connect REST API calls: `https://piconnect.flattrade.in/PiConnectAPI` (page 1, right-hand code column).
- **Registration steps:**
  1. Log in to the Wall at `https://wall.flattrade.in`.
  2. Navigate to **Pi** in the top menu bar and click **"CREATE NEW API KEY"**.
  3. Select the order volume to create the api key.
- **Order-volume branch:**
  - **Yes** = More than 10 orders per second → section **3.2**.
  - **No** = Less than 10 orders per second → section **3.1**.
- The **high-volume (3.2)** flow additionally requires uploading **Strategy / Algorithm details** and choosing **Segments** (Strategy, Segment, File upload) before submitting; the **low-volume (3.1)** flow does not.
- **Approval workflow (both flows):** review the Configuration Summary, tick the box to accept Terms & Conditions, and Submit. The request shows as **Pending** until approved; once approved the API key is generated. Click the **eye** icon to reveal the Secret Key, then copy **both** the API Key and the Secret Key.
- Source PDF typos preserved verbatim: "Secondry IP Address" and "reciving order updates".
- The form-field tables above are web-portal UI fields, not API body fields.
