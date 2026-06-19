# Paper-trade sizing: locked replay of the source run's sizing policy

- **Date:** 2026-06-19
- **Status:** Design approved (brainstorming) — pending spec review, then plan
- **Owner:** Haroon
- **Area:** `backend/app` (deployment + auto-paper) and `frontend/src/pages/LiveSignals.jsx`

## Problem

A paper deployment trades a **fixed** lot count: `build_auto_trade` reads
`lots = deployment.risk.default_lots` ([paper_auto.py:289](../../../backend/app/paper_auto.py)),
a scalar chosen in the deploy wizard (`req.default_lots or 1`,
[deployments.py:233](../../../backend/app/routers/deployments.py)). This count
is **never reconciled with the source backtest/optimizer run** the deployment was
created from. The deployment doc does not even persist the run's sizing — its
`source_snapshot` keeps only name/dates/metrics.

Consequences:

1. **Forward test is not comparable to the backtest.** The run may have sized
   from a premium-at-risk policy (lots vary per trade by capital + risk% + the
   option stop, capped by `max_lots`); the live deployment ignores that and
   trades a flat number.
2. **Oversizing amplifies losses.** With live-tick paper realism, a flat
   ~10-lot deployment now realizes ~−₹13–14k per stop — far above any 1%-of-
   capital risk budget. The flat count, not a risk policy, is the cause.

## Goal

Deployed paper lots should **replay the source run's sizing policy** so live P&L
scales the same way the backtest did. Faithfulness (forward == backtest) is the
objective; this is comparability/realism work, not a new trading feature.

## Non-goals

- No deploy-time editing/override of sizing (locked replay). Overrides are a
  possible later graduation, explicitly deferred.
- No aggregate risk-of-ruin / portfolio cap across deployments. The per-trade
  `risk_per_trade_pct` plus `max_lots` are the survival bounds for now.
- No DB backfill of existing presets (see Migration).
- No change to the legacy approve-to-trade path beyond shared `build_auto_trade`.

## Key decisions (resolved during brainstorming)

| Decision | Choice |
|---|---|
| Sizing intent | **Replay the policy live** — recompute lots per signal via `size_position()`, not copy a scalar. |
| Adjustability | **Locked replay** — pinned config is immutable at deploy. |
| Capital basis | The **run's notional** (e.g. ₹200k) — comparability over real-account realism. |
| Cap | Inherit the run's `max_lots`; `risk_per_trade_pct` is the real limiter. No extra cap layer. |
| Premium-at-risk vs fixed lots | Travels with the run's `sizing_config.mode`; fixed_lots is the degenerate constant case. |
| SENSEX / BANKNIFTY lot-size | Handled for free — `lot_size` comes from the live contract; only the lot **count** is sized from rupee risk, held constant across instruments. |
| Old presets (no `sizing_config`) | **Visible fallback only** — no backfill; wizard labels the fallback. |
| Wizard | **Same PR** — read-only sizing summary replaces the editable lots input. |

## Architecture

Three backend units + one frontend change. Each is independently testable.

### Unit 1 — Source sizing deriver (pure)

`deployment_sizing_from_source(source_type, source_doc) -> Optional[dict]`
(new pure helper, in `strategy_deployments.py` or a small sibling module).

Returns `{"sizing_config": {...}, "lots": int, "source_id": str}` or `None`
when the source carries no sizing info (→ legacy fallback).

Read paths (defensive `.get` chains; any missing → `None`):

- **backtest_run:** `ob = source_doc.get("option_backtest") or {}`
  - `sizing_config = ob.get("sizing_config")`
  - `lots = (ob.get("request") or {}).get("lots")`
- **preset:** `ex = (source_doc.get("config") or {}).get("execution") or {}`
  - `sizing_config = ex.get("sizing_config")`
  - `lots = ex.get("lots")`

`sizing_config` is normalized through `SizingConfig.from_dict(...).to_dict()` so
the pinned shape is canonical. `lots` defaults to 1 when absent.

### Unit 2 — Pin on the deployment doc

`build_deployment_doc` ([strategy_deployments.py:67](../../../backend/app/strategy_deployments.py))
stamps the deriver output onto a new **immutable** field:

```
risk.sizing = {
  "sizing_config": { mode, capital, risk_per_trade_pct, fixed_lots, max_lots,
                     assumed_stop_pct_of_premium, enabled },
  "lots": <run's lots scalar>,
  "source_id": <source_id>,
}
```

Mirrors how `strategy_source_sha` is already pinned at creation. When the
deriver returns `None`, `risk.sizing` is omitted and the deployment keeps using
`default_lots` (unchanged legacy behavior). `default_lots` remains on the doc as
the legacy fallback value.

### Unit 3 — Preset deriver fidelity fix

Extend `execution_from_option_config`
([preset_execution.py:28](../../../backend/app/preset_execution.py)) to also
carry `sizing_config` (currently only `lots` survives — premium-at-risk is
dropped). Without this, policy replay is impossible from a preset (only from a
backtest_run). Carried only when present, kept canonical via `SizingConfig`.

### Unit 4 — Live replay in `build_auto_trade`

In `build_auto_trade` ([paper_auto.py:279](../../../backend/app/paper_auto.py)),
replace the `lots = default_lots` read. **Reorder** so sizing runs *after* the
friction-adjusted `fill_entry` and *after* `compute_auto_risk_levels` (the
premium stop feeds risk-per-unit):

