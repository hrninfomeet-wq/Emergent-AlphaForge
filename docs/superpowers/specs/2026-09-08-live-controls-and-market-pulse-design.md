# Live-trading controls + Market Pulse rework — design

**Date:** 2026-09-08
**Status:** Stage 1 (controls) approved for build. Stage 2 (Market Pulse) specified, deferred.

Three operator-requested improvements to `/live-trading`. The user chose
**"controls now, pulse later"**: sections 1–3 build now, section 4 is the
standalone spec for a later session.

---

## 1. Remove the typed-ENABLE gate

### Problem
`DeployToLivePanel` step 2 requires typing `ENABLE` exactly. The user considers
the existing consent checkbox sufficient friction.

### Constraint found during design
That checkbox only renders when forward validation **fails**
(`unvalidated === true`, `DeployToLivePanel.jsx:530`). A deployment that PASSES
validation has no checkbox at all — so simply deleting the typed gate would
leave the validated path with *no* affirmative consent, only a button click.

### Design
Step 2 keeps its review content (caps summary, exits-in-force, arm advisories)
and drops the text input. **One checkbox always renders**, wording follows the
evidence:

| Evidence state | Checkbox wording | Style |
|---|---|---|
| validated | "I approve real-money trading for this deployment with the caps above." | neutral |
| unvalidated | existing red block listing the failed checks (unchanged) | danger |

`ENABLE — Go Live` is enabled by the checkbox alone.

**Backend contract unchanged.** `accept_unvalidated_live` stays
`Boolean(unvalidated && consent)` — it goes true only on the unvalidated path,
so `explicit_unvalidated_live_consent_required` still behaves exactly as before.

### Test impact
`tests/test_live_exit_preview_ui.py:240` slices source at `disabled={confirmText`.
Retarget it at the new disabled condition. Its assertion — *the exit preview must
not gate ENABLE* — remains true and still needs pinning.

---

## 2. Reversible live pause

### Problem
The user wants pause/resume on live deployments. Copying the paper strip's
semantics is unsafe: `_set_deployment_status` (`runtime.py:2571`) demotes
`mode: live -> paper` on **any** transition out of ACTIVE. So today Pause and Stop
both silently revoke live authorization, and Resume returns the deployment as
**paper**. Re-going-live needs the full caps + consent ceremony again.

That invariant is deliberate and correct: it stops resume / re-pin / un-retire —
which set status back to ACTIVE and inspect nothing else — from silently
re-authorizing real money.

### Design — gate entries, not status

The pause flag is checked in `is_deployment_live_allowed` (`live/mode.py:270`),
*the* authorization predicate for the auto-live entry path:

```python
if bool((deployment.get("risk") or {}).get("live", {}).get("paused")):
    return False, "live_paused"
```

Why this is safe:

* It blocks **new entries only**. `mode` and `status` are untouched, so the
  v0.56.0 invariant is never engaged — nothing transitions out of ACTIVE,
  nothing gets demoted.
* Resume is not "silently re-authorizing": authorization was never lost. This is
  an explicit, visible, operator-initiated hold, distinct in both state and
  reason code from pause / archive / drift.
* It fails closed, like every other branch in that predicate.
* **Open positions keep being managed.** The software guard, OCO backstop, exit
  monitor and reconcile all run off the position registry, not this predicate.
  A paused deployment still exits its book normally.

### Surface

| Route | Effect |
|---|---|
| `POST /deployments/{id}/live/pause` | set `risk.live.paused = true` + `paused_at` |
| `POST /deployments/{id}/live/resume` | clear `paused` + `paused_at` |

* Both 404 on unknown id; pause 409s if the deployment is not in live mode
  (pausing a paper deployment through the live route is a category error —
  the paper Pause is the right control there).
* `/live/enable` and `/live/disable` both **clear** the flag, so a stale pause
  can never be inherited by a fresh enable.
* `_live_status_payload` gains `live_paused` so the strip can render the state.
* Stop-ALL still reaches paused deployments — it selects `{"mode": "live"}`,
  which a paused deployment still matches. That is the safe direction.

### UI wording
`paused != flat`. The row must state that open positions remain open and are
still guarded. Copy: *"Paused — no new entries. Open positions stay open and
keep their stop/target."*

---

## 3. Live Deployments pane on the page

### Problem
`LiveDeploymentStrip` already exists but is mounted inside the ⚙ Configure
drawer (`ConfigDrawer.jsx:120`), so there is no at-a-glance view of what is
trading, and it has no pause/resume.

### Design
* **Move** the strip out of the drawer onto the page, full-width, directly under
  the alert rail — above the two-column core. Not a second mount: a duplicate
  would double-fire `onArmedSummaryChange` and double the control surface.
* The drawer keeps GTT/OCO backstop + Overall controls.
* `DeploymentSummary`'s "Real-money controls are in ⚙ Configure" pointer is
  updated to reference the pane.
* `LiveRow` gains Pause / Resume beside Disable / Stop, driven by
  `live_paused`. Row dot: pulsing red = live trading, amber = live but paused.

---

## 4. Market Pulse rework — DEFERRED SPEC

