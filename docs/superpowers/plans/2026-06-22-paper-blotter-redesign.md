# Paper Trade Blotter Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure the Paper Trade blotter to a 17-column layout (split Entry/Exit, add Exit Price / Exit Date/Time / Qty / Exit Reason; drop R, Now, Actions) with server-side per-column header filters (Side, Status, Exit Reason, Strategy).

**Architecture:** Backend gains two optional filters (`direction`, `exit_reason` bucket) on `GET /api/paper/trades` plus `exit_price` sortability; exit-reason bucketing logic (precedence `target > manual > eod > stop(¬time_stop) > other`) is centralized in `paper_analytics.py` (Python) and mirrored in `frontend/src/lib/exitReason.js` + `paperAgg.js` (JS). The blotter becomes presentational with new column set and a header filter row wired to the parent's existing `setFilter`.

**Tech Stack:** FastAPI + Motor/Mongo (backend), React 19 + Tailwind + CRA/craco (frontend). Backend tests: pytest from repo root (`python -m pytest tests/ -v`; test files self-bootstrap `backend/` onto `sys.path`, test PURE functions, no live DB). Frontend has NO unit-test runner — verify via `yarn build` + Docker rebuild + Chrome live-render (matches prior Paper-redesign phases).

**Spec:** `docs/superpowers/specs/2026-06-22-paper-blotter-redesign-design.md`

---

## File Structure

- `backend/app/runtime.py` — add `exit_price` to `_TRADES_SORT_FIELDS`.
- `backend/app/paper_analytics.py` — reorder `normalize_exit_reason`; add pure `exit_reason_query(bucket)` + `merge_conditions(q, extra)`.
- `backend/app/routers/journals.py` — add `direction` + `exit_reason` query params; build/merge the conditions into the Mongo query.
- `tests/test_paper_blotter_filters.py` — NEW: unit tests for the above pure functions (+ a tiny Mongo-condition evaluator).
- `frontend/src/lib/exitReason.js` — NEW: `classifyExitReason` + `EXIT_REASON_OPTIONS`.
- `frontend/src/lib/paperAgg.js` — reorder `normalizeExitReason` to match.
- `frontend/src/components/paper/TradeBlotter.jsx` — 17-column layout, header filter row, Exit Reason cell, new props.
- `frontend/src/pages/PaperTrading.jsx` — `direction`/`exit_reason` filter state + params, `strategyOptions`, move status filter to header, pass props.

---

## Task 1: Backend — make `exit_price` sortable

**Files:**
- Modify: `backend/app/runtime.py` (`_TRADES_SORT_FIELDS`, ~line 897)
- Test: `tests/test_paper_blotter_filters.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_paper_blotter_filters.py`:

```python
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.runtime import _TRADES_SORT_FIELDS  # noqa: E402


def test_exit_price_is_sortable():
    assert "exit_price" in _TRADES_SORT_FIELDS
    # existing allowlist entries are unchanged
    for f in ("created_at", "closed_at", "entry_price", "realized_pnl", "mfe_value", "mae_value"):
        assert f in _TRADES_SORT_FIELDS
```

- [ ] **Step 2: Run it — expect FAIL**

Run: `python -m pytest tests/test_paper_blotter_filters.py::test_exit_price_is_sortable -v`
Expected: FAIL (`exit_price` not in the set).

- [ ] **Step 3: Add `exit_price` to the allowlist**

In `backend/app/runtime.py`, find `_TRADES_SORT_FIELDS = {...}` (~line 897) and add `"exit_price"`:

```python
_TRADES_SORT_FIELDS = {
    "updated_at", "created_at", "closed_at", "realized_pnl",
    "entry_price", "exit_price", "mfe_value", "mae_value",
}
```
(Match the file's existing literal style — it may be a set or tuple; add `"exit_price"` next to `"entry_price"`.)

- [ ] **Step 4: Run it — expect PASS**

Run: `python -m pytest tests/test_paper_blotter_filters.py::test_exit_price_is_sortable -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/runtime.py tests/test_paper_blotter_filters.py
git commit -m "feat(paper): allow sorting trades by exit_price"
```

---

## Task 2: Backend — reorder `normalize_exit_reason` (manual before eod, time_stop carve-out)

**Files:**
- Modify: `backend/app/paper_analytics.py` (`normalize_exit_reason`, lines 50–60)
- Test: `tests/test_paper_blotter_filters.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_paper_blotter_filters.py`:

```python
from app.paper_analytics import normalize_exit_reason  # noqa: E402

# (raw exit_reason, expected bucket) — every value the backend can write (spec §2).
RAW_BY_BUCKET = {
    "target": ["target_hit", "spot_target_hit"],
    "manual": ["manual_square_off", "manual_close_at_market"],
    "eod": ["auto_square_off_15_00_IST"],
    "stop": ["stop_hit", "spot_stop_hit"],
    "other": ["time_stop"],
}
ALL_RAW = [(raw, bucket) for bucket, raws in RAW_BY_BUCKET.items() for raw in raws]


def test_normalize_exit_reason_buckets_every_raw_value():
    for raw, bucket in ALL_RAW:
        assert normalize_exit_reason(raw) == bucket, f"{raw} -> {normalize_exit_reason(raw)} (want {bucket})"


def test_normalize_exit_reason_manual_squareoff_is_manual_not_eod():
    # contains both "manual" and "square"; manual must win
    assert normalize_exit_reason("manual_square_off") == "manual"


def test_normalize_exit_reason_time_stop_is_other_not_stop():
    assert normalize_exit_reason("time_stop") == "other"
```

- [ ] **Step 2: Run — expect FAIL**

Run: `python -m pytest tests/test_paper_blotter_filters.py -k normalize -v`
Expected: FAIL (`manual_square_off`→`eod`, `time_stop`→`stop` under current order).

- [ ] **Step 3: Reorder the function**

In `backend/app/paper_analytics.py`, replace `normalize_exit_reason` (lines 50–60) with:

```python
def normalize_exit_reason(reason: Any) -> str:
    # Precedence: target > manual > eod > stop(not time_stop) > other.
    # `manual` is checked before `eod` because "manual_square_off" contains both
    # "manual" and "square" and is a user square-off, not End-of-day. "time_stop"
    # is a time exit, not a price stop, so it is carved out of `stop`.
    r = str(reason or "").lower()
    if "target" in r:
        return "target"
    if "manual" in r:
        return "manual"
    if "eod" in r or "square" in r or "expiry" in r:
        return "eod"
    if "stop" in r and r != "time_stop":
        return "stop"
    return "other"
```

- [ ] **Step 4: Run — expect PASS**

Run: `python -m pytest tests/test_paper_blotter_filters.py -k normalize -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Check for existing tests asserting the OLD buckets**

Run: `git grep -n "normalize_exit_reason\|manual_square_off\|time_stop" tests/`
If any existing test asserts `manual_square_off`→`eod` or `time_stop`→`stop`, update it to the corrected bucket (these are corrections). Then re-run the full suite at Task 9.

- [ ] **Step 6: Commit**

```bash
git add backend/app/paper_analytics.py tests/test_paper_blotter_filters.py
git commit -m "fix(paper): exit-reason precedence — manual before eod, time_stop is other"
```

---

## Task 3: Backend — `exit_reason_query` bucket builder + `merge_conditions`

**Files:**
- Modify: `backend/app/paper_analytics.py` (add two pure functions near `normalize_exit_reason`)
- Test: `tests/test_paper_blotter_filters.py`

- [ ] **Step 1: Write the failing test (with a minimal Mongo-condition evaluator)**

Append to `tests/test_paper_blotter_filters.py`:

```python
import re  # noqa: E402
from app.paper_analytics import exit_reason_query, merge_conditions  # noqa: E402


def _match_field(doc, field, spec):
    present = field in doc
    value = doc.get(field)
    if not isinstance(spec, dict):
        return value == spec
    for op, operand in spec.items():
        if op == "$options":
            continue
        if op == "$regex":
            flags = re.I if "i" in spec.get("$options", "") else 0
            if value is None or re.search(operand, str(value), flags) is None:
                return False
        elif op == "$ne":
            if value == operand:
                return False
        elif op == "$exists":
            if bool(operand) != present:
                return False
        elif op == "$not":
            if _match_field(doc, field, operand):
                return False
        else:
            raise AssertionError(f"unsupported field op {op}")
    return True


def _match(doc, cond):
    """Tiny Mongo-condition evaluator: supports $and/$or/$nor + field ops."""
    for key, val in cond.items():
        if key == "$and":
            if not all(_match(doc, c) for c in val):
                return False
        elif key == "$or":
            if not any(_match(doc, c) for c in val):
                return False
        elif key == "$nor":
            if any(_match(doc, c) for c in val):
                return False
        elif not _match_field(doc, key, val):
            return False
    return True


BUCKETS = ("target", "manual", "eod", "stop", "other")


def test_exit_reason_query_selects_only_its_own_bucket():
    for raw, bucket in ALL_RAW:
        doc = {"exit_reason": raw}
        assert _match(doc, exit_reason_query(bucket)), f"{raw} should match {bucket}"
        for other in BUCKETS:
            if other == bucket:
                continue
            assert not _match(doc, exit_reason_query(other)), f"{raw} wrongly matched {other}"


def test_exit_reason_query_agrees_with_normalizer():
    for raw, _ in ALL_RAW:
        doc = {"exit_reason": raw}
        matched = [b for b in BUCKETS if _match(doc, exit_reason_query(b))]
        assert matched == [normalize_exit_reason(raw)], (raw, matched)


def test_exit_reason_query_unknown_bucket_is_none():
    assert exit_reason_query("bogus") is None
    assert exit_reason_query("") is None


def test_open_trade_matches_no_exit_reason_bucket():
    doc = {"exit_reason": None}  # OPEN trade
    for b in BUCKETS:
        assert not _match(doc, exit_reason_query(b)), b


def test_merge_conditions_appends_without_clobbering_keys():
    q = {"status": "CLOSED", "deployment_id": "d1"}
    out = merge_conditions(q, [{"direction": "CE"}])
    assert out["status"] == "CLOSED" and out["deployment_id"] == "d1"
    assert out["$and"] == [{"direction": "CE"}]


def test_merge_conditions_extends_existing_and():
    q = {"$and": [{"a": 1}]}
    out = merge_conditions(q, [{"b": 2}])
    assert out["$and"] == [{"a": 1}, {"b": 2}]


def test_merge_conditions_empty_extra_is_noop():
    assert merge_conditions({"status": "OPEN"}, []) == {"status": "OPEN"}
```

- [ ] **Step 2: Run — expect FAIL**

Run: `python -m pytest tests/test_paper_blotter_filters.py -k "exit_reason_query or merge_conditions or open_trade" -v`
Expected: FAIL (`exit_reason_query`/`merge_conditions` not defined).

- [ ] **Step 3: Implement the two pure functions**

In `backend/app/paper_analytics.py`, just after `normalize_exit_reason`, add:

```python
def exit_reason_query(bucket: str):
    """Mongo condition selecting CLOSED trades whose exit_reason is in `bucket`.

    Buckets mirror normalize_exit_reason's precedence (target > manual > eod >
    stop(not time_stop) > other) by excluding every higher-precedence substring.
    Returns None for an unknown/empty bucket (interpreted as "no filter").
    """
    def R(p):
        return {"exit_reason": {"$regex": p, "$options": "i"}}

    def notR(p):
        return {"exit_reason": {"$not": {"$regex": p, "$options": "i"}}}

    target = R("target")
    manual = {"$and": [R("manual"), notR("target")]}
    eod = {"$and": [R("eod|square|expiry"), notR("target|manual")]}
    stop = {"$and": [
        R("stop"),
        {"exit_reason": {"$ne": "time_stop"}},
        notR("target|manual|eod|square|expiry"),
    ]}
    other = {"$and": [
        {"exit_reason": {"$exists": True, "$ne": None}},
        {"$nor": [target, manual, eod, stop]},
    ]}
    return {"target": target, "manual": manual, "eod": eod, "stop": stop, "other": other}.get(bucket)


def merge_conditions(q: Dict[str, Any], extra: list) -> Dict[str, Any]:
    """Append `extra` conditions to q's `$and` list without clobbering top-level
    keys. No-op when `extra` is empty."""
    if not extra:
        return q
    existing = q.get("$and")
    q["$and"] = (list(existing) if existing else []) + list(extra)
    return q
```

(`Dict`, `Any` are already imported at the top of `paper_analytics.py`.)

- [ ] **Step 4: Run — expect PASS**

Run: `python -m pytest tests/test_paper_blotter_filters.py -v`
Expected: PASS (all tests in the file).

- [ ] **Step 5: Commit**

```bash
git add backend/app/paper_analytics.py tests/test_paper_blotter_filters.py
git commit -m "feat(paper): exit_reason_query bucket builder + merge_conditions helper"
```

---

## Task 4: Backend — wire `direction` + `exit_reason` into the list endpoint

**Files:**
- Modify: `backend/app/routers/journals.py` (`list_paper_trades`, lines 334–366)

- [ ] **Step 1: Add the two query params to the signature**

In `backend/app/routers/journals.py`, add to `list_paper_trades(...)` after the `instrument` param (line 338):

```python
    direction: Optional[str] = Query(None, description="CE or PE"),
    exit_reason: Optional[str] = Query(None, description="bucket: target|manual|eod|stop|other"),
```

- [ ] **Step 2: Build + merge the conditions after the date-range block**

In the same function, after the `created_at` date-range block (ends ~line 366) and BEFORE the `field = sort.lstrip("-")` line, insert:

```python
    extra: list = []
    if direction:
        extra.append({"direction": direction.upper()})
    if exit_reason:
        cond = paper_analytics.exit_reason_query(exit_reason)
        if cond is not None:
            extra.append(cond)
    q = paper_analytics.merge_conditions(q, extra)
```

(`paper_analytics` is already imported in `journals.py` — it is used at line 387 as `paper_analytics.per_trade_analytics`. Confirm the import; if it is imported as a bare name, keep the `paper_analytics.` prefix.)

- [ ] **Step 3: Verify the full backend suite still passes (no DB needed for the new logic; endpoint import must not break)**

Run: `python -m pytest tests/test_paper_blotter_filters.py -v`
Expected: PASS.

Run (import smoke — the endpoint module must import cleanly with the new params):
`python -c "import sys; sys.path.insert(0,'backend'); import app.routers.journals"`
Expected: no error.

- [ ] **Step 4: Commit**

```bash
git add backend/app/routers/journals.py
git commit -m "feat(paper): server-side direction + exit_reason bucket filters on /paper/trades"
```

---

## Task 5: Frontend — `exitReason.js` classifier + options

**Files:**
- Create: `frontend/src/lib/exitReason.js`

- [ ] **Step 1: Create the module**

```javascript
// Shared exit-reason classification for the Paper blotter. Maps a raw backend
// exit_reason string to {bucket, label}. Precedence (first match wins):
// target > manual > eod > stop(not time_stop) > other. Mirrors the backend
// normalize_exit_reason (paper_analytics.py) and paperAgg.normalizeExitReason —
// keep all three in lockstep.
export function classifyExitReason(raw) {
  const r = String(raw || "").toLowerCase();
  if (r.includes("target")) return { bucket: "target", label: "Target achieved" };
  if (r.includes("manual")) return { bucket: "manual", label: "Manual" };
  if (r.includes("eod") || r.includes("square") || r.includes("expiry")) return { bucket: "eod", label: "End of day" };
  if (r !== "time_stop" && r.includes("stop")) return { bucket: "stop", label: "Stoploss hit" };
  return { bucket: "other", label: "Others" };
}

// Ordered options for the Exit Reason header filter (value = backend bucket key).
export const EXIT_REASON_OPTIONS = [
  { value: "target", label: "Target achieved" },
  { value: "stop", label: "Stoploss hit" },
  { value: "eod", label: "End of day" },
  { value: "manual", label: "Manual" },
  { value: "other", label: "Others" },
];
```

- [ ] **Step 2: Verify it builds (smoke via the build at Task 9; no JS test runner exists)**

No standalone command — this module is exercised by Task 7. The bucketing logic is identical to the Python `normalize_exit_reason`, which is unit-tested in Task 2/3.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/exitReason.js
git commit -m "feat(paper): shared exit-reason classifier + filter options"
```

---

## Task 6: Frontend — reorder `normalizeExitReason` in `paperAgg.js`

**Files:**
- Modify: `frontend/src/lib/paperAgg.js` (lines 6–13)

- [ ] **Step 1: Replace the function to match the new precedence**

```javascript
export const normalizeExitReason = (reason) => {
  const r = String(reason || "").toLowerCase();
  if (r.includes("target")) return "target";
  if (r.includes("manual")) return "manual";
  if (r.includes("eod") || r.includes("square") || r.includes("expiry")) return "eod";
  if (r.includes("stop") && r !== "time_stop") return "stop";
  return "other";
};
```

(Leave `EXIT_BUCKETS` and `exitReasonBreakdown` unchanged.)

- [ ] **Step 2: Commit**

```bash
git add frontend/src/lib/paperAgg.js
git commit -m "fix(paper): align breakdown-card exit-reason buckets with the new precedence"
```

---

## Task 7: Frontend — rebuild `TradeBlotter.jsx` (17 columns, filter row, Exit Reason cell)

**Files:**
- Modify: `frontend/src/components/paper/TradeBlotter.jsx` (full rewrite of the table)

- [ ] **Step 1: Replace the whole component**

Replace the entire body of `frontend/src/components/paper/TradeBlotter.jsx` with:

```jsx
import { Fragment, useState } from "react";
import { fmtINR, fmtINRSigned, fmtNum, fmtPct, fmtDuration, colorPnL } from "@/lib/fmt";
import TradeSparkline from "./TradeSparkline";
import TradeDetailDrawer from "./TradeDetailDrawer";
import { classifyExitReason, EXIT_REASON_OPTIONS } from "@/lib/exitReason";
import { Zap } from "lucide-react";

const IST_OFFSET_MS = 330 * 60 * 1000;
const pad = (n) => String(n).padStart(2, "0");
const istParts = (iso) => {
  if (!iso) return null;
  const d = new Date(new Date(iso).getTime() + IST_OFFSET_MS);
  return { day: `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`,
           time: `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}` };
};

const COLSPAN = 17;

export default function TradeBlotter({
  rows, sort, onToggleSort, onCloseAtMarket, busy,
  selected, onToggleRow, onToggleAll, allClosedSelected,
  filters = {}, onSetFilter, strategyOptions = [],
}) {
  const [open, setOpen] = useState(() => new Set());
  const toggle = (id) => setOpen((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const mark = (col) => (sort === col ? " ▲" : sort === `-${col}` ? " ▼" : null);
  const H = ({ col, children, right }) => (
    <th className={`p-2 ${right ? "text-right" : "text-left"} ${col ? "cursor-pointer hover:text-foreground" : ""}`}
      onClick={col ? () => onToggleSort(col) : undefined}>{children}{col ? mark(col) : null}</th>
  );
  const FilterSelect = ({ k, title, children }) => (
    <select value={filters[k] || ""} onChange={(e) => onSetFilter?.(k, e.target.value)} onClick={(e) => e.stopPropagation()}
      className="w-full h-6 rounded border border-line bg-bg-2 px-1 text-[10px] text-foreground" title={title}>
      {children}
    </select>
  );
  return (
    <div className="rounded-lg border border-line bg-bg-1 overflow-x-auto" data-testid="paper-trade-blotter">
      <table className="w-full text-xs" data-testid="paper-trade-table">
        <thead className="sticky top-0 bg-bg-2 z-10">
          <tr className="text-dim border-b border-line">
            <th className="p-2 w-8 text-center">
              <input type="checkbox" checked={!!allClosedSelected} onChange={onToggleAll} data-testid="paper-select-all" title="Select closed trades on this page" />
            </th>
            <H col="created_at">Entry Date/Time</H>
            <H>Strategy / Contract</H>
            <H right>Side</H>
            <H col="entry_price" right>Entry Price</H>
            <H col="exit_price" right>Exit Price</H>
            <H col="closed_at">Exit Date/Time</H>
            <H right>Duration</H>
            <H right>Qty (lots × size)</H>
            <H right>SL / TP</H>
            <H col="mfe_value" right>Max P&amp;L</H>
            <H col="mae_value" right>Min P&amp;L</H>
            <H right>P&amp;L%</H>
            <H col="realized_pnl" right>Net P&amp;L</H>
            <H right>P&amp;L curve</H>
            <H right>Status</H>
            <H right>Exit Reason</H>
          </tr>
          <tr className="text-dim border-b border-line bg-bg-1" data-testid="paper-filter-row">
            <td className="p-1" />
            <td className="p-1" />
            <td className="p-1">
              <FilterSelect k="strategy_id" title="Filter by strategy">
                <option value="">All strategies</option>
                {strategyOptions.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </FilterSelect>
            </td>
            <td className="p-1">
              <FilterSelect k="direction" title="Filter by side">
                <option value="">All</option>
                <option value="CE">Call (CE)</option>
                <option value="PE">Put (PE)</option>
              </FilterSelect>
            </td>
            <td className="p-1" /><td className="p-1" /><td className="p-1" /><td className="p-1" />
            <td className="p-1" /><td className="p-1" /><td className="p-1" /><td className="p-1" />
            <td className="p-1" /><td className="p-1" /><td className="p-1" />
            <td className="p-1">
              <FilterSelect k="status" title="Filter by status">
                <option value="">All</option>
                <option value="OPEN">Open</option>
                <option value="CLOSED">Closed</option>
              </FilterSelect>
            </td>
            <td className="p-1">
              <FilterSelect k="exit_reason" title="Filter by exit reason">
                <option value="">All</option>
                {EXIT_REASON_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </FilterSelect>
            </td>
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 && (
            <tr><td colSpan={COLSPAN} className="p-6 text-center text-dimmer">No paper trades match these filters.</td></tr>
          )}
          {rows.map((t) => {
            const isOpen = String(t.status || "").toUpperCase() === "OPEN";
            const a = t.analytics || {};
            const entry = istParts(t.created_at);
            const exit = istParts(t.closed_at);
            const net = isOpen ? a.running_pnl : Number(t.realized_pnl || 0);
            const notional = Number(t.entry_price || 0) * Number(t.quantity || 0);
            const pct = notional ? (Number(net || 0) / notional) * 100 : null;
            const reason = isOpen ? null : classifyExitReason(t.exit_reason);
            return (
              <Fragment key={t.id}>
                <tr className="border-b border-line hover:bg-bg-2 cursor-pointer" onClick={() => toggle(t.id)} data-testid="paper-trade-row">
                  <td className="p-2 text-center" onClick={(e) => e.stopPropagation()}>
                    {!isOpen && (
                      <input type="checkbox" checked={selected?.has(t.id) || false} onChange={() => onToggleRow?.(t.id)} data-testid="paper-row-select" />
                    )}
                  </td>
                  <td className="p-2 font-mono whitespace-nowrap">{entry ? entry.day : "—"}<div className="text-dimmer">{entry ? entry.time : ""}</div></td>
                  <td className="p-2"><div className="font-medium truncate max-w-[150px]" title={t.deployment_name}>{t.deployment_name || t.strategy_id}</div><div className="text-dimmer font-mono truncate max-w-[150px]">{t.trading_symbol || t.instrument}</div></td>
                  <td className="p-2 text-right"><span className={`font-mono ${t.direction === "CE" ? "text-emerald-400" : t.direction === "PE" ? "text-red-400" : "text-dim"}`}>{t.direction || "—"}</span></td>
                  <td className="p-2 text-right font-mono">{fmtNum(t.entry_price)}</td>
                  <td className="p-2 text-right font-mono">{t.exit_price != null ? fmtNum(t.exit_price) : (isOpen ? "live" : "—")}</td>
                  <td className="p-2 font-mono whitespace-nowrap">{exit ? exit.day : "—"}<div className="text-dimmer">{exit ? exit.time : ""}</div></td>
                  <td className="p-2 text-right font-mono text-dim">{fmtDuration(a.duration_s)}</td>
                  <td className="p-2 text-right font-mono whitespace-nowrap">{t.quantity != null ? fmtNum(t.quantity) : "—"}<div className="text-dimmer">{t.lots != null && t.lot_size != null ? `${t.lots} × ${t.lot_size}` : ""}</div></td>
                  <td className="p-2 text-right font-mono text-dimmer whitespace-nowrap">{a.sl ?? "—"} / {a.tp ?? "—"}</td>
                  <td className="p-2 text-right font-mono text-success">{fmtINRSigned(a.mfe_value)}</td>
                  <td className="p-2 text-right font-mono text-danger">{fmtINRSigned(a.mae_value)}</td>
                  <td className={`p-2 text-right font-mono ${colorPnL(pct)}`}>{pct == null ? "—" : fmtPct(pct, 1)}</td>
                  <td className={`p-2 text-right font-mono ${colorPnL(net)}`}>{fmtINRSigned(net)}</td>
                  <td className="p-2 text-right"><div className="flex justify-end"><TradeSparkline points={a.spark} /></div></td>
                  <td className="p-2 text-right"><span className={`text-[10px] px-1.5 py-0.5 rounded border font-mono ${isOpen ? "border-emerald-500/40 text-emerald-300" : "border-line text-dim"}`}>{t.status}</span></td>
                  <td className="p-2 text-right" onClick={(e) => e.stopPropagation()}>
                    {isOpen ? (
                      <button disabled={busy} onClick={() => onCloseAtMarket(t)} className="h-7 text-[11px] bg-bg-3 border border-line hover:bg-bg-2 px-2 rounded inline-flex items-center" data-testid="close-paper-trade" title="Close at last live mark">
                        <Zap className="w-3 h-3 mr-1" /> @ market
                      </button>
                    ) : (
                      <span className="text-[10px] px-1.5 py-0.5 rounded border border-line text-dim" title={t.exit_reason || ""}>{reason ? reason.label : "—"}</span>
                    )}
                  </td>
                </tr>
                {open.has(t.id) && (
                  <tr><td colSpan={COLSPAN} className="p-0"><TradeDetailDrawer trade={t} /></td></tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
```

