# AI-agent takeover prompt

_Current as of **2026-08-30**, branch `feat/chain-recorder` @ `39e5f4f`, clean working tree._
_Copy everything below the line into a fresh agent session._

---

You are taking over active development of **AlphaForge Trading Lab** — a local-first
research, forward-test and live-execution app for Indian index options (NIFTY / BANKNIFTY /
SENSEX). React (CRA + craco) frontend on `:3000`, FastAPI backend on `:8001` (**every route
under `/api`**), MongoDB via motor, all in Docker Compose. **Upstox** supplies market data;
**Flattrade** (Noren / PiConnect) is the live broker. **It trades real money when the
operator enables it.**

The loop the app exists to serve: warehouse 1-minute spot + option candles → backtest or
optimize a strategy → save a preset → deploy for signals, paper trading, and (only with
explicit operator consent and hard gates) live execution.

## Read before writing any code

1. **`docs/HANDOFF.md`** — start here. §1.1 tells you where everything lives; §2.0f/§2.0g
   are the most recent work; **§2.1 is four traps that will cost you hours** if you skip it.
2. **`docs/AGENT_TODO.md`** — the live work board. Do not invent priorities.
3. **`docs/BACKTEST_INTEGRITY_AUDIT.md`** — read before trusting any number the app produces.
4. `docs/DEVELOPER_GUIDE.md` and `docs/ARCHITECTURE.md` as needed.

## Run it

`start-app.bat --rebuild --no-browser` rebuilds **both** halves. Call it by ABSOLUTE path —
this machine sets `NoDefaultCurrentDirectoryInExePath=1`, so a bare `call start-app.bat`
reports "not recognized". Frontend `http://localhost:3000`, health
`http://localhost:8001/api/health`.

Read the database directly with:
`docker exec alphaforge_mongo mongosh alphaforge --quiet --eval '<js>'`
In Git Bash a JS regex literal starting with `/` gets path-mangled — use `new RegExp("...")`.

## Testing — there is no single green number

Neither the host nor the container is zero-failure, and both counts are dominated by
environment, not defects (measured 2026-08-30: host 4,254 passed / 80 failed / 56 collection
errors from missing `motor`; container whole-suite 4,870 passed / 309 failed, almost all UI
source-pin tests that read `frontend/` files absent inside the backend container).

**So measure the delta, never the absolute:** run your targeted subset, save the FAILED list,
`git stash` your change, re-run the identical command on clean HEAD, diff the two. Introduced
failures should be zero. And a new test that passes both before and after your fix has not
tested your fix — confirm it fails on clean HEAD.

Host tests are pure/contract/JSX-string-pins. Motor and route tests must run inside the
container (`docker cp tests/. alphaforge_backend:/app/tests`, then
`docker exec -w //app alphaforge_backend python -m pytest tests/<file> -q` — note the double
slash on Windows).

## Rules this project has already paid for

- **Checkpoint before risky work.** Commit the validated state and tag it; keep unvalidated
  work out of that commit. Latest: `checkpoint/validated-3-5-6-2026-08-30`.
- **Get confirmation before changing shared backtest/optimizer computation code**
  (`backtest.py`, `optimizer.py`, `option_backtest.py`, `wfo.py`). A change there reprices
  every future result for every strategy. UI/export-only changes do not need it.
- **Get confirmation before anything that could reach the broker or activate a deployment.**
  Static inspection and dry runs are fine; triggering is not.
- **Never place, modify or cancel a real order**, and never flip a deployment to live mode.
  Also never call the Flattrade MCP's `login`/`logout` — one API key, one redirect URI, and a
  second login invalidates the app's token.
- **Push only when the operator says so.** Commit freely; nothing is auto-pushed.
- **Verify across multiple saved runs, not one.** A positional-join bug passed on all four
  runs first sampled and was only caught by sweeping all 105 — dense-leg runs pass a
  positional join by accident.
- **Never call something fixed without running it.** For UI work that means clicking it in
  the browser. A `CI=true` build compiled cleanly and still shipped a runtime
  `fmtINR is not defined` that blanked a whole page — only opening it caught that.
- **A subagent panel that returns 0 completed agents is not a passed check.** Two workflows
  in the last session died on usage limits and returned nothing; treat that as unverified and
  do the work yourself.
- **Clean up test artifacts.** Probe runs, jobs, presets and deployments must be deleted, and
  saved artifacts must never be modified while investigating.

## Where things stand

Recently fixed and verified: Backtest Lab action buttons (Trades.csv exported the raw spot
list and premium-native runs downloaded the literal string `"(empty)"`); optimizer incumbent
seeding (a known-good preset inside the search space was never evaluated — the same clean
config went from −19,957 to +148,602 INR); bounds transparency; two reporting-unit defects;
and the deploy gate, which was blocking on the optimizer's search bounds and had made 4 of 12
saved presets undeployable.

**Known open, deliberately deferred:** `net_pnl_inr` is `total_pnl_pts × a constant lot_size`,
so it ranks trials identically to `total_pnl_pts` and models no premium — the search optimises
a SPOT proxy while the operator only ever trades options. Making it option-native is blocked
on a measured ceiling (4.38M option rows / 4,294 keys for NIFTY over a 10-month window against
`_option_rerank`'s 4M-row cap) and on reusing Stage-1 trades in Stage 2. **Read the
`AGENT_TODO.md` entry for #1 and #2 before attempting either** — the obvious version of #1
(cache Stage-1 trades) fails validation: `_evaluate` runs in worker processes, so the trades
would have to be pickled back at ~351 KB per trial (~99 MB per job), against an explicit
design rule in `parallel_eval.py`.

There is no roadmap beyond that board. The program is "fix and harden what is here", not a
feature plan. Ask the operator rather than inventing scope.

## Configuration

Secrets live in `backend/.env` (git-ignored) and are injected by Docker Compose — broker
credentials, `FERNET_KEY`, and the `LIVE_AUTOPLACE_ARMED` gate. Never commit `.env`, tokens or
any credential file, and never print secret values into logs, docs or commit messages. Live
auto-placement requires `LIVE_AUTOPLACE_ARMED=1` **and** a deployment the operator personally
set to live mode, within its caps and before the 15:00 IST cutoff.
