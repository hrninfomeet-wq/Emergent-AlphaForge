# Takeover checklist — the first hour

_Written 2026-08-15. For a new engineer or AI agent inheriting AlphaForge._

> **This is the checklist, not the orientation.** Read [`HANDOFF.md`](HANDOFF.md) first — it is
> the START HERE document and it has the current state. This file tells you what to *do*, in
> order, and what will hurt you if you skip it.

---

## 0. The 10 minutes that save you a day

| # | Do this | Why |
|---|---|---|
| 1 | Read [`HANDOFF.md`](HANDOFF.md) §2 — especially **§2.0c** | The last live session found two defects a green suite did not. One cost a real trade. |
| 2 | Read [`BACKTEST_INTEGRITY_AUDIT.md`](BACKTEST_INTEGRITY_AUDIT.md) before trusting ANY number | Every paired-option backtest saved before 2026-07-30 is wrong. 13 verified-but-unfixed findings remain, 4 of them HIGH, all in the optimizer. |
| 3 | Read the **safety rules** in §3 below, in full | These are not style preferences. Two of them can silently kill the operator's live broker session. |
| 4 | Run the suite: `.venv/Scripts/python.exe -m pytest tests -q` | Baseline **4,887 passed / 4 xfailed / 0 failed**. If it differs, find out why before writing code. |
| 5 | Skim [`AGENT_TODO.md`](AGENT_TODO.md) | The live work board. Do not invent priorities. |

---

## 1. Purpose, scope, verified status

