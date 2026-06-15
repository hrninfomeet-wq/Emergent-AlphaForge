# Exit / Risk Controls — Design Spec (piece 2 of 3)

**Date:** 2026-06-15
**Status:** Approved design, hardened by an adversarial multi-agent audit (§13); pending user spec review
**Branch:** `feat/exit-risk-controls` (off `feat/live-tick-paper-realism` 0.41.x)
**Scope:** Optimizer ↔ Backtest improvement track, **piece 2**: add trailing-stop,
breakeven / move-SL-on-profit, and per-day loss/target/max-trades caps as a single
**execution overlay** that lives where rupees exist (option pairing + the live mark),
so the optimizer + survival gate evaluate them AND the live engine enforces them — at
**decider-level** sim↔live parity.

> Follows the Piece-1 (survivable-optimization) pattern: a new pure module, strictly
> additive, behind a flag, `enabled=false` ⇒ byte-identical to today. A 5-lens
> adversarial audit (40 findings) reshaped the parity model; resolutions in §13.

---

## 1. Problem

The engine today supports only a **fixed** premium stop/target (and a spot-mirror
exit). It has **no** trailing stop, **no** breakeven / move-SL-on-profit, and **no**
per-day risk caps inside the backtest. Per-day caps exist only live, in
`deployment_kill_switch.py`, and only as a *hard pause* (manual resume) plus a
max-open block — there is no daily **target** cap, no **max-trades-per-day** count,
and nothing the **backtest** can model so the optimizer/survival gate can reward it.

Consequence: a strategy that would be survivable *with* a trailing stop or a daily
loss halt cannot be discovered or validated, and a deployed strategy cannot enforce
the same discipline the backtest assumed. This piece closes that gap on the **option
premium** axis — the series that is the actual ₹ P&L, that the survival gate scores,
and that the live `mark_trade_to_market` already stops on.

## 2. Goal

Add a composable **execution overlay** — premium trailing stop + breakeven + per-day
caps — that is:

1. **Enforced in the sim** (`option_backtest.py`) so the ₹ equity curve reflects it;
2. **Evaluated by the optimizer + survival gate** (it already runs the option sim at
   the finalist/re-rank stage), so a survivable exit config is rewarded — and
   (Commit 2) **searched** over a bounded grid;
3. **Enforced live** (`mark_open_deployment_trades` + `deployment_kill_switch.py`,
   driven by the existing `LiveExitMonitor` cadence) at **decider-level parity** with
   the sim (the shared pure functions return identical results for identical inputs;
   bar-vs-tick fill differences are documented, not eliminated — §7);
4. **Off by default** — `enabled=false` / unset caps ⇒ byte-identical behavior.

## 3. Decisions (locked with the user)

| Decision | Choice |
|---|---|
| Trail/breakeven axis | **Option premium** (the actual ₹ P&L; what survival scores; what live already stops on). Spot-mirror trailing is out of scope (documented future). |
| Per-day cap semantics | **Soft daily halt + auto-resume**: a tripped cap halts NEW entries for the rest of the IST session and auto-resets next session. **Sticky within a session** (§5.5). Existing hard kill-switches stay as a separate, stricter, independently-enabled layer. |
| Optimizer role | **Phased, one branch.** Commit 1: enforce + evaluate a user-set config (survival gate scores the ₹ curve). Commit 2: search a bounded grid of exit/cap configs per surviving finalist. |
| Units | **Percent-of-premium primary**, points accepted (consistent with `resolve_premium_levels` pts-over-pct). |
| Default state | `exit_controls.enabled=false` and `daily_caps` unset ⇒ unchanged engine. |
| Session attribution | The governor attributes a trade (count **and** realized ₹) to its **ENTRY** IST session, in **both** sim and live (§5.4/§5.5) — resolves the entry-vs-close bucketing parity gap (§13 F2). |

## 4. Architecture — a pure overlay module + thin wiring

**New `app/exit_controls.py`** (pure-Python, host-testable — **no** `motor`/`optuna`,
like `survival.py` / `rerank_select.py`). It is the **single source** both the sim and
the live path call, so the decision can never drift:

