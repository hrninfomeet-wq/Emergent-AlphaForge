# Fix: `/api/paper/strategy-stats` 500 (NaN/Inf not JSON-compliant)

> Bug fix — root-caused via systematic-debugging. Spec + plan + TDD execution + verification in one focused doc (scaled to a bug, not a feature).

## Bug
`GET /api/paper/strategy-stats` (and latently `GET /api/paper/account-analytics`) return **500 `ValueError: Out of range float values are not JSON compliant`** on the current paper data. Effect: the per-strategy stats panel on the Paper page errors/blanks.

## Root cause (confirmed)
`profit_factor = gross_win / gross_loss`. When a strategy has **profitable trades but zero losing trades** (`gross_loss == 0`, `gross_win > 0`), the code returns **`float("inf")`** — at two sites in `backend/app/paper_analytics.py`:
- **line 251** (`period_pnl`, feeds `build_account_analytics` → account-analytics endpoint)
- **line 390** (`per_strategy_stats` → strategy-stats endpoint)

FastAPI's default JSON response uses `allow_nan=False`, so `Infinity` (and `NaN`) raise `ValueError: Out of range float values are not JSON compliant` → 500. The current paper data has ≥1 all-wins (no-loss) strategy → the strategy-stats endpoint 500s; the global one is finite *today* (it has losses, PF 3.06) but is latently broken the same way.

Secondary gap: `_f` (line 38) coerces with bare `float(value)` and **no finite check**, so a `NaN`/`Inf` already in a trade's `realized_pnl`/`risk_amount` would propagate into sums/derived stats.

Not a regression from the live-feed or AI-authoring work (paper-analytics untouched; the endpoint had a prior projection bug `1a07028`).

## Fix (root cause + defense-in-depth)
1. **Root cause** — both sites: replace `else (None if gross_win == 0 else float("inf"))` with `else None`. "No losses" → `profit_factor = None` (JSON-safe; the frontend distinguishes all-wins from no-trades via `win_rate`/`closed_count`/`closed_trades`).
2. **Harden `_f`** — return `default` when the coerced value is non-finite (`math.isfinite`), so input `NaN`/`Inf` can't propagate.
3. **Defense-in-depth** — add a recursive `json_safe_floats(obj)` (NaN/Inf float → `None`, walks dict/list/tuple) in `paper_analytics.py`, and apply it in BOTH journal route handlers on the FINAL response (after the drift/name enrichment, which also does float math) so this endpoint class can never 500 on a stray NaN/Inf again.

## Files
- `backend/app/paper_analytics.py` — `_f` finite-check; `period_pnl` line 251; `per_strategy_stats` line 390; new `json_safe_floats`.
- `backend/app/routers/journals.py` — apply `json_safe_floats` to the `paper_strategy_stats` + account-analytics responses.
- `tests/test_paper_analytics.py` — add no-loss / sanitizer / `_f` tests.

## TDD steps
1. **Failing tests** (`tests/test_paper_analytics.py`):
   - `test_per_strategy_stats_no_losses_profit_factor_none_and_json_safe`: a strategy with only winning CLOSED trades → `profit_factor is None`, and `json.dumps(per_strategy_stats(rows), allow_nan=False)` does NOT raise. (Currently raises → red.)
   - `test_period_pnl_no_losses_profit_factor_none_and_json_safe`: all-win closed trades → `period_pnl(...)["profit_factor"] is None` + JSON-safe.
   - `test_json_safe_floats_replaces_nan_inf`: `json_safe_floats({"a": float("inf"), "b": float("nan"), "c": [1.0, float("-inf")], "d": 2})` → `{"a": None, "b": None, "c": [1.0, None], "d": 2}`.
   - `test_f_rejects_nan_inf`: `_f(float("inf")) == 0.0`, `_f(float("nan")) == 0.0`, `_f("inf") == 0.0`, `_f(3.5) == 3.5`.
2. Run → confirm red (the first two raise on `json.dumps(allow_nan=False)` today; `json_safe_floats`/`_f` finite-check don't exist yet).
3. Implement the 4 changes.
4. Run the new tests → green.
5. Regression: `python -m pytest tests/test_paper_analytics.py -v` (existing with-losses tests — `test_period_pnl_buckets...` PF, `test_per_strategy_stats_attribution...` PF==5.0 — must stay green; the change only affects the `gross_loss==0` branch) + a broad host-suite run.
6. Commit on `main`.

## Verify (deploy)
- Rebuild backend: `docker compose up -d --build backend`.
- `curl -s -o /dev/null -w "%{http_code}" http://localhost:8001/api/paper/strategy-stats` → **200** (was 500).
- Chrome: the per-strategy stats panel on the Paper page renders (no blank/error); a no-loss strategy shows `profit_factor` as "—"/null, not a crash.
