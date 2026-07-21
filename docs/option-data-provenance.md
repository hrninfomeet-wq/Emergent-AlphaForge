# Historical Option Data Provenance and Promotion Gate

## Verdict as of 2026-07-20

AlphaForge's historical option candles are useful for **research triage**, but they
are **not point-in-time certified** and must not be used as paper-to-live promotion
evidence.

The timestamps are market-candle timestamps returned by Upstox, but that alone does
not make the dataset point-in-time. A point-in-time dataset must also prove which
contract metadata and market fields were available at each historical decision,
when each row was first obtained, and that a reusable broker token never joined two
different contracts.

Current warehouse snapshot:

| Test | Result |
|---|---:|
| Option-contract documents | 63,868 |
| Two-part broker tokens mapped to more than one contract identity | 8,714 |
| Collision tokens that currently hold candles | 2,423 |
| Candles attached to those collision tokens | 2,551,919 of 7,229,203 |
| Tokens with candle history for multiple identities at distinct periods | 183 |
| Chain snapshots | 0 |
| Legacy candles with `first_ingested_at` / `retrieval_run_id` | 0 |

Example: `NSE_FO|52526` identifies a 23,850 PE expiring 2025-01-02 in one
period and a 57,900 CE expiring 2026-03-30 in another. A two-part
`SEGMENT|TOKEN` value is therefore a live routing address, not a durable historical
contract identity.

## What has been corrected

New ingestion now derives `contract_key = canonical_token + expiry_date`, and stores:

- canonical `instrument_key` for live/API compatibility;
- immutable `contract_key` for historical joins;
- `source_endpoint`;
- `first_ingested_at` on insert;
- `last_retrieved_at` on refresh; and
- `retrieval_run_id` for the warehouse job.

Option simulation groups by contract identity. A canonical token alias is permitted
only when it maps to at most one dated identity. A reused token spanning multiple
expiries receives no alias, so the backtester cannot silently cross-pair contracts.

Optimizer option re-rank and WFO option-OOS results now carry a machine-readable
`data_integrity` verdict. Until the blockers are removed, the UI labels them
**research only** and `promotion_allowed` remains false.

That verdict controls the forward-validated label, not strategy selection. A
research-only source can still be deployed for live signals or paper trading and,
after the separate explicit unvalidated-live consent, real-money execution. The
override is audited and does not upgrade the underlying evidence verdict.

## Why the existing bars still fail the point-in-time test

1. Legacy rows were fetched retrospectively and do not record first-observed time or
   fetch-run identity.
2. The contract master is retrospective; rows generally lack a dated listing/master
   snapshot proving the contract was known at the decision time.
3. Candle `ts` is treated as a minute label, but there is no explicit `bar_end_ts` or
   rule proving a strategy decision used only a completed bar.
4. Legacy capture used Upstox `ltpc`, which carried last trade information—not a
   historical executable bid/ask/depth surface. v0.56.1 changes the default to
   `full` and retains five depth levels, OI, IV, Greeks and receive/exchange
   timestamps, but this only improves new forward rows; it cannot repair history.
5. A fixed spread/cost model cannot prove that a historical option was executable at
   the modelled price and size.

## Controlled rebuild—not an in-place guess

Do not bulk-edit all 7.2 million legacy rows and call the result certified. Use this
sequence:

1. Keep the current collection read-only for reproducibility and record its indexes,
   counts, token/expiry collisions, and hashes.
2. Create a shadow v2 collection with unique `(contract_key, ts)` and explicit
   `bar_end_ts`, source endpoint, retrieval run, and first-ingest fields.
3. Re-fetch each contract by its full metadata identity and expiry. Ambiguous legacy
   rows may be comparison inputs, but not the source of truth for the v2 identity.
4. Compare per-contract date ranges, OHLCV/OI hashes, duplicate timestamps, and
   expected trading-session coverage. Quarantine conflicts rather than choosing a
   row silently.
5. Run paired-option regression tests and replay representative saved runs against
   both collections; explain every material P&L change.
6. Swap readers only after a backup and signed audit report. Retire the old unique
   `(instrument_key, ts)` index only when v2 coverage is complete.

## Forward capture required for execution evidence

During market hours, v0.56.1 now captures the Upstox Full surface for the eligible
ATM band. At minimum the qualifying forward rows retain:

- exchange timestamp and local receive timestamp;
- contract key, expiry, strike, side, lot/tick size, and dated master snapshot;
- best bid/ask prices and quantities plus depth when available;
- last price/quantity, volume, OI and previous OI;
- feed mode, reconnect/gap markers, and sequence/run identity; and
- strategy `decision_ts`, chosen contract, modelled price, executable price, and
  rejection reason.

Paper entries/exits persist their bounded market snapshots. When both timestamps are
fresh and top-of-book quantity covers the trade, AlphaForge also computes an
`execution_realized_pnl` at entry ask / exit bid after statutory charges; the forward
promotion series uses that value and requires ≥95% complete surfaces. IV and Greeks
remain diagnostics—bid/ask, depth, timestamp alignment, and gap detection determine
execution eligibility.

Official references: [Upstox Historical Candle Data V3](https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/)
and [Upstox Market Data Feed V3](https://upstox.com/developer/api-documentation/v3/get-market-data-feed/).