- `effective_premium_stop(*, entry, running_max, base_stop, cfg) -> Optional[float]`
  Returns the ratcheted stop = `max(base_stop, breakeven_stop?, trailing_stop?)`,
  **monotonic non-decreasing** for a long option. Owns the `unit` (pct/pts) arithmetic
  so sim and live compute it identically. Defensively normalizes/ignores out-of-range
  cfg (validation also happens at the router, §6) — never raises. `None` only when no
  stop of any kind exists.
- `daily_governor_decision(*, session_realized_cumulative_extremum, session_entry_count, cfg) -> {halt, reason}`
  Soft per-session cap decision from precomputed inputs. Trips on the session's
  **cumulative realized extremum** (running min for loss, running max for target) so a
  halt is **sticky** once tripped (§5.5, §13 G3), plus `session_entry_count ≥ max_trades`.
- `validate_exit_risk_config(cfg, *, costs_on, option_exec_on) -> list[str]`
  Pure validator returning error strings; the routers (corpus-visible) call it and 400.
  Host-testable; centralizes the range/guard rules so they cannot go silently inert.

**Signature policy (revised after §13 F1):** one strictly-additive, keyword-only,
`default-None` pair of params — `exit_controls` and `daily_caps` — is added to
`simulate_paired_option_trades` (after `sizing_config`). Default-None ⇒ every existing
pinned test stays green (none pass it) and the disabled path is byte-identical.
`build_rupee_equity_curve` and `resolve_premium_levels` signatures stay **frozen**.
`_walk_option_exit` (a private helper, not pinned) is extended.

```
SIM  (option_backtest.simulate_paired_option_trades, finalist/re-rank stage)
  _walk_option_exit: running-max premium ratchet via effective_premium_stop  → trail/breakeven exits
  pairing loop:      per-ENTRY-session ₹ ledger → daily_governor_decision     → skip entries after a cap

LIVE (paper_auto.mark_open_deployment_trades, driven by LiveExitMonitor ~1.5s)
  per fresh tick: ratchet via effective_premium_stop (prior running_max) → raise risk.stop_price in the SAME mark write
  deployment_kill_switch: entry-session governor (OPEN+CLOSED entries today, sticky) → block new entries

OPTIMIZER (optimizer.py finalist loop)
  Commit 1: survival_verdict scores the overlay-applied ₹ curve (overlay forwarded via the new kwargs)
  Commit 2: sweep bounded exit/cap grid per surviving finalist → best survivor
```

## 5. Components

### 5.1 Exit controls — mechanics & units
Under one optional `exit_controls` block, two **composable** controls on top of the
existing fixed premium stop/target (long option; percents are of entry premium):

- **Breakeven** (`trigger`, `lock`): once `running_max ≥ entry × (1 + trigger)`, raise
  the stop to `entry × (1 + lock)` (`lock = 0` ⇒ pure breakeven). A one-step ratchet.
- **Trailing** (`activation`, `distance`): once `running_max ≥ entry × (1 + activation)`,
  trail the stop at `running_max × (1 − distance)`. An MFE-giveback model.
- **Units:** percent primary; `unit:"pts"` switches to absolute premium points
  (`pts` ⇒ `entry+trigger_pts`, `running_max−distance_pts`). The unit arithmetic lives
  **inside** `effective_premium_stop` so sim and live can't diverge.

`effective_premium_stop` = `max` of {base fixed stop, breakeven (if triggered),
trailing (if active)}. Because `running_max` is non-decreasing, the effective stop
**only ratchets up**. A stop above entry (locked profit) is valid.

### 5.2 The look-ahead rule (correctness keystone)
A trailing/breakeven stop computed from a bar's **own** high and then allowed to exit
**within that same bar** is intrabar look-ahead. Enforced in `_walk_option_exit`:

```
running_max = entry_price          # the ENTRY bar's high is excluded (§13 L-notes)
for each forward bar i (entry_ts < ts ≤ backstop_ts):
    eff_stop = effective_premium_stop(entry, running_max, base_stop, cfg)   # uses max THROUGH i-1
    level, reason = intrabar_exit(high_i, low_i, eff_stop, target, is_long=True)   # stop-first
    if level is not None:
        fill = _stop_fill(level, reason, open_i)    # §5.2a gap honesty (overlay path)
        return exit(fill, reason)
    running_max = max(running_max, high_i)          # update AFTER the check
```