Note: `fmtSigned` is no longer imported (the R column used it); confirm no other usage remains in this file.

- [ ] **Step 2: Build to verify it compiles**

Run: `cd frontend && npx craco build` (expect `Compiled successfully`). The blotter is also wired in Task 8; if the build complains about `filters`/`onSetFilter` being undefined that is fine until Task 8 supplies them (props default to `{}`/noop, so build still passes).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/paper/TradeBlotter.jsx
git commit -m "feat(paper): 17-column blotter with header filter row + Exit Reason column"
```

---

## Task 8: Frontend — wire `PaperTrading.jsx` (filter state, params, strategyOptions, move status to header)

**Files:**
- Modify: `frontend/src/pages/PaperTrading.jsx`

- [ ] **Step 1: Add `direction` + `exit_reason` to filter state**

In the `filters` `useState` (lines 69–76) add two keys:

```jsx
  const [filters, setFilters] = useState({
    deployment_id: searchParams.get("deployment") || "",
    instrument: "",
    status: "",
    strategy_id: "",
    direction: "",
    exit_reason: "",
    date_from: "",
    date_to: "",
  });
```

- [ ] **Step 2: Include them in the table `params` useMemo**

In the `params` useMemo (lines 83–92), after the `status` line add:

```jsx
    if (filters.direction) p.direction = filters.direction;
    if (filters.exit_reason) p.exit_reason = filters.exit_reason;
