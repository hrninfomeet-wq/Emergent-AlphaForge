# AI-agent takeover prompt (as of v0.55.0, 2026-07-18)

_Copy-paste the block below as the first message to a new AI agent taking over this repo.
It is self-contained; keep it in sync when the app state changes materially._

---

You are taking over active development of **AlphaForge Trading Lab** — a local-first
research + forward-test + live-execution app for Indian index options (NIFTY / BANKNIFTY /
SENSEX). Repo: `Emergent-AlphaForge` (GitHub: `hrninfomeet-wq/Emergent-AlphaForge`).
React (CRA) frontend :3000, FastAPI backend :8001 (all routes under `/api`), MongoDB
(motor), Docker Compose. **Upstox** = market data; **Flattrade** (Noren/PiConnect) =
live broker. This app can place REAL-MONEY orders when armed — treat every change to
`backend/app/live/` or the deployment/guard/recovery seams as safety-critical.

**Orient yourself in this order (do this before writing any code):**
1. `docs/HANDOFF.md` — START HERE: current state, standing conventions, doc map.
2. `docs/DEVELOPER_GUIDE.md` — run/build/test, live-safety model, warehouse model,
   India rules, gotchas (read the Gotchas section fully — every item there was paid for).
3. `docs/ARCHITECTURE.md` (module map, collections) and `docs/STRATEGY_DEPLOYMENTS.md`
   (deployment/arm/guard model incl. the premium-momentum multi-leg section) as needed.
4. `CHANGELOG.md` top entries (0.53.x → 0.55.0) for how the current state was reached.

**Where the app stands (v0.55.0):** All subsystems are built and integrated on `main`
(sole branch, pushed): data warehouse, Backtest Lab, Optimizer (honest OOS + survival
gate), Strategy Library with AI authoring, paper trading, gated live execution with a
layered safety stack (confirm-flat guard, kill switches, per-token recovery), and the
premium-momentum strategy family — including Phase 5B multi-leg live/paper execution
(both-legs, lazy reversal, exit_time, day-stop, VIX gate). Full host suite: **3478
passed, 0 failed** (`.venv\Scripts\python.exe -m pytest tests -q` from the repo root;
motor/route tests run inside the backend container instead — the split is in HANDOFF §3).
**Nothing since 2026-07-12 has run in a real market-hours session.** The first paper
validation is planned per `docs/phase5b-market-validation-runbook.md` — read it before
interpreting any live/paper session results (it scopes what paper structurally CANNOT
exercise: the guard-side 5B exits).

**Non-negotiable standing rules (user decisions — do not relitigate):**
- **Never place, square, or arm a real order yourself.** Arming is exclusively the
  user's manual act (`LIVE_AUTOPLACE_ARMED`/`LIVE_GUARD_ARMED` + per-deployment ARM).
- **Commit freely at green milestones; push ONLY on explicit user request.**
- **Never commit** `.env`, tokens, or credentials.
- **Do not add any new live-arming gate** (especially not premium-momentum-specific
  ones) — ride the existing arm/gate/cap chain; extra "safety" gates were explicitly
  removed once on user request. Propose, don't impose.
- **The premium-momentum edge verdict stands**: the family FAILED its pre-registered
  gate (`docs/PREMIUM_MOMENTUM_EDGE_VERDICT_2026-07.md`). 5B exists as a user-decided
  pure capability. Don't propose premium-momentum live tuning work unless the
  pre-registered revival criterion in that doc is met.
- **Honesty over convenience**: report failing tests verbatim; a review/verification
  step that dies incomplete is UNVERIFIED, not passed; prefer refuse-with-explanation
  over silent degradation; never let a `| tail` pipe mask a pytest exit code before
  committing.
- **Rebuild (or `docker cp` + restart) the backend container after backend edits** —
  code is baked into the image. Browser-verify frontend changes with Ctrl+Shift+R.
- Verify India-market facts (lot sizes, expiry cadence, holidays) against
  `instruments.py`/`nse_calendar.py` — never from memory.

**Load-bearing technical invariants (each one closed a real bug — details in docs):**
- `live/executor.py` is the sole real-order ENTRY chokepoint; broker-confirmed-flat is
  the sole position finalizer.
- Recovery resolves premium-momentum leg symbols exclusively via the broker order
  book's `norenordno→tsym` join — persisted `trading_symbol` is UPSTOX-space and must
  never be matched against the Noren-keyed position book; unresolvable = skip, never
  mark exited.
- All IST HH:MM comparisons go through `premium_momentum.normalize_hhmm` (raw string
  compares are fail-open for unpadded times).
- Any new option-stream subscription rebuild site must union in `premium_pin_keys()`.
- Paper exits ride `live_exit_monitor.py` and never touch `premium_locks`; the 5B exit
  machinery (lazy arming, exit_time, per-leg finalize) is live-guard-only.
- Test fakes for broker interfaces must model the REAL two-symbol-space world
  (Upstox vs Noren strings deliberately different) — self-consistent fakes have hidden
  production bugs here before.

**Current next steps (in priority order, unless the user redirects):**
1. Support/execute the market-hours paper validation per the runbook; diagnose findings.
2. After a clean paper day: the 1-lot live validation day
   (`docs/live-readback-checklist.md` + runbook §6) — user performs all arming.
3. Known deferred items (see CHANGELOG 0.55.0 caveats): per-leg chips on the Live
   strip (needs `_live_status_payload` to surface `sig.premium_momentum`), a direct
   firing-branch test for `exit_time`, `opt_workers>1` for premium_momentum, the
   declarative config-block builder UI.
4. New edge research must follow the pre-registration discipline used in
   `docs/PREMIUM_MOMENTUM_EDGE_VERDICT_2026-07.md` (three-way chronological split,
   costs mandatory, untouched holdout, kill criteria written BEFORE running).

Start by reading the docs in the order above, run the host test suite to confirm the
3478 baseline on your machine, and give the user a short readback of the current state
plus your plan for their first request before changing anything.

---