Not built in this pass. Everything below is the design to execute later.

### 4.1 Diagnosis

**Wrong instrument.** `LiveDataProvider.jsx:70` hardcodes
`api.marketAnalysis("NIFTY")`. Half the user's deployments are SENSEX, so the
card describes an index they are not trading.

**Self-contradicting regime.** `classify_structure` (`market_analysis.py:74`):
ADX 28.2 with `direction=flat` yields `kind="transitional"` but `label="CHOPPY"`.
Chop is *definitionally* low ADX. High ADX with no net direction means the move
**reversed inside the window** — two-way volatility, the opposite of chop, and a
condition the explosive-reversal strategies care about. Needs its own label plus
an exhaustive label-vs-kind consistency test.

**Degenerate S/R.** Observed: S 23773 / pivot 23773 / R 23787 — a 14-point band,
support equal to spot. Five compounding causes:

| # | Cause | Effect |
|---|---|---|
| 1 | `lookback=60` on **1-minute** bars (`context_signals.py:133`) | the whole S/R universe is one hour of micro-swings |
| 2 | `levels_from_sr` takes the **nearest** level each side (`market_analysis.py:244`) | picks the tightest pair from an already-narrow window |
| 3 | `_cluster_levels` returns cluster **means**, discarding touch count and recency | a level touched 7x ranks identically to one touched once (the docstring claims recency-weighting; there is none) |
| 4 | `pivot = (h+l+c)/3` of the **last 1-minute bar** (`market_analysis_build.py:181`) | pivot == spot; a floor pivot uses the *previous day's* HLC |
| 5 | no daily / prior-day / opening-range / OI / round-number sources | only micro-swings are candidates |

### 4.2 New engine — `backend/app/sr_levels.py`

Pure, host-testable, no I/O — same design rules as `market_analysis.py`.

**Stage 1 — harvest.** Each candidate carries provenance:

| Source | Derivation |
|---|---|
| `SWING` | swing-high/low clusters, **full session** lookback (not 60 bars) |
| `PDH` / `PDL` / `PDC` | previous IST day's high / low / close |
| `PIVOT` P/S1/S2/R1/R2 | classic floor pivots from the **previous day's** HLC |
| `ORH` / `ORL` | opening-range high/low (first 15m) |
| `OI_WALL` | peak CE OI (resistance) / peak PE OI (support) |
| `MAXPAIN` | already computed |
| `ROUND` | round-number magnets |

`OI_WALL` and `MAXPAIN` come from `options.chain`, already in the same payload —
zero extra cost, and peak-OI strikes are the most-watched S/R in Indian index
options.

> **Round-number spacing MUST be derived from the instrument's own price scale,
> never a constant.** SENSEX is ~3.2x NIFTY's point scale; a hardcoded step
> reproduces the documented project-wide blind spot that made point-bounded
> params non-transferable across indices.

**Stage 2 — confluence merge.** Candidates within `0.25 x ATR` collapse into one
level. `strength` = recency-decayed touch count + source-diversity bonus,
normalized 0..1. A swing cluster sitting on an OI wall *and* a round number
outranks a lone micro-pivot.

**Stage 3 — selection.** Top-N by **strength**, not proximity. Each level emits
`price`, `distance_pts`, `distance_atr`, `sources[]`, `touches`, `strength`.

**Stage 4 — honesty guard.** If the selected band is narrower than `0.5 x ATR`
it is noise: emit `sr_band_degenerate` and render "spot is inside noise — no
meaningful band" rather than a bar. This is the direct fix for the 14-point range.

**Data access (approved):** aggregate prior-day HLC server-side via the same
`$group`-by-IST-day pipeline `_daily_closes` already uses, extended to
high/low; widen the intraday window to the full session. One extra aggregation
per 8s cache refresh.

### 4.3 Display — a ladder, not a marker

```
R2  23,890  ######..  1.4 ATR   OI WALL x2 · ROUND
R1  23,845  ####....  0.7 ATR   SWING x3 · PDH
--  23,773  <- spot
S1  23,710  #####...  0.9 ATR   SWING x4 · PIVOT S1
S2  23,640  ###.....  2.0 ATR   PDL · ROUND
```

ATR distance says whether a level is reachable this session; provenance chips
make each level auditable rather than asserted.

### 4.4 Card splits into three panels

* **Session** — instrument tabs driven by *deployed* instruments (kills the
  NIFTY hardcode), regime in plain English, open->now, day-range position, time
  left in session, expiry/DTE, IV percentile stated in words
  ("premium expensive — VIX in the 78th percentile of 30d").
* **S/R ladder** — section 4.3.
* **Your deployments right now** — per live deployment: evaluating vs stalled
  (`last_evaluated_ts`), signalled today?, blocked with reason
  (`signals.blockers`), or "no setup seen". Every field already exists. This is
  the "why isn't it trading" answer, and it matches the documented triage step
  of checking `signals` first — a no-trade session is normal for these
  strategies (sensex_explosive_reversal takes zero trades on ~33% of sessions).

### 4.5 Non-goals
No predictive claim. The pulse describes and attributes; it never asserts that a
regime *favours* a strategy without measured evidence.
