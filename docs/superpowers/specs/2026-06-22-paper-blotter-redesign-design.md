# Paper Trade Blotter Redesign — Design Spec

**Date:** 2026-06-22
**Status:** Draft (adversarial audit applied — see §10)
**Area:** Paper Trading page → trade list pane (blotter)

## 1. Goal

Restructure the Paper Trade blotter columns and add per-column header filters,
per the user's request. Concretely: split entry/exit into separate columns, add
exit price / exit time / lot size, drop the R column, replace the Actions column
with an Exit Reason column, and add server-side categorical filters surfaced as
per-column header dropdowns.

## 2. Current state (facts)

- **Frontend blotter:** `frontend/src/components/paper/TradeBlotter.jsx`
  - Columns today (16 total): ☐ select · Date/time (`created_at`) · Strategy/contract ·
    Side (`direction`) · Entry→Exit (`entry_price`→`exit_price`, one cell) · Dur · SL/TP ·
    Max (`mfe_value`) · Min (`mae_value`) · Now (`running_pnl`) · R (`r_multiple`) ·
    P&L curve (sparkline) · Net P&L (`realized_pnl`) · P&L% (computed) · Status · Actions.
  - `colSpan="16"` is used in the empty-state row (line 51) and the detail-drawer row (line 92).
  - There is **no Lot-size column and no Exit-Date/Time column today**: exit time
    (`closed_at`) is not shown at all, and entry/exit price share the single
    `Entry→Exit` cell (line 72).
  - Per-trade fields come from `t.*` plus `t.analytics.{duration_s, sl, tp,
    mfe_value, mae_value, running_pnl, r_multiple, spark}`.
  - `net = isOpen ? a.running_pnl : Number(t.realized_pnl||0)`; `pct = net/(entry_price*quantity)*100`.
  - Props today: `rows, sort, onToggleSort, onCloseAtMarket, busy, selected,
    onToggleRow, onToggleAll, allClosedSelected` — **no** filter props.
- **Parent page:** `frontend/src/pages/PaperTrading.jsx`
  - `filters` state (lines 69–76): `{deployment_id, instrument, status, strategy_id,
    date_from, date_to}` — **no `direction`, no `exit_reason`**.
  - Default table sort is `-created_at` (line 77). The backend endpoint's own
    default when a bad/absent sort is given is `-updated_at`.
  - Filtering/sorting/pagination is **server-side**: `fetchRows` builds `params`
    (useMemo, lines 83–92) and sends them plus `sort`, `skip`, `limit`,
    `include_analytics:true` to the trades endpoint, rendering one page
    (`data.items`). The blotter must NOT filter client-side (it would only filter
    the visible page).
  - The top filter bar (lines 477–503) holds dropdowns for deployment / instrument /
    status + a date range; the **status `<select>` is at lines 493–498**. A strategy
    chip (lines 469–475) clears `strategy_id`. `toggleSort(field)` (lines 220–224)
    cycles `field`/`-field`. `setFilter(k,v)` (line 197) resets `skip` + selection.
  - `api.listPaperTrades(params)` (`frontend/src/lib/api.js:156–157`) forwards
    `params` verbatim as query string, so new params need no api.js change.
- **Backend list endpoint:** `GET /api/paper/trades` in
  `backend/app/routers/journals.py` (`list_paper_trades`, lines 333–392).
  - Accepts `status, deployment_id, strategy_id, instrument, date_from, date_to,
    sort, skip, limit, format, include_analytics` (signature lines 334–346).
  - Sort allowlist `_TRADES_SORT_FIELDS` (`backend/app/runtime.py:897`):
    `{updated_at, created_at, closed_at, realized_pnl, entry_price, mfe_value,
    mae_value}`. Invalid sort field → defaults to `-updated_at` (lines 368–371).
    `closed_at` is **already** allowlisted; `exit_price` is **not**.
  - **No `direction` filter and no `exit_reason` filter exist yet.**
