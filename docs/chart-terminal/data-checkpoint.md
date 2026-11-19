# Chart data implementation checkpoint

Owner: chart_data_impl sub-agent. Only `backend/app/chart_data.py`, optional new
catalogue helper, `tests/test_chart_data.py`, and this checkpoint are in scope.

Contracts agreed with root: search returns `{items,warnings,as_of_ms}`; resolve
returns an instrument; history follows the implementation plan. `chart_bucket_time`
is shared with root's live aggregator: intraday anchored at 09:15 IST, daily at
midnight IST, weekly at Monday midnight IST. No warehouse writes or broker actions.

Pieces and checks:
1. Candle identity/alignment (decision-critical): independently calculated OHLC
   fixtures, exclusive pagination and REST/live bucket equality.
2. Catalogue (costs redone work): fake master covers NSE/BSE cash groups, indices,
   current derivatives, excludes expired/currency/commodity, refresh/cache bounds.
3. Retrieval (costs redone work): saved-history fallback, generic-key REST,
   bounded requests, native interval validation, null index volume.
4. Review (costs redone work): focused offline pytest, inspect diff and report
   limitations to root. Root updates shared learning_log/TODO.

Verified source seams: `warehouse.load_candles_df` accepts `include_off_session=True`
and caps each read at 200,000 rows; Upstox `_authenticated_get` provides existing
auth and retry behavior. Execution resolver is limited to three underlyings and
will not be reused. Official Upstox V3 historical/intraday and instrument docs were
opened this session. Public master downloaded in memory: BSE equities use groups
including A/B/X, so `instrument_type == EQ` alone is insufficient.

Implementation is in progress; no test pass claimed yet.

## Resumed implementation

- New `backend/app/chart_data.py` written: catalogue, saved aliases, UTC-second
  bucket helper, native REST histories and read-only warehouse fallback.
- Public master fetched in memory again: BSE equity groups differ from NSE;
  cash segments also include debt. No broker account or token action.
- Root requested optional `exchange_token` and `underlying` identity fields; added.
- Focused tests and adversarial review are in progress; no test pass claimed.

## Root verification after resumption — 2026-11-01

Data tests are complete and included in the 112-pass selected backend run.
The production master was fetched read-only: search returned NIFTY 100, NIFTY
200, NSE/BSE RELIANCE, SENSEX50 and current NIFTY/RELIANCE futures with exchange
tokens and dated expiries. No account/session action or persistent data write.
Latest-saved fallback uses a read-only last timestamp query. Saved finalized
buckets take precedence; REST fills gaps/reconciles forming buckets. Special
session timestamps are retained with a warning. `peek_history` provides detached
cache snapshots and an optional watermark filter for stream reconciliation.

Remaining: real authenticated candle/API integration and comparison to the
running warehouse after safe Docker startup; these are not certified by mocks.
