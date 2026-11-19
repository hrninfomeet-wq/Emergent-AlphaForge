# B1 — saved backtest deployment transfer

## Scope / safety

User-authorized source fix only. Owner: backtest_truth. No broker calls, deployment
submissions, service actions, schema changes, or backend deployment-validation edits.
Shared docs and other agents' files are outside this package.

## Pieces and checks

1. Reproduce raw saved-run prefill loss. Done when actual current wizard effect
   returns default costs/DTE for a complete run with non-default execution settings.
2. Normalize effective execution and load full detail. Done when recorded-shape
   ordinary/premium fixtures preserve costs, spread, DTE, exits, window and sizing;
   failed/malformed/stale loads cannot publish a ready source.
3. Wire the wizard and request preview. Done when it consumes the normalized source,
   carries complete execution fields into the existing request contract, and pure
   regression tests pass. Root independently reviews before acceptance.

Critical part: effective policy identity (drives a financial decision). Second check:
compare request output independently to fixture execution fields and existing backend
consumer contracts. No running-container result will be claimed from source-only tests.

## Progress

- Initial checkpoint: read audit master checkpoint and traced current wizard,
  `preset_execution.py`, option result envelopes, entry-window and risk consumers.
- Current failure: run-list includes only `config.mode`; even full run config has
  `option_backtest`, while wizard prefill reads only `config.execution`.
- Additional confirmed omissions: spread floor/rates and entry window are absent from
  the outgoing request; risk subsection enable flags are not restored; fixed-lot
  premium sizing is labelled premium-at-risk.
- RED milestone: in-memory Node execution of the actual existing prefill effect
  with a recorded-shape option run printed `costs=false,dte=[],lots=1,spread=0`
  then failed `assert.equal(form.friction_costs_enabled, true)` with `false !== true`.
  No API or broker handler was called.
- Added `frontend/src/lib/backtestDeploySource.js` and
  `tests/js/backtestDeploySource.test.mjs`. First helper run: 10 passed / 1 failed;
  failure was an assertion before the loader's promise microtask invoked its injected
  fetch. Test now awaits that microtask. No wizard wiring applied yet.
- GREEN milestone: `node --test tests/js/backtestDeploySource.test.mjs` now passes
  16/16 tests. Tests include actual wizard request-object execution (without calling
  `create` or any API), progression expression, raw/thin detail, effective vs requested
  costs/sizing, explicit disabled costs, mixed exit units, overlay enable flags,
  unbounded/scalar-zero DTE, premium no-point-slippage/expiry-tail behavior, malformed
  source, wrong response ID, cancelled late success/error and detail-read failure.
- Wired `frontend/src/pages/LiveSignals.jsx`: cancellable full-detail read, source-key
  application fence, loading/error/retry, complete request fields, truthful fixed-lot
  sizing and entry-window display. Babel JSX parser passed.
- Additional effective-policy distinction: premium session backtests do not consume
  pairing-form DTE or generic pairing exit/cap overlays. Those ignored requests are
  not introduced as fresh deployment restrictions. Engine params remain source-pinned.
- Unrepresentable saved moneyness or entry window is explicitly reported instead of
  defaulting to ATM or silently letting the backend clamp a differently labelled window.

## Assumptions / limits

- Backend `risk` accepts the existing `trade_window_start/end` keys; evaluator reads
  those keys through `resolve_entry_window`. No backend validation changes needed.
- Source detail failure must block this transfer instead of substituting defaults.
- Existing explicit operator edits after loading remain possible; source changes reset
  policy fields so prior selections cannot contaminate another run.

## Next

Review policy edge cases and source-to-payload parity independently, add Python suite
entrypoint, run targeted checks, and report to root for independent review. No browser
smoke or running deployment check has been performed; source changes remain local.

## Root acceptance — 2026-10-23

The final Node suite has 17 cases and is included through the Python entrypoint
in the root's 406-case isolated run. Root reviewed the request payload and checked
four actual saved audit records through the helper without writing to MongoDB.
The production frontend build passed. Earlier interrupted-agent status is superseded
by this root check. No deployment was submitted and no connected image was rebuilt.
Source transfer does not certify paper/backtest exit parity; existing strategy-hint
precedence remains an explicit limit in the master checkpoint.

2026-10-24 rollout update: included in the approved rebuilt frontend; served source
map matches the reviewed source. Final selected integration run: 443 passed.
No deployment submission or live-money validation was performed.
