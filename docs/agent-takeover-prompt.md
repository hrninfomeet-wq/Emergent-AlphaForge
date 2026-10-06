# AI-agent takeover prompt

_Current as of **2026-09-30**. Git state moves: confirm it yourself with `git status -sb` and
`git log --oneline origin/main..HEAD` before relying on the snapshot below._
_Copy everything below the line into a fresh agent session._

---

You are taking over active development of **AlphaForge Trading Lab**, a local-first research,
paper and live-execution app for Indian index options (NIFTY and SENSEX weeklies are the active
work; BANKNIFTY is also supported). React (CRA + craco) frontend on `:3000`, FastAPI backend on
`:8001` (**every route under `/api`**), MongoDB via motor, all in Docker Compose. **Upstox**
supplies market data; **Flattrade** (Noren / PiConnect) is the live broker (static IP required,
daily OAuth, limit and SL-limit orders only). **It trades real money when the operator enables
it.**

The loop the app serves: warehouse 1-minute spot + option candles → backtest or optimize a
strategy → save a preset → deploy as signal-only, paper, or (only when the operator sets it)
live → the software exit guard manages the position.

## Read before writing any code

1. **`docs/HANDOFF.md`**: start here. §1.1 is where everything lives and §1.2 the four core flows,
   §2 is the current state (§2.5 lists the traps that cost previous agents hours, §2.6 is the
   latest work), §3 is run and test, and §4 is the full set of standing conventions.
2. **`docs/AGENT_TODO.md`**: the only live work board. The next market-session checklist is
   `docs/LIVE_VALIDATION_PLAN_2026-08.md` §1, and AGENT_TODO points to it. Do not invent
   priorities; ask the operator rather than adding scope.
3. **`docs/BACKTEST_INTEGRITY_AUDIT.md`**: read before trusting any number the app produces.
4. `learning_log.md` (dead ends and lessons), then `docs/DEVELOPER_GUIDE.md` and
   `docs/ARCHITECTURE.md` as needed. `CLAUDE.md` / `AGENTS.md` load automatically.

## Where things stand (2026-09-30)

- **Git.** `origin/main` is `13f06f4` (pushed 2026-09-29). Local `main` is ahead by `6c949ad`
  (retire unactioned signals) plus the 2026-09-30 cleanup and docs work. None of that is pushed.
  `main` is the only branch and there are no other worktrees.
- **Suite.** 6,586 passed, 4 xfailed, 0 failed on the host `.venv`. There is **no CI**, so the
  local suite is the only evidence.
- **Latest work: the Live Deployments uplift (2026-09-26 → 09-30).** See HANDOFF §2.6 and
  `docs/superpowers/specs/2026-09-26-live-deployments-uplift-handoff.md`. In short:
  - Reconcile proves exits from the broker trade book (own fills, carry-free, filled qty only).
    Stale OPEN rows close only on proof and are tagged `exit_day_unknown`.
  - The caps governor is split into precheck → measure → decide. Its read-only view is the
    `governor` key on `GET /deployments/live/status`.
  - Status surfaces now say what is true: an expired broker session reads as expired, and guard
    health is one of watching / idle / off_hours / blind / stalled / not_running.
  - A server trading clock (`live/session_clock.describe_session`, `GET /live-broker/session-clock`).
  - New live write routes: `POST /deployments/{id}/live/caps` (tighten-only, compare-and-set) and
    `POST /deployments/{id}/live/flatten` (squares and stays live). Timeline:
    `GET /deployments/{id}/timeline`.
  - A position open across a restart re-attaches to its deployment only when that is provable
    (`live/ownership.resolve_rehydrate_attribution`).
  - The transmit fence (`auto_live._recheck_authorization`) refuses an in-flight order whose caps
    were tightened.
  - Signals: a signal's bar is `candle_ts`. Every paper/live close moves its signal
    ACTIVE → EXITED. `expire_unactioned_signals` (at boot and at 15:00) retires CONFIRMED signals
    that were never acted on to AUDITED with reason `unactioned_bar_passed`.
- **None of the live parts has run in a market session.** The next gate is the market-session
  validation in `docs/LIVE_VALIDATION_PLAN_2026-08.md`: §1 (U1–U8) first, then the rest of the
  plan. The operator runs it on the Flattrade-registered static IP. The reminder fired 2026-10-06
  09:00 IST. The gate stays open until the plan's §11 records an outcome. Realized-P&L
  differences read `ESTIMATED_EXIT`, not PASS (U7 / L6).
- **Next development:** E1, the durable live execution episode ledger with a fail-closed admission
  reservation (`docs/AUTONOMY_DEVELOPMENT_PLAN_2026-08.md`). Then E2 (experiment/cohort ledger),
  then the Stage 2 Dashboard v2 built on it.
- **The resting broker OCO is off by default** (`LIVE_BROKER_OCO_ENABLED=0`, since its stop leg
  fired at placement and was rejected). With it off, the in-process software guard is the only
  protection for an open position, and it runs only while the app runs.
- **No strategy has a proven edge.** These questions are closed; do not re-run them without new
  evidence: `OPTIMIZER_VERDICT_2026-07`, `POOLED_REGIME_VERDICT_2026-07`,
  `PREMIUM_MOMENTUM_EDGE_VERDICT_2026-07`, `PROFIT_LEVERAGE_ANALYSIS_2026-07`,
  `OPTION_BUYING_MICROSTRUCTURE_2026-08` and `INTRADAY_OPTION_BUYING_CANDIDATES_2026-08` (the
  unconditioned ATM baseline has no edge on either index, and the short-side verticals were
  closed).