- **Trade data model** (`backend/app/paper_trading.py`): a trade has
  `created_at, closed_at, direction, lots, lot_size, quantity, entry_price,
  exit_price, realized_pnl, unrealized_pnl, status, strategy_id, deployment_id,
  trading_symbol, instrument, exit_reason` (closed only), `risk.stop_price`,
  `risk.target_price`. `deployment_name` is joined in by the endpoint (lines 377–383),
  not stored on the trade. `quantity = lots * lot_size` (paper_trading.py:91). OPEN
  trades have null `exit_price/closed_at/exit_reason`.
- **Exit reason raw values** (written by backend) — verified at source:
  - `target_hit`, `spot_target_hit` (`execution_policy.py:92–94,157–158`)
  - `stop_hit`, `spot_stop_hit` (same)
  - `time_stop` (`paper_auto.py:605`)
  - `auto_square_off_15_00_IST` (`paper_squareoff.py:106`, `runtime.py:183`)
  - `manual_square_off` (manual square-off endpoint, `journals.py:328`)
  - `manual_close_at_market` (manual single-trade close — frontend passes this
    `reason`, stored verbatim by `close_trade`, `journals.py:478`)
  - Both manual variants contain the substring `manual`; both auto/EOD variants
    contain `square`; both stop variants contain `stop` but are **not** the literal
    `time_stop`. This substring structure is what the classifier and the Mongo
    bucket queries below rely on.

## 3. Target column layout (17 columns, left → right)

| # | Header | Source field | Sortable? | Filter? |
|---|--------|--------------|-----------|---------|
| 1 | ☐ select | — | no | no |
| 2 | Entry Date/Time | `created_at` | yes (`created_at`) | no |
| 3 | Strategy / Contract | `deployment_name` + `trading_symbol` | no | yes (strategy) |
| 4 | Side | `direction` | no | yes (CE/PE) |
| 5 | Entry Price | `entry_price` | yes (`entry_price`) | no |
| 6 | Exit Price | `exit_price` (OPEN → `live`) | yes (`exit_price`) | no |
| 7 | Exit Date/Time | `closed_at` (OPEN → `—`) | yes (`closed_at`) | no |
| 8 | Duration | `analytics.duration_s` | no | no |
| 9 | Qty (lots × size) | `lots` × `lot_size` → `quantity` | no | no |
| 10 | SL / TP | `analytics.sl` / `analytics.tp` | no | no |
| 11 | Max P&L | `mfe_value` | yes (`mfe_value`) | no |
| 12 | Min P&L | `mae_value` | yes (`mae_value`) | no |
| 13 | P&L % | computed | no | no |
| 14 | Net P&L | `realized_pnl` / `running_pnl` | yes (`realized_pnl`) | no |
| 15 | P&L curve | `analytics.spark` | no | no |
| 16 | Status | `status` | no | yes (Open/Closed) |
| 17 | Exit Reason | `exit_reason` (OPEN → close button) | no | yes (5 buckets) |

**Removed vs today:** R (`r_multiple`), Now (standalone `running_pnl` — Net P&L
already shows live P&L for open trades), Actions (folded into Exit Reason).
**Renamed:** Date/time → Entry Date/Time; Dur → Duration; Max → Max P&L; Min → Min P&L.
**Added:** Exit Price, Exit Date/Time, Qty, Exit Reason.
**Split:** Entry→Exit (one cell) → Entry Price + Exit Price (two cells).

**Column count math:** 1 select cell + 16 data cells = **17 total**. Both the
empty-state row and the detail-drawer row update `colSpan` **16 → 17** (lines 51, 92).

**Cell-content decisions:**
- **Col 6 Exit Price:** CLOSED → `fmtNum(exit_price)`. OPEN → `live` (matches the
  current Entry→Exit behaviour where the exit half rendered `live` for open trades).
- **Col 7 Exit Date/Time:** CLOSED → IST day + HH:MM of `closed_at`. OPEN → `—`.
- **Col 9 Qty:** render `quantity` as the headline number with the composition
  beneath, i.e. `"{quantity}"` on the first line and `"{lots} × {lot_size}"`
  dimmed beneath. Header label: **Qty (lots × size)**. (`quantity` is the source of
  truth — `quantity = lots * lot_size`; `lots`/`lot_size` are shown only as the
  breakdown.)
