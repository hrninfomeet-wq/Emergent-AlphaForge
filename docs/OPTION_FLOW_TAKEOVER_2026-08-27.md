# Takeover — register item #10: option flow into `evaluate()` (2026-08-27)

**Paste §0 into a fresh Claude session opened on this repo folder.** Everything
below it is the state that session needs.

Companion to [`LOCAL_TAKEOVER_2026-08-23.md`](LOCAL_TAKEOVER_2026-08-23.md)
(the campaign takeover that preceded this one) and to the register in
[`AGENT_TODO.md`](AGENT_TODO.md), which is the authoritative status board.

---

## 0. The prompt to paste

> You are picking up a single, well-scoped build on **AlphaForge Trading Lab**:
> **register item #10 — get option-side flow (CE/PE volume and open interest)
> into `evaluate()`**. It is the last open item of fifteen and the only large one
> still justified by evidence.
>
> **Read first, in this order:**
> 1. `docs/OPTION_FLOW_TAKEOVER_2026-08-27.md` — this file: what to build, the two
>    constraints that shape it, and the traps that will each cost you a day
> 2. `docs/AGENT_TODO.md` ★ register — the authoritative status of all 15 items
> 3. `docs/INTRADAY_OPTION_BUYING_CANDIDATES_2026-08.md` §7.1 (the proposed shape),
>    §11.1 (the join rule), §12.3 (why the baseline cannot live in the strategy)
> 4. `docs/HANDOFF.md` §2 and `docs/BACKTEST_INTEGRITY_AUDIT.md` — before trusting
>    any number the app produces
>
> **Branch: `feat/chain-recorder`, 8 commits ahead of `origin/main`.** Check
> whether they are pushed (`git log --oneline origin/main..HEAD`); as of writing
> the push was pending. Do not push to `main`. Branch from where you are.
>
> **Two constraints are non-negotiable and they decide the whole design:**
> - The live window is hard-capped at **1,000 bars** (~2.7 sessions). A 20-session
>   baseline therefore **cannot** be computed from the strategy's own frame. It
>   must be computed in the **data layer**, where the query window is independent
>   of what the strategy holds. Getting this wrong produces a feature that is
>   right in backtest and silently different live.
> - Option bars join by **identity** (`underlying` + `expiry_date` + `strike` +
>   `side` + `ts`), **never by token**. `option_contracts` stores a 3-part
>   `instrument_key`; `options_1m` stores a 2-part one; `contract_key` is on only
>   ~10% of bars. A token join returns ZERO rows and looks exactly like missing
>   data — it already cost this project one wrong "the warehouse is empty" verdict.
>
> **Standing rules, inherited and non-negotiable:**
> - Never place, modify or cancel a broker order. Never enable live mode. Never
>   call the Flattrade MCP's `login`/`logout`.
> - **Missing market data must DEGRADE the app, never disable or mislead it.**
>   Missing is `None`, never `0` — they are different facts. A gap must be
>   visible, never silently absorbed into an aggregate.
> - A docstring is not a test, and a test that greps source is not a test either.
>   **Mutate every guard you ship**; expect survivors and fix them.
> - Report failures with their output. Run the full regression pass (below)
>   before claiming anything is done.

---

## 1. Where the work is

| | |
|---|---|
| Branch | `feat/chain-recorder`, upstream `origin/feat/chain-recorder` |
| Commits | 8 ahead of `origin/main` (`f7fc1f6` … `7de63c7`) |
| Push state | **Pending as of 2026-08-27** — a push was blocked by a permission classifier and left to the operator. Verify before assuming. |
| Suite | **5,287 passed, 0 failed** (~2 min). Anything else is a regression you introduced. |
| Register | 12 of 15 items closed. #10 is yours. #12 (multi-leg engine) is **NOT JUSTIFIED** — do not build it. |

The eight commits, newest first:

```
7de63c7 fix(audit): deleting an optimizer job no longer orphans the run it produced
4e495e8 fix(live): the rolling window now says when it cannot honour a strategy
c010729 feat(smc): bound the carry-forward so FVG, order blocks and CHoCH can deploy
fdb83a0 fix(ui): the Backtest Lab still SHOWED and POSTED the old 15:00 window
5a1eede fix(window): one entry window for live, backtest, optimizer and the screen
be6a8ec research(screen): the short-side thesis is CLOSED — the wing eats the edge
1237c89 feat(screen): measure the SHORT side — first CANDIDATE in four campaigns
f7fc1f6 feat(data): record option-chain history — the one dataset that cannot be backfilled
```

---

## 2. What #10 is, and why it is worth building

`options_1m` carries `volume` and `oi` on every bar. **No strategy can read
either**, because `build_eval_ctx` hands `evaluate()` a spot frame and nothing
else. That blocks Candidate A (ATM Premium-Flow Scalp), which is the only
genuinely untried *information channel* left: every one of the 18 registered
strategies is underlying-led, and all 25 indicators read spot OHLCV.

**The premise is already confirmed, so this is justified work rather than a
gamble.** OI is populated on **99.55–99.61% (NIFTY)** and **99.86% (SENSEX)** of
sampled option bars — measured, recorded in §10 of the deliverable.

Be clear-eyed about what it does *not* mean: four campaigns have failed, the
unconditioned ATM baseline is NO_EDGE on both indices, and the short side is
closed (§14). #10 buys the ability to **test** a hypothesis nothing has tested,
not an edge.

---

## 3. The seam already exists — use it, do not invent one

`StrategyBase.required_data` → `warehouse.attach_required_data` →
`data_columns.attach_data_columns`. It already gives you:

