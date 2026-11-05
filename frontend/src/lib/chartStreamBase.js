import { API } from "./api";

// Docker's frontend proxies these long-lived reads on its own origin. This
// leaves the API origin available for quotes, tickets and the live cockpit.
// A standalone dev server or fixture still connects directly to its configured API.
export const chartStreamBase = process.env.REACT_APP_CHART_STREAM_BASE || API;
