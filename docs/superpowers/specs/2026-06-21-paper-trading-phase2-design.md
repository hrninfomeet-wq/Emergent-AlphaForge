# Paper Trading redesign — Phase 2 design spec

- Date: 2026-06-21
- Status: Draft for review
- Depends on: Phase 1 ([2026-06-21-paper-trading-redesign-design.md](2026-06-21-paper-trading-redesign-design.md), PR #1 on `feat/paper-analytics-redesign`)
- Route: `/paper`

## 1. Context

Phase 1 shipped the analytics dashboard (account value, equity curve, period P&L,
per-strategy stats, redesigned blotter with MFE/MAE + sparkline + detail drawer).
Phase 2 adds the five items deliberately deferred from Phase 1, all extending the
same files (`paper_analytics.py`, `routers/journals.py`, the `components/paper/`
set). No new architecture — backend computes, frontend renders.

## 2. Goals (the five deferred items)

1. **Forward-vs-backtest drift** per deployed strategy.
2. **R-multiple** — per-trade column + Avg R in the per-strategy table.
3. **Exit-reason breakdown** — global card + per-strategy mini.
4. **Selection checkboxes** back in the blotter (closed-only) → delete-selected.
5. **Monthly P&L bars** in the P&L calendar card.

## 3. Locked decisions

- **Drift baseline = OPTION-₹ from the pinned source**, compared on **win-rate and
  avg ₹/trade** (NOT profit factor — the source stores no option PF). PF stays
  live-only in the table.
- **Exit-reason = BOTH** a global card (respects the page filters) and a
  per-strategy mini breakdown.
- **R-multiple** = `realized_pnl ÷ risk_amount` per trade (shown "—" when
  `risk_amount` is absent: fixed-lots / legacy trades) + **Avg R** (mean over
  trades that have R) in the per-strategy table.
- Monthly bars live inside the existing `PnlCalendar` card; selection checkboxes
  are closed-only and reuse the existing `purge({ids})` route.

## 4. Data sources (verified)

- **Drift live side:** `app/forward_metrics.py::compute_forward_metrics_for_deployment`
  — session-gated `win_rate`, `avg_pnl`, `profit_factor`, plus `library_gate.visible`
  (false until `MIN_COMPLETE_SESSIONS_FOR_LIBRARY = 10` complete sessions).
- **Drift baseline side:** the deployment pins `source_type`/`source_id`;
  `deployments._gather_deployment_evidence(db, strategy_id, instrument, params, source_doc)`
  returns `option_evidence = { win_rate (option), net_pnl_value, paired_trade_count,
  params_match }`. Baseline win-rate = `option_evidence.win_rate`; baseline avg ₹/trade
  = `net_pnl_value / paired_trade_count`. **Only used as a baseline when
  `params_match` is true**, else the chip reads "no exact baseline".
- **R-multiple:** `paper_auto.py` already persists `risk_amount` (and
  `risk_per_unit`, `sizing_mode`) on auto-sized trades (premium_at_risk). R =
  `realized_pnl / risk_amount`. Absent on fixed-lots / legacy trades → "—".
- **Exit reasons:** `exit_reason` on closed trades (e.g. `target`, `stop`,
  `eod`/`square_off`, `manual_*`). Normalize to {target, stop, eod, manual, other}.
- **Monthly P&L:** aggregate the Phase-1 equity-curve / closed trades by IST month.

## 5. Backend changes

### 5.1 `app/paper_analytics.py` (pure, TDD)
- `r_multiple(trade) -> float | None`: `realized_pnl / risk_amount` (None when
  `risk_amount` missing/≤0). Add `r_multiple` to the `per_trade_analytics` block.
- `exit_reason_breakdown(closed_trades) -> {counts, pct}` over normalized buckets.
- Extend `per_strategy_stats` with `avg_r` (mean R over trades with R) and
  `exit_mix` (per-strategy normalized breakdown).
- `monthly_pnl(closed_trades) -> [{month: 'YYYY-MM', pnl}]` (IST months).

### 5.2 `routers/journals.py`
- `/paper/analytics`: add `exit_reason_breakdown` (global, honoring the same
  filters the route already accepts — extend it to accept the trade filters) and
  `monthly_pnl`.
- `/paper/strategy-stats`: enrich each item with `drift` and `avg_r`/`exit_mix`.
  `drift` per deployment = `{ live_win_rate, base_win_rate, live_avg, base_avg,
  state }` where `state ∈ {ok, insufficient_sample, no_baseline}`. Computed by
  combining `compute_forward_metrics_for_deployment` (live) with
  `_gather_deployment_evidence(...).option_evidence` (baseline). Import the
  evidence gatherer from the deployments router (note the coupling; if it becomes
  awkward, lift `_gather_deployment_evidence` into a shared `app/deployment_evidence.py`
  — a small, in-scope refactor).
- `/paper/trades?include_analytics`: per-row `analytics.r_multiple` (from 5.1).

Drift is computed per active deployment; with a handful of deployments this is
fine. If it ever gets slow, cache per (deployment_id, params_sha).

## 6. Frontend changes

- `StrategyStatsTable.jsx`: add an **Avg R** column and a **vs backtest** drift
  chip column (green ▲ / amber ▼ / muted "insufficient sample" / muted "no
  baseline"), per the mockup. Chip text: `WR {live} vs {base} · ₹/t {live} vs {base}`.
- `TradeBlotter.jsx`: add an **R** column (from `analytics.r_multiple`, "—" when
  null) and re-add a **closed-only selection checkbox** column + header
  select-all-on-page (OPEN rows have no checkbox).
- New `components/paper/ExitReasonBreakdown.jsx`: horizontal bars
  (target=success, stop=danger, eod=muted, manual=info). Used twice — a global
  card next to `PnlCalendar`, and a compact variant inside the per-strategy
  detail.
- `PnlCalendar.jsx`: add the **monthly P&L bar chart** (the deferred Phase-1
  item) from the analytics `monthly_pnl`.
- `PaperTrading.jsx`: restore the **Delete selected ({n})** toolbar action wired
  to `purge({ ids: [...selected] })`; pass the global exit-reason breakdown to the
  new card; thread selection state into `TradeBlotter`.

## 7. Number / color / a11y

Reuse Phase-1 conventions: `fmtINR`/`fmtINRSigned`, sign-carrying P&L (not
colour-only), `font-mono tabular-nums`. R-multiple shows one decimal with sign
(`+1.8` / `−1.0`). Drift arrows pair with text (▲/▼ + the numbers) so they are
not colour-only.

## 8. Testing

- Backend (pytest, extends `tests/test_paper_analytics.py`): `r_multiple`
  (present / absent / zero-risk), `exit_reason_breakdown` normalization + pct,
  `per_strategy_stats` avg_r + exit_mix, `monthly_pnl` IST bucketing. A focused
  test for the drift combiner with stubbed forward-metrics + evidence dicts
  (pure combine function so it's unit-testable without the DB).
- Routes verified against live Mongo via `asyncio.run` (as in Phase 1); the
  drift route exercised on the real deployments.
- Frontend verified via the worktree run + Chrome screenshot (Phase-1 harness:
  scheduler-free `verify_app.py` on alt ports — see [[paper-trading-redesign-2026]]).

## 9. Phasing (within Phase 2)

Build the four cheap items first (R-multiple, exit-reason, monthly bars,
checkboxes), then drift last (the only item touching `forward_metrics` +
deployment evidence). Each is independently shippable.

## 10. Non-goals

- No change to how sizing/risk is recorded (R-multiple consumes existing
  `risk_amount`; trades without it simply show "—").
- No backfill of `risk_amount` onto legacy trades.
- No new charting dependency (recharts + inline SVG already cover it).

## 11. Risks / open questions

- Coupling: `/paper/strategy-stats` importing `_gather_deployment_evidence` from
  the deployments router. Acceptable; lift to a shared module only if it gets
  awkward.
- Drift is meaningful only for deployments whose pinned source has params-matched
  option evidence AND ≥10 complete forward sessions; many deployments will show
  "no baseline" / "insufficient sample" honestly (this is by design, matching the
  project's evidence-gated philosophy).