Live uses the **same ordering**: on a fresh tick, compute `eff_stop` from the **prior**
`running_max_premium`, run the exit check, and **only if it does not close** advance
`running_max_premium = max(prior, tick)` — persisted in the single mark write. This
removes the §5.5↔§5.2 contradiction the audit caught (§13 F4).

### 5.2a Gap-fill honesty (corrects the old §12)
For a long premium stop, `intrabar_exit` returns the stop **level** even when a bar
**gaps below** it — optimistic, and amplified by trailing (the trailed stop sits
higher). The old claim that "friction absorbs some" is **wrong** (SELL friction lowers
both fills equally and does not close the gap; §13 D2). Resolution, **overlay path
only** (legacy disabled walk stays byte-identical): the walk reads the bar **open** and
fills a long stop at `_stop_fill = min(level, open_i)` when the bar opened below the
stop. The legacy fixed-stop level-fill is unchanged (out of scope to perturb pinned
results). Live books the **actual breaching tick**, which is already the honest fill —
so the open-clamp brings the sim toward live, not the reverse.

### 5.3 Exit precedence (one tested ladder)
```
per bar (sim) / per tick (live) — pessimistic, stop-first:
  1. eff_stop = effective_premium_stop(...)          # ratchet up only (§5.1)
  2. intrabar_exit(high, low, eff_stop, target)      # STOP-first; → OPTION_TRAIL_STOP /
                                                     #   OPTION_BREAKEVEN_STOP / OPTION_STOP / OPTION_TARGET
  3. time_stop  (risk_hints.time_stop_minutes)       # unchanged
  4. spot-mirror exit / EOD backstop                 # unchanged
entry-side (NOT mid-trade):
  daily governor checked BEFORE a new entry; the breaching trade completes,
  later same-session entries are skipped.
```
Exit-reason taxonomy gains `OPTION_TRAIL_STOP`, `OPTION_BREAKEVEN_STOP` and
skip-reasons `DAILY_LOSS_HALT`, `DAILY_TARGET_HALT`, `MAX_TRADES_HALT`. The exit is
tagged by **which candidate produced the binding level** (attribution, §5.7).

### 5.4 Sim integration — `option_backtest.py`
- **Trail/breakeven:** extend `_walk_option_exit` with the running-max ratchet (§5.2)
  + gap-fill (§5.2a). Disabled ⇒ the current fixed-level walk, unchanged.
- **Daily governor:** spot trades arrive chronological and the spot engine is
  **single-position** (no overlap), so the pairing loop keeps a per-IST **entry-session**
  ledger. Before realizing trade T: increment that session's **entry count**; if the
  session is already halted (cumulative-realized extremum or count tripped, §5.5) →
  append `{**base, status:"SKIPPED_DAILY_CAP", skip_reason}`; else pair, realize
  **net-of-charges** ₹, and fold it into the session's running realized cumulative.
  `session_date` derives from `entry_ts` in IST (both count and realized keyed by
  ENTRY session — §3, §13 F2).
- **SKIPPED rows are a non-PAIRED status** (joins the existing `MISSING_*` family). All
  PAIRED-gated consumers already exclude them; the spec **pins the invariant** and adds
  two safeguards (§13 G1/G2): (a) governor-skipped spots are excluded from the survival
  **coverage denominator** via a `coverage["skipped_by_cap"]` count, so a deliberate
  halt cannot trip `MIN_COVERAGE` low-coverage; (b) the `routers/research.py` response
  must not surface SKIPPED rows in the public `trades` list (filter at the boundary or
  emit a separate `skipped_trades` key).

