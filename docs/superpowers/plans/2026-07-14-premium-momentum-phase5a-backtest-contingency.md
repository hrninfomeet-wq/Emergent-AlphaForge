# Phase 5A — full contingency ("lazy legs") in the BACKTEST engine only

Status: implementation brief. Parent spec:
`docs/superpowers/specs/2026-07-13-premium-momentum-phase4-5-full-contingency-design.md` (§4).
Target strategy ground truth: `docs/NF_CE_PE_EXP2_Strategy_Spec.md` (the user's decoded AlgoTest
export — exact values, exact semantics; where this doc and that one disagree, THAT one wins).

## 0. Scope discipline (read first)

**Backtest-only.** The live path stays single-leg by construction — and by a mechanism, not a
promise: `strategies/plugins/premium_momentum.py`'s `parameter_schema` is NOT extended in 5A, and
`strategy.merged_params()` is a strict allow-list (see `routers/research.py` "strict allow-list"
comment), so a deployment literally cannot carry `leg_mode`/`lazy_*` params into the live
evaluator. Do NOT "helpfully" add these params to the plugin schema — that is exactly how a silent
backtest↔live divergence would be born. Same reason: do NOT extend `PremiumTriggerConfig` /
`_CONFIG_FIELDS` in `premium_trigger_dispatch.py` — the Phase-4 general Optimizer/Backtest Lab
path stays single-leg until Phase 5B. The two-leg/lazy capability lives in the bespoke
`/premium-momentum` page + its backtest/tune routes only, for now.

