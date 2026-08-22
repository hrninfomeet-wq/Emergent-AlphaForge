# AI-agent takeover prompt (as of 2026-08-15, `main` @ `c2b3d7a` + verified working tree)

You are taking over active development of **AlphaForge Trading Lab** — a local-first
research + forward-test + live-execution app for Indian index options (NIFTY / BANKNIFTY /
SENSEX). Repo: `Emergent-AlphaForge` (GitHub: `hrninfomeet-wq/Emergent-AlphaForge`).
React (CRA + craco) frontend `:3000`, FastAPI backend `:8001` (**all routes under `/api`**),
MongoDB (motor), Docker Compose. **Upstox** = market data; **Flattrade** (Noren / PiConnect
OMS) = live broker execution. It trades **real money** when the operator enables it.

## Read these first, in this order — before writing any code

1. **`docs/HANDOFF.md`** — START HERE. Current state. Read §2 in full, especially **§2.0c**
   (the 2026-08-14 live session and the two defects a green test suite did not catch).
2. **`docs/TAKEOVER_CHECKLIST.md`** — what to DO, in order: safety rules, setup, verified
   status, lessons, known limitations, and the one-source-of-truth map.
3. **`docs/BACKTEST_INTEGRITY_AUDIT.md`** — read before trusting ANY number the app produces.
4. **`docs/AGENT_TODO.md`** — the live work board. Do not invent priorities.
5. `docs/DEVELOPER_GUIDE.md` (deep onboarding) and `docs/ARCHITECTURE.md` (module map) as needed.

Then run the suite and confirm the baseline before changing anything:
`.venv/Scripts/python.exe -m pytest tests -q` → **4,896 passed, 4 xfailed, 0 failed**.

## Where the app stands

| | |
|---|---|
| Branch | `main` at `c2b3d7a`, **2 commits ahead of `origin/main` (`c5d380b`)**, plus the verified 2026-08-15 working tree; do not discard the existing prompt edit |
| Suite | 4,896 passed / 4 xfailed / 0 failed |
| Real money traded | **Twice** — 2026-08-04 (journalled wrong; fixed) and 2026-08-14 (exposed a live/paper parity break) |
| Proven edge? | **No.** Three independent campaigns failed a holdout. Do not re-litigate without new data. |
| What blocks live | Not code — a **Flattrade-registered static IP** and a market-hours validation session |
| Active program | Capability work: make backtest/paper/live fully usable. Edge hunting is parked. |

The 2026-08-14 live session is the most important recent context. Correlating the live trade
against the SAME deployment's paper trade exposed that **live silently discarded
`risk.exit_controls`** — paper ratcheted its stop and booked +₹4,882.69 while live dropped the
trail entirely. Separately, a commit of mine had **broken every Flattrade API call** by
percent-encoding `jData`; the suite was green because the implementation and its test shared the
same wrong assumption about the server. Both are documented in `HANDOFF.md` §2.0c.

## ⛔ Non-negotiable safety rules — operator decisions, do not relitigate

**Two of these can silently destroy the operator's live broker session.**

1. **NEVER call `mcp__flattrade__login` / `mcp__flattrade__logout`.** The Flattrade MCP shares
   AlphaForge's single API key and Flattrade is **last-login-wins** — a second login silently
   invalidates the app's live token. Recover a stale MCP session with
   `backend/scripts/resync_mcp_session.py --clean`. Read tools are fine and useful.
2. **Never place, modify, cancel or square any broker order** — via the app or the MCP. Keep even
   read calls sparse; the rate budget is shared with the operator's live account.
3. **Never flip a deployment to live mode.** Going live is exclusively the operator's manual act.
4. **Never refresh Flattrade OAuth while `LIVE_AUTOPLACE_ARMED` is on**; never create a second
   Flattrade API key.
5. **Do not add new live-ARMING gates or research-qualification gates.** Both were removed on
   explicit instruction (v0.56.0). A **data-integrity** gate that blocks a NEW live activation
   when today's candles cannot be verified is a different thing and is intentional.
6. **Push only with per-changeset operator approval.** Commit freely at green milestones.
7. **Never commit** `.env`, tokens, credentials, or MCP client configs.
8. `realized_pnl` is the caps basis and stays **GROSS** — a ₹5,000 cap means ₹5,000 of premium
   move, excluding charges. The operator confirmed this explicitly.

## Load-bearing technical invariants — each closed a real bug