- **Col 13 P&L %:** `net / (entry_price * quantity) * 100`, `—` when notional is 0
  (unchanged from today).

## 4. Exit Reason — labels, classifier, close action

A single shared classifier maps a raw `exit_reason` string to one bucket + label.
It lives in the NEW module `frontend/src/lib/exitReason.js` as
`classifyExitReason(raw) → {bucket, label}` and is used by BOTH the blotter display
cell and the filter-dropdown option list so they never diverge. First match wins,
in this exact order:

1. matches `/target/i` → bucket `target`, label **Target achieved**
2. matches `/manual/i` → bucket `manual`, label **Manual**
3. matches `/square|eod|expiry/i` → bucket `eod`, label **End of day**
4. `reason !== "time_stop"` **and** matches `/stop/i` → bucket `stop`, label **Stoploss hit**
5. else → bucket `other`, label **Others**

**Order matters — `manual` is checked BEFORE `eod`.** The real raw value
`manual_square_off` (manual square-off endpoint, `journals.py:328`) contains BOTH
`manual` and `square`; it must classify as **Manual** (a user-initiated close), not
End of day. Only the automatic `auto_square_off_15_00_IST` (no `manual` substring) is
**End of day**.

`time_stop` deliberately falls to **Others** (step 5) — it is a time-based exit,
not a price stop. The literal-equality carve-out in step 4 (`reason !== "time_stop"`)
is what diverts it; `time_stop` matches none of steps 1–3, so it lands in `other`.

**Consistency proof (all raw values resolve to exactly one bucket).** Applying the
classifier above and the §5.2 Mongo bucket queries to every raw value from §2:

| raw `exit_reason` | classifier bucket | Mongo bucket query that matches |
|---|---|---|
| `target_hit` | target | target |
| `spot_target_hit` | target | target |
| `auto_square_off_15_00_IST` | eod | eod |
| `manual_square_off` | manual | manual |
| `manual_close_at_market` | manual | manual |
| `stop_hit` | stop | stop |
| `spot_stop_hit` | stop | stop |
| `time_stop` | other | other |

Two raw values match multiple substrings — `manual_square_off` matches both `manual`
and `square`, and the `stop` variants must be guarded against `time_stop` — so
**precedence matters**. The classifier resolves these by step order; the §5.2 Mongo
bucket queries reproduce that exact precedence with explicit guards (each bucket
excludes the substrings of every higher-precedence bucket). With those guards every
raw value matches exactly one bucket query, identical to the classifier.

**Pre-existing analytics normalizers must be aligned.** Two functions already bucket
exit reasons for the Exit-Reason breakdown card and currently use the order
`target → stop → eod → manual`, which mis-buckets TWO real values:
- `manual_square_off` → `eod` (the `square` substring wins before `manual`) — wrong;
  it is a user-initiated square-off, not End of day.
- `time_stop` → `stop` (no carve-out) — wrong; it is a time exit, not a price stop.

Both are reordered to the new classifier precedence
(`target → manual → eod → stop(¬time_stop) → other`) so the breakdown card and the new
filter agree on every value:
- `backend/app/paper_analytics.py::normalize_exit_reason` (lines 50–60): reorder to
  target, then manual, then eod, then `if "stop" in r and r != "time_stop": return "stop"`, else other.
- `frontend/src/lib/paperAgg.js::normalizeExitReason` (lines 6–13): the same reorder.

NOTE — this changes the Exit-Reason breakdown card's bucket counts:
`manual_square_off` moves End-of-day → Manual, and `time_stop` moves Stop → Other.
Both are corrections. The new `classifyExitReason` and these two normalizers must
produce identical buckets for all raw values; a parity test asserts this (§7).