### 5.5 Live integration
- **Trail/breakeven** in `mark_open_deployment_trades` (on the `LiveExitMonitor` ~1.5s
  cadence): on a fresh tick `P`, compute `eff_stop = effective_premium_stop(entry,
  running_max_premium, base_stop, cfg)` from the **prior** `running_max_premium`;
  raise `risk.stop_price` to `max(current, eff_stop)`; let
  `mark_trade_to_market(..., auto_close_on_risk=True)` fire at the raised stop; **if it
  does not close**, advance `running_max_premium = max(prior, P)`. The raised stop +
  new `running_max_premium` are written in the **single** status-conditional
  `replace_one({id, status:"OPEN"})` that mark already performs (no second write,
  §13 G-notes). `running_max_premium` is seeded at entry in `build_auto_trade`. Stale
  ticks (>120s) do not advance the trail (conservative; documented §13 H).
- **Daily governor** in `deployment_kill_switch.py`: a new **soft, session-scoped,
  entry-keyed** decision — **distinct** from the existing `daily_realized_summary`
  (which keys by `closed_at` and serves the hard kill-switch). It computes, over this
  deployment's trades whose **entry** IST date is today: `session_entry_count` =
  OPEN **+** CLOSED entered today (so `max_trades` sees in-flight entries — §13 F3);
  `session_realized` = net realized of those that have closed, accumulated in close
  order, taking the running **extremum** (min for loss, max for target) so the halt is
  **sticky** within the session (§13 G3). Halt blocks **new entries only**; an open
  trade always runs to its own exit. Auto-resets next session (the entry-date filter
  rolls over — no persisted flag). **Precedence:** the existing **hard pause** is
  evaluated first and short-circuits; the soft halt only gates entries when not
  hard-paused (§13 G4). The cap uses the **net** realized figure on both sides;
  ₹ caps require costs on (§6), so gross/net can't be mismatched.

### 5.6 Optimizer + survival integration — `optimizer.py`
- **Commit 1 (enforce + evaluate):** `exit_controls` + `daily_caps` are forwarded into
  the finalist option sim via the new kwargs (§4) at **both** call sites —
  `_survival_eval_oos` and `_option_rerank` — so `survival_verdict` scores the
  overlay-applied ₹ curve. A test asserts the kwarg **actually reaches** `_walk_option_exit`
  (the enabled ₹ curve differs from disabled on a crafted series — §13 F1). Optuna still
  searches only signal/entry params.
- **Saved best carries the overlay (§13 G5):** the persisted best backtest + the
  "apply as preset" payload must carry the `exit_controls`/`daily_caps` config (and,
  where the saved run is re-simulated, reflect the overlay) so a deployed preset enforces
  live what survival scored. Without this the saved best is a spot re-run that silently
  drops the overlay.
- **Per-fold governor (§13 G6):** `_survival_eval_oos` runs the sim per OOS fold and
  stitches; the entry-session ledger therefore re-arms per fold. This matches the
  existing per-fold floor/DD isolation and the walk-forward discarded-train gaps; the
  **live** governor runs one continuous per-session ledger on the real trade stream, and
  parity is asserted on the pure `daily_governor_decision`, **not** on stitched-vs-live
  session continuity. Documented as an accepted approximation; a unit test asserts a
  session whose entries map to one IST date is governed under one ledger within a call.
- **Commit 2 (search):** behind `search_exit_controls=true`, sweep a grid of exit/cap
  configs for each *surviving* finalist; rank by the existing objective under the
  survival gate. **Bounded contract (§13 G7):** a hard `|grid|` ceiling + a total
  K×folds×|grid| budget with seeded sub-sampling (mirroring `_grid_combinations`,
  optimizer.py:236-244); enumerated in fixed order (no new RNG); reuses the Piece-1
  shared option-data load + survival fail-fast. Cannot resurrect a survival-disqualified
  finalist (runs only on survivors).

### 5.7 Proactive high-value additions (in-scope)
- **Control attribution** (metrics block): counts of trail-stop vs breakeven vs
  base-stop vs target exits; trades skipped per daily cap; an estimated ₹ impact of the
  overlay vs the same finalist without it.
- **Journal/parity surfacing:** the new exit reasons + daily-halt events flow to the
  Signal Journal, auditable like the time-stop is today.

## 6. Config + validation