```

(Do NOT add them to `statsParams` — the calendar/breakdown keep the broader scope, per spec §5.1.)

- [ ] **Step 3: Compute `strategyOptions` from deployments**

Near the existing `activeDeployments` memo (~line 403), add:

```jsx
  const strategyOptions = useMemo(() => {
    const seen = new Map();
    for (const d of deployments) {
      if (String(d.status || "").toUpperCase() === "ARCHIVED") continue;
      const sid = d.strategy_id;
      if (!sid || seen.has(sid)) continue;
      seen.set(sid, d.name || sid);
    }
    return [...seen.entries()]
      .map(([value, label]) => ({ value, label }))
      .sort((a, b) => a.label.localeCompare(b.label));
  }, [deployments]);
```

(If a deployment object has no `strategy_id` field, fall back to grouping by `d.id`/`d.name`; verify the deployment shape by logging one, then adjust. The filter sets `filters.strategy_id`, which the backend already supports.)

- [ ] **Step 4: Remove the status `<select>` from the top bar**

Delete the status filter block (lines 493–498):

```jsx
          <select value={filters.status} onChange={(e) => setFilter("status", e.target.value)}
            className="h-7 rounded-md border border-input bg-bg-2 px-2 text-xs text-foreground" data-testid="paper-status-filter">
            <option value="">All statuses</option>
            <option value="OPEN">OPEN</option>
            <option value="CLOSED">CLOSED</option>
          </select>