```
fill_entry  = apply_entry_friction(...)            # existing
stop, target = compute_auto_risk_levels(...)       # existing (already after fill_entry)
# lots is now computed HERE, after stop/target (was computed first, at line 289)
lot_size = int((signal_doc.get("option_contract") or {}).get("lot_size") or 1)
pin = (deployment.get("risk") or {}).get("sizing")
if pin:
    cfg = SizingConfig.from_dict(pin["sizing_config"])
    if cfg.enabled:
        sized = size_position(entry_premium=fill_entry, lot_size=lot_size,
                              stop_level=stop, cfg=cfg)
        lots = int(sized["lots"]); sizing_audit = sized
    else:
        lots = max(1, int(pin.get("lots") or 1)); sizing_audit = {"sizing_mode": "fixed_lots"}
else:
    lots = max(1, int((deployment.get("risk") or {}).get("default_lots") or 1))
    sizing_audit = {"sizing_mode": "fixed_lots_legacy"}
```

This mirrors the backtest's exact rule
(`sized_lots = size_position(...) if enabled else max(1, lots)`,
[option_backtest.py:512](../../../backend/app/option_backtest.py)), so forward
== backtest by construction. `assumed_stop_pct_of_premium` (50%) covers the
no-premium-stop case identically to the backtest's spot_exit mode.

Carry `sizing_mode` / `risk_per_unit` / `risk_amount` / `risk_exceeded` onto the
trade doc (and into the `auto_paper` signal snapshot) for audit parity with the
backtest trade rows.

### Unit 5 — Deploy wizard (frontend)

Replace the editable "Lots per trade" `Input`
([LiveSignals.jsx:601](../../../frontend/src/pages/LiveSignals.jsx)) with a
read-only **sizing summary** derived from the selected source's `execution`
(or run) sizing:

- `premium_at_risk` → `"Sizing: premium-at-risk · {risk%}% of ₹{capital} · max {max_lots} lots — inherited from run"`
- `fixed_lots` (or disabled with a real `lots`) → `"Sizing: fixed {lots} lots — inherited from run"`
- **no `sizing_config`** → `"Sizing: fixed {lots} lots (source predates policy capture; re-save the preset, or deploy from the backtest run, to inherit premium-at-risk)"`

`default_lots` stays in the create payload only as the legacy fallback for
sources with no sizing. No new editable sizing controls (locked replay).

## Data flow

```
source run (backtest_run | preset)
  │  carries sizing_config + lots
  ▼
deployment_sizing_from_source()            ── Unit 1
  │  {sizing_config, lots, source_id} | None
  ▼
build_deployment_doc → risk.sizing (pinned, immutable)   ── Unit 2
  │
  ▼   (live, per confirmed signal)
build_auto_trade:                          ── Unit 4
  fill_entry → stop/target → lot_size(from live contract)
  → size_position(cfg, fill_entry, lot_size, stop)  [if enabled]
  → lots → paper_trade_from_signal
```

## Edge cases & error handling

- **No pin / legacy deployment** → `default_lots` path, unchanged. The 5 live
  `confluence_scalper` deployments are unaffected.
- **`sizing_config.enabled == False`** → fixed-lots replay using the pinned
  `lots` scalar (mirrors backtest's `lot_count = max(1, lots)`).
- **No premium stop configured** (spot-mirror exits) → `size_position` uses
  `assumed_stop_pct_of_premium`, matching the backtest.
- **`risk_exceeded` (one lot over budget)** → still trades one lot, tags
  `risk_exceeded=True` (sizing never blocks — "tag, don't block").
- **Missing `lot_size` on the live contract** → default to 1 (degrade, never
  crash); rare since `option_contracts` docs carry `lot_size`.
- **`max_lots` cap** inherited from the run; applied inside `size_position`.

## Backward compatibility

- New field `risk.sizing` is additive; absent on all existing docs → legacy path.
- `default_lots` retained on the doc and in the payload as the fallback.
- Preset `execution` gains an optional `sizing_config` key; readers that ignore
  it are unaffected.

## Migration (visible fallback, no backfill)

Existing survival-gated presets were saved before Unit 3, so they carry only
scalar `lots`. Deploying from them replays **fixed lots**, not the policy. We do
**not** mutate stored presets. Instead the wizard's sizing summary explicitly
states when a source has no `sizing_config` and is falling back to fixed lots,
so the user can re-save the preset (or deploy from the backtest_run, which
carries the full config) to inherit premium-at-risk. Fallback is visible, never
silent.

## Testing (host tests, mirroring the `paper_auto` suite)

1. `deployment_sizing_from_source` extracts `{sizing_config, lots}` from a
   backtest_run doc and from a preset doc; returns `None` when absent.
2. `build_auto_trade` premium-at-risk replay: lots equal `size_position(...)`
   for a given `fill_entry` / stop / `lot_size`; audit fields stamped.
3. Fixed-lots replay (pinned, `enabled=False`) → `max(1, pinned.lots)`.
4. Legacy fallback (no `risk.sizing`) → `max(1, default_lots)`, unchanged.
5. Instrument lot-size: identical policy yields different lot counts for a
   NIFTY (75) vs BANKNIFTY contract — rupee risk held constant.
6. `execution_from_option_config` now carries `sizing_config` when present and
   omits it when absent.
7. `build_deployment_doc` pins `risk.sizing` from a source with sizing and omits
   it otherwise.
8. Frontend: sizing summary renders the three states (premium-at-risk / fixed /
   no-config fallback) from the source's execution block.

## Open questions

None — direction (A: locked replay), migration (visible fallback), and wizard
scope (same PR) are resolved.
