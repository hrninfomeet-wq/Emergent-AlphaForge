# Governed + Fast Analyzing Stage — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make the optimizer "Analyzing" stage (a) never silently exceed a configurable wall-clock budget (default 30 min) with always-on ETA, and (b) much faster via a vectorized option-exit walk and an opt-in deterministic parallel sweep — all **byte-identical** to today's results.

**Architecture:** Three independently-shippable, parity-gated phases. Spec: `docs/superpowers/specs/2026-06-20-analyzing-governed-fast-design.md`. Branch: `feat/analyzing-governed-fast`.

**The non-negotiable invariant:** the option-rerank + survival is the deployability gate. Every perf change MUST pass a host test proving byte-identical output vs the current sequential code; governance is inert unless the budget is hit. Run host tests from repo root: `python -m pytest tests/...`. Host tests MUST NOT import `server`/`optimizer`/`runtime`/`paper_auto`. Commit only named files via pathspec; never commit `CHANGELOG.md`/`docs/HANDOFF.md`. No push/merge without explicit user instruction.

---

## PHASE 1 — Governance (budget + ETA + heartbeat); ships first

### Task 1: pure budget/ETA helpers (host-TDD)
**Files:** Create `backend/app/analyze_budget.py`; Test: `tests/test_analyze_budget.py`.

- [ ] **Step 1 — failing tests:**
```python
import sys; from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.analyze_budget import over_budget, eta_seconds, ewma

def test_over_budget_false_when_unlimited():
    assert over_budget(elapsed=10_000.0, budget_sec=0) is False
def test_over_budget_false_under():
    assert over_budget(elapsed=100.0, budget_sec=1800) is False
def test_over_budget_true_at_or_over():
    assert over_budget(elapsed=1800.0, budget_sec=1800) is True
    assert over_budget(elapsed=2000.0, budget_sec=1800) is True
def test_ewma_first_sample_is_value():
    assert ewma(prev=None, sample=4.0, alpha=0.3) == 4.0
def test_ewma_blends():
    assert abs(ewma(prev=2.0, sample=4.0, alpha=0.5) - 3.0) < 1e-9
def test_eta_seconds_remaining_times_per_item():
    assert eta_seconds(done=10, total=150, per_item_sec=4.0) == (150-10)*4.0
def test_eta_zero_when_done():
    assert eta_seconds(done=150, total=150, per_item_sec=4.0) == 0.0
def test_eta_none_when_no_estimate():
    assert eta_seconds(done=0, total=150, per_item_sec=None) is None
```
- [ ] **Step 2 — run, expect FAIL.** `python -m pytest tests/test_analyze_budget.py -v`.
- [ ] **Step 3 — implement** `backend/app/analyze_budget.py`:
```python
"""Pure helpers governing the optimizer Analyzing stage: a wall-clock budget and
a self-calibrating ETA. No I/O — the caller supplies monotonic elapsed + counters."""
from __future__ import annotations
from typing import Optional


def over_budget(*, elapsed: float, budget_sec: int) -> bool:
    """True once elapsed >= budget. budget_sec <= 0 means unlimited (always False)."""
    return budget_sec > 0 and elapsed >= float(budget_sec)


def ewma(prev: Optional[float], sample: float, alpha: float = 0.3) -> float:
    """Exponential moving average of per-item wall-times. First sample seeds it."""
    s = float(sample)
    return s if prev is None else (alpha * s + (1.0 - alpha) * float(prev))


def eta_seconds(*, done: int, total: int, per_item_sec: Optional[float]) -> Optional[float]:
    """Remaining seconds = (total-done)*per_item. None until we have a per-item estimate."""
    if per_item_sec is None:
        return None
    return max(0, int(total) - int(done)) * float(per_item_sec)
```
- [ ] **Step 4 — run, expect PASS.** Full suite `python -m pytest tests/ -q` green.
- [ ] **Step 5 — commit** `backend/app/analyze_budget.py tests/test_analyze_budget.py` → `feat(optimizer): pure analyze-budget + ETA helpers`.

