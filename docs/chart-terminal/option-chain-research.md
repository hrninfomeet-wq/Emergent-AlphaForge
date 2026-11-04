# Item 4 option-chain contract research

Checked 2026-11-04. Research only; no authenticated API request, broker action,
source change, deployment, or claim of market-hours acceptance.

Recommendation: reuse `fetch_option_chain` plus `normalize_chain_response` for a
read-only REST snapshot panel. Treat capture freshness and quote freshness as
different facts. Keep vendor values and their provenance intact.

## Checkpoint and done tests

Critical part: data meaning/freshness, because a wrong version drives a trading
decision. Done: compared the existing parser with the official response schema,
then independently checked the V3 streaming schema and existing stream parser.
Coverage and expiry discovery: done when catalogue fields and local restrictions
are identified. Layout: done when one official terminal interaction reference is
opened. All research checks passed; actual API coverage remains untested.

## Existing implementation

Confirmed: `backend/app/upstox_client.py:323` accepts a concrete underlying key and
expiry and returns the raw chain response. `backend/app/chain_snapshot.py` already
preserves numeric zero versus absent/null, nested CE/PE market/Greek fields, sorted
strikes, per-strike PCR, spot, expiry, source and local capture timestamp (checked
against both files). Reuse this parser; do not alter the historical storage
meaning of `ts` or promote per-strike PCR to a chain summary.

Confirmed: `fetch_option_contracts` and `normalize_option_contract` reject symbols
outside `INSTRUMENT_KEYS`; `backend/app/instruments.py` maps NIFTY to
`NSE_INDEX|Nifty 50` and SENSEX to `BSE_INDEX|SENSEX`. Equity options require a
separate validated generic-key catalogue seam; the present helper is not generic
(checked against client and instruments source).

## REST fields and explicitly unknown semantics

Confirmed: each strike has `call_options`/`put_options`, instrument identity,
`market_data.{ltp,oi,prev_oi,volume,close_price}` and `option_greeks.iv` (checked
against the [official chain schema](https://upstox.com/developer/api-documentation/get-pc-option-chain/)).
The documented response has no exchange/as-of timestamp. `prev_oi` is described
only as previous OI; its baseline is unspecified. IV has no stated unit; volume
has no explicit session boundary or lot/unit convention. `close_price` is not
explicitly identified as previous-session close. MCX chain is excluded.

Recommendation: expose raw nullable fields, `oi_change = oi - prev_oi` only when
both exist, and label it “ΔOI vs vendor previous”. Keep IV as “IV (vendor)” without
a percent conversion. Do not label volume/OI as lots or infer price day change
from `close_price`. A missing baseline yields null, never zero. Avoid percentage
ΔOI when the denominator is absent or zero. These are implementation rules,
not verified vendor baseline definitions.

## Expiries and coverage

Confirmed: [Option Contracts](https://upstox.com/developer/api-documentation/get-option-contracts/)
accepts an underlying key and optional expiry; returned identity includes expiry,
strike, CE/PE, lot size, underlying type, exchange and weekly flag. Documented
underlying types include INDEX and EQUITY; segments include NSE_FO and BSE_FO.

Confirmed: the [instrument master documentation](https://upstox.com/developer/api-documentation/instruments/)
provides NSE/BSE catalogues, equity-option examples, underlying keys and instrument
types (checked against the options example and field table). Catalogue presence
is not proof that every underlying/expiry returns a populated chain now.

Recommendation: discover distinct ISO expiries from current contracts, sort them,
select the nearest nonexpired date using the IST trading date, preserve a user's
still-valid selection and reload on expiry rollover. Never infer expiry weekdays.
NIFTY/SENSEX are the first acceptance cases; expose equities only after their
generic identity path and real response are checked. An index/stock with no
contracts gets “Option chain unavailable”, not synthetic rows.

## Timestamps and update strategy

Confirmed: [V3 feed documentation](https://upstox.com/developer/api-documentation/v3/get-market-data-feed/)
distinguishes `ltpc.ltt` (last trade) from envelope `currentTs` (tick timestamp).
Full feed includes OI, volume and IV. Normal full subscriptions allow 2,000 keys
alone or 1,500 in mixed modes, with two connections per user. The documented IV
example is `0.1334381103515625`; the REST example is `262.31`. This does not prove
conversion semantics across endpoints (checked against both examples).

Confirmed: `normalize_feed_response` stores `ltt` as `ts`, falling back to
`currentTs`, and stores envelope time as `received_ts`; it passes IV through
without scaling (checked against `backend/app/upstox_stream.py:521`). An old LTT
can accompany a newer market-data message. No per-field OI/IV timestamp is
documented in the opened feed table.

Recommendation: begin with shared REST polling for the selected chain; do not
create another socket or replace only some REST fields with stream values.
Response metadata should include `source`, underlying, expiry, strike count,
`captured_at_ms`, `exchange_ts_ms: null`, retrieval age, last error and stale
state. Display “Retrieved …; exchange timestamp unavailable”. A successful fetch
does not prove a new trade occurred. On error retain the previous snapshot with
its original capture time and an error/stale badge. Reject an old in-flight
response after the user switches underlying/expiry.

Confirmed: [standard REST limits](https://upstox.com/developer/api-documentation/rate-limiting/)
are per API/per user: 50 requests/second, 500/minute, 2,000/30 minutes. The page
does not specifically classify option-chain calls. A 10-second interval consumes
`30 × 60 ÷ 10 = 180` calls per half hour per distinct chain, before retries.

Recommendation: use that conservative interval, server-side coalescing/cache by
user/key/expiry, catalogue caching, hidden-panel pause and bounded retry/backoff.
Respect existing recorder traffic and HTTP 429; do not multiply requests per tab.

## Layout reference and handoff

Confirmed: [Kite terminal mode](https://support.zerodha.com/category/trading-and-markets/trading-faqs/terminal-mode/articles/what-is-kite-terminal-mode)
documents linked chart/option-chain/order panels and strike hover actions (checked
against its widget table). This is interaction inspiration only; no assets used.

Recommendation: sticky expiry/source/freshness toolbar; mirrored CE/PE columns
around a sticky strike column; nearest-to-spot ATM highlight with an explicit
jump-to-ATM action; optional bounded strike window with displayed/full returned
counts. Show unavailable values as dashes. Chart action loads the selected
contract into the active tile; ticket action only prefills the existing ticket
and inherits its scope and confirmation. No chain-cell action sends an order.

## Risks and acceptance handoff

Critical risk: capture time mistaken for quote time or unverified IV/baseline
units imply confidence the source cannot support. If wrong, stale or mis-scaled
research can drive an incorrect trade. I do not know the precise IV scale or
`prev_oi` boundary. To find out: obtain a vendor clarification or independently
reconcile simultaneous official terminal and API values before enabling percent
IV/day-OI labels. Meanwhile, preserve raw fields with the explicit labels above.

Not done: real nearest NIFTY/SENSEX API calls, entitlement verification, equity
chain response and market-hours changes. Root acceptance must check these when
the existing data session permits; synthetic fixtures prove rendering and update
reconciliation only. Test null/zero, one missing leg, empty/error payloads,
selection race, expiry rollover, stale retention and original capture-time
preservation before calling item 4 complete.

Core lesson: provenance is part of the field contract. Confirmed approach:
compare REST schema and streaming schema before sharing a display conversion.
Dead ends: inferring units from one sample, hard-coded expiry weekdays, calling
a recent HTTP retrieval an exchange-fresh quote. Root owns the shared learning
log/checkpoint update; this subtask owns this document only.
