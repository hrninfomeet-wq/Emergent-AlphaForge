# Removal plan — awaiting approval

**Nothing has been deleted. This is the proposal.**

## Headline: there is almost nothing to remove

The audit found **zero obsolete tracked files in `docs/`** and **zero dead backend modules**.
`.git` is 11 MB, the tree is clean, no secrets are tracked, and 223/223 documentation links
resolve. What the repository actually has is **correctness debt** — documents that describe
behaviour the code no longer has — plus **two live-safety defects** that the audit surfaced as a
side effect. Deleting things would not help; fixing four documents and one unmounted component
would.

---

## TIER 1 — safe, no information value (5 items)

All are **untracked and already gitignored**, so this is local-disk hygiene, not a git operation.

| Path | Reason | Evidence nothing references it | In git history? |
|---|---|---|---|
| `backend/scripts/_train.log` | Shell-redirect byproduct of the pooled-regime campaign | repo-wide grep for the filename → **no matches** | never committed |
| `backend/scripts/_train2.log` | same | same | never committed |
| `backend/scripts/_val.log` | same | same | never committed |
| `backend/scripts/_val2.log` | same | same | never committed |
| `backend/scripts/__pycache__/` | Python bytecode | generated | never committed |

**Caveat worth your call:** these four logs are the run record of a research campaign whose
verdict is documented in `POOLED_REGIME_VERDICT_2026-07.md`. They are a weak audit trail. If you
would rather keep them, the cost is 48 KB. **I recommend keeping them** and doing nothing here.

---

## TIER 2 — obsolete or superseded content

**EMPTY.** No tracked document was found to be obsolete, duplicate, or abandoned.

Four documents are **misleading and must be CORRECTED, not deleted** (see the correctness-debt
section). Correction preserves their inbound links and their history.

---

## TIER 3 — judgement calls for you

| Item | Argument to remove | Argument to keep | My recommendation |
|---|---|---|---|
| `My custom strategies/` (empty, untracked) | Empty directory, no content | **User-owned.** The name says it is yours; an empty dir may be a placeholder you rely on | **KEEP — not mine to remove** |
| `.playwright-cli/` (empty, untracked) | Empty, likely tool residue | Harmless; a tool may recreate it | **KEEP** |
| `docs/phase5b-market-validation-runbook.md` | Target date "Monday 2026-07-20" is 4 weeks past | `LIVE_VALIDATION_PLAN_2026-08.md:2` explicitly says it "Supersedes nothing; sits alongside" this runbook. Both are live. | **KEEP, add a dated status line** |
| `backend/app/exit_controls_level.py` | 11-line wrapper; production never calls it | Pinned by `tests/test_scenario_adaptive_exits.py` (10 call sites) as a **parity anchor**; listed in `ARCHITECTURE.md:126`; `CHANGELOG.md:1547` records it as deliberately kept | **KEEP** |
| `frontend/src/components/charts/MultiPaneChart.jsx` | Not rendered anywhere | `tests/test_backtest_performance_overview.py:118` asserts `"MultiPaneChart" not in page` — a test pins its non-use | **KEEP** |

---

## DO NOT REMOVE — things that look removable but must stay