- **Known open, deliberately deferred:** the optimizer's `net_pnl_inr` objective is
  `total_pnl_pts × a constant lot_size`, so it ranks trials exactly like spot points and models no
  premium. Only the re-ranked finalists see real option P&L. Read `docs/AGENT_TODO.md` items 1 and
  2 before touching this. The obvious fix (cache Stage-1 trades) would pickle every trial's
  trades back from the worker processes, against the small-per-task-payload design in
  `parallel_eval.py`.

## Run it

- **Launch:** `start-app.bat` (flags `--rebuild`, `--no-browser`, `--check-only`). It starts
  Docker Desktop itself. Call it by **absolute path**: this machine sets
  `NoDefaultCurrentDirectoryInExePath=1`, so a bare `start-app.bat` is "not recognized".
- **Deploy a code change:** `docker compose up -d --build`, and only with no open live position,
  because recreating the backend briefly interrupts the software guard. Backend code is baked
  into the image; only `backend/app/strategies/plugins` is bind-mounted. **A restart is not a
  rebuild**, and `start-app.bat` without `--rebuild` reuses a healthy backend, so old code keeps
  running. Confirm the image has your change:
  `MSYS_NO_PATHCONV=1 docker exec alphaforge_backend grep -c <symbol> /app/app/<file>.py`.
- **Browser:** `http://localhost:3000`, never `127.0.0.1:3000`, because `CORS_ORIGINS` allows only
  `http://localhost:3000`. **Host scripts** dial `127.0.0.1` (for example
  `mongodb://127.0.0.1:27017`), because `localhost` resolves to `::1` first and stalls about 2 s
  against IPv4-only Docker.
- **Database:** `docker exec alphaforge_mongo mongosh alphaforge --quiet --eval '<js>'`. In Git Bash,
  a JS regex literal starting with `/` gets path-mangled; use `new RegExp("...")`.
- **The operator's shell is Windows PowerShell 5.1**, where `&&` is a parser error and neither
  command runs. Give one command per line, or `A; if ($?) { B }`.

## Test it

```bash
./.venv/Scripts/python.exe -m pytest tests/ -q -p no:cacheprovider   # whole suite, host, ~4 min
cd frontend && CI=true npx --no-install craco build                   # frontend gate: warnings fail it
```

- Run the suite on the host `.venv`, which needs `node` for the frontend-logic tests. The backend
  image is built from `backend/` only (no `tests/`, no `frontend/`), so a suite copied into the
  container reds every test that reads `frontend/`.
- A new test must **fail on clean HEAD** (stash your change and re-run). A test that passes before
  and after the fix has not tested the fix. For safety rules, mutate the code and confirm a test
  goes red.
- Put frontend decision logic in `frontend/src/lib/*.js` and test it by executing it through node,
  not by grepping JSX.
- For UI work, finish in the browser: hard-reload (Ctrl+Shift+R) and click the thing.

## Never do these

- **Never call `mcp__flattrade__login` or `logout`.** The MCP shares AlphaForge's single API key,
  and a second login invalidates AlphaForge's live token (last login wins). Recover a stale MCP
  session with `backend/scripts/resync_mcp_session.py --clean`. Details are in
  `docs/flattrade-mcp-integration.md`.
- **Never place, modify or cancel an order**, through the MCP or anything else. Never set a
  deployment to live mode, never enable, resume or flatten one, and never change
  `LIVE_AUTOPLACE_ARMED`. Static inspection and dry runs are fine; triggering is not.
- **Never push without the operator's explicit approval for that changeset.** Commit locally;
  nothing is auto-pushed.
- **Never assume a restart deployed your code.** Rebuild and verify in the container, as above.
- **Never invent a number.** Every count, P&L, timing or test total you write in a doc, commit or
  report must come from a command you ran. If you did not measure it, say so.
- **Never change shared backtest or optimizer computation** (`backtest.py`, `optimizer.py`,
  `option_backtest.py`, `wfo.py`) without the operator's go-ahead. It reprices every future result
  for every strategy. UI-only and export-only changes do not need it.
- **Never commit** `.env`, tokens or credentials, and never print secret values.
- **Never run `docker compose down -v`**; it deletes the warehouse volume.
- **Never reintroduce the removed per-deployment ARM ceremony or the research-qualification
  gate**, and do not add a new live gate unasked. Propose it and let the operator decide.
- **Never treat a subagent panel that returned 0 completed agents as a passed check.** It is
  unverified; do the check yourself.

## Rules this project has already paid for

- **Checkpoint before risky work.** Commit the validated state and tag it, keeping unvalidated work
  out of that commit. Latest tag: `checkpoint/validated-3-5-6-2026-08-30`.
- **Verify across many saved runs, not one.** A positional-join bug passed on the four runs first
  sampled and was only caught by sweeping all 105.
- **Never call something fixed without running it.** A `CI=true` build compiled cleanly and still
  shipped a runtime `fmtINR is not defined` that blanked a page; only opening it caught that.
- **Safety gates fail closed.** A check that cannot verify must refuse, not report "ok".
- **Clean up after yourself.** Delete probe runs, jobs, presets and deployments you create, and
  never modify saved artifacts while investigating.

## Configuration

Secrets live in `backend/.env` (git-ignored; template `backend/.env.example`) and reach the
container through Docker Compose: broker credentials, `FERNET_KEY`, `LIVE_AUTOPLACE_ARMED` (the
entry master switch, `0` in the template) and `LIVE_BROKER_OCO_ENABLED` (`0`). A deployed entry
transmits only when `LIVE_AUTOPLACE_ARMED=1` **and** the operator has set that deployment to live
mode **and** the broker is connected, the time is before the 15:00 IST cutoff, and the caps allow
it. Without the env gate, the executor dry-runs and sends nothing.