- **causal as-of joins** — every value joined at-or-before the bar's own `ts`,
  bounded by `max_staleness_ms`; the only join the module implements
- **coverage reporting** — per-column `{bars, present, coverage_pct,
  sessions_missing}`, and it degrades LOUDLY when a column is partially covered
- **honest absence** — NaN where no print reaches the bar, never a filled default
- **opt-in** — no declaration means the module never runs and the frame is
  byte-identical, so no existing strategy changes behaviour

And critically, the AI authoring layer (`ai/capability.py`, `ai/compiler.py`,
`ai/grounding.py`) validates `required_data` names against
`DATA_COLUMN_REGISTRY`. **Register your columns there and the whole authoring
stack works unchanged** — no new declaration surface, no parallel plumbing.

`DATA_COLUMN_REGISTRY` currently holds exactly one entry (`vix`).

---

## 4. What has to be built

### 4.1 The fetch is hardcoded to `candles_1m`

`warehouse.attach_required_data` does
`db.candles_1m.find({"instrument": spec.instrument, ...})`. Option flow needs
`options_1m`, resolved to the ATM CE and PE legs of the nearest upcoming expiry
**per session**. That is a second source kind, not a registry entry.

Keep `app.data_columns` pure (no motor, no I/O) — that is what makes it
host-importable and testable. The I/O belongs in `app.warehouse`.

### 4.2 The 20-session baseline must be computed in the data layer

§4.1 of the deliverable specifies `flow_imbalance` from z-scores against a
**causal 20-session rolling distribution for the same time-of-day bucket**, plus
a liquidity floor of a 20-session causal median.

`deployment_evaluator` clamps the live window:

```python
live_lookback = max(LIVE_LOOKBACK_FLOOR, want)   # refused above LIVE_LOOKBACK_MAX = 1000
```

**1,000 bars is under three sessions.** A `session_precompute` deriving a
20-session baseline would see full history in backtest and under three sessions
live, computing a *different number* in each path while both look healthy. That
is the `live-window-anchors-session-indicators` failure — a session-VWAP anchor
error of 2.12 ATR silently inverted nine shipped strategies — and it is
invisible to a backtest by construction.

So the z-scores are computed **by the fetch**, whose query window is independent
of the strategy's frame, and delivered per-bar as ready-made columns. The
strategy reads a number; it does not derive one.

### 4.3 Suggested columns

From §7.1: `ce_volume`, `pe_volume`, `ce_oi`, `pe_oi`, `ce_oi_delta`,
`pe_oi_delta`, plus causal time-of-day z-scores of each. Whether
`flow_imbalance` itself is a column or is composed in the strategy is your call —
but **store raw, derive late**: a derived value frozen into a column freezes
today's definition.

### 4.4 Then, and only then, the plugin

Candidate A's full specification is §4.1 of the deliverable, including its
pre-registered kill thresholds and a frozen 6-dimension parameter budget (324
combinations). **Screen before you write the plugin** — that ordering is why the
short-side campaign cost one day and built no engine.

---

## 5. Traps that will each cost you a day

1. **Token joins return zero rows and look like missing data.** Join by identity.
   §11.1 has the measured evidence: 3-part vs 2-part `instrument_key`,
   `contract_key` on 823,829 of 7,967,661 bars.
2. **The 1,000-bar cap** (§4.2 above).
3. **`bool(NaN) is True`** — this keyed every legacy candle `"nan"` and made every
   paired-option backtest before 2026-07-30 wrong. Be careful with NaN in keys
   and conditionals.
4. **`backend/app/` is NOT bind-mounted.** Only `backend/app/strategies/plugins`
   is. Any change outside that needs `docker compose up -d --build backend` or the
   container keeps running the old code.
5. **The frontend is nginx serving a production build.** `restart` does nothing;
   it needs `docker compose up -d --build frontend`. Verify UI changes in the
   running app — grepping JSX has already produced a false pass on this branch.
6. **PowerShell 5.1**: `&&` is not a statement separator. Use `;` or separate lines.
7. **Mongo on the host is `127.0.0.1:27017`**, never `localhost` (IPv6 stall).
8. Off-hours the option-chain endpoint answers with the PREVIOUS session's book.
   `chain_recorder.capture_once` gates on market hours for exactly this reason.

---

## 6. The verification bar

Unit tests alone are not enough — that was established the hard way on this
branch. Before claiming #10 is done:

- [ ] Full suite green (`5,287 passed` is the current floor; ~2 min)
- [ ] **Mutation-sweep every guard you ship.** Expect survivors. On this branch
      the first sweep had survivors 4 times out of 6, and every one was a test
      that was true but not *discriminating*.
- [ ] `docker compose up -d --build backend`, then: boot clean, 0 tracebacks,
      **173 routes** still registered, API sweep 200s
- [ ] **Backtest determinism** — replay saved runs against their STORED configs
      and confirm identical `trade_count / win_rate / profit_factor /
      total_pnl_pts / max_dd_pts`. This is the single strongest regression check
      available; a probe exists in the 2026-08-27 session transcript.
- [ ] Confirm the deployed image really carries your code (import it in-container
      and print a value) — a rebuild that silently no-ops has happened here
- [ ] Live loops healthy: deployment evaluator and chain recorder both running

---

## 7. Safety — unchanged and inherited

No order placed, modified or cancelled. No live mode enabled. No deployment,
preset or broker session created or altered. No Flattrade MCP `login`/`logout`.
The chain recorder is read-only against Upstox and writes only `chain_snapshots`.

Any future live deployment requires separate explicit user authorisation and must
pass the app's existing live-safety, data-completeness and risk-authorisation
gates. Nothing in this work constitutes that authorisation.