```jsonc
risk / option_config / OptionBacktestReq: {       // homed on ALL THREE request paths (§13 V1)
  exit_controls: {
    enabled: false,
    unit: "pct",                 // "pct" (of entry premium) | "pts"
    breakeven: { trigger: 0.30, lock: 0.0 },       // raise stop to entry×(1+lock) once up trigger
    trailing:  { activation: 0.40, distance: 0.25 } // trail running_max×(1−distance) once up activation
  },
  daily_caps: {                  // all optional; omit a field to disable that cap
    mode: "soft",                // soft daily halt + auto-resume (only mode in this piece)
    loss: 15000,                 // ₹ realized loss (positive; halt at session cum-realized ≤ −loss)
    target: 25000,               // ₹ realized profit (halt at session cum-realized ≥ target)
    max_trades: 6                // entries per IST session (OPEN+CLOSED entered today)
  }
}
```

**Config home (§13 V1):** the overlay is read by three request paths — the backtest run
(`OptionBacktestReq`), the optimizer (`option_config`), and the deployment
(`DeploymentCreateReq.risk`). Each gets the block; the **same pure validator**
(`validate_exit_risk_config`) is called from each corpus-visible router.

**Validation (router, in the contract corpus):**
- Any ₹ cap (`loss`/`target`) requires costs on — but the **flag differs per path**
  (§13 V2): `costs_enabled` on the backtest/optimizer path, `friction`/`costs` on the
  deployment path. The validator takes a resolved `costs_on` so each router passes its
  own flag → **400** otherwise.
- `exit_controls.enabled` requires option execution (per-path predicate — `option_levels`
  / option-rerank for sim, option policy for deploy; §13 V3) → **400** spot-only.
- Range guards (in the pure validator, with explicit unit branching so they can't go
  inert — §13 V4): `0 < distance < 1` (pct) / `> 0` (pts); `0 ≤ lock < trigger`;
  `activation`/`trigger` > 0; positive `loss`/`target`; `max_trades ≥ 1`. → **400**.
- **Pinning (§13 V5):** request-side shapes go in `schemas.py` (corpus-visible). The
  **response-side** attribution/exit-reason keys are built in corpus-invisible
  `option_backtest.py`, so they are pinned via **named constants** exported from a
  corpus-visible module (e.g. `schemas.py`) and asserted there; their **values** are
  behavior-tested.

## 7. Parity, determinism & backward compatibility
- **Decider parity (revised — §13 F5):** `effective_premium_stop`,
  `daily_governor_decision`, and `validate_exit_risk_config` are **the** shared pure
  deciders; `test_execution_policy.py` goldens assert they return the **identical**
  result for identical inputs. The sim (bar-walk, fills at the level / open-clamp) and
  live (tick-walk, fills at the breaching tick) feed those deciders the same way but
  **cannot be byte-identical end-to-end** — bar vs tick is a granularity difference that
  the **existing** fixed-stop already has (sim books `OPTION_STOP` at the level, live
  books the tick). Trailing **amplifies** it; §5.2a's open-clamp makes the sim no more
  optimistic than live. The spec claims decider parity, not fill parity.
- Deterministic given inputs; the Optuna search stays stochastic exactly as today.
- `exit_controls.enabled=false` **and** unset `daily_caps` ⇒ **byte-identical**: the new
  kwargs default None, the walk branch + skip path are gated, the ratchet collapses to
  the fixed base stop, and no legacy request/route behavior changes.

## 8. Error handling & safety
- Overlay on + option/costs off / bad ranges → **400** via the pure validator.
- Live writes idempotent + status-conditional; a concurrent manual close always wins.
- No fill on stale premium (existing >120s guard); the trail does not advance on stale.
- Daily halt blocks **entries only**; an open trade always runs to its own exit.
- **No real broker orders.** Paper only — permanent.

## 9. Testing (host-safe, existing patterns)
- **`exit_controls.py` units:** the **look-ahead regression** (one bar cannot
  peak-and-stop); monotonic ratchet; breakeven step; trail giveback; `unit:"pts"` parity
  with `"pct"`; gap-fill open-clamp on a gap-down bar; governor sticky-extremum trips +
  auto-reset across `session_date`; `max_trades` counts OPEN+CLOSED entries; empty/1-trade
  inputs fail safe.