**AlphaForge Trading Lab** is a local-first research + forward-test app for Indian index options
(NIFTY / BANKNIFTY / SENSEX). Warehouse 1-minute candles → backtest/optimize → save a preset →
deploy for signals, paper trading, and (only on the operator's explicit act) live Flattrade
execution.

**Verified status as of 2026-08-15:**

| Question | Answer |
|---|---|
| Real money traded? | **Twice.** 2026-08-04 (recorded wrong, since fixed) and 2026-08-14 (exposed the `exit_controls` parity break). |
| Does any strategy have a proven edge? | **No.** Three independent campaigns failed a holdout. Do not re-litigate without new data — the verdicts are in `*VERDICT*.md` and their kill criteria were pre-registered. |
| What blocks live? | Not code. A **Flattrade-registered static IP** and market-hours validation. |
| Suite | 4,887 passed / 4 xfailed / 0 failed |
| Branch | `main`, clean, level with `origin/main` |

---

## 2. Architecture and the flows that matter

Stack: **React** (CRA + craco) `:3000` · **FastAPI** `:8001` (every route under `/api`) ·
**MongoDB** `:27017` — all in Docker Compose. **Upstox** = market data. **Flattrade** (Noren /
PiConnect) = broker execution.

Deep reference: [`ARCHITECTURE.md`](ARCHITECTURE.md). The four flows worth knowing before you
touch anything:

1. **Candles.** Ticks → `live_candle_roller` → `candles_1m`. The roller only aggregates ticks it
   personally witnesses, so a mid-session boot leaves a hole; `candle_recovery` closes it from
   the REST APIs at startup and on the feed supervisor. **WebSockets cannot supply history.**
2. **Signal → order.** `deployment_evaluator` → `auto_live.resolve_live_exit_plan` →
   `live_deploy_context.arm_for` → `executor` (the single real-order chokepoint) →
   `live_position_guard` registers the position.
3. **Exits.** The software guard is the real protection: premium stop, target, and the
   `exit_controls` overlay via `exit_controls.effective_premium_stop` — the SAME decider the sim
   and paper use. The broker OCO is a PC-down backstop only, and on this account it usually
   cannot rest (a resting NRML sell is margined as a naked short).
4. **Two symbol spaces.** Upstox `trading_symbol` ("NIFTY 24300 PE 18 AUG 26") vs Noren
   `noren_tsym` ("NIFTY18AUG26P24300"). **Joining them wrongly is a recurring real-bug source** —
   it once journalled an open live position as CLOSED.

---

## 3. ⛔ Safety rules — non-negotiable

**Two of these can silently destroy the operator's live session. Read them twice.**

1. **NEVER call `mcp__flattrade__login` / `logout`.** The Flattrade MCP shares AlphaForge's single
   API key, and Flattrade is **last-login-wins** — a second login silently invalidates the app's
   live token. Recover a stale MCP session with `backend/scripts/resync_mcp_session.py --clean`.
   Detail: [`flattrade-mcp-integration.md`](flattrade-mcp-integration.md).
2. **Never place, modify, cancel or square any broker order** — through the app or the MCP. Read
   tools are fine; keep them sparse (shared rate budget with the live account).
3. **Never flip a deployment to live mode.** Going live is exclusively the operator's manual act.
4. **Never refresh Flattrade OAuth while `LIVE_AUTOPLACE_ARMED` is on**, and never create a second
   API key.
5. **Do not add new live-ARMING gates or research-qualification gates.** Both were removed on
   explicit instruction (v0.56.0). A **data-integrity** gate blocking a NEW activation when
   today's candles cannot be verified is a different thing and is intentional.
6. **Push only with per-changeset operator approval.** Commit freely at green milestones.
7. **Never commit** `.env`, tokens, credentials, or MCP client configs.
8. `realized_pnl` stays the caps basis and stays **GROSS** — the operator confirmed a ₹5,000 cap
   means ₹5,000 of premium move, excluding charges.

---

## 4. Setup, run, test, validate

Full detail: [`LOCAL_SETUP.md`](LOCAL_SETUP.md) · [`STARTUP_MANUAL.md`](STARTUP_MANUAL.md) ·
[`DEVELOPER_GUIDE.md`](DEVELOPER_GUIDE.md).

```bash
docker compose up -d --build backend        # backend edits need a rebuild — the image bakes code in
.venv/Scripts/python.exe -m pytest tests -q # host suite; baseline 4,887
cd frontend && CI=true npx --no-install craco build
```

* Backend is `:8001`, every route under `/api`. Mongo is `alphaforge_mongo`, db `alphaforge`.
* On Windows, prefix `docker exec` paths with `MSYS_NO_PATHCONV=1`.
* Frontend changes need a hard reload (**Ctrl+Shift+R**) or you will debug a stale bundle.
* Before a market session: [`LIVE_VALIDATION_PLAN_2026-08.md`](LIVE_VALIDATION_PLAN_2026-08.md).

---

## 5. How to work here without repeating our mistakes

These are earned, each from a defect that shipped green. Full narrative in
[`../learning_log.md`](../learning_log.md).

| Rule | The failure that taught it |
|---|---|
| **A contract cannot be validated against a mock that shares the implementation's assumption.** When testing an INTERFACE (wire format, schema another module consumes), hit the real other side at least once. | `jData` percent-encoding: impl used `urlencode`, its test used `parse_qs`, both agreed, production returned HTTP 400 on **every** Flattrade call. |
| **Drive the code; never grep source for behaviour.** | Source-text assertions have misfired 5+ times against CORRECT implementations. **112 test files still assert on source text** — `test_intraday_backfill.py` is named `..._are_wired` yet only checks the route is *declared*, so it could not notice the route had zero callers. |
| **Test frontend logic through node, not by reading JSX.** Put logic in `frontend/src/lib/*.js` and drive it. | A card used a `text-warn` class this theme does not define; it would have rendered colourless and no amount of grepping would have shown it. |
| **Suspect the fixture before the code.** | Four separate false failures: a wrong epoch constant, a stub missing `prd`, a clock mismatch, an assumed field. |
| **Audit your own commits with the machinery you use on others'.** | `358fcc3` and `58ef491` were regressions I introduced; both passed the full suite and an adversarial audit caught them. |
| **When two modules must agree, make one call the other** — do not translate between them. | Live spoke a different `exit_controls` dialect than paper/sim and silently discarded the config. The fix DELEGATES to the shared decider. |
| **Verify a claim before repeating it.** | An audit agent reported "the per-position Square is unreachable, FLATTEN is your only manual exit". Checking the backend showed that route squares the manual **test** position; deployed exits were never affected. |

---

## 6. Known limitations and unfinished work

Prioritised. The board is [`AGENT_TODO.md`](AGENT_TODO.md).

1. **No strategy has a proven edge.** Everything else is capability work.
2. **13 verified-but-unfixed findings**, 4 HIGH, all optimizer —
   [`BACKTEST_INTEGRITY_AUDIT.md`](BACKTEST_INTEGRITY_AUDIT.md).
3. **41 UNVERIFIED findings** in [`live-cockpit-audit-2026-07-25.md`](live-cockpit-audit-2026-07-25.md)
   — that file is a **live backlog**, not history.
4. **Flattrade TPSeries fallback is implemented and live-verified, but Upstox stays primary** —
   the two vendors disagree on the `open` of ~2 bars in 60 (max 1.85 pts). Mixing sources within
   one day mixes conventions.
5. **No same-day candle source for OPTION contracts.** Upstox intraday serves only the 3 index
   keys. Live exits are unaffected (the guard marks from the broker position book).
6. **The evaluator's new-bar trigger is hardcoded to NIFTY** (`runtime.py:962-967`) — if NIFTY
   stalls, no SENSEX deployment is evaluated either.
7. **112 source-text test files** to convert to behavioural tests, over time.
8. **`PositionMonitor.jsx` is unmounted** — an L2-era manual test-order panel. Wire it or delete
   it; deployed-position exits do not depend on it.

---

## 7. One clear source of truth

| Topic | Authoritative file |
|---|---|
| **Start here / current state** | [`HANDOFF.md`](HANDOFF.md) |
| Do-this-first checklist | **this file** |
| Deep onboarding, run/build/test | [`DEVELOPER_GUIDE.md`](DEVELOPER_GUIDE.md) |
| Technical reference, module map | [`ARCHITECTURE.md`](ARCHITECTURE.md) |
| Can I trust this number? | [`BACKTEST_INTEGRITY_AUDIT.md`](BACKTEST_INTEGRITY_AUDIT.md) |
| Live work board | [`AGENT_TODO.md`](AGENT_TODO.md) |
| Next market session | [`LIVE_VALIDATION_PLAN_2026-08.md`](LIVE_VALIDATION_PLAN_2026-08.md) |
| Broker API (58 endpoints, vision-verified) | [`Resources/flattrade-pi-api/INDEX.md`](Resources/flattrade-pi-api/INDEX.md) |
| MCP / broker session rules | [`flattrade-mcp-integration.md`](flattrade-mcp-integration.md) |
| Real-money readback runbook | [`live-readback-checklist.md`](live-readback-checklist.md) |
| Per-session lessons | [`../learning_log.md`](../learning_log.md) |
| Version history | [`../CHANGELOG.md`](../CHANGELOG.md) |

**Research verdicts are CLOSED questions.** `OPTIMIZER_VERDICT`, `POOLED_REGIME_VERDICT`,
`PREMIUM_MOMENTUM_EDGE_VERDICT`, `PROFIT_LEVERAGE_ANALYSIS` each carry a **pre-registered** kill
or revival criterion, and several are cited from source code. Do not reopen one without new data,
and do not delete one — a deleted verdict gets re-litigated at real cost.

**Dated session records** (`STAGE1_INTEGRITY_SESSION_HANDOFF_2026-08-01`, `audit-report-2026-07`,
`live-exit-controls-parity-2026-08-14`, `midsession-startup-recovery-2026-08`) are historical
evidence. They are correct **as of their date** and are not maintained; `HANDOFF.md` is.
