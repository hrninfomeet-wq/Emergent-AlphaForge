# Conditional rules implementation checkpoint

Implemented `backend/app/conditional/models.py` and `rules.py` only. Parent owns integration. User steering explicitly deferred the test campaign/audit; no broad automated tests were added or run in this work unit.

## Public interfaces

- `normalize_spec(dict, max_lots=5) -> dict`: strict JSON types, finite numerics, positive whole lots, enum/date/time validation, bounded stop percentage, mutually exclusive validity choices, explicit defaults. Reject unknown fields. `max_entry_premium` is required for live, optional for paper.
- `resolve_contract(spec, contracts, spot) -> dict`: match selected expiry/type/index, validate required metadata, reject duplicate contracts/strikes, choose from actual strike ladder. ATM tie chooses lower strike; CE ITM moves down, PE ITM moves up. DIRECT must exist.
- `arm_condition_ok(spec, spot) -> bool`: reject already-true cross mode, permit level.
- `entry_ready(spec, previous_spot, spot, premium, reference) -> bool`: ALL selected predicates on the same evaluation; previous tick strictly on the opposite side, current tick inclusive at threshold. Invalid inputs do not fire.
- `deadline(spec, now_epoch_seconds) -> epoch_seconds`: IST validity capped at same-day forced square-off; reject past expiry/deadline. Parent supplies calendar gating.
- `time_today(HH:MM, now_epoch_seconds) -> epoch_seconds` and `validate_contract(dict) -> dict` are also available.
- `exit_state(spec, fill_price, previous_state=None) -> dict`: fill-based absolute stop/target; prior tighter stop/peak survives fill-basis updates.
- `evaluate_exit(spec, state, ltp) -> (new_state, reason_or_None)`: stop/target, activated step trailing, profit-lock ratchet. Single winning reason; parent owns broker OCO cancellation. State keys: entry, stop, initial_stop, target, peak, stop_reason, trail_active, lock_active.
- `price_limit(reference, band, tick, side='B') -> float`: Decimal directional rounding with exact band bounds; rejects when no marketable tick fits. `split_quantity(qty, lot, freeze) -> list[int]`: whole-lot freeze slicing with no metadata defaults.

All invalid configurations/metadata raise `ValueError`. The pure module reads no network, clocks, database or production runtime.

Confirmed basic verification: `py_compile` passed for both files. A smoke import produced `paper 200.4 stop` for the default mode, a 0.2% band at premium 200/tick 0.05, and stop evaluation at 100 from entry 200. Re-normalizing the normalized specification compared equal. These are basic implementation checks, not acceptance tests or broker evidence.

Not verified: complete boundary matrix, three-instrument tick lifecycles, integration contracts, broker behavior. Deferred under the user's implementation-only instruction.

Core lesson: treat an unknown trigger-time option as unbounded until the user provides a premium cap. Confirmed approach: JSON normalization with finite strict numerics and Decimal tick/band arithmetic. Dead end avoided: copying hardcoded index strike spacing into resolution or using floating-point band comparison at an exact boundary.