**Out of scope for 5A** (deferred, do not build): global day-level target/SL (EXP2 §5 explicitly
has none), re-entries beyond the one-shot lazy leg (EXP2: "Target / further re-entry: None /
none"), any file under `backend/app/live/`, `deployment_evaluator.py`,
`premium_momentum_live.py`, `optimizer.py`, `premium_trigger_config.py`,
`premium_trigger_dispatch.py`.

**Hard gate this build serves**: after 5A lands, the EXP2-parameter comparison run
(single-leg vs both-legs vs both+lazy, real 2026-H1 NIFTY data, costs ON) decides whether
Phase 5B (live execution) is justified. 5A's job is to make that measurement possible and honest.

## 1. New params (backtest route `params` dict; all default to OFF = byte-identical today)

| Param | Type / default | Meaning |
|---|---|---|
| `leg_mode` | `"first_to_trigger"` (default) \| `"both"` | `both` = CE and PE primaries fully independent; either, both, or neither may enter (EXP2 §3). |
| `lazy_enabled` | bool, `False` | Arm the opposite-side reversal leg when a PRIMARY exits with reason `STOP` (never on TARGET/EOD). One shot per side per session, structural (EXP2 §4). Works in either leg_mode. |
| `lazy_momentum_pct` / `lazy_momentum_pts` | float / None | Lazy entry trigger from the ACTIVATION snapshot (exactly one required when `lazy_enabled`; fail-loud ValueError otherwise). |
| `lazy_stop_pct` / `lazy_stop_pts` | float / None | Lazy stop (same exclusivity rule as primary). |
| `lazy_target_pct` / `lazy_target_pts` | float / None | Optional. |
| `lazy_trail_x` / `lazy_trail_y` | pts, optional pair | Lazy stepped ratchet, points mode. |
| `lazy_trail_x_pct` / `lazy_trail_y_pct` | %, optional pair | Lazy stepped ratchet, %-of-entry mode. Pts XOR pct — both given = ValueError. |
| `lazy_moneyness` | str, default = primary `moneyness` | Fresh strike criteria at activation (EXP2: ITM1). |
| `trail_x_pct` / `trail_y_pct` | %, optional pair | PRIMARY stepped ratchet in %-of-entry mode (EXP2 §3 rule 4: "raise the SL by 5% of entry price" — discrete steps, NOT fixed points when % is configured; blueprint execution note is explicit about this). XOR with existing `trail_x`/`trail_y`. |
| `entry_cutoff` | IST "HH:MM", default None | No PRIMARY entries at/after this bar; no lazy ARMING (stop-out at/after cutoff arms nothing); no lazy ENTRIES at/after it either (EXP2: 14:40 blocks "momentum triggers and reversal entries both"). |
| `exit_time` | IST "HH:MM", default None | Hard exit bound: premium series sliced to min(session_end, exit_time); the EOD exit fires at that bar's close, reason stays `"EOD"` (EXP2: 15:13). None = today's behavior (session end). |

## 2. Where the code goes

### `backend/app/premium_momentum.py` (pure helpers)
- `walk_premium_momentum(...)`: add optional kwarg `entry_cutoff_ts: Optional[int] = None`. In the
  entry-search loop, a bar with `ts[i] >= entry_cutoff_ts` ends the search (no entry). Default None
  = byte-identical (parity test pins this). Exits are NOT cutoff-bound — an open position keeps
  managing its stop/target/trail after the cutoff.
- New `stepped_trail_stop_pct(*, entry_premium, running_high, base_stop, x_pct, y_pct)`:
  delegates to `stepped_trail_stop` with `x = entry_premium * x_pct / 100.0`,
  `y = entry_premium * y_pct / 100.0` (the running_high cap inside the delegate still applies).
  EXP2 arithmetic to pin in a test: entry 100, x_pct=y_pct=5, high=112 → favorable 12 →
  floor(12/5)=2 steps → stop = base + 10.0 pts, capped at 112.

### `backend/app/premium_momentum_backtest.py` (session state machine)
Per-session flow when the new params are active (pseudo, preserving all existing coverage gates):

```
lock CE+PE strikes at ref bar (unchanged); exit_bound = min(session_end, exit_time_ts or inf)
primaries:
  first_to_trigger: walk both sides (series sliced [ref_ts, exit_bound], entry_cutoff_ts passed),
                    keep earliest entry (today's tie-break preserved)
  both:             keep EVERY side that entered (0, 1, or 2 primary trades)
lazy (if lazy_enabled), for EACH primary trade with exit_reason == "STOP":
  stop_out_ts = trade.exit_ts
  if entry_cutoff and stop_out_ts >= cutoff_ts: count lazy_blocked_cutoff; skip
  opposite = "PE" if primary.side == "CE" else "CE"
  fresh spot  = spot close at the stop-out bar (exact bar from the session spot slice)
  fresh strike = lock_reference_strike(contracts, spot=fresh_spot, side=opposite,
                                       moneyness=lazy_moneyness, expiry=session expiry)
  lazy series = that strike's candles in [stop_out_ts, exit_bound] (canonical key)
  ref bar     = candle at ts == stop_out_ts, else asof <= stop_out_ts within 180s;
                missing -> count lazy_excluded_no_data; skip (NEVER mis-fill)
  walk from bars STRICTLY AFTER the ref bar with lazy params + entry_cutoff_ts
  one-shot: a lazy leg's own STOP arms nothing further
trades emitted: primaries + lazies (0..4/session — EXP2's "worst case A, B, A', B'")
```

Notes that are correctness-critical:
- **Look-ahead**: the lazy ref is the stop-out BAR CLOSE (bar-close granularity — the blueprint's
  "exact millisecond" is out of reach, already accepted in the parent spec; do not pretend
  otherwise in comments). The lazy walk's entry search must start at the bar AFTER the ref bar.
  The primary's stop fill happened intra-bar; using that same bar's close as the lazy ref is the
  honest 1m-bar approximation.
- Legs are fully independent (EXP2 §2 "Leg exits: Independent"). A lazy CE can coexist with a
  still-open primary CE on a different strike; that's correct, not a bug.
- Cost overlay (`apply_costs_to_trade`) applies per leg exactly as today.
- Trail resolution per leg: pts pair XOR pct pair (ValueError if both); pct uses the new helper.

Per-trade additions: `leg: "primary"|"lazy"`; lazy trades also carry `lazy_parent_side` and
`lazy_activated_ts` (= the stop-out ts). Existing fields unchanged (route consumers +
`_json_safe` tolerate extras).

Coverage additions: `lazy_armed`, `lazy_entered`, `lazy_blocked_cutoff`, `lazy_excluded_no_data`.
Summary additions: `summary["by_leg"] = {"primary": {trades, net_pnl_rupees, ...},
"lazy": {...}}` — the whole point of the 5B gate is reading the lazy legs' OWN net contribution.

### `backend/app/routers/premium_momentum_routes.py`
- `_load_window`: when `lazy_enabled`, the fresh lazy strike is locked from spot at an unknown
  future bar → its instrument_key is NOT in today's per-session ref-time locks. Widen the preload:
  for lazy runs, use the full warehouse moneyness band `["itm2","itm1","atm","otm1","otm2"]` for
  BOTH sides (union with whatever was requested). The warehouse only ingests that band anyway, so
  this is the honest maximum; a fresh strike outside it (big intraday move) becomes
  `lazy_excluded_no_data`, counted, never mis-filled. Contracts are already loaded unfiltered.
- `TUNABLE_KEYS` += `lazy_momentum_pct`, `lazy_stop_pct`, `lazy_target_pct`, `trail_x_pct`,
  `trail_y_pct`, `lazy_trail_x_pct`, `lazy_trail_y_pct`. (Base params like `leg_mode`/
  `lazy_enabled` flow through the tune request's base `params`, not the grid.)

### `frontend/src/pages/PremiumMomentum.jsx`
- Setup: leg-mode select (First-to-trigger / Both legs), primary trail pct pair, a "Reversal
  (lazy) leg" section (enable, momentum %, stop %, target %, trail % pair, moneyness), session
  fields (entry cutoff, hard exit time).
- Results: `leg` column in the trades table, the four lazy coverage counters, and the
  `by_leg` summary split (primary net vs lazy net — this is the decision number).

## 3. Tests (host-safe, TDD — write failing first; fixture style = tests/test_premium_momentum_backtest.py)

1. **Parity**: default params (none of the new keys) → byte-identical trades/coverage/summary to
   the current sim on the existing fixtures. This is the non-negotiable one.
2. both-mode: CE and PE both cross → two primary trades (same fixture yields one under
   first_to_trigger).
3. Lazy arming: primary CE STOP → lazy PE armed at the stop bar, fresh strike from THAT bar's
   spot, ref = lazy strike's close at that bar, enters on the lazy trigger after it.
4. Lazy NOT armed on TARGET or EOD exits.
5. One-shot: a lazy leg's STOP arms nothing.
6. Cutoff: (a) primary cross at/after cutoff → no entry; (b) stop-out at/after cutoff → no
   arming (`lazy_blocked_cutoff`); (c) lazy cross at/after cutoff → no lazy entry; (d) an OPEN
   position still exits normally after the cutoff.
7. exit_time: open leg exits at the exit_time bar close, reason `"EOD"`; bars after it never
   touched.
8. `stepped_trail_stop_pct`: the entry-100/5-5/high-112 arithmetic above; cap at running_high.
9. Look-ahead: lazy entry cannot trigger on its own ref bar.
10. Missing lazy-strike candles → `lazy_excluded_no_data`, no phantom trade.
11. Fail-loud: pts+pct trail both set → ValueError; `lazy_enabled` with no lazy trigger →
    ValueError; both `lazy_momentum_pct` and `_pts` → ValueError.

## 4. The EXP2 comparison run (after the build — the 5B gate)

Window: 2026-01-01 → 2026-07-10 NIFTY, costs ON, lots 2. Three configs:
A) today's single-leg first_to_trigger (mom 15 / stop 20 / trail 5-5 pct);
B) `leg_mode=both`, same primary params;
C) B + `lazy_enabled` (mom 10, stop 10, trail 5-5 pct, itm1) + `entry_cutoff=14:40` +
`exit_time=15:13` — i.e., EXP2 verbatim.
Report: net ₹ per config, C's `by_leg` split (do the lazy legs ADD or SUBTRACT money?),
lazy coverage counters (how often did warehouse band limits exclude an activation — this bounds
how much we can trust C). The parent spec's gate: 5B is justified only if the contingency is
measurably better, not merely different.