**Exit Reason cell behaviour:** OPEN trade → render the existing "Close @ market"
button (preserves the close action that lived in the old Actions column). CLOSED
trade → render the exit-reason label as a subtle badge. This is the one column that
carries both the action (while open) and the outcome (once closed).

**Close-button styling (unchanged from the old Actions cell):** `h-7 text-[11px]
bg-bg-3 border border-line hover:bg-bg-2 px-2 rounded inline-flex items-center`,
disabled while `busy`, `title="Close at last live mark"`, icon `Zap` (`w-3 h-3 mr-1`),
label `@ market`. The cell stops click propagation so the row-expand toggle does not
fire. The CLOSED badge uses `text-[10px] px-1.5 py-0.5 rounded border border-line
text-dim` to match the existing Status badge weight.

## 5. Filtering

### 5.1 Frontend (per-column header dropdowns)
- A dedicated **filter row** is a separate `<tr>` placed directly **after** the
  sort-header `<tr>`, both inside `<thead>`. Non-filterable columns render an empty
  `<td>`; filterable columns render a `<select>` whose first option is `All`
  (empty value). Column-to-control mapping (17 cells):

  `[1 ☐ empty] [2 entry-date empty] [3 Strategy select] [4 Side select]
  [5 entry-price empty] [6 exit-price empty] [7 exit-date empty] [8 dur empty]
  [9 qty empty] [10 sl/tp empty] [11 max empty] [12 min empty] [13 pct empty]
  [14 net empty] [15 spark empty] [16 Status select] [17 Exit Reason select]`

  - **Strategy / Contract** → `filters.strategy_id`. Options are the distinct
    `strategy_id` values present in the loaded `deployments` list, excluding
    deployments whose `status` is `ARCHIVED`, sorted alphabetically by deployment
    `name`; each option's `value` is the `strategy_id` and its label is the
    deployment `name` (fallback `strategy_id`). (`strategy_id`, not `deployment_id`,
    keeps this filter consistent with the existing strategy chip and the per-strategy
    StrategyStatsTable row-click, both of which set `strategy_id`.)
  - **Side** → `filters.direction`. Options: `All` (empty) / `CE` / `PE`. Values are
    sent uppercase; display labels are `All` / `Call (CE)` / `Put (PE)`.
  - **Status** → `filters.status`. Options: `All` (empty) / `OPEN` / `CLOSED`.
  - **Exit Reason** → `filters.exit_reason`. Options come from
    `EXIT_REASON_OPTIONS` exported by `exitReason.js`: `All` (empty) plus the five
    buckets `target / stop / eod / manual / other`, each shown with its classifier
    label (Target achieved / Stoploss hit / End of day / Manual / Others). The
    `value` sent to the backend is the bucket key.
- `TradeBlotter` gains props: `filters`, `onSetFilter(key, value)`, and
  `strategyOptions` (array of `{value, label}`). It stays presentational — each
  `<select>` calls `onSetFilter(key, value)`, which is the parent's existing
  `setFilter` (resets `skip`, clears selection, refetches).
- `PaperTrading.jsx` adds `direction` and `exit_reason` to `filters` state and
  conditionally includes them in the table `params` useMemo (omit when empty, like
  the other optional filters). It computes `strategyOptions` from the loaded
  `deployments` (per the rule above) and passes `filters`, `setFilter` (as
  `onSetFilter`), and `strategyOptions` to `TradeBlotter`.
- The **top bar** keeps deployment / instrument / date (no header column for those).
  The standalone **status** `<select>` is **removed from the top bar (lines 493–498)**;
  this is a pure DOM move — `filters.status` state and its API param are unchanged,
  only the control's location changes. The strategy chip behaviour is preserved
  (clicking a strategy-stats row still sets `strategy_id`, now also reflected in the
  header Strategy dropdown).
- The stats fetch (`statsParams`, PaperTrading.jsx:96–104) is **not** extended with
  `direction`/`exit_reason`: the per-day P&L calendar, per-deploy fallback, and
  exit-reason breakdown card intentionally reflect the broader deployment/instrument/
  date set, exactly as today. Only the paginated table `params` carry the new filters.