```

(Status now lives in the blotter header filter row. `filters.status` state + its param are unchanged.)

- [ ] **Step 5: Pass the new props to `TradeBlotter`**

Update the `<TradeBlotter ... />` usage (lines 542–543):

```jsx
      <TradeBlotter rows={data.items} sort={sort} onToggleSort={toggleSort} onCloseAtMarket={closeAtMarket} busy={busy}
        selected={selected} onToggleRow={toggleRow} onToggleAll={toggleAll} allClosedSelected={allClosedSelected}
        filters={filters} onSetFilter={setFilter} strategyOptions={strategyOptions} />
```

- [ ] **Step 6: Build**

Run: `cd frontend && npx craco build`
Expected: `Compiled successfully`.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/pages/PaperTrading.jsx
git commit -m "feat(paper): wire blotter header filters (direction/exit_reason) + move status to header"
```

---

## Task 9: Verify end-to-end + deploy to the running stack

**Files:** none (verification)

- [ ] **Step 1: Full backend suite**

Run: `python -m pytest tests/ -q`
Expected: all pass (no regressions; new `test_paper_blotter_filters.py` green). Fix any pre-existing test that asserted the old `manual_square_off`/`time_stop` buckets (Task 2 Step 5).

- [ ] **Step 2: Frontend build**