* **Two symbol spaces.** Upstox `trading_symbol` ("NIFTY 24300 PE 18 AUG 26") vs Noren
  `noren_tsym` ("NIFTY18AUG26P24300"). Joining them wrongly once journalled an OPEN live position
  as CLOSED. Always join by id/timestamp, never by array position.
* **`exit_controls` has ONE decider.** `app/exit_controls.effective_premium_stop` is called by the
  sim, by paper, and now by live. If you need the behaviour somewhere new, **call it** — do not
  translate its config into another schema. That translation is exactly what silently dropped
  every trail on the live path.
* **The software guard is the real protection.** The broker OCO is a PC-down backstop and on this
  account usually cannot rest (a resting NRML sell is margined as a naked short).
* **Empty position book == UNKNOWN, never flat.** Fail safe.
* **`_raise_stop` is a monotonic ratchet** — the sole writer of `stop_level`. Never bypass it.
* **WebSockets cannot supply history.** Same-day candles come from Upstox's *intraday* endpoint
  (no date args) or Flattrade TPSeries (explicit `st`/`et`); the plain historical endpoint is
  empty for the current day. Never write the in-progress minute — `persist_candles_df` is
  last-writer-wins with no merge.
* **`candles_1m` has a unique index on `(instrument, ts)`.** Read `ts`, never the `datetime`
  string — two datetime formats coexist and sort inverted against each other.
* **Spot is 375 bars/day, options 385** (the index freezes 15:15–15:30 for the closing auction
  while F&O runs to 15:40). `app/session_spec.py` models this; use it.

## How to work here — earned the hard way

* **A contract cannot be validated against a mock that shares the implementation's assumption.**
  When the thing under test is an INTERFACE (a wire format, a schema another module consumes),
  hit the real other side at least once. This has bitten twice.
* **Drive the code; never grep source for behaviour.** Source-text assertions have misfired 5+
  times against CORRECT implementations. 112 test files still do this — converting them is a
  standing backlog item.
* **Test frontend logic through node**, not by reading JSX. Put logic in `frontend/src/lib/*.js`
  and drive it from a test.
* **Suspect the fixture before the code** when a brand-new test fails.
* **Audit your own commits** with the same machinery you use on others'. Two regressions I
  introduced passed the full suite and were caught only by an adversarial pass.
* **Verify a claim before repeating it** — including one from a subagent. One "live-safety
  escalation" dissolved on contact with the backend docstring.
* **Red-before-green.** Write the failing test first; if you wrote the code first, mutate it and
  confirm the test actually fails.
* Backend edits need `docker compose up -d --build backend` (the image bakes code in). Frontend
  edits need a hard reload (**Ctrl+Shift+R**) or you will debug a stale bundle. On Windows,
  prefix `docker exec` paths with `MSYS_NO_PATHCONV=1`.

## Current priorities (unless the operator redirects)

1. **Market-session validation** of the live path — `docs/LIVE_VALIDATION_PLAN_2026-08.md`.
   Most live-path changes have never run in a real session.
2. The optimizer HIGH/MED register is **closed**; disputed LOW #31 remains separate —
   `BACKTEST_INTEGRITY_AUDIT.md`.
3. **38 UNVERIFIED findings** in `docs/live-cockpit-audit-2026-07-25.md` — that file is a live
   backlog, not history.
4. The evaluator's NIFTY-only wakeup was fixed in the 2026-08-15 working tree: NIFTY,
   BANKNIFTY and SENSEX now wake evaluation independently. It is regression-tested but still
   needs market-session validation.
5. No same-day candle source for **option** contracts (Upstox intraday serves only the 3 index
   keys). Live exits are unaffected — the guard marks from the broker position book.
6. `frontend/src/components/live/PositionMonitor.jsx` is unmounted — an L2-era manual test-order panel. Wire it or delete it;
   deployed-position exits do not depend on it.

**Research verdicts are CLOSED questions.** `OPTIMIZER_VERDICT`, `POOLED_REGIME_VERDICT`,
`PREMIUM_MOMENTUM_EDGE_VERDICT` and `PROFIT_LEVERAGE_ANALYSIS` each carry a **pre-registered**
kill or revival criterion, and several are cited from source code. Do not reopen one without new
data, and do not delete one — a deleted verdict gets re-litigated at real cost.

Ask before any irreversible or outward-facing action. Report honestly: if a test fails, say so
with the output; if something is unverified, say "unverified" rather than implying it works.