- **Select-all interaction:** the header `Select all` checkbox continues to operate
  only on CLOSED trades on the **current visible page** (`closedVisibleIds`,
  PaperTrading.jsx:211–217), independent of which header filters are active. No
  change is required here.

### 5.2 Backend (`list_paper_trades`)
Add two optional query params to the signature (after `date_to`):
- `direction: Optional[str] = Query(None)` — exact match on `direction`, uppercased
  ("CE"/"PE"): contributes the condition `{"direction": direction.upper()}`.
- `exit_reason: Optional[str] = Query(None)` — a **bucket key**
  (`target|stop|eod|manual|other`), translated to a Mongo condition (regex on the
  stored `exit_reason`), NOT a raw value:
  Each bucket excludes the substrings of every HIGHER-precedence bucket
  (target > manual > eod > stop), reproducing the classifier precedence exactly. Let
  `R(p) = {exit_reason: {$regex: p, $options: "i"}}` and
  `notR(p) = {exit_reason: {$not: {$regex: p, $options: "i"}}}`:
  - `target`: `R("target")`
  - `manual`: `{$and: [R("manual"), notR("target")]}`
  - `eod`: `{$and: [R("eod|square|expiry"), notR("target|manual")]}`
  - `stop`: `{$and: [R("stop"), {exit_reason: {$ne: "time_stop"}}, notR("target|manual|eod|square|expiry")]}`
  - `other`: `{$and: [{exit_reason: {$exists: true, $ne: null}}, {$nor: [<target>, <manual>, <eod>, <stop>]}]}`
    where each `<bucket>` is that bucket's full condition above. A `time_stop` row
    fails every positive bucket (stop is guarded by `$ne: "time_stop"`), so `$nor`
    admits it → it lands in `other`.
  - This builder lives in `backend/app/paper_analytics.py` as
    `exit_reason_query(bucket) → dict | None` (next to `normalize_exit_reason`), so it
    is importable and unit-testable without the DB. Unknown bucket → `None` (no filter).
  - The regexes use plain substring matching (no word-boundary anchors). This is
    intentional and matches the frontend classifier and the verified raw values; the
    only literal-equality test in the whole scheme is the `time_stop` carve-out.
  - An unrecognised bucket key contributes no condition (treated as "no filter"),
    mirroring how an empty value is handled.
- **Composition.** The new conditions are accumulated, not assigned by key, so they
  never clobber the existing `status/deployment_id/strategy_id/instrument/created_at`
  keys (and `other`/`stop` carry their own nested `$and`/`$nor`). Concretely: build
  `extra = []`; append the `direction` condition if set; append the `exit_reason`
  bucket condition if set. Then merge into `q`:
  if `extra` is non-empty, set `q["$and"] = extra` when `q` has no existing `$and`,
  otherwise extend the existing `q["$and"]` list. (Equivalently, a small
  `_merge_conditions(q, extra)` helper.) This keeps the simple equality filters as
  top-level keys and the categorical ones in the `$and` accumulator.