### Task 2: schema field
**Files:** Modify `backend/app/schemas.py`; Test: extend `tests/test_early_stop.py` (the import-light home for optimizer-schema default checks) OR the existing schema test.
- [ ] Add to `OptimizerStartReq` (after the early_stop fields): `analyze_budget_sec: int = 1800  # Analyzing wall-clock budget (30 min; 0 = unlimited)`.
- [ ] Add a construction test asserting the default `OptimizerStartReq(strategy_id="x").analyze_budget_sec == 1800`.
- [ ] `python -m pytest tests/ -q` green. **Commit** with `feat(optimizer): analyze_budget_sec on OptimizerStartReq`.

### Task 3: wire budget + per-candidate progress/ETA into the analyzing loops (backend)
**Files:** Modify `backend/app/optimizer.py`. Verified on the stack (not host-importable).
- [ ] **Step 1 — read knob + stamp start.** Near the `status="analyzing"` transition (`optimizer.py:1102`): `import time`; `from app.analyze_budget import over_budget, ewma, eta_seconds`; `analyze_budget_sec = int(payload.get("analyze_budget_sec", 1800) or 0)`; `_an_t0 = time.monotonic()`; `_per_item = None`; `analyze_budget_hit = False`.
- [ ] **Step 2 — rerank loop progress + budget.** In `_option_rerank` is a separate fn, so instead instrument at the call site is not possible per-candidate. **Decision:** move the per-candidate budget/progress INTO `_option_rerank` and the survival loop. Pass `budget_deadline` (a monotonic deadline float, or `analyze_budget_sec`+`_an_t0`) + a `progress_cb(done,total,per_item,eta)` async callback into `_option_rerank` and the survival loop. Inside each candidate iteration (`:751` and `:1162`): measure `t=time.monotonic()` before, run the sim, `dt=time.monotonic()-t`, `_per_item = ewma(_per_item, dt)`, `await progress_cb(...)` (throttled to ~1/sec), and `if over_budget(elapsed=time.monotonic()-_an_t0, budget_sec=analyze_budget_sec): analyze_budget_hit=True; break`. (Keep the existing cancel checks first.)
- [ ] **Step 3 — progress payload.** `progress_cb` writes `rerank_progress = {"stage": ..., "done": i, "total": K, "elapsed_sec": ..., "per_item_sec": _per_item, "eta_sec": eta_seconds(done=i,total=K,per_item_sec=_per_item)}`. Add a `analyze_warning` when the best candidate `trade_count > 2000`.
- [ ] **Step 4 — pre-heatmap budget gate.** Before the heatmap/robustness passes (`:1261`), `if over_budget(...): skip them` (they're the cheap-but-not-free tail).
- [ ] **Step 5 — record outcome.** On the finished job doc: `analyze_budget_hit`, `analyzed_candidates: "<done>/<K>"`. Ensure best-pick runs on completed candidates (already does — `ranked`/`survivors` are whatever was filled).
- [ ] **Step 6 — heartbeat log** every ~10 candidates.
- [ ] **Step 7 — verify** `python -m py_compile backend/app/optimizer.py`; full host suite still green. **Commit** `backend/app/optimizer.py` → `feat(optimizer): analyzing budget + live ETA + partial-result governance`.

### Task 4: frontend — budget input + live ETA readout
**Files:** Modify `frontend/src/pages/Optimizer.jsx`.
- [ ] **Step 1 — budget input.** In the option-execution panel, add "Analyzing budget (min)" number input bound to `config.analyze_budget_min` (default 30), and include `analyze_budget_sec: Number(config.analyze_budget_min || 30) * 60` in the start payload. `data-testid="opt-analyze-budget"`.
- [ ] **Step 2 — live ETA readout.** In `CurrentJobView`, when `job.status === "analyzing"` and `job.rerank_progress` has `done/total`, render `Analyzing {done}/{total} · ETA {fmt(eta_sec)}` (and the `analyze_warning` if present) under the progress bar. `data-testid="opt-analyze-eta"`.
- [ ] **Step 3 — budget-hit badge.** When `job.analyze_budget_hit`, show a small amber note "Analyzed {analyzed_candidates} — budget hit; raise the budget or lower K for full coverage."
- [ ] **Step 4 — build** `cd frontend && npm run build` clean (only pre-existing warnings). **Commit** `frontend/src/pages/Optimizer.jsx` → `feat(optimizer-ui): analyzing budget input + live ETA + budget-hit note`.

---

## PHASE 2 — Vectorize `_walk_option_exit` (byte-identical)

### Task 5: parity battery (TDD safety net — write BEFORE touching production)
**Files:** Create `tests/test_walk_option_exit_parity.py`.
- [ ] **Step 1 — copy the CURRENT `_walk_option_exit` verbatim into the test as `_ref(...)`** (a frozen reference; include the `effective_premium_stop`/`stop_fill_price`/`intrabar_exit`/`_breakeven_binding` imports it needs from `app.option_backtest`/`app.exit_engine`/`app.execution_policy`). Build a battery of synthetic candle slices (numpy/pandas) + params covering: target-only hit; stop-only hit; both in one bar (stop-first); gap-down through stop (fills at open); no hit → OPTION_SIGNAL_EXIT; overlay OFF; overlay ON with trailing; overlay ON with breakeven; empty forward window. For each case, assert `app.option_backtest._walk_option_exit(...) == _ref(...)` field-for-field (`exit_ts`, `exit_price`, `exit_reason`).
- [ ] **Step 2 — run, expect PASS** (production currently == ref). `python -m pytest tests/test_walk_option_exit_parity.py -v`. This pins current behavior. **Commit** the test → `test(option): _walk_option_exit parity battery (pins current behaviour)`.

### Task 6: vectorize the walk + candles_by_key reuse
**Files:** Modify `backend/app/option_backtest.py`; the parity test from Task 5 is the gate.
- [ ] **Step 1 — vectorize `_walk_option_exit`.** Replace the `.iterrows()` body: drop the redundant `.sort_values` (slice is pre-sorted), bound the window with `np.searchsorted` on the `ts` array, pull `high/low/open/close` as numpy arrays. Overlay-OFF: first-crossing via vectorized `low<=stop`/`high>=target` (stop-first on same bar). Overlay-ON: `running_max = np.maximum.accumulate(high)` shifted one bar (entry seed), vectorized `eff_stop` per bar via the `effective_premium_stop` formula, then first-crossing + gap-open fill + breakeven/trail reason. **If any overlay case cannot be made identical**, keep the iterrows path ONLY for overlay-ON and vectorize overlay-OFF (still the dominant case) — note it.
- [ ] **Step 2 — `candles_by_key` once.** Add optional `candles_by_key: Optional[Dict[str,pd.DataFrame]] = None` to `simulate_paired_option_trades`; when provided, skip the internal groupby (`:327`). In `_option_rerank` (optimizer.py), build it ONCE after loading `candles_df` and pass it to every per-candidate sim.
- [ ] **Step 3 — parity gate.** `python -m pytest tests/test_walk_option_exit_parity.py -v` → still PASS (new == ref for every case). If red, fix until identical. Full suite green.
- [ ] **Step 4 — `py_compile`** optimizer.py; **commit** `backend/app/option_backtest.py backend/app/optimizer.py` → `perf(option): vectorize _walk_option_exit + reuse candles_by_key (byte-identical)`.

---

## PHASE 3 — Parallelize rerank + survival (opt-in, deterministic)

### Task 7: parallel rerank workers + seq-vs-parallel parity (host-TDD)
**Files:** Modify/extend `backend/app/parallel_eval.py` (host-importable) with rerank worker fns; Test: `tests/test_rerank_parallel_parity.py`.
- [ ] **Step 1 — design the worker.** A top-level picklable fn that, given a candidate's (merged params, resolved per-trade contract keys/expiries) + fork-inherited globals (`_RAW_DF`, a new `_RERANK_CANDLES` global, `_RERANK_CONTRACTS`), re-derives the strategy from the registry, re-enriches from the inherited cache, and runs `simulate_paired_option_trades` → returns the candidate's option metrics. Mirror `_worker_evaluate`'s global-via-fork + registry pattern. NO DB handle in the worker (candles pre-loaded in the parent).
- [ ] **Step 2 — failing parity test.** With a synthetic raw frame + a tiny option-candles set + 2 strategies/params, run the rerank over N candidates (a) sequentially and (b) via a fork pool of 4 workers; assert the per-candidate option-pnl metrics are **identical** (order-normalized by candidate index). (Mirror the existing `tests/test_parallel_eval*.py` fixtures.) Expect FAIL (worker not built).
- [ ] **Step 3 — implement** the worker + a `parallel_rerank(candidates, ...)` dispatcher (fork pool, COW globals, clamp workers to `min(req, cpu-1, AF_ANALYZE_WORKERS)`, sequential fallback when workers<=1 == the exact current path).
- [ ] **Step 4 — run, expect PASS** (seq == parallel). Full suite green. **Commit** `backend/app/parallel_eval.py tests/test_rerank_parallel_parity.py` → `feat(optimizer): deterministic parallel rerank workers (+ seq==parallel parity)`.

### Task 8: wire opt-in parallelism + budget interaction into optimizer
**Files:** Modify `backend/app/schemas.py` (`analyze_workers: int = 1`), `backend/app/optimizer.py`.
- [ ] **Step 1 — schema** `analyze_workers: int = 1` (1 = sequential, untouched).
- [ ] **Step 2 — wire `_option_rerank`** to dispatch via `parallel_rerank` when `analyze_workers > 1`, else the current sequential loop (byte-identical). Parallelize the survival loop similarly IF the survival worker is cleanly extractable to a host-importable fn; if it entangles optimizer-internal closures, leave survival sequential this pass and note it (rerank is the bigger K-loop). Decide + note during implementation.
- [ ] **Step 3 — budget under parallelism.** Check `over_budget(...)` at batch boundaries; on hit stop submitting + drain in-flight + flag partial. Progress/ETA updated per completed batch.
- [ ] **Step 4 — frontend** (Optimizer.jsx): an "Analyze workers" input (1–15, default 1) next to the parallel-trial-workers control, label "experimental · deterministic results · more RAM", payload `analyze_workers`. `data-testid="opt-analyze-workers"`.
- [ ] **Step 5 — `py_compile` + build + full suite green. Commit** the backend + frontend files → `feat(optimizer): opt-in deterministic parallel analyzing (analyze_workers) + budget-aware dispatch`.

---

## PHASE 4 — Running-stack verification (controller-run)

### Task 9: end-to-end verification + finishing
**Files:** none.
- [ ] Docker rebuild backend + frontend; health.
- [ ] **P1 budget:** start an option_rerank on a high-trade strategy (e.g. confluence, K=80, a window that would exceed 30 min) with `analyze_budget_sec=120` (2 min for the test) → confirm it STOPS at ~2 min, the job records `analyze_budget_hit=true` + `analyzed_candidates`, returns a partial best, and the UI showed a live `Analyzing N/K · ETA`.
- [ ] **P1 inertness:** a small option_rerank that finishes within budget → `best_option_pnl_value`/`rerank.ranked` match a pre-change run (no result drift).
- [ ] **P2 speed+identity:** run the same small option_rerank before/after — confirm a clear wall-clock speedup AND identical `rerank.ranked` option-pnl values (vectorize is byte-identical).
- [ ] **P3 identity+OOM:** `analyze_workers=4` vs `1` on the same job → identical ranking + survivors, faster wall-clock, watch `docker stats` mem (no OOM-recycle).
- [ ] On PASS: **superpowers:finishing-a-development-branch** — verify host tests, present merge/keep options. No push/merge without explicit instruction.

---

## Completion
After P1–P3 host-green + Task 9 verified: finishing-a-development-branch. Each phase is independently shippable; if P3 proves too risky/heavy on the stack, P1+P2 still deliver the governance guarantee + the byte-identical speedup, and P3 can be dropped with evidence.
