# Local takeover — intraday option-buying campaign (2026-08-23)

**Paste the block in §0 into a fresh Claude session opened on this repo folder.**
Everything below it is the state that session needs. Written by the cloud session
that produced branch `claude/hello-g2itta`, which cannot reach the local machine.

Companion to [`agent-takeover-prompt.md`](agent-takeover-prompt.md) (the general
project takeover) — this file is narrower: it hands over **one campaign in
flight**, not the whole app.

---

## 0. The prompt to paste

> You are taking over an intraday option-buying research campaign on **AlphaForge
> Trading Lab**, mid-flight. The previous session ran in an isolated cloud
> container with **no MongoDB and no warehouse data**; you are on the operator's
> Windows PC where the real warehouse lives, which is the entire reason for the
> handoff.
>
> **Read first, in this order:**
> 1. `docs/LOCAL_TAKEOVER_2026-08-23.md` — this campaign's state (this file)
> 2. `docs/INTRADAY_OPTION_BUYING_CANDIDATES_2026-08.md` — the deliverable: audit,
>    constraints, parity gap, two frozen specs, verdicts, §10 real-warehouse
>    measurements
> 3. `docs/OPTION_BUYING_MICROSTRUCTURE_2026-08.md` — why the honest prior is that
>    both candidates fail
> 4. `docs/HANDOFF.md` §2 and `docs/BACKTEST_INTEGRITY_AUDIT.md` — before trusting
>    any number the app produces
>
> **You are on branch `claude/hello-g2itta`** (draft PR #7). Do not push to `main`.
>
> **Your immediate task is §4 of this file: diagnose why the screen builds no ATM
> option series.** Do not proceed to backtests, the Optimizer, or the holdout until
> that is resolved — an empty screen means the campaign has measured nothing.
>
> **Standing rules for this campaign, inherited and non-negotiable:**
> - Never place, modify or cancel a broker order. Never enable live mode. Never
>   call the Flattrade MCP's `login`/`logout`.
> - The protected holdout is read **once**, by recorded finalists, after
>   train+validation selection is frozen. See §5 — the script's current holdout
>   label is wrong and you must fix it before anyone reads a holdout number.
> - A docstring is not a test. This branch found three defects that were
>   "documented as correct" and weren't; mutate a guard before believing it.
> - Report failures with their output. "Research-only" verdicts stay research-only
>   until evidence changes them.

---

## 1. Where the work is

| | |
|---|---|
| Branch | `claude/hello-g2itta` @ `8987bf5` — **9 commits**, 8 files, 4,260 insertions, all additive |
| Base | `origin/main` @ `6e6e1cc`, unmoved; 0 conflict markers |
| PR | [#7](https://github.com/hrninfomeet-wq/Emergent-AlphaForge/pull/7), draft, no reviews, no comments |
| CI | **None exists** — this repo has no `.github/workflows`. The host suite is the only evidence. |
| Suite | `5,086 passed, 2 failed, 10 skipped, 4 xfailed`. The two failures are `test_premium_momentum_route.py`, which needs a live MongoDB — **they should PASS on your machine.** If they fail there too, that is a real finding. |

### Commits, newest first

```
8987bf5 fix(screen): diagnose an empty screen instead of shrugging at it
cf8c1d6 fix(screen): fetch option bars by identity, not by a reusable token
199f269 docs(screen): correct the run instructions — they would have failed first try
0af9cf3 test: mutation-sweep every shipped invariant; one survivor, now killed
78e1e37 test(screen): guard the block-boundary fix where it lives, verified by mutation
6f41737 fix(screen): three defects the CLI's untested database path was hiding
7220cf7 docs: correct a §7.8 finding that was wrong, and say why it was wrong
4640856 feat(strategy): candidate B as a registered research-only plugin
10390d5 research: option-buying audit, verified constraints, and two frozen candidates
```

### What was added

| File | |
|---|---|
| `backend/app/option_screen.py` | The pre-plugin screening gate. Pure — no DB. |
| `backend/scripts/screen_option_buying.py` | Read-only CLI: validate → split → baseline → conditions. |
| `backend/app/strategies/plugins/expiry_regime_trend_continuation.py` | Candidate B, registered, research-only, **never run**. |
| `tests/test_option_screen.py` | 36 tests |
| `tests/test_screen_option_buying_script.py` | 16 tests |
| `tests/test_screen_option_buying_db_paths.py` | 27 tests (strict fake Mongo) |
| `tests/test_strategy_expiry_regime_trend_continuation.py` | 39 tests |
| `docs/INTRADAY_OPTION_BUYING_CANDIDATES_2026-08.md` | The deliverable |

---

## 2. Verification — run this before changing anything

```powershell
cd C:\Users\haroo\OneDrive\Documents\New project\Emergent-AlphaForge
git fetch origin
git checkout claude/hello-g2itta
git pull origin claude/hello-g2itta
git log --oneline -1          # expect 8987bf5

# The rebuild is REQUIRED. Dockerfile bakes source with `COPY . .` and compose
# bind-mounts ONLY backend/app/strategies/plugins — so a new plugin appears live
# but scripts/ and app/option_screen.py do not exist in a running container until
# the image is rebuilt.
docker compose up -d --build backend

# Suite. On your machine the two Mongo-dependent failures should disappear.
.venv\Scripts\python.exe -m pytest tests -q

# The campaign's own tests
.venv\Scripts\python.exe -m pytest tests\test_option_screen.py tests\test_screen_option_buying_script.py tests\test_screen_option_buying_db_paths.py tests\test_strategy_expiry_regime_trend_continuation.py -q
# expect 118 passed
```

**Confirm the plugin registered** — it is visible in the UI as
*"Expiry-Regime Trend Continuation"* in Strategy Library and the Backtest Lab
strategy dropdown. (The operator initially read the list as not containing it; it
was there. The dropdown shows `name`, not the snake_case id.)

```powershell
docker compose exec backend python -c "from app.strategies.base import get_registry; r=get_registry(); r.auto_discover(); print(r.get('expiry_regime_trend_continuation'))"
```

---

## 3. What is settled (do not re-derive)

- **Candidate A's premise is CONFIRMED.** OI is populated on **99.55–99.61%**
  (NIFTY) and **99.86%** (SENSEX) of sampled option bars. The §7.1 option-flow
  feature build is justified. Candidate A is still *not implementable* — option
  data does not reach `evaluate()` — and still unscreened.
- **433/433 complete spot sessions** on both indices, 2024-11-25 → 2026-08-21.
  Data completeness is not a constraint.
- **`chain_snapshots` is empty and has no writer.** Option-chain structure is not
  historically testable and cannot be backfilled.
- **`ticks` has a 30-day TTL** (26.7M rows retained). Not a quote-replay source.
- **Three NIFTY lot regimes in one window: 25 → 75 → 65.** No single lot number
  sizes a 2024-11 → 2026-08 run. Charges are premium-invariant as a percentage,
  so the cost model is unaffected; rupee P&L is not.
- **`contract_key` coverage is 61.39% NIFTY / 6.61% SENSEX.** Already handled —
  `_fetch_contract_bars` asks for a contract by identity
  (`underlying`/`expiry_date`/`strike`/`side`, the tuple `db.py` indexes), with
  `contract_key` second and a labelled `instrument_key_unverified` token path
  last. Low coverage is no longer a blocker.
- **Flattrade allows `LMT` and `SL-LMT` only.** No market orders.
- **The live entry window is hardcoded 09:25–14:50** and is not per-deployment,
  while `backtest.py` defaults to 09:25–15:00. Ten minutes of default-backtest
  signals are untradeable.

---

## 4. YOUR IMMEDIATE TASK — the screen builds no ATM series

Run on the train slice (191 sessions), both indices returned **no ATM option
series**. Cause unknown. `8987bf5` added a per-stage funnel so the next run
diagnoses it instead of shrugging.

**Do not accept the script's own error message at face value.** The first time it
printed "this is a DATA finding", the cause was a typo in its own query
(`option_type` vs `side`) and it blamed the warehouse.

### Step 1 — the no-rebuild probe (separates filter from data)

`--dte` with no values yields `[]`, and `dte_filter=args.dte or None` disables
filtering entirely:

```powershell
docker compose exec backend python scripts/screen_option_buying.py --instrument NIFTY --dte
```

- Still empty → the DTE filter is innocent; look at contract lookup or coverage.
- Produces a baseline → the `[1,2,3]` filter excluded everything, which is itself
  a finding about expiry metadata.

### Step 2 — the funnel

```powershell
docker compose exec backend python scripts/screen_option_buying.py --instrument NIFTY --json-out /app/nifty_screen.json
```

The stage where the count collapses to zero **is** the cause:

| Collapse at | Means | Fix lives in |
|---|---|---|
| `dropped_dte_unresolved` | `compute_dte` returned None | `nse_calendar` / expiry metadata |
| `dropped_dte_excluded` | The `--dte` filter, not the data | the run's flags |
| `dropped_contract_not_found` | Contract-master gap, or the lookup key is still wrong | `option_contracts` / the query |
| `dropped_too_few_bars` | Genuine `options_1m` coverage | ingestion |

Up to five verbatim sample misses print with the exact failing lookup. Compare one
against the real documents:

```powershell
docker compose exec backend python -c "from pymongo import MongoClient; d=MongoClient('mongodb://mongo:27017')['alphaforge']; print(d.option_contracts.find_one({'underlying':'NIFTY'}))"
```

### Step 3 — what a healthy screen looks like

The unconditioned ATM MFE/MAE must reproduce **0.90–0.95**. A materially different
baseline means the data changed, and *that* is the finding — stop and report it
rather than proceeding to conditions.

---

## 5. Known defect you must fix before any holdout number is read

The screen printed:

```
holdout > 2025-12-31 : 158 sessions (PROTECTED)
```

**That label is wrong and it is dangerous.** Prior campaigns already consumed
**2026-01-01 → 2026-07-10** (the premium-momentum finalists read it — see
`PREMIUM_MOMENTUM_EDGE_VERDICT_2026-07.md`). Only roughly **30 sessions**,
2026-07-11 → 2026-08-21, are genuinely untouched.

The tool is telling the operator they hold 158 clean holdout sessions when they
hold about 30. Acting on that number would invalidate the campaign's central
claim. `chronological_split` needs to carry the consumed window explicitly and
label those sessions `consumed`, not `holdout`. **Not started.**

Note the consequence, which is already recorded in §5.3 of the deliverable: ~30
sessions is below the 60-session / 120-trade promotion minimum, so a campaign
started now **cannot** produce a promotion-grade holdout result. The remaining
evidence has to come forward from paper, at roughly 20 sessions a month.

---

## 6. Verdicts as they stand

| Candidate | Verdict |
|---|---|
| A — ATM Premium-Flow Scalp | Research-only. **Premise confirmed** (OI populated), build unblocked, not implementable until option features reach `evaluate()`. |
| B — Expiry-Regime Trend Continuation, 1DTE | Research-only, implemented, cleared to screen. **Never run.** |
| B — 0DTE arm | Research-only, **pre-registered as expected to fail** (net −4.43% NIFTY / −2.01% SENSEX per 5-min ATM hold). |

Neither is paper-ready. Neither is eligible for a live-readiness review.

---

## 7. Three mistakes this branch made — the pattern is one mistake

Repeated because the next session will be tempted by the same shortcut.

1. **§7.8 reported a closed defect as open.** Taken from a stale `HANDOFF.md`
   note without reading the code, in a repo whose handoff says the code is the
   source of truth. (`HANDOFF.md` §2.0e is still stale — worth fixing.)
2. **A contract asserted in a docstring was broken by its only caller.** The
   excursion window was documented as not crossing sessions; the CLI stacked
   every session and both option legs into one frame.
3. **A fix was believed correct because it was written correctly.** Deleting the
   entire block-grouping fix left all 29 tests in its own module passing. Found
   by mutating it.

All three are the same error: **treating an assertion as evidence.** A mutation is
cheap and answers it directly — *if this were wrong, would anything go red?* A
ten-mutant sweep over every shipped invariant is recorded in §9.3 of the
deliverable; one survived and is now killed.

---

## 8. Safety — unchanged and inherited

No order was placed, modified or cancelled. No live mode enabled. No deployment,
preset or broker session created or altered. No Flattrade MCP `login`/`logout`.
Candidate B's plugin is registered, **unrun and undeployed**. The screen CLI opens
one read-only Mongo connection and writes nothing.

Any future live deployment requires separate explicit user authorisation and must
pass the app's existing live-safety, data-completeness and risk-authorisation
gates. Nothing in this campaign constitutes that authorisation.