- **Parity goldens** (`test_execution_policy.py`): identical **decider** result across
  sim-bar and live-tick inputs for `effective_premium_stop` + `daily_governor_decision`.
- **Conduit test (§13 F1):** with `exit_controls.enabled=true`, the survival-scored ₹
  curve differs from disabled on a crafted premium series — proving the kwarg reaches
  `_walk_option_exit`.
- **Skip-invariant test (§13 G1/G2):** inject a `SKIPPED_DAILY_CAP` row → metrics,
  `build_rupee_equity_curve`, `build_option_equity_curve`, context_breakdown, and survival
  `trade_pnls` unchanged; coverage `skipped_by_cap` excludes it from the ratio; the
  research.py response does not leak it.
- **Contract corpus:** `exit_controls`/`daily_caps` request fields + attribution-key
  constants + the router 400s (per-path cost flag + option-exec + ranges).
- **Backward compat:** existing optimizer/portfolio/option-backtest tests stay green.

## 10. UI surface
- **Deploy wizard:** an Exit/Risk panel (breakeven, trailing, daily caps) mirroring the
  400 guard (option exec + costs required for ₹ caps).
- **Optimizer (Commit 2):** exit-search toggle + bounds; per-finalist chosen config
  beside the survivability badges.
- **Backtest results:** the new exit-reason mix + the control-attribution block.

## 11. Phasing & verification
- **Commit 1** = §5.1–5.5 + §5.6 Commit-1 + §5.7 + tests + UI panel.
- **Commit 2** = §5.6 Commit-2 (bounded finalist-grid search) + optimizer UI.
- Per commit: `pytest -q` green, `npm run build` clean, `docker compose up -d --build`,
  running-stack smoke — force a trail-stop and a daily-loss halt; confirm the live stop
  ratchets up, the halt blocks new entries (and `max_trades` sees in-flight entries),
  and it auto-resumes next session.

## 12. (Resolved) gap-fill realism
Folded into **§5.2a** and **§13 D1–D2**: the open-clamp on the overlay path; the false
"friction absorbs" claim removed; decider-not-fill parity stated in §7.

## 13. Audit findings → resolutions

5-lens adversarial audit, 40 raw findings (the skeptic-refutation phase was degraded by
API rate-limiting, so findings were triaged on the merits with code context). Deduped:

| # | Finding (severity) | Resolution |
|---|---|---|
| **F1** | Overlay had **no signature-legal path** into `simulate_paired_option_trades` — §4 froze it, so survival would score the un-overlaid curve (BLOCKER, verified) | One additive `default-None` `exit_controls`/`daily_caps` kwarg (§4); forwarded from both finalist call sites; conduit test (§9, §5.6) |
| **F2** | Governor session axis: sim buckets by `entry_ts`, live `daily_realized_summary` by `closed_at` → halt on different days (BLOCKER, 4×) | Attribute count **and** realized ₹ to the **ENTRY** session both sides (§3, §5.4, §5.5); new entry-keyed live helper, not the closed-at one |
| **F3** | `max_trades` unenforceable from a closed-only live recompute (BLOCKER) | Live count = OPEN **+** CLOSED entered today (§5.5) |
| **F4** | §5.5 live-ratchet ordering contradicted §5.2 (peak-tick self-stop) (BLOCKER) | Live computes eff-stop from **prior** `running_max`, checks, then advances — same ordering as the sim (§5.2, §5.5) |
| **F5** | Parity **overclaimed**: sim books the level, live books the tick — not byte-identical (BLOCKER on framing) | Narrow to **decider parity** (§7); document bar-vs-tick as pre-existing + amplified |
| **D1** | §12 gap-through optimism real + amplified by trailing; `min(level, high)` remedy is a near no-op (needs the **open**) (HIGH) | Overlay walk reads bar **open**, fills long stop at `min(level, open)` (§5.2a); legacy walk unchanged |
| **D2** | §12 "friction absorbs the gap" is **backwards** (HIGH) | Claim removed; live tick-fill is the honest reference, open-clamp brings sim toward it (§5.2a, §7) |
| **G1** | `SKIPPED_DAILY_CAP` skips lower paired/spot coverage → can trip survival `MIN_COVERAGE` hard-fail, suppressing the survivors caps create (MEDIUM) | Exclude governor-skips from the coverage denominator via `coverage["skipped_by_cap"]` (§5.4) |
| **G2** | `SKIPPED` rows live in the returned `trades` list → could leak to non-PAIRED-filtering consumers (e.g. research.py API) (MEDIUM) | Pin the "all consumers gate on PAIRED" invariant; filter/segregate SKIPPED at the research.py boundary (§5.4, §9) |
| **G3** | Stateless soft-halt could **un-halt within a session** when cumulative realized re-crosses the threshold (MEDIUM) | Trip on the session **cumulative extremum** (running min/max) → sticky within session, auto-reset next (§4, §5.5) |
| **G4** | Soft-halt vs hard kill-switch precedence unspecified (MEDIUM) | Hard pause evaluated first + short-circuits; soft halt only gates entries when not paused (§5.5) |
| **G5** | Saved "best" is a spot re-run → silently drops the overlay the gate scored (HIGH) | Persist overlay config into the saved best + preset/deployment; re-simulate-or-carry so live enforces it (§5.6) |
| **G6** | Per-fold sim re-arms the governor ledger; a fold-straddling session governed twice (HIGH→accepted approx) | Matches existing per-fold floor/DD isolation; live runs one continuous ledger; parity at the decider; documented + unit test (§5.6) |
| **G7** | Commit-2 grid cost `K×folds×|grid|` unbounded; "documented" ≠ enforced (MEDIUM) | Hard `|grid|` ceiling + budget with seeded sub-sampling (mirror `_grid_combinations`); fixed order, no new RNG (§5.6) |
| **G8** | Net-of-charges (sim) vs possibly-gross live realized (MEDIUM) | Governor uses **net** realized both sides; ₹ caps require costs on (§5.5, §6) |
| **V1** | `exit_controls`/`daily_caps` had no home on the backtest/optimizer request schemas (only deployment `risk`) (HIGH) | Homed on all three request models; shared validator (§4, §6) |
| **V2** | ₹-cap cost guard keys on a flag that **differs per path** (HIGH) | Validator takes resolved `costs_on`; each router passes its own flag (§6) |
| **V3** | "requires option execution" gate defined only by analogy (MEDIUM) | Per-path predicate stated (sim vs deploy) (§6) |
| **V4** | Range guards (`0≤lock<trigger`, pts/pct distance) risk going inert in the router (LOW) | Centralized in the pure validator with explicit unit branching (§4, §6) |
| **V5** | Attribution/exit-reason shape **not pinnable** in `schemas.py` (response object in corpus-invisible `option_backtest.py`) (HIGH) | Pin key **names** as constants in a corpus-visible module; behavior-test values (§6, §9) |
| **L** | Minor: entry-bar high excluded vs MFE window inclusive; stop-vs-target on one gap-up bar; trail lag on stale/tickless marks; single-write ratchet; ledger-order vs exit-ts resort (covered by single-position) | Noted in §5.2/§5.5; single-position guarantees entry-order == exit-order within a session, so the re-sort does not reorder |

## 14. Out of scope (this piece) / future
- **Spot-mirror trailing/breakeven** (trail on the underlying) — reuses spot_exit
  plumbing; deferred (user chose premium-only).
- **Capital-aware trials** (Piece-1 Approach B).
- **Partial / scale-out exits** — engine is single-position, full-size.
- **Cross-deployment / overlapping-position daily caps** — the sim models one position;
  the live governor is per-deployment on real realized; cross-deployment overlap is an
  accepted out-of-model approximation (§13 G6, L).
- **Hard-pause daily caps** — the existing kill-switch covers; this piece adds the soft,
  auto-resuming, sticky layer only.
- **Legacy fixed-stop gap-fill** — left byte-identical; only the overlay path gets the
  open-clamp. A unified gap-fill hardening is a separate change.
- No real broker orders — permanent. All simulation.
