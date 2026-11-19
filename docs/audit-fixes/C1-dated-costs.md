# C1 — dated option STT checkpoint

Status: focused implementation and caller checks GREEN; ready for root review.

Authorization: dated default cost fix and cumulative checkpoints; no broker calls,
orders, connected-stack mutation, database migration or historical-record rewrite.

Confirmed source: NSE Securities Transaction Tax Computation, opened 2026-10-23:
https://www.nseindia.com/static/products-services/equity-derivatives-securities-transaction-tax
Table: sale of an option, seller pays on premium; 0.10% through 2026-03-31,
0.15% from 2026-04-01. Existing default is 0.001 (0.10%).

Plan / binary checks:
1. Reproduce incorrect current default and IST date boundary in isolated tests.
   Done when tests fail because the dated schedule is absent.
2. Resolve automatic rates at charge time using exit date; preserve explicit rates
   across serialization. Done when boundary/arithmetic/preservation tests pass.
3. Forward exit timestamps in paired/premium backtests, paper friction/evidence and
   live charge journalling. Done when focused behavioral checks pass.
4. Review diff and report limits. No running-container update is claimed.

Caller inventory: option_backtest, premium_momentum (also consumed by premium
backtest/tuner), live_friction, paper_auto execution evidence, live/close_loop.
Scalping/costs explicitly sets its own 0.0015 schedule; leave it unchanged.
screen_option_buying computes a current estimate and may use missing-date fallback.

Critical risk: an explicit saved numeric rate must never be silently replaced.
An automatic rate must remain automatic when serialized for mixed-date history.

RED checkpoint: `python -B -m pytest --noconftest -p no:cacheprovider -q
tests/test_option_costs_dated_stt.py` with plugin autoload disabled: 13 failed.
Current missing-date arithmetic produced STT 9.00 instead of 13.50 rupees on
9,000 rupees sell turnover. Date cases fail because no exit_ts input exists;
automatic serialization currently freezes 0.001. No production edits yet.

Core implementation: `None` means automatic dated rate and remains JSON null
through to_dict/from_dict; existing numeric serialized rates (including zero)
remain pinned. Missing date means current IST date; malformed supplied dates
raise. No frontend stt_sell_rate consumers found by source search. Raw gross
realized_pnl, deduction toggles and cap policy remain unchanged.

Caller RED checkpoint after the core fix: 5 failed, 14 passed. Old-date paired,
premium, both paper-friction branches and recorded live-close costing incorrectly
used today's schedule until forwarding the existing exit timestamps. Narrow
forwarding applied; verification pending. Live reconcile already calls
realized_fields with the CLOSED doc, so closed_at supplies its historical date
without changing the reconcile module or rewriting stored records.

GREEN checkpoint: 101 passed in 1.83s with plugin autoload disabled:
```
.venv/Scripts/python.exe -B -m pytest --noconftest -p no:cacheprovider -q tests/test_option_costs_dated_stt.py tests/test_option_costs.py tests/test_live_friction.py tests/test_live_charges_journaled.py tests/test_premium_momentum.py tests/test_paper_pnl_basis_parity.py tests/test_live_symbol_space_and_segment.py
```
Includes actual paired-option/premium-overlay/paper-close/live-journal helpers,
an in-memory fake live collection, and source-extracted pure paper touch-evidence
helper (no paper_auto/runtime startup import). Prior generic parity expectations
now supply their historical fixture timestamps instead of comparing a historical
trade to today's date-free calculation.

Observed arithmetic: 9,000 sell turnover yields 9.00 STT on March 31 and 13.50 on
April 1. Only STT and total charges change; the difference is 4.50 rupees. Tests
cover the UTC-to-IST midnight boundary, ISO/aware datetime/epoch-ms inputs,
explicit old/custom/zero rates, and automatic serialization across the boundary.

Limit: no historical documents or database records are recalculated. Existing
explicit numeric schedules stay pinned even when stale. The pre-April branch
preserves the app's 0.10% baseline for its post-October-2024 warehouse history;
this change does not reconstruct older statutory schedules or exercise STT.
Current source changes have not been deployed or applied to running containers.

Core lesson: resolve default taxes at the sell timestamp rather than config-load
time. Confirmed approach: JSON null preserves automatic intent through shared
serializers while numeric rates preserve explicit historical intent. Dead end to
avoid: replacing the global constant alone charges mixed-date history at today's
rate; caller RED checks reproduced that error after the initial core change.

Final review checkpoint: same focused command re-run after docstring review:
101 passed in 1.81s; `git diff --check` on all touched tracked C1 files exited 0.
Only existing LF/CRLF notices were emitted. Root review remains required; no
commit, connected-stack rebuild or deployment was performed by this agent.

Root acceptance — 2026-10-23: reviewed the date schedule, explicit-rate preservation,
caller timestamps and unchanged gross cap policy. Included in the final 406-case
isolated integration run. Local source only; the broader exchange-fee schedule and
running-image rollout remain open, and no historical records were rewritten.

2026-10-24 rollout update: included in the approved rebuilt backend; running source
hashes match the reviewed files. Final selected integration run: 443 passed.
The broader exchange-fee schedule remains open; no historical records were rewritten.