- An `exit_reason` filter implicitly restricts to CLOSED trades (open trades have a
  null `exit_reason`, excluded by every bucket's `$exists`/regex); this is acceptable
  and intended. Combining `exit_reason` with `status=OPEN` therefore yields no rows —
  acceptable.

Add `exit_price` to `_TRADES_SORT_FIELDS` (`backend/app/runtime.py:897`) so the new
Exit Price column is sortable (consistent with Entry Price):
`{"updated_at", "created_at", "closed_at", "realized_pnl", "entry_price",
"exit_price", "mfe_value", "mae_value"}`. `closed_at` is already present, so the new
Exit Date/Time column is already sortable. **No other sort fields change.** The
table's default sort stays `-created_at` (frontend); the endpoint's fallback stays
`-updated_at`.

## 6. Files touched

- `backend/app/routers/journals.py` — new `direction` + `exit_reason` params (lines
  334–346) and the accumulated-`$and` query build (after line 366).
- `backend/app/runtime.py` — add `exit_price` to `_TRADES_SORT_FIELDS` (line 897).
- `backend/app/paper_analytics.py` — reorder `normalize_exit_reason` to
  target→manual→eod→stop(¬time_stop)→other (fixes `manual_square_off` + `time_stop`);
  add `exit_reason_query(bucket)`, the precedence-guarded Mongo condition builder.
- `frontend/src/lib/exitReason.js` — NEW: `classifyExitReason(raw) → {bucket, label}`
  and `EXIT_REASON_OPTIONS` (ordered `[{value,label}]`) for the filter dropdown.
- `frontend/src/lib/paperAgg.js` — reorder `normalizeExitReason` to
  target→manual→eod→stop(¬time_stop)→other (matches the backend + new classifier).
- `frontend/src/components/paper/TradeBlotter.jsx` — column set/order (17 cols),
  the header filter row, the Exit Reason cell, `colSpan` 16→17 (lines 51, 92), and
  the new `filters`/`onSetFilter`/`strategyOptions` props.
- `frontend/src/pages/PaperTrading.jsx` — add `direction` + `exit_reason` to filter
  state and table query params; compute `strategyOptions`; pass
  `filters`/`setFilter`/`strategyOptions` to the blotter; remove the top-bar status
  `<select>` (lines 493–498).

## 7. Testing

- **Backend:** new tests in the paper-trades list test module covering:
  `direction=CE/PE` filtering; each `exit_reason` bucket
  (target/stop/eod/manual/other) returns the right trades against a fixture that
  includes every raw value from §2; `time_stop` lands in `other` and is excluded from
  `stop`; `other` excludes target/stop/eod/manual; `exit_price` is an accepted sort
  field (and an unknown field still falls back to `-updated_at`); the categorical
  filters compose correctly with `status`/`deployment_id` (the `$and` accumulator does
  not clobber existing keys). Mirror existing endpoint tests.
- **Classifier parity:** a unit test asserts that for every raw value in §2,
  `classifyExitReason` (frontend), `normalizeExitReason` (frontend `paperAgg.js`),
  `normalize_exit_reason` (backend), and the backend bucket-query selection all agree
  on the same single bucket.
- **Frontend:** `yarn build` passes; rebuild the frontend Docker image; live-verify
  in Chrome against a deployment with closed trades — confirm the 17 columns render
  (Exit Price, Exit Date/Time, Qty, Exit Reason), the Exit Reason labels are correct,
  the close button still works on open trades, the status control now lives in the
  header (and is gone from the top bar), and each header filter actually narrows the
  server result (page count changes).

## 8. Out of scope / non-goals

- No numeric range filters (Duration, P&L%, P&L) — sort-only, per decision.
- No changes to the detail drawer, P&L calendar, or per-strategy stats.
- **CSV export is unchanged.** `_TRADES_CSV_COLUMNS` (runtime.py:900–904) already
  includes `exit_price` and `exit_reason`; the new display-only columns (Exit
  Date/Time, Qty breakdown) are NOT added to the CSV, and `_TRADES_CSV_COLUMNS` is not
  modified by this change.
- No new sort fields beyond `exit_price`.
- No change to how trades are opened/closed or how `exit_reason` is written.
- No change to the stats/calendar/breakdown data set (those keep the existing
  deployment/instrument/date scope and ignore the new categorical filters).

## 9. Risks

- **Exit-reason regex buckets** are the most error-prone piece (especially `other`
  via `$nor`, and the `time_stop` carve-out). Covered by backend + classifier-parity
  tests (§7).
- **Query composition**: the bucket condition must not clobber existing query keys;
  use the `$and` accumulator. Covered by a compose test.
- **Status moves to header**: ensure no code path still reads the removed top-bar
  control; `filters.status` semantics are unchanged.
- **Column width creep**: 17 columns may need horizontal scroll (blotter already has
  `overflow-x-auto`). The header filter row scrolls with the table (it is inside the
  same `<thead>`), so the `<select>` controls stay aligned under their columns on
  narrow viewports. Acceptable.

## 10. Adversarial audit resolutions

- **Applied — `time_stop` carve-out (codebase-accuracy #1, logic #1, ambiguity #1, completeness #4):** verified both `paper_analytics.py:50–60` and `paperAgg.js:6–13` mis-bucket `time_stop` as `stop`; added the `reason != "time_stop"` carve-out to the new `classifyExitReason` and to both existing normalizers so the filter and the breakdown card agree.
- **Applied — backend `direction`/`exit_reason` params (logic #2, completeness #1/#2):** verified `list_paper_trades` (journals.py:334–346) accepts neither; added both as optional `Query(None)` params with the §5.2 query build.
- **Applied — `exit_price` sort allowlist (logic #3, completeness #6):** verified `_TRADES_SORT_FIELDS` (runtime.py:897) lacks `exit_price` (but already has `closed_at`); added `exit_price`.
- **Applied — frontend filter state + blotter props (logic #4, completeness #5):** verified `filters` (PaperTrading.jsx:69–76) lacks `direction`/`exit_reason` and the blotter gets no filter props; added state, params plumbing, header filter row, and `filters`/`onSetFilter`/`strategyOptions` props.
- **Applied — 17-column layout + Exit Reason cell + colSpan (logic #5, completeness #3, ambiguity #5):** verified 16 columns and `colSpan="16"` (lines 51, 92), no Lot-size/Exit-Date/Time columns; specified the 17-column split and `colSpan` 16→17 (1 select + 16 data).
- **Applied — filter-row layout precision (ambiguity #4):** specified a separate `<tr>` after the sort-header inside `<thead>`, empty `<td>` for non-filterable columns, `<select>` with `All` first, and the full per-column mapping.
- **Applied — `$and` accumulation precision (ambiguity #6):** specified building an `extra` list and merging into `q["$and"]` so categorical filters never clobber the equality keys.
- **Applied — Qty column choice (ambiguity #7, completeness #9):** chose `quantity` as the headline with a `lots × lot_size` breakdown line; header `Qty (lots × size)`.
- **Applied — Side case (ambiguity #9):** values uppercase `CE`/`PE`, labels `Call (CE)`/`Put (PE)`.
- **Applied — status move semantics (ambiguity #10, completeness #7):** clarified it is a pure DOM move of lines 493–498; state/param unchanged.
- **Applied — null OPEN exit cells (completeness #10):** Exit Price → `live`, Exit Date/Time → `—` for OPEN trades.
- **Applied — close-button styling (ambiguity #8):** documented the existing classes/icon/tooltip/disabled-on-busy.
- **Applied — default sort confirmation (completeness #12):** confirmed table default stays `-created_at` (frontend) / `-updated_at` fallback (endpoint); corrected the spec's earlier conflation of the two.
- **Applied — CSV scope (completeness #11):** confirmed CSV already carries `exit_price`/`exit_reason`; stated `_TRADES_CSV_COLUMNS` is untouched and new columns are display-only.
- **Applied — regex anchoring clarification (completeness #14):** chose substring matching (no word boundaries), documented as intentional and consistent with the verified raw values.
- **Applied — select-all vs filters (completeness #15):** clarified `Select all` always targets CLOSED rows on the visible page regardless of header filters.
- **Applied — mobile/filter-row scroll (completeness #13):** noted the filter row lives in the same `<thead>` and scrolls with `overflow-x-auto`, keeping controls aligned.
- **Rejected — switch Strategy filter to `deployment_id` (ambiguity #3):** the existing strategy chip and StrategyStatsTable both set `strategy_id`; switching to `deployment_id` would split filter semantics and expand scope. Kept `strategy_id`; resolved the open questions (distinct `strategy_id` from non-archived deployments, sorted by name, value=`strategy_id`) instead.
- **Rejected — add Exit Date/Time + Qty to CSV (completeness #11 alt):** out of scope per §8; CSV columns are deliberately frozen.
