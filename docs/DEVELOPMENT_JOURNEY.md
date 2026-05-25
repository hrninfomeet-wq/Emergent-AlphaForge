# Development Journey — Phases, Decisions, Lessons

> A log of how AlphaForge Trading Lab was built. Read this to understand WHY things are the way they are.

## Pre-Phase: Interview & Plan Lock

The user came with:
- Existing Node.js + SQLite trading app for NIFTY/BANKNIFTY/SENSEX intraday option scalping (working locally on Windows)
- Wanted to rebuild it on Emergent (React/FastAPI/MongoDB) — bigger, modular, better optimizer, more strategies, live signals, options backtest, local PC deployable
- Self-described as a "vibe coder" — asked us to challenge his ideas pragmatically

Key design decisions made up-front (after 5 rounds of clarification):

1. **Option BUYING win rate is realistically capped at 55-65% with discipline**, not 80%+. Top 1% is achievable but requires the system + psychology + capital management together.
2. **Time-bound probability signals** (Kaplan-Meier survival analysis) is the differentiator vs retail apps. **Deferred to Phase 5** because it needs 6+ months of warehouse history first.
3. **Custom strategy plugins** = drop-in Python files, auto-discovered. Same backtest/optimize/live pipeline as built-ins.
4. **3 trading modes**: SCALP (1m-5m) + INTRADAY (5m-30m) baked into core from Phase 1; SWING (1H-1D+) as Phase 6.
5. **3 optimizers**: Bayesian (TPE) + Grid + Genetic (CMA-ES) — all with walk-forward by default.
6. **Pre-trade checklist must be fully configurable** with 3 profile presets (Conservative/Balanced/Aggressive) + anti-over-filter safeguard.
7. **Signal lifecycle**: every signal logged with full state snapshot; click-to-load past runs; full audit trail.
8. **Final delivery**: Docker Compose for local PC use (Phase 7).

---

## Phase 1 — POC (Single-File Validation)

**Goal**: prove the core data → indicators → strategy → backtest → walk-forward → significance loop works end-to-end on REAL NIFTY data before building any UI.

**Approach**: one file (`backend/test_core.py`) that does the entire pipeline.

**Tools used**: yfinance, pandas, numpy, motor (MongoDB).

**Key bug encountered**: yfinance returns `datetime64[s]` (second precision). Doing `astype('int64') // 10**6` on second-precision gives garbage (all docs got the same `ts` value).  
**Fix**: explicit cast to nanosecond precision first: `astype('datetime64[ns, UTC]')` then divide by `10**6`.

**Another fix**: cost model was wildly unrealistic for spot mode (each trade losing 200+ NIFTY points). Replaced with `spot_round_trip_pts: 1.5` (industry standard).

**Lesson**: **always test with real data BEFORE building the UI**. Saved ~2 hours of debugging that would have been UI-flavored.

---

## Phase 2 — V1 Full Lab

**Goal**: 6 built-in strategies, custom plugin support, full UI.

**Sub-agents used**:
- `design_agent` (10 min) for design guidelines — produced a 564-line spec with color tokens, typography, component patterns. Worth every second.
- `testing_agent_v3` for end-to-end validation (97.5% pass).

**Approach**: parallel bulk_file_writer batches.
- Batch 1 (backend foundation, 10 files): db, models, costs, indicators, regime.
- Batch 2 (strategies, 7 files): base + 6 built-ins.
- Batch 3 (backend engines, 4 files): backtest, walkforward, yfinance, warehouse.
- Batch 4 (frontend foundation, 11 files): index.css, App.js, Layout, badges, MetricCard, charts.
- Batch 5 (frontend pages, 7 files): all pages.

**Major bug**: TradingView Lightweight Charts crashed with `Invalid language tag: en-US@posix`. The Emergent container runs with `LANG=POSIX`, so `Date.toLocaleString()` fails.  
**Fix 1**: Explicit `localization: {locale: "en-US", timeFormatter, dateFormat}` in chart config.  
**Fix 2**: Rewrote `lib/fmt.js` to avoid `toLocaleString()` entirely. Use UTC offset arithmetic for IST.

**Lesson**: **NEVER use `toLocaleString()` in this container**. The fmt.js helpers exist for a reason.

**Lesson 2**: shadcn `bg-info` doesn't auto-generate from a CSS variable. You must add `.bg-info { background-color: var(--color-info); }` to the `@layer utilities` block. This caused the Phase 3 progress bar to be invisible — caught only when the user complained.

---

## Phase 3 — Auto-Optimizer

**Goal**: One-click Bayesian + Grid + Genetic optimization with walk-forward, importance, heatmap, robustness, top-N alternatives.

**Approach**:
- Async background task (FastAPI `asyncio.create_task`) — frontend polls every 2 seconds.
- Pre-compute indicators ONCE per optimization → 100× speedup.
- Optuna for Bayesian (TPE) + Genetic (CMA-ES via `optuna.samplers.CmaEsSampler`).
- Grid search with sampling cap to keep things tractable.
- Robustness = perturb each numeric param ±10/20% and count how many stay within 85% of best objective.
- Heatmap = 8×8 grid over top-2 important params.
- Auto-save best params as a full `backtest_run` with walk-forward — linked to the optimization job.