Run: `cd frontend && npx craco build`
Expected: `Compiled successfully`.

- [ ] **Step 3: Rebuild the running containers**

Run: `docker compose up -d --build frontend`
(The backend `normalize_exit_reason` + endpoint changes also need the backend image; run `docker compose up -d --build backend frontend`.)
Then confirm: `docker ps` shows both healthy and `GET http://localhost:8001/api/health` → 200.

- [ ] **Step 4: Chrome live-verify** (via the Chrome MCP, against a deployment with closed trades)

Confirm, on `http://localhost:3000/paper`:
- The blotter shows the 17 columns in order, including Entry Price, Exit Price, Exit Date/Time, Qty (with `lots × size` beneath), and Exit Reason.
- Closed trades show the right Exit Reason label (a `manual_square_off` reads **Manual**, an `auto_square_off_15_00_IST` reads **End of day**).
- Open trades show the `@ market` close button in the Exit Reason column and it still closes the trade.
- The status filter is gone from the top bar and present in the header; deployment/instrument/date remain in the top bar.
- Each header filter (Side, Status, Exit Reason, Strategy) changes the result set: pick a value and confirm the row count / `total` updates (server-side — check the pagination count text).

- [ ] **Step 5: Final commit (if any verification fixes were needed)**

```bash
git add -A
git commit -m "test(paper): verify blotter redesign end-to-end on the running stack"
```

---

## Self-Review notes

- **Spec coverage:** §3 columns → Task 7; §4 classifier → Tasks 2/3/5/6; §5.1 frontend filters → Tasks 7/8; §5.2 backend filters + sort → Tasks 1/3/4; §7 testing → Tasks 1–4 (backend) + Task 9 (frontend build/Chrome); §6 files all covered.
- **Frontend unit tests:** none added — the repo has no JS test runner and verifies UI via build + Chrome (prior Paper-redesign convention). The exit-reason LOGIC is unit-tested on the Python side (Tasks 2–3), and `exitReason.js`/`paperAgg.js` mirror it 1:1; the parity is checked visually in Task 9 Step 4.
- **Type/name consistency:** `exit_reason_query`, `merge_conditions`, `normalize_exit_reason` (Python); `classifyExitReason`, `EXIT_REASON_OPTIONS`, `normalizeExitReason` (JS); blotter props `filters`/`onSetFilter`/`strategyOptions` used identically in Tasks 7 and 8.
