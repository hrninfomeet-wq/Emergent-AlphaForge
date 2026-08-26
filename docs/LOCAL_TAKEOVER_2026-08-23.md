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
> **§4 is now CLOSED** — the screen builds series on both indices, and the empty
> screen was the script's own token lookup, not the warehouse. Read §4 before
> doing anything: it also carries two findings that shape the next run, including
> one you must apply *before* the first conditioned screen. The unconditioned
> baseline is **NO_EDGE on both indices** (MFE/MAE 0.86–0.90, session-level t
> −32 to −109), so the next step is a decision about whether to condition at all,
> not a foregone one.
>
> **Still non-negotiable:** do not touch the Optimizer or the holdout. §5 stands —
> only ~30 sessions are untouched, below the promotion minimum.
>
> **Standing rules for this campaign, inherited and non-negotiable:**
> - Never place, modify or cancel a broker order. Never enable live mode. Never
>   call the Flattrade MCP's `login`/`logout`.
> - The protected holdout is read **once**, by recorded finalists, after
>   train+validation selection is frozen. Read §5: only ~30 sessions are actually
>   untouched, which is below the promotion minimum.
> - A docstring is not a test. This branch found three defects that were
>   "documented as correct" and weren't; mutate a guard before believing it.
> - Report failures with their output. "Research-only" verdicts stay research-only
>   until evidence changes them.

---

## 1. Where the work is