| Item | Why it is tempting | Why it must stay |
|---|---|---|
| `backend/scripts/pooled_regime_train.json`, `pooled_regime_validation.json` | Untracked, gitignored, look like output | **They are INPUTS.** `verdict_pooled_regime.py:36` reads them; `run_pooled_regime_campaign.py:247` writes them. They are the evidence that a **pre-registered kill criterion** was applied and that the untouched holdout stayed clean. Deleting them lets that gate be re-run without its prior. **Dataset — protected by your rule.** |
| `docs/Resources/flattrade-pi-api/` (126 files) | Large; vendor material | Vision-verified decode of a 93-page PDF, 58 endpoints. **Irreplaceable broker documentation.** |
| `docs/OPTIMIZER_VERDICT_2026-07.md` | **Zero** inbound doc references | Cited by **source**: `backend/app/strategies/plugins/explosive_reversal.py` and `tests/test_strategy_column_contract.py` |
| `docs/PREMIUM_MOMENTUM_EDGE_VERDICT_2026-07.md` | A closed "no edge" verdict | Cited by `backend/app/forward_metrics.py` and `tests/test_premium_momentum_advisory_ui.py`; carries the **pre-registered revival criterion** |
| `docs/PROFIT_LEVERAGE_ANALYSIS_2026-07.md` | Dated analysis | `run_pooled_regime_campaign.py` and `verdict_pooled_regime.py` cite its §4/§6 for their kill criterion |
| `docs/live-cockpit-audit-2026-07-25.md` | Dated "audit" | **41 UNVERIFIED findings still open.** This is a live backlog, not history |
| `docs/audit-report-2026-07.md` | 88 findings, all resolved | HANDOFF §5 keeps it as the `L##`/`O##`/`S##` **ID decoder** for commit messages |
| `docs/BACKTEST_INTEGRITY_AUDIT.md` | Long | Permanent defect register; `backend/app/walkforward.py` cites it |
| `docs/forward-validation-policy.md`, `live-readback-checklist.md`, `flattrade-mcp-integration.md` | — | **Live-trading safety material.** The never-call-MCP-login rule lives in the third |
| `AGENTS.md` | 0 inbound refs; byte-identical to `CLAUDE.md` except line 1 | **Deliberate per-tool duplication** — it is Codex's entry-point convention file, as `CLAUDE.md` is Claude's |
| `docs/HANDOFF.md`, `ARCHITECTURE.md`, `USER_MANUAL.md`, `PROJECT_OVERVIEW.md`, `STRATEGY_PLUGINS.md` | — | **Test-pinned.** `tests/test_bootstrap_contract.py` and `test_strategy_plugins_doc.py` read them; deleting REDs the suite |
| `frontend/src/components/live/LiveBanner.jsx` | Orphaned by the cockpit redesign | `tests/test_stage1_operator_guidance.py:37` reads it; and it is **the only renderer** of the `autoplaceArmed` dry-run warning |
| `backend/.env.example`, `frontend/.env.example`, `memory/.gitkeep` | — | Setup templates / intentional placeholder |

---

## The real work: correctness debt (no deletion involved)

| # | File | Defect | Fix |
|---|---|---|---|
| 1 | `docs/HANDOFF.md` | Header says "As of 2026-08-11 · v0.58.0", baseline "4,573 passed" (actual **4,887**); §2.0b/§2.2 predate three sessions of live-path work | Rewrite §2 in place — it has 14 inbound refs, so it must not move |
| 2 | `README.md` | Safety Note describes **two gates deleted in v0.56.0** | Correct to the current model (`mode=="live"` + connected + 15:00 cutoff) |
| 3 | `docs/agent-takeover-prompt.md` | Contradicts HANDOFF; title says "as of 2026-08-01" but committed 08-12 | Re-point at the updated HANDOFF |
| 4 | `docs/USER_MANUAL.md` | **Zero mentions of "cockpit"** — the 2026-07-22 Live Cockpit redesign is undocumented | Add the cockpit page section |
| 5 | `docs/CAPABILITY_PHASE_PLAN_2026-07.md` | Line 19 claims `PositionMonitor.jsx` "remains wired via LiveDataProvider" — **false**, and line 158 of the same file says the opposite | Correct line 19 |

---

## ⚠ Two live-safety findings that are NOT cleanup — escalating

**1. The per-position Square button is unreachable from the UI.**
`components/live/PositionMonitor.jsx` is mounted by nothing — `LiveCockpit.jsx:19-24` mounts six
other panels, not this one. `api.squareLivePosition` (`lib/api.js:310`) has exactly one caller,
`PositionMonitor.jsx:153`, which never renders. **The account-wide FLATTEN EVERYTHING kill switch
is currently the only manual exit.** Already logged as UNVERIFIED at
`docs/live-cockpit-audit-2026-07-25.md:211`; this audit **verifies** it.

**2. A fast poll with zero consumers is burning the shared Flattrade rate budget.**
`LiveDataProvider.jsx:60` polls `api.getLiveTestSession()` at `FAST_MS`; `session` is destructured
only by the unmounted `PositionMonitor.jsx:56`.

**3. (Correctness, not safety) The warehouse integrity hash has two disagreeing implementations.**
`routers/warehouse.py:314-349` hand-rolls the upsert+hash that `warehouse.persist_candles_df`
already provides — and the hash halves differ: the canonical one re-reads the **whole IST day**
from Mongo, the inline copy hashes **only the incoming chunk**. Same day ingested two ways
produces two different integrity hashes.

---

## Test-quality finding (backlog, not removal)

**112 test files assert on source TEXT rather than behaviour.** The worst offenders carry 10–24
such assertions each. The specimen is `tests/test_intraday_backfill.py`, named
`test_intraday_fetch_and_backfill_route_are_wired`, which asserts only that the route is
*declared* — it could not detect that the route had **zero callers**, which is exactly the defect
I found and fixed in `46e934e`. These should be converted to behavioural tests over time.
**Deleting them would reduce coverage — this is a conversion backlog, not a removal list.**