**Key learning**: A single Bayesian run with 150 trials on 1872 NIFTY candles finishes in ~20 seconds. Most of that time is in `run_backtest` per trial. The indicator pre-compute means we avoid recomputing them 150 times.

**User feedback after Phase 3** (3 issues, all fixed in Phase 3.5):
1. Progress bar invisible during run → `bg-info` CSS utility missing. Added.
2. Saved presets had no UI to use them → added "Load preset" dropdown in BacktestLab + "Saved Presets" panel in Optimizer + click-to-load deep-link `/backtest?preset=<name>`.
3. No Stop/Pause button → added `POST /api/optimize/jobs/{id}/cancel` + UI button. Worker checks the flag every 5 trials.
4. Couldn't view the 15 trades from optimization → optimizer now auto-saves the best params as a full `backtest_run` (with trades + equity + walk-forward). "View Best in Lab" button navigates there.
5. No exports from optimizer → added 3 buttons (Config JSON, Result JSON, Alts CSV).

**Lesson**: **the testing agent catches function but not always UX**. The progress bar passing tests technically but being invisible to the user is a perfect example. Always do a human visual review.

---

## Tips, Tricks, Sanity Checks

### Token Efficiency

- **Use `mcp_bulk_file_writer`** for >3 files. It's 10× faster than individual writes.
- **Use parallel tool calls** for independent ops (linting + log checks + bash commands).
- **Never call sub-agents in parallel** (testing_agent_v3, troubleshoot_agent, etc.).
- **Don't `cat`/`head`/`tail` files** — use `mcp_view_file` with `view_range`.
- **Don't view a file you just edited** — trust the edit.

### Backend Sanity

- `pip freeze | grep -i <pkg>` to verify a package was actually installed.
- `tail -n 50 /var/log/supervisor/backend.err.log` after every restart.
- The startup log should show `Discovered N strategy plugins` and `Pre-trade profiles seeded`.
- `curl -s http://localhost:8001/api/health` should return `{"db": "ok"}`.

### Frontend Sanity

- `tail -n 50 /var/log/supervisor/frontend.err.log` — look for "Compiled successfully".
- The first compile after a bulk write often reports "Module not found" for files that don't yet exist; the second compile (after all files are written) succeeds.
- `mcp_lint_javascript /app/frontend/src` should show "✅ No issues found".
- **Screenshot test every UI change** — `mcp_screenshot_tool`.

### MongoDB Sanity

```python
# Quick connection test
from motor.motor_asyncio import AsyncIOMotorClient
import asyncio
async def main():
    c = AsyncIOMotorClient("mongodb://localhost:27017")
    db = c["test_database"]  # or whatever DB_NAME is
    print(await c.admin.command("ping"))
    print("Collections:", await db.list_collection_names())
asyncio.run(main())
```

### Strategy Plugin Sanity

If a plugin doesn't show up:
1. Backend log: `Failed to import strategy <name>` or `Failed to instantiate <ClassName>`.
2. Check `StrategyRegistry._errors` via `GET /api/strategies` (failed plugins appear with `is_loaded: false` + `error` field).
3. Class must inherit from `StrategyBase` and have a non-empty `id`.
4. `evaluate()` must return a `Signal` (not raise an exception).

### Common Bug Patterns

1. **Bar-by-bar logic referencing the future**: be careful with `df.iloc[i+1]` — only `df.iloc[i]` (current) and `df.iloc[i-1]` (prev) are available at signal time.
2. **VWAP without volume**: indices have zero volume in yfinance → use `expanding().mean()` of typical price as fallback. Already done in `app/indicators.py:session_vwap`.
3. **Cooldown bug**: `i - last_signal_bar < cooldown` MUST be `last_signal_bar = i` ONLY when a signal fires, not when one is rejected.
4. **TP/STOP intrabar ordering**: we conservatively check STOP first (worst-case fill). Don't reverse without good reason.
5. **Datetime serialization**: always use `serialize_doc()` before returning Mongo docs.

## What I'd Do Differently Next Time

1. **TypeScript for the frontend** — would catch chart prop bugs at compile time.
2. **Pydantic strict models for the strategy plugin contract** — currently it's class attributes; a stronger contract would prevent some plugin authoring mistakes.
3. **WebSocket-based progress updates** instead of polling for optimizer — would feel snappier (Phase 4 will need WS anyway, can reuse).
4. **Charting**: TradingView Lightweight is excellent but its v5 panes API still has rough edges. Consider lightweight-charts plus a custom marker layer for trade entries/exits.
5. **Schema versioning** for MongoDB collections with explicit migrations (currently relying on backwards-compatible additions).

## Critical Reminders

- **NEVER claim something works without screen-checking it.** Progress bar = great example.
- **NEVER mock data** unless explicitly approved. User wants real signals on real data.
- **NEVER skip the POC step** for hard features. The yfinance datetime bug would have ruined a finished UI build.
- **ALWAYS surface honest stats** — Wilson CI, OOS divergence warnings, robustness scoring. The user explicitly asked for this; don't hide bad results.