| | |
|---|---|
| Branch | `claude/hello-g2itta` — all changes **additive**; `git diff --name-status origin/main...HEAD` is every `A` |
| Base | `origin/main` @ `6e6e1cc`, unmoved; 0 conflict markers |
| PR | [#7](https://github.com/hrninfomeet-wq/Emergent-AlphaForge/pull/7), draft, no reviews, no comments |
| CI | **None exists** — this repo has no `.github/workflows`. The host suite is the only evidence. |
| Suite | **Measured locally 2026-08-23: `5,098 passed, 5 failed, 4 xfailed`.** The prediction held — the two `test_premium_momentum_route.py` failures cleared with a real MongoDB. The 5 remaining are all `tests/test_bootstrap_contract.py`, **pre-existing and unrelated to this branch** (nine added files, no launcher touched); cause and fix in §4.1. |

### Commits, newest first

```
docs: local takeover handoff for the option-buying campaign
fix(screen): diagnose an empty screen instead of shrugging at it
fix(screen): fetch option bars by identity, not by a reusable token
docs(screen): correct the run instructions — they would have failed first try
test: mutation-sweep every shipped invariant; one survivor, now killed
test(screen): guard the block-boundary fix where it lives, verified by mutation
fix(screen): three defects the CLI's untested database path was hiding
docs: correct a §7.8 finding that was wrong, and say why it was wrong
feat(strategy): candidate B as a registered research-only plugin
research: option-buying audit, verified constraints, and two frozen candidates
```

(Plus this document's own commit. Run `git log --oneline origin/main..HEAD`
for the authoritative list.)

### What was added

| File | |
|---|---|
| `backend/app/option_screen.py` | The pre-plugin screening gate. Pure — no DB. |
| `backend/scripts/screen_option_buying.py` | Read-only CLI: validate → split → baseline → conditions. |
| `backend/app/strategies/plugins/expiry_regime_trend_continuation.py` | Candidate B, registered, research-only, **never run**. |
| `tests/test_option_screen.py` | 41 tests |
| `tests/test_screen_option_buying_script.py` | 16 tests |
| `tests/test_screen_option_buying_db_paths.py` | 27 tests (strict fake Mongo) |
| `tests/test_strategy_expiry_regime_trend_continuation.py` | 39 tests |
| `docs/INTRADAY_OPTION_BUYING_CANDIDATES_2026-08.md` | The deliverable |

---

## 2. Verification — run this before changing anything

> **Shell note — this cost a round trip.** The operator runs **Windows PowerShell
> 5.1**, where `&&` is not a statement separator:
> `The token '&&' is not a valid statement separator in this version.`
> Every command below is on its own line for that reason. Do not hand the
> operator chained one-liners. If you need chaining in PS 5.1, use `;` (runs the
> next command regardless of failure) or `if ($?) { ... }` (runs only on
> success) — `&&` works in PowerShell 7+ / `pwsh`, which is not what is
> installed here. `cmd.exe` and bash accept `&&`; PowerShell 5.1 does not.

```powershell
cd C:\Users\haroo\OneDrive\Documents\New project\Emergent-AlphaForge
git fetch origin
git checkout claude/hello-g2itta
git pull origin claude/hello-g2itta
# Confirm you are at the tip of the branch (deliberately not a pinned SHA —
# this document should not rot every time the branch moves).
git rev-parse HEAD
git rev-parse origin/claude/hello-g2itta      # the two must match

# The rebuild is REQUIRED. Dockerfile bakes source with `COPY . .` and compose
# bind-mounts ONLY backend/app/strategies/plugins — so a new plugin appears live
# but scripts/ and app/option_screen.py do not exist in a running container until
# the image is rebuilt.
docker compose up -d --build backend

# Suite. On your machine the two Mongo-dependent failures should disappear.
.venv\Scripts\python.exe -m pytest tests -q

# The campaign's own tests
.venv\Scripts\python.exe -m pytest tests\test_option_screen.py tests\test_screen_option_buying_script.py tests\test_screen_option_buying_db_paths.py tests\test_strategy_expiry_regime_trend_continuation.py -q
# expect 123 passed
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

## 4. RESOLVED (2026-08-23, local) — the screen builds series on both indices

> This section was *"the screen builds no ATM series — cause unknown"*. It is
> closed. Full evidence is **§11 of the deliverable**; the summary is here so a
> future reader does not re-run the diagnosis.

**The warehouse was never the problem.** The empty screen was the screen's own
bar lookup, and `cf8c1d6` had already fixed it — written blind in a container
with no data and never run against real data until now.

The pre-`cf8c1d6` builder fetched bars by token, and *both* branches were
unsatisfiable against this warehouse:

- **`contract_key`** — on 61.39% of NIFTY `option_contracts` but only **10.3% of
  `options_1m` rows** (823,829 / 7,967,661), and none in the train slice.
- **`instrument_key`** — **stored in different formats on the two collections.**
  `option_contracts` holds a three-part expired-contract value
  (`NSE_FO|42965|28-11-2024`); `options_1m` holds the two-part `NSE_FO|42965`. In
  a 30,000-row sample: `option_contracts` 18,290 of 22,345 three-part;
  `options_1m` 30,000 / 30,000 two-part. The strings cannot compare equal.

Confirmed by mutation, not by reading: replaying the verbatim pre-fix lookup over
the same 191 train sessions reproduces the failure exactly — `frame empty? True`,
242/242 contract-sessions dropped, `contract_key_EMPTY=56,
instrument_key_EMPTY=186`. The shipped identity lookup returns 236/242, and the
bars it returns carry a single homogeneous `trading_symbol` matching the
requested strike/side/expiry — the right contract, not merely a contract.

**What the screen now measures** (train only, DTE 1–3; holdout untouched, guard
left armed):

| | NIFTY | SENSEX |
|---|---|---|
| Contract-sessions | 236 / 242 | 232 / 234 |
| Bars | 88,500 | 86,997 |
| MFE/MAE @ 5/10/15/30 min | 0.892 / 0.898 / 0.897 / 0.892 | 0.876 / 0.863 / 0.868 / 0.875 |
| Net %, session median | −2.38 / −2.74 / −2.96 / −3.98 | −2.35 / −2.58 / −2.89 / −3.55 |
| Verdict, every cell | **NO_EDGE** | **NO_EDGE** |

Session-level t-stats −32 to −109 over 116–118 sessions. The unconditioned ATM
buyer's payoff is negative before costs on both indices — a fourth independent
confirmation of the register's headline, and the first from shipped tested code.

**§5.2's reproduction gate: passed, with a caveat recorded.** The train-slice
baseline is below the recorded 0.90–0.95 only because it is a 191-session
sub-window. Re-measured over the 403 already-spent sessions (≤ 2026-07-10 — the
window the register used; the 30 untouched sessions were **not** read), NIFTY
gives 0.914 / 0.906 / 0.903 against the register's 0.92 / 0.95 / 0.90, and SENSEX
0.894 / 0.883 / 0.888 against 0.92 / 0.94 / 0.90. The DTE filter was tested and
is innocent. The residual is localised to the **10-minute horizon on both
indices** (−0.044, −0.057); the register's throwaway scripts no longer exist, so
that one cell cannot be re-derived. No conclusion depends on it — everything is
far below the 0.95 base rate in both sources.

Residual `dropped_too_few_bars` (6 NIFTY, 2 SENSEX) are genuine ingestion gaps,
verified against the collection: NIFTY expiry 2024-12-26 has no bars for any
strike within ±300 of 23900; SENSEX 80400 exp 2024-11-29 has 1,500 bars but none
on 2024-11-26.

### 4.1 Two findings, neither blocking — read before the first conditioned run

1. **`--entry-from` / `--entry-to` do not constrain what is measured.** They pick
   the ATM *strike*; the option frame is fetched for the whole day, so **13.6% of
   measured entry bars are outside the window** — and outside live's hardcoded
   09:25–14:50. Effect on the unconditioned baseline is ≤0.010 MFE/MAE, so no
   verdict changes, but a condition that fires at the open would be scored on
   entries live cannot take. Apply the window as a `screen_condition` mask before
   conditioning. Deliverable §11.7.

2. **The suite is 5,098 passed / 5 failed and the 5 are unrelated to this
   branch.** §2's prediction held — the two `test_premium_momentum_route.py`
   failures cleared with a real MongoDB. The 5 are all
   `tests/test_bootstrap_contract.py`; this branch adds nine files and touches no
   launcher. This environment sets `NoDefaultCurrentDirectoryInExePath=1`, so
   `cmd.exe` will not resolve a bare `start-app.bat` from the cwd, and the tests
   invoke it by bare name. Reproduced from PowerShell and bash alike. A fix
   (absolute path) belongs on `main`, not here.

### 4.2 How to re-run it

The host venv has `pandas` and `pymongo`, so no rebuild is needed for the script
path — and Mongo is published on **127.0.0.1** (dial the IPv4 literal, not
`localhost`, which resolves to `::1` first and stalls):

```powershell
cd C:\Users\haroo\OneDrive\Documents\New project\Emergent-AlphaForge
.venv\Scripts\python.exe backend\scripts\screen_option_buying.py --instrument NIFTY --mongo-url mongodb://127.0.0.1:27017
.venv\Scripts\python.exe backend\scripts\screen_option_buying.py --instrument SENSEX --mongo-url mongodb://127.0.0.1:27017
```

The container path still works and still needs `docker compose up -d --build
backend` after a pull, for the reason §2 gives.

---

## 5. The holdout mislabel — FIXED, but read this before using a holdout number

The first screen run printed `holdout > 2025-12-31 : 158 sessions (PROTECTED)`.
Prior campaigns had already read **2026-01-01 → 2026-07-10**
(`PREMIUM_MOMENTUM_EDGE_VERDICT_2026-07.md`), so only ~30 sessions were
untouched — the tool was reporting a holdout 5× larger than the one that exists.

**Fixed.** `chronological_split` takes `consumed_until` and returns a fourth
slice, `consumed`, excluded from the holdout. The CLI defaults it to
`2026-07-10`, prints the consumed count separately, and warns when the true
holdout is under the 60-session promotion minimum.

**The consequence stands and is not fixable by code:** ~30 untouched sessions is
below the 60-session / 120-trade minimum, so **a campaign started now cannot
produce a promotion-grade holdout result.** The remaining evidence has to come
forward from paper, at roughly 20 sessions a month. Do not let a positive number
on 30 sessions be read as promotion evidence — the CLI now prints that warning
for you, and §5.3 of the deliverable says the same.

If you ever pass `--consumed-until ""`, you must first be able to show no prior
campaign touched those sessions.

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
