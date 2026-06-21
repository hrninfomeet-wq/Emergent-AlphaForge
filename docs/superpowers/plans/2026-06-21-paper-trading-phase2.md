# Paper Trading Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the five deferred Paper-page analytics — forward-vs-backtest drift, R-multiple, exit-reason breakdown, monthly P&L bars, and blotter selection checkboxes — on top of the merged Phase 1.

**Architecture:** Extends the same files. Per-trade `r_multiple` + per-strategy `avg_r`/`exit_mix`/`drift` are computed server-side (drift combines `forward_metrics` with the deployment's pinned option-₹ evidence); the page-global exit-reason breakdown + monthly bars are computed client-side from the already-fetched filtered `statsRows`.

**Tech Stack:** Python/FastAPI + MongoDB (motor), pytest; React 19 + Tailwind (dark tokens) + recharts/inline-SVG.

**Spec:** [docs/superpowers/specs/2026-06-21-paper-trading-phase2-design.md](../specs/2026-06-21-paper-trading-phase2-design.md)

---

## Confirmed environment facts (same as Phase 1 — they still hold)

- **Worktree:** `C:/Users/haroo/af-wt-paper-redesign`, branch `feat/paper-analytics-phase2` (off `main`, which now contains Phase 1). `node_modules` junctioned. Use absolute paths; git via `git -C "C:/Users/haroo/af-wt-paper-redesign" ...`.
- **Pytest (main venv):** `"C:/Users/haroo/OneDrive/Documents/New project/Emergent-AlphaForge/.venv/Scripts/python.exe" -m pytest "<abs path>" -v`. No `conftest.py` — DB/route tasks verify via `asyncio.run` against live Mongo (`mongodb://localhost:27017`, db `alphaforge`), NOT TestClient.
- **FastAPI app:** `backend/server.py` (`from server import app`); routes under `/api`.
- **Theme tokens (inline styles):** backgrounds `--bg-0/1/2/3`, borders `--border-1/2`, text `--text-1/2/3`, semantics `--color-success`/`--color-danger`/`--color-info`/`--color-warning`. Tailwind classes `bg-bg-*`, `border-line`, `text-dim/dimmer/success/danger/info`. Use `&amp;` for ampersands in JSX.
- **Frontend has no jest suite** — verify by reading + a final `yarn build` (`cd frontend && corepack yarn build`) + the live-render harness (scheduler-free `backend/verify_app.py` on alt ports 8002/3001 + Chrome MCP).
- **Commits:** honor the user's per-changeset cadence; commit steps below are the units.

## Phase-2 data facts (verified)

- **R-multiple input:** `paper_auto.py` writes top-level `risk_amount` (and `risk_per_unit`, `sizing_mode`, `risk_exceeded`) onto auto-sized (premium_at_risk) trades. R = `running_pnl / risk_amount`. Absent on fixed-lots / legacy trades → `r_multiple = None`.
- **Drift live side:** `app/forward_metrics.py::compute_forward_metrics_for_deployment(db, dep)` → `win_rate`, `avg_pnl`, `library_gate.visible`.
- **Drift baseline side:** `app/routers/deployments.py::_gather_deployment_evidence(db, *, strategy_id, instrument, params, source_doc)` → `option_evidence = {win_rate, net_pnl_value, paired_trade_count, params_match}`. Baseline avg ₹/trade = `net_pnl_value / paired_trade_count`. The deployment doc carries `strategy_id`, `instrument`, and params at `config.params` (fallback `params`).
- **Existing pure fns** (`app/paper_analytics.py`): `per_trade_analytics(trade,*,now_ms,spark_points)` returns `{mfe_value,mae_value,running_pnl,spark,duration_s,sl,tp}`; `per_strategy_stats(trades)` returns list of `{strategy_id,deployment_id,net_pnl,closed_trades,open_count,open_mtm,win_rate,profit_factor,expectancy,avg_hold_s,contribution_pct}`; helpers `_f`, `_to_ms`, `_ist_day`.

## File structure

- Modify `backend/app/paper_analytics.py` — `_r_multiple` + `r_multiple` in `per_trade_analytics`; `_normalize_exit_reason` + `exit_mix`/`avg_r` in `per_strategy_stats`; new `drift_compare`.
- Modify `backend/app/routers/journals.py` — enrich `/paper/strategy-stats` with `drift`.
- Modify `tests/test_paper_analytics.py` — new tests.
- Create `frontend/src/lib/paperAgg.js` — `normalizeExitReason`, `exitReasonBreakdown`.
- Create `frontend/src/components/paper/ExitReasonBreakdown.jsx`.
- Modify `frontend/src/components/paper/{StrategyStatsTable,TradeBlotter,PnlCalendar}.jsx`.
- Modify `frontend/src/pages/PaperTrading.jsx`.

---

## Task 1: Per-trade R-multiple

**Files:** Modify `backend/app/paper_analytics.py` (`per_trade_analytics`); Test `tests/test_paper_analytics.py`.

- [ ] **Step 1: Add failing tests** (append to `tests/test_paper_analytics.py`):

```python
def test_r_multiple_present_when_risk_amount():
    t = _trade([], status="CLOSED", realized_pnl=1800.0, risk_amount=1000.0,
               closed_at="2026-06-20T05:00:00+00:00")
    assert per_trade_analytics(t)["r_multiple"] == 1.8


def test_r_multiple_none_without_risk_amount():
    t = _trade([], status="CLOSED", realized_pnl=1800.0,
               closed_at="2026-06-20T05:00:00+00:00")
    assert per_trade_analytics(t)["r_multiple"] is None


def test_r_multiple_none_when_zero_risk():
    t = _trade([], status="CLOSED", realized_pnl=500.0, risk_amount=0.0,
               closed_at="2026-06-20T05:00:00+00:00")
    assert per_trade_analytics(t)["r_multiple"] is None
```

- [ ] **Step 2: Run, verify FAIL** — `... -m pytest ".../tests/test_paper_analytics.py" -k r_multiple -v` → KeyError 'r_multiple'.

- [ ] **Step 3: Implement** — in `paper_analytics.py`, add the helper above `per_trade_analytics`:

```python
def _r_multiple(trade: Dict[str, Any], running_pnl: float) -> Optional[float]:
    """Realized/unrealized P&L as a multiple of the trade's initial ₹ risk.
    None when risk wasn't recorded (fixed-lots / legacy trades)."""
    try:
        ra = float(trade.get("risk_amount"))
    except (TypeError, ValueError):
        return None
    if ra <= 0:
        return None
    return round(running_pnl / ra, 2)
```

Then in `per_trade_analytics`, add `r_multiple` to the returned dict (after `running` is computed):

```python
        "r_multiple": _r_multiple(trade, running),
```

- [ ] **Step 4: Run, verify PASS** — `... -m pytest ".../tests/test_paper_analytics.py" -v` (all green).

- [ ] **Step 5: Commit**

```bash
git -C "C:/Users/haroo/af-wt-paper-redesign" add backend/app/paper_analytics.py tests/test_paper_analytics.py
git -C "C:/Users/haroo/af-wt-paper-redesign" commit -F - <<'MSG'
feat(paper): per-trade R-multiple in analytics block

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
MSG
```

---

## Task 2: Per-strategy avg_r + exit_mix

**Files:** Modify `backend/app/paper_analytics.py` (`per_strategy_stats` + new helper); Test `tests/test_paper_analytics.py`.

- [ ] **Step 1: Add failing tests**:

```python
from app.paper_analytics import normalize_exit_reason  # noqa: E402


def test_normalize_exit_reason_buckets():
    assert normalize_exit_reason("target_hit") == "target"
    assert normalize_exit_reason("premium_stop") == "stop"
    assert normalize_exit_reason("eod_square_off") == "eod"
    assert normalize_exit_reason("manual_close_at_market") == "manual"
    assert normalize_exit_reason("") == "other"


def test_per_strategy_stats_avg_r_and_exit_mix():
    rows = [
        {"strategy_id": "orr", "deployment_id": "d1", "status": "CLOSED",
         "realized_pnl": 2000.0, "risk_amount": 1000.0, "exit_reason": "target_hit",
         "created_at": "2026-06-20T04:00:00+00:00", "closed_at": "2026-06-20T04:30:00+00:00"},
        {"strategy_id": "orr", "deployment_id": "d1", "status": "CLOSED",
         "realized_pnl": -500.0, "risk_amount": 1000.0, "exit_reason": "premium_stop",
         "created_at": "2026-06-20T05:00:00+00:00", "closed_at": "2026-06-20T05:20:00+00:00"},
    ]
    s = per_strategy_stats(rows)[0]
    assert s["avg_r"] == 0.75            # mean of (2.0, -0.5)
    assert s["exit_mix"]["target"] == 50
    assert s["exit_mix"]["stop"] == 50


def test_per_strategy_avg_r_none_without_risk():
    rows = [{"strategy_id": "x", "status": "CLOSED", "realized_pnl": 100.0,
             "exit_reason": "target", "created_at": "2026-06-20T04:00:00+00:00",
             "closed_at": "2026-06-20T04:10:00+00:00"}]
    assert per_strategy_stats(rows)[0]["avg_r"] is None
```

- [ ] **Step 2: Run, verify FAIL** (ImportError on `normalize_exit_reason` / missing keys).

- [ ] **Step 3: Implement** — add the normalizer near the top of `paper_analytics.py`:

```python
_EXIT_BUCKETS = ("target", "stop", "eod", "manual", "other")


def normalize_exit_reason(reason: Any) -> str:
    r = str(reason or "").lower()
    if "target" in r:
        return "target"
    if "stop" in r:
        return "stop"
    if "eod" in r or "square" in r or "expiry" in r:
        return "eod"
    if "manual" in r:
        return "manual"
    return "other"
```

In `per_strategy_stats`, in the per-group accumulator dict (where `"_wins": 0, ...` are initialised) add:

```python
            "_r_sum": 0.0, "_r_n": 0, "_exit": {b: 0 for b in _EXIT_BUCKETS}, "_exit_n": 0,
```

In the CLOSED branch of the loop (where `pnl = _f(t.get("realized_pnl"))` etc.), after the win/loss accounting add:

```python
            try:
                ra = float(t.get("risk_amount"))
            except (TypeError, ValueError):
                ra = 0.0
            if ra > 0:
                g["_r_sum"] += pnl / ra
                g["_r_n"] += 1
            g["_exit"][normalize_exit_reason(t.get("exit_reason"))] += 1
            g["_exit_n"] += 1
```

In the output dict (the per-group `out.append({...})`), add:

```python
            "avg_r": round(g["_r_sum"] / g["_r_n"], 2) if g["_r_n"] else None,
            "exit_mix": ({b: round(g["_exit"][b] / g["_exit_n"] * 100) for b in _EXIT_BUCKETS}
                         if g["_exit_n"] else {b: 0 for b in _EXIT_BUCKETS}),
```

- [ ] **Step 4: Run, verify PASS** (full file green).

- [ ] **Step 5: Commit**

```bash
git -C "C:/Users/haroo/af-wt-paper-redesign" add backend/app/paper_analytics.py tests/test_paper_analytics.py
git -C "C:/Users/haroo/af-wt-paper-redesign" commit -F - <<'MSG'
feat(paper): per-strategy avg R-multiple + exit-reason mix

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
MSG
```

---

## Task 3: Drift combiner (pure)

**Files:** Modify `backend/app/paper_analytics.py` (new `drift_compare`); Test `tests/test_paper_analytics.py`.

- [ ] **Step 1: Add failing tests**:

```python
from app.paper_analytics import drift_compare  # noqa: E402


def test_drift_no_baseline_when_params_mismatch():
    out = drift_compare({"win_rate": 55, "avg": 100, "visible": True},
                        {"win_rate": 60, "avg": 120, "params_match": False})
    assert out["state"] == "no_baseline"


def test_drift_insufficient_sample_when_not_visible():
    out = drift_compare({"win_rate": 55, "avg": 100, "visible": False},
                        {"win_rate": 60, "avg": 120, "params_match": True})
    assert out["state"] == "insufficient_sample"
    assert out["base_win_rate"] == 60


def test_drift_ok_with_deltas():
    out = drift_compare({"win_rate": 54, "avg": 90, "visible": True},
                        {"win_rate": 60, "avg": 120, "params_match": True})
    assert out["state"] == "ok"
    assert out["win_rate_delta"] == -6.0
    assert out["avg_delta"] == -30.0
```

- [ ] **Step 2: Run, verify FAIL** (ImportError).

- [ ] **Step 3: Implement** — append to `paper_analytics.py`:

```python
def drift_compare(live: Optional[Dict[str, Any]],
                  baseline: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Forward-vs-backtest drift state. live={win_rate,avg,visible} (session-gated
    forward metrics); baseline={win_rate,avg,params_match} (pinned option-₹ evidence)."""
    if not baseline or not baseline.get("params_match") or baseline.get("win_rate") is None:
        return {"state": "no_baseline"}
    if not live or not live.get("visible"):
        return {"state": "insufficient_sample",
                "base_win_rate": baseline.get("win_rate"),
                "base_avg": (round(float(baseline["avg"]), 2) if baseline.get("avg") is not None else None)}
    lw, bw = live.get("win_rate"), baseline.get("win_rate")
    la, ba = live.get("avg"), baseline.get("avg")
    return {
        "state": "ok",
        "live_win_rate": lw, "base_win_rate": bw,
        "win_rate_delta": round(lw - bw, 1) if lw is not None and bw is not None else None,
        "live_avg": round(la, 2) if la is not None else None,
        "base_avg": round(ba, 2) if ba is not None else None,
        "avg_delta": round(la - ba, 2) if la is not None and ba is not None else None,
    }
```

- [ ] **Step 4: Run, verify PASS** (full file green).

- [ ] **Step 5: Commit**

```bash
git -C "C:/Users/haroo/af-wt-paper-redesign" add backend/app/paper_analytics.py tests/test_paper_analytics.py
git -C "C:/Users/haroo/af-wt-paper-redesign" commit -F - <<'MSG'
feat(paper): drift_compare pure combiner (forward vs backtest)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
MSG
```

---

## Task 4: Enrich /paper/strategy-stats with drift

**Files:** Modify `backend/app/routers/journals.py` (`paper_strategy_stats`).

- [ ] **Step 1: Implement** — at the top of `journals.py` add the imports:

```python
from app.forward_metrics import compute_forward_metrics_for_deployment
from app.routers.deployments import _gather_deployment_evidence
```

In `paper_strategy_stats`, after `stats = paper_analytics.per_strategy_stats(rows)` and the `deployment_name` enrichment, add the drift enrichment:

```python
    for s in stats:
        dep_id = s.get("deployment_id")
        if not dep_id:
            s["drift"] = {"state": "no_baseline"}
            continue
        dep = await db.strategy_deployments.find_one({"id": dep_id}, {"_id": 0})
        if not dep:
            s["drift"] = {"state": "no_baseline"}
            continue
        try:
            fm = await compute_forward_metrics_for_deployment(db, dep)
            live = {"win_rate": fm.get("win_rate"), "avg": fm.get("avg_pnl"),
                    "visible": bool((fm.get("library_gate") or {}).get("visible"))}
            cfg = dep.get("config") or {}
            evidence = await _gather_deployment_evidence(
                db,
                strategy_id=cfg.get("strategy_id") or dep.get("strategy_id") or "",
                instrument=cfg.get("instrument") or dep.get("instrument") or "",
                params=cfg.get("params") or dep.get("params") or {},
                source_doc=dep,
            )
            oe = evidence.get("option_evidence") or {}
            paired = oe.get("paired_trade_count")
            base_avg = (oe.get("net_pnl_value") / paired) if paired else None
            baseline = {"win_rate": oe.get("win_rate"), "avg": base_avg,
                        "params_match": bool(oe.get("params_match"))}
            s["drift"] = paper_analytics.drift_compare(live, baseline)
        except Exception:
            s["drift"] = {"state": "no_baseline"}
```

(Drift is per-deployment; with a handful of deployments this is fine. If it ever gets slow, cache per (deployment_id, params).)

- [ ] **Step 2: Verify (live, asyncio.run)** — start nothing; call the handler directly:

```bash
"C:/Users/haroo/OneDrive/Documents/New project/Emergent-AlphaForge/.venv/Scripts/python.exe" -c "import sys, asyncio; sys.path.insert(0,'C:/Users/haroo/af-wt-paper-redesign/backend'); from app.routers.journals import paper_strategy_stats; r=asyncio.run(paper_strategy_stats()); items=r['items']; print('items', len(items)); print([{'s':i['strategy_id'],'avg_r':i.get('avg_r'),'exit_mix':i.get('exit_mix'),'drift':i.get('drift',{}).get('state')} for i in items][:3])"
```
Expected: items each carry `avg_r`, `exit_mix`, and a `drift` with a `state` (likely `no_baseline`/`insufficient_sample` for the legacy confluence deployment — that's correct). No exception.

- [ ] **Step 3: Confirm r_multiple flows through /paper/trades** (no route change needed — `per_trade_analytics` now includes it):

```bash
"C:/Users/haroo/OneDrive/Documents/New project/Emergent-AlphaForge/.venv/Scripts/python.exe" -c "import sys, asyncio; sys.path.insert(0,'C:/Users/haroo/af-wt-paper-redesign/backend'); from app.routers.journals import list_paper_trades; r=asyncio.run(list_paper_trades(include_analytics=True, limit=2)); print([i['analytics'].get('r_multiple') for i in r['items']])"
```
Expected: a list of `r_multiple` values (numbers or `None`), no exception.

- [ ] **Step 4: Commit**

```bash
git -C "C:/Users/haroo/af-wt-paper-redesign" add backend/app/routers/journals.py
git -C "C:/Users/haroo/af-wt-paper-redesign" commit -F - <<'MSG'
feat(paper): forward-vs-backtest drift on /paper/strategy-stats

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
MSG
```

---

## Task 5: Client exit-reason aggregation helper

**Files:** Create `frontend/src/lib/paperAgg.js`.

- [ ] **Step 1: Implement**:

```javascript
// Pure client aggregations for the Paper page (computed from the filtered
// statsRows the page already fetches, so they respect the active filters).

export const EXIT_BUCKETS = ["target", "stop", "eod", "manual", "other"];

export const normalizeExitReason = (reason) => {
  const r = String(reason || "").toLowerCase();
  if (r.includes("target")) return "target";
  if (r.includes("stop")) return "stop";
  if (r.includes("eod") || r.includes("square") || r.includes("expiry")) return "eod";
  if (r.includes("manual")) return "manual";
  return "other";
};

// rows -> { counts, pct, total } over CLOSED trades only.
export const exitReasonBreakdown = (rows) => {
  const counts = { target: 0, stop: 0, eod: 0, manual: 0, other: 0 };
  let total = 0;
  for (const t of rows || []) {
    if (String(t.status || "").toUpperCase() !== "CLOSED") continue;
    counts[normalizeExitReason(t.exit_reason)] += 1;
    total += 1;
  }
  const pct = {};
  for (const k of EXIT_BUCKETS) pct[k] = total ? Math.round((counts[k] / total) * 100) : 0;
  return { counts, pct, total };
};
```

- [ ] **Step 2: Verify (node)** — confirm the algorithm:

```bash
cd "C:/Users/haroo/af-wt-paper-redesign/frontend" && node -e "const f=(reason)=>{const r=String(reason||'').toLowerCase();if(r.includes('target'))return 'target';if(r.includes('stop'))return 'stop';if(r.includes('eod')||r.includes('square')||r.includes('expiry'))return 'eod';if(r.includes('manual'))return 'manual';return 'other';};console.log(f('target_hit'),f('premium_stop'),f('eod_square_off'),f('manual_close_at_market'),f(''))"
```
Expected: `target stop eod manual other`.

- [ ] **Step 3: Commit**

```bash
git -C "C:/Users/haroo/af-wt-paper-redesign" add frontend/src/lib/paperAgg.js
git -C "C:/Users/haroo/af-wt-paper-redesign" commit -F - <<'MSG'
feat(paper-ui): client exit-reason aggregation helper

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
MSG
```

---

## Task 6: ExitReasonBreakdown component

**Files:** Create `frontend/src/components/paper/ExitReasonBreakdown.jsx`.

- [ ] **Step 1: Implement** (two variants: `full` labeled bars, `compact` single stacked bar):

```jsx
import { EXIT_BUCKETS } from "@/lib/paperAgg";

const LABEL = { target: "target", stop: "stop", eod: "end-of-day", manual: "manual", other: "other" };
const COLOR = {
  target: "var(--color-success)", stop: "var(--color-danger)",
  eod: "var(--text-3)", manual: "var(--color-info)", other: "var(--text-2)",
};

// breakdown: { pct: {bucket: int}, counts?: {...}, total?: int }
export default function ExitReasonBreakdown({ breakdown, variant = "full" }) {
  const pct = breakdown?.pct || breakdown || {};
  const total = breakdown?.total;
  if (variant === "compact") {
    const segs = EXIT_BUCKETS.filter((b) => (pct[b] || 0) > 0);
    if (segs.length === 0) return <span className="text-[10px] text-dimmer">—</span>;
    return (
      <div className="flex h-2.5 w-24 rounded-sm overflow-hidden" data-testid="exit-mix-compact"
        title={EXIT_BUCKETS.map((b) => `${LABEL[b]} ${pct[b] || 0}%`).join(" · ")}>
        {segs.map((b) => (
          <div key={b} style={{ width: `${pct[b]}%`, backgroundColor: COLOR[b] }} />
        ))}
      </div>
    );
  }
  return (
    <div className="rounded-lg border border-line bg-bg-1 p-3" data-testid="paper-exit-breakdown">
      <div className="text-xs font-semibold uppercase tracking-wider text-dim mb-2">
        Exit reasons {total != null ? <span className="text-dimmer font-normal">· {total} closed</span> : null}
      </div>
      {(total === 0) ? (
        <div className="text-[11px] text-dimmer font-mono">No closed trades for this filter.</div>
      ) : (
        <div className="flex flex-col gap-2">
          {EXIT_BUCKETS.map((b) => (
            <div key={b}>
              <div className="flex justify-between text-[11px]"><span className="text-dim">{LABEL[b]}</span><span className="font-mono text-dimmer">{pct[b] || 0}%</span></div>
              <div className="h-2 rounded-sm bg-bg-3"><div className="h-2 rounded-sm" style={{ width: `${pct[b] || 0}%`, backgroundColor: COLOR[b] }} /></div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Verify** at the final `yarn build` (Task 11). Re-read for balanced JSX + import.

- [ ] **Step 3: Commit**

```bash
git -C "C:/Users/haroo/af-wt-paper-redesign" add frontend/src/components/paper/ExitReasonBreakdown.jsx
git -C "C:/Users/haroo/af-wt-paper-redesign" commit -F - <<'MSG'
feat(paper-ui): ExitReasonBreakdown component (full + compact)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
MSG
```

---

## Task 7: StrategyStatsTable — Avg R + drift chip + exit mix

**Files:** Modify `frontend/src/components/paper/StrategyStatsTable.jsx`.

- [ ] **Step 1: Implement** — replace the file with (adds 3 columns + a DriftChip helper; existing columns/behaviour unchanged):

```jsx
import { fmtINRSigned, fmtPct, fmtNum, fmtSigned, colorPnL } from "@/lib/fmt";
import ExitReasonBreakdown from "./ExitReasonBreakdown";

function DriftChip({ drift }) {
  const d = drift || {};
  if (d.state === "no_baseline") return <span className="text-[10px] text-dimmer">no baseline</span>;
  if (d.state === "insufficient_sample") return <span className="text-[10px] text-dimmer" title="Needs ≥10 complete forward sessions">insufficient sample</span>;
  if (d.state !== "ok") return <span className="text-[10px] text-dimmer">—</span>;
  const wrUp = (d.win_rate_delta ?? 0) >= 0;
  const avUp = (d.avg_delta ?? 0) >= 0;
  const cls = (wrUp && avUp) ? "bg-emerald-500/10 text-emerald-300 border-emerald-500/30"
    : (!wrUp && !avUp) ? "bg-rose-500/10 text-rose-300 border-rose-500/30"
    : "bg-amber-500/10 text-amber-300 border-amber-500/30";
  return (
    <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded border ${cls}`}
      title="Live (session-gated) vs pinned backtest (option-₹)">
      WR {fmtNum(d.live_win_rate, 0)} vs {fmtNum(d.base_win_rate, 0)} {wrUp ? "▲" : "▼"} · ₹/t {fmtSigned(d.live_avg, 0)} vs {fmtSigned(d.base_avg, 0)} {avUp ? "▲" : "▼"}
    </span>
  );
}

export default function StrategyStatsTable({ stats, onFilterStrategy }) {
  if (!stats || stats.length === 0) {
    return <div className="rounded-lg border border-line bg-bg-1 p-3 text-[11px] text-dimmer">No strategy activity yet.</div>;
  }
  return (
    <div className="rounded-lg border border-line bg-bg-1 overflow-x-auto" data-testid="paper-strategy-stats">
      <table className="w-full text-xs">
        <thead className="bg-bg-2 text-dim">
          <tr className="border-b border-line">
            <th className="text-left p-2">Strategy</th>
            <th className="text-right p-2">Net P&amp;L</th>
            <th className="text-right p-2">Trades</th>
            <th className="text-right p-2">Win%</th>
            <th className="text-right p-2">PF</th>
            <th className="text-right p-2">Avg R</th>
            <th className="text-left p-2">vs backtest</th>
            <th className="text-left p-2">Exit mix</th>
            <th className="text-right p-2">Expectancy</th>
            <th className="text-right p-2">Open</th>
            <th className="text-right p-2">Contrib.</th>
          </tr>
        </thead>
        <tbody>
          {stats.map((s) => (
            <tr key={s.strategy_id + (s.deployment_id || "")} className="border-b border-line hover:bg-bg-2 cursor-pointer"
              onClick={() => onFilterStrategy?.(s.strategy_id)} data-testid="paper-strategy-row">
              <td className="p-2">
                <div className="font-medium truncate max-w-[200px]" title={s.deployment_name || s.strategy_id}>{s.deployment_name || s.strategy_id}</div>
                <div className="text-dimmer truncate max-w-[200px]">{s.strategy_id}</div>
              </td>
              <td className={`p-2 text-right font-mono tabular-nums ${colorPnL(s.net_pnl)}`}>{fmtINRSigned(s.net_pnl)}</td>
              <td className="p-2 text-right font-mono text-dim">{s.closed_trades}</td>
              <td className="p-2 text-right font-mono">{s.win_rate == null ? "—" : fmtPct(s.win_rate, 0)}</td>
              <td className="p-2 text-right font-mono">{s.profit_factor == null ? "—" : (s.profit_factor === "Infinity" || s.profit_factor === Infinity ? "∞" : fmtNum(s.profit_factor, 2))}</td>
              <td className={`p-2 text-right font-mono ${colorPnL(s.avg_r)}`}>{s.avg_r == null ? "—" : fmtSigned(s.avg_r, 2)}</td>
              <td className="p-2"><DriftChip drift={s.drift} /></td>
              <td className="p-2"><ExitReasonBreakdown breakdown={{ pct: s.exit_mix }} variant="compact" /></td>
              <td className={`p-2 text-right font-mono ${colorPnL(s.expectancy)}`}>{s.expectancy == null ? "—" : fmtINRSigned(s.expectancy)}</td>
              <td className="p-2 text-right font-mono text-dim">{s.open_count}{s.open_count ? ` · ${fmtINRSigned(s.open_mtm)}` : ""}</td>
              <td className="p-2 text-right font-mono text-dim">{s.contribution_pct == null ? "—" : `${fmtNum(s.contribution_pct, 0)}%`}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

(`fmtSigned` already exists in `lib/fmt.js`.)

- [ ] **Step 2: Verify** at Task 11. **Step 3: Commit**

```bash
git -C "C:/Users/haroo/af-wt-paper-redesign" add frontend/src/components/paper/StrategyStatsTable.jsx
git -C "C:/Users/haroo/af-wt-paper-redesign" commit -F - <<'MSG'
feat(paper-ui): strategy table Avg R + drift chip + exit mix

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
MSG
```

---

## Task 8: TradeBlotter — R column + selection checkboxes

**Files:** Modify `frontend/src/components/paper/TradeBlotter.jsx`.

- [ ] **Step 1: Implement** — change the signature to accept selection props and add two columns. New props: `selected` (Set), `onToggleRow`, `onToggleAll`, `allClosedSelected`.

Signature:
```jsx
export default function TradeBlotter({ rows, sort, onToggleSort, onCloseAtMarket, busy, selected, onToggleRow, onToggleAll, allClosedSelected }) {
```

In `<thead>`, add a leading checkbox header and an R header (between "Now" and "P&L curve"):
```jsx
            <th className="p-2 w-8 text-center">
              <input type="checkbox" checked={!!allClosedSelected} onChange={onToggleAll} data-testid="paper-select-all" title="Select closed trades on this page" />
            </th>
            <H col="created_at">Date / time</H>
```
…and after the "Now" `<H>` add:
```jsx
            <H right>R</H>
```

In each row, add the leading checkbox cell (closed-only) and the R cell. Add at the very start of the `<tr>` (before the Date cell):
```jsx
                  <td className="p-2 text-center" onClick={(e) => e.stopPropagation()}>
                    {!isOpen && (
                      <input type="checkbox" checked={selected?.has(t.id) || false} onChange={() => onToggleRow?.(t.id)} data-testid="paper-row-select" />
                    )}
                  </td>
```
…and after the "Now" cell (the `running_pnl` cell) add:
```jsx
                  <td className={`p-2 text-right font-mono ${colorPnL(a.r_multiple)}`}>{a.r_multiple == null ? "—" : fmtSigned(a.r_multiple, 2)}</td>
```

Update both `colSpan="14"` occurrences (empty-state row and the drawer row) to `colSpan="16"`.

Add `fmtSigned` to the import from `@/lib/fmt`:
```jsx
import { fmtINR, fmtINRSigned, fmtNum, fmtPct, fmtDuration, fmtSigned, colorPnL } from "@/lib/fmt";
```

- [ ] **Step 2: Verify** at Task 11. **Step 3: Commit**

```bash
git -C "C:/Users/haroo/af-wt-paper-redesign" add frontend/src/components/paper/TradeBlotter.jsx
git -C "C:/Users/haroo/af-wt-paper-redesign" commit -F - <<'MSG'
feat(paper-ui): blotter R-multiple column + closed-only selection checkboxes

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
MSG
```

---

## Task 9: PnlCalendar — monthly P&L bars

**Files:** Modify `frontend/src/components/paper/PnlCalendar.jsx`.

- [ ] **Step 1: Implement** — add a monthly bar strip derived from the `dayPnl` Map (no new prop). Add a `MonthlyBars` helper above the default export and render it under the heat-grid.

```jsx
function MonthlyBars({ dayPnl }) {
  const months = new Map(); // 'YYYY-MM' -> pnl
  for (const [day, info] of dayPnl.entries()) {
    const m = day.slice(0, 7);
    months.set(m, (months.get(m) || 0) + Number(info.pnl || 0));
  }
  const entries = [...months.entries()].sort().slice(-6);
  if (entries.length === 0) return null;
  const maxAbs = Math.max(1, ...entries.map(([, v]) => Math.abs(v)));
  return (
    <div className="mt-3 pt-3 border-t border-line" data-testid="paper-monthly-bars">
      <div className="text-[10px] uppercase tracking-wider text-dimmer mb-2">Monthly P&amp;L</div>
      <div className="flex items-end gap-3 h-20">
        {entries.map(([m, v]) => {
          const h = Math.round((Math.abs(v) / maxAbs) * 56) + 2;
          const pos = v >= 0;
          return (
            <div key={m} className="flex flex-col items-center justify-end gap-1" title={`${m}: ₹${fmtNum(v, 0)}`}>
              {pos && <div className="text-[9px] font-mono text-success">{fmtNum(v, 0)}</div>}
              <div style={{ height: `${h}px`, backgroundColor: pos ? "var(--color-success)" : "var(--color-danger)" }} className="w-7 rounded-sm" />
              {!pos && <div className="text-[9px] font-mono text-danger">{fmtNum(v, 0)}</div>}
              <div className="text-[9px] font-mono text-dimmer">{m.slice(5)}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
```

In the default export, inside the `{showCalendar && (<div className="p-3">...)}` block, add `<MonthlyBars dayPnl={dayPnl} />` after `<CalendarHeatGrid dayPnl={dayPnl} />`. Remove the "deferred Phase 2" NOTE comment above the export.

- [ ] **Step 2: Verify** at Task 11. **Step 3: Commit**

```bash
git -C "C:/Users/haroo/af-wt-paper-redesign" add frontend/src/components/paper/PnlCalendar.jsx
git -C "C:/Users/haroo/af-wt-paper-redesign" commit -F - <<'MSG'
feat(paper-ui): monthly P&L bars in the P&L calendar

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
MSG
```

---

## Task 10: PaperTrading — restore delete-selected + global exit card + wiring

**Files:** Modify `frontend/src/pages/PaperTrading.jsx`.

- [ ] **Step 1: Imports** — add to the existing imports:

```jsx
import ExitReasonBreakdown from "@/components/paper/ExitReasonBreakdown";
import { exitReasonBreakdown } from "@/lib/paperAgg";
```

- [ ] **Step 2: Selection state** — after the `olderDays` state, add:

```jsx
  const [selected, setSelected] = useState(() => new Set());
```

After `data` is set in `fetchRows` (or as a memo), compute the page's closed ids + select-all helpers. Add these memos/handlers near `toggleSort`:

```jsx
  const closedVisibleIds = useMemo(
    () => data.items.filter((t) => String(t.status || "").toUpperCase() === "CLOSED").map((t) => t.id),
    [data.items],
  );
  const allClosedSelected = closedVisibleIds.length > 0 && closedVisibleIds.every((id) => selected.has(id));
  const toggleRow = (id) => setSelected((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const toggleAll = () => setSelected((s) => (allClosedSelected ? new Set() : new Set(closedVisibleIds)));
```

Reset selection when filters/page change — in `setFilter` add `setSelected(new Set());`, and in the `purge` success path add `setSelected(new Set());`.

- [ ] **Step 3: deleteSelected handler** — add next to `deleteOlder`:

```jsx
  const deleteSelected = () => {
    if (selected.size === 0) return;
    purge({ ids: [...selected] }, `Delete ${selected.size} selected CLOSED trade${selected.size === 1 ? "" : "s"}? OPEN trades are never deleted. This cannot be undone.`);
  };
```

- [ ] **Step 4: Global exit-reason breakdown** — add a memo:

```jsx
  const exitBreakdown = useMemo(() => exitReasonBreakdown(statsRows), [statsRows]);
```

- [ ] **Step 5: Render** — (a) put the global exit card next to the calendar: replace the `<PnlCalendar dayPnl={dayPnl} />` line with a 2-col grid:

```jsx
      <div className="grid lg:grid-cols-[2fr_1fr] gap-3">
        <PnlCalendar dayPnl={dayPnl} />
        <ExitReasonBreakdown breakdown={exitBreakdown} variant="full" />
      </div>
```

(b) In the cleanup toolkit row, add the Delete-selected button before the "Older than" span:

```jsx
          <Button variant="outline" size="sm" disabled={busy || selected.size === 0} onClick={deleteSelected}
            className="h-6 text-[11px] border-rose-500/40 text-rose-300 hover:text-rose-200" data-testid="paper-delete-selected">
            Delete selected ({selected.size})
          </Button>
```

(c) Pass selection props to the blotter:

```jsx
      <TradeBlotter rows={data.items} sort={sort} onToggleSort={toggleSort} onCloseAtMarket={closeAtMarket} busy={busy}
        selected={selected} onToggleRow={toggleRow} onToggleAll={toggleAll} allClosedSelected={allClosedSelected} />
```

(d) Update the deferred-comment above `purge` to note delete-selected is now restored.

- [ ] **Step 6: Verify** at Task 11. **Step 7: Commit**

```bash
git -C "C:/Users/haroo/af-wt-paper-redesign" add frontend/src/pages/PaperTrading.jsx
git -C "C:/Users/haroo/af-wt-paper-redesign" commit -F - <<'MSG'
feat(paper-ui): restore delete-selected + global exit-reason card

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
MSG
```

---

## Task 11: Full verification

- [ ] **Step 1: Backend green** — `"<venv python>" -m pytest "C:/Users/haroo/af-wt-paper-redesign/tests/test_paper_analytics.py" -v` (Phase 1 + Phase 2 tests pass). Plus the two `asyncio.run` checks from Task 4.

- [ ] **Step 2: Frontend build** — `cd "C:/Users/haroo/af-wt-paper-redesign/frontend" && corepack yarn build` → "Compiled successfully". Fix any errors (unused imports, unbalanced JSX) until green.

- [ ] **Step 3: Live render (optional, Phase-1 harness)** — recreate the scheduler-free `backend/verify_app.py` (mounts routers, no `@app.on_event("startup")`), run it on 8002 + `corepack yarn start` on 3001 (`REACT_APP_BACKEND_URL=http://localhost:8002`), Chrome MCP to `/paper`; confirm: drift chips / Avg R in the strategy table, exit-reason card next to the calendar, monthly bars, R column + checkboxes + working Delete-selected, no console errors. Then kill the servers and delete `verify_app.py`.

- [ ] **Step 4: Commit any fixes**

```bash
git -C "C:/Users/haroo/af-wt-paper-redesign" add -A
git -C "C:/Users/haroo/af-wt-paper-redesign" commit -F - <<'MSG'
fix(paper): Phase 2 verification + polish

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
MSG
```

---

## Self-review (author check)

- **Spec coverage:** drift → T3+T4+T7; R-multiple → T1 (per-trade) + T2 (avg) + T7/T8 (display); exit-reason → T2 (per-strategy) + T5/T6/T10 (global+display); monthly bars → T9; checkboxes → T8+T10. All five covered, plus the §4a refinement (client-side global exit + monthly).
- **Placeholder scan:** none — every step has concrete code/commands.
- **Type consistency:** `drift` object keys (`state`, `live_win_rate`, `base_win_rate`, `win_rate_delta`, `live_avg`, `base_avg`, `avg_delta`) identical across T3 (producer), T4 (route passthrough), T7 (DriftChip consumer). `exit_mix` pct dict keys = `EXIT_BUCKETS` across T2 (Python) and T5/T6 (JS). `r_multiple` key consistent across T1/T4/T8. Selection prop names (`selected`/`onToggleRow`/`onToggleAll`/`allClosedSelected`) consistent across T8 and T10.
- **Assumptions to confirm at execution:** that `_gather_deployment_evidence` imports cleanly into `journals.py` without a circular import (deployments router imports are already loaded at app startup; if a circular import surfaces, do the import lazily inside `paper_strategy_stats`).

## Phase 3 / out of scope

None planned — this completes the Paper Trading redesign. Future ideas (alerting, multi-account) would be their own spec.
