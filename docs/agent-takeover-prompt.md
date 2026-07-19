# AI-agent takeover prompt (as of v0.55.2, 2026-07-19)

_Copy-paste the block below as the first message to a new AI agent taking over this repo.
It is self-contained; keep it in sync when the app state changes materially._

---

You are taking over active development of **AlphaForge Trading Lab** — a local-first
research + forward-test + live-execution app for Indian index options (NIFTY / BANKNIFTY /
SENSEX). Repo: `Emergent-AlphaForge` (GitHub: `hrninfomeet-wq/Emergent-AlphaForge`).
React (CRA) frontend :3000, FastAPI backend :8001 (all routes under `/api`), MongoDB
(motor), Docker Compose. **Upstox** = market data; **Flattrade** (Noren/PiConnect) =
live broker. This app can place REAL-MONEY orders when armed — treat every change to
`backend/app/live/`, the deployment/guard/recovery seams, or the broker-token path as
safety-critical.

## Orient yourself in this order (before writing any code)

1. `docs/HANDOFF.md` — START HERE: current state, standing conventions, doc map.
2. `docs/DEVELOPER_GUIDE.md` — run/build/test, live-safety model (§E, read twice),
   warehouse model, India rules, **Gotchas (§H — read fully; every item was paid for)**.
3. `docs/ARCHITECTURE.md` (module map, Mongo collections, L0–L3 gate chain) and
   `docs/STRATEGY_DEPLOYMENTS.md` (deployment/arm/guard model) as needed.
4. `CHANGELOG.md` top entries (0.53.x → 0.55.2) for how the current state was reached.
5. `docs/flattrade-mcp-integration.md` **before touching the broker token path or using
   the Flattrade MCP tools** — the account is shared with a separate MCP server now.

## Where the app stands (v0.55.2)

All subsystems are built and integrated on `main` (sole branch): data warehouse,
Backtest Lab, Optimizer (honest OOS + survival gate), Strategy Library with AI authoring,
paper trading, gated live execution with a layered safety stack (confirm-flat guard, kill
switches, per-token recovery), the premium-momentum strategy family including Phase 5B
multi-leg live/paper execution, and Flattrade-MCP session sharing.

- **Host test baseline: 3486 passed, 4 xfailed** — `.venv\Scripts\python.exe -m pytest tests -q`
  from the repo root. Motor/route tests run **inside the backend container** instead
  (`docker cp tests/. alphaforge_backend:/app/tests` then `docker exec -w /app ... pytest`).
  Confirm this baseline on your machine before changing anything.
- **Git state:** `origin/main` = `10f68d1` (v0.55.1). Local `main` is **ahead** with the
  v0.55.2 Flattrade-MCP work (`f67f463`) plus docs commits — **unpushed by design**.
  Always run `git log origin/main..main --oneline` before describing "current state".
- **Nothing since 2026-07-12 has run in a real market-hours session.** The first paper
  validation follows `docs/phase5b-market-validation-runbook.md` — read it before
  interpreting any live/paper result (it scopes what paper structurally CANNOT exercise:
  the guard-side 5B exits — lazy arming, exit_time, recovery join).

## Non-negotiable standing rules (user decisions — do not relitigate)

- **Never place, square, modify or arm a real order yourself** — through the app OR the
  Flattrade MCP's write tools. Arming is exclusively the user's manual act
  (`LIVE_AUTOPLACE_ARMED` / `LIVE_GUARD_ARMED` + per-deployment ARM).
- **Never call the Flattrade MCP's `login` / `logout` tools.** One API key ⇒ one redirect
  URI (AlphaForge owns it) ⇒ the MCP cannot OAuth on its own, and Flattrade is
  last-login-wins so a second login would silently kill AlphaForge's live session.
  The user's AlphaForge login is the ONLY login. Stale MCP session ⇒
  `backend/scripts/resync_mcp_session.py --clean`.
- **Never create a second Flattrade API key.** API V2 = one key per account; a second
  requires the paid registered-algo tier (₹5,000+GST per exchange, for >10 orders/sec).
  The user has declined it, and AlphaForge is orders of magnitude below that threshold.
- **Commit freely at green milestones; push ONLY on explicit user request.**
- **Never commit** `.env`, tokens, credentials, or MCP client configs.
- **Do not add any new live-arming gate** — ride the existing arm/gate/cap chain
  (`DEVELOPER_GUIDE.md` §E). Extra "safety" gates were explicitly removed once on user
  request. Propose, don't impose.
- **The premium-momentum edge verdict stands**: the family FAILED its pre-registered gate
  (`docs/PREMIUM_MOMENTUM_EDGE_VERDICT_2026-07.md`). 5B exists as a user-decided pure
  capability. Don't propose premium-momentum tuning work unless that doc's pre-registered
  revival criterion is met.
- **Honesty over convenience**: report failing tests verbatim; a review/verification step
  that dies incomplete is UNVERIFIED, not passed; prefer refuse-with-explanation over
  silent degradation; never let a `| tail` pipe mask a pytest exit code before committing.
- **Rebuild the backend container after backend edits** (code is baked into the image).
  Browser-verify frontend changes with **Ctrl+Shift+R** (CRA bundle goes stale; client-side
  navigation does not reload JS).
- Verify India-market facts (lot sizes, expiry cadence, holidays) against
  `instruments.py` / `nse_calendar.py` / `dte.py` — **never from memory**; they rotate.

## Load-bearing technical invariants (each one closed a real bug)

- `live/executor.py` is the sole real-order ENTRY chokepoint; **broker-confirmed-flat** is
  the sole position finalizer (never place-accept).
- A broker **read failure is UNKNOWN, never flat** — an empty position book must never be
  interpreted as "position closed".
- Recovery resolves premium-momentum leg symbols exclusively via the broker order book's
  `norenordno→tsym` join. Persisted `trading_symbol` is **Upstox-space** and must never be
  matched against the **Noren-keyed** position book; unresolvable ⇒ skip, never mark exited.
- All IST `HH:MM` comparisons go through `premium_momentum.normalize_hhmm` — raw string
  compares are **fail-open** for unpadded times (a cutoff that never fires).
- Any new option-stream subscription rebuild site must union in `premium_pin_keys()`.
- Paper exits ride `live_exit_monitor.py` and never touch `premium_locks`; the 5B exit
  machinery (lazy arming, exit_time, per-leg finalize) is **live-guard-only**.
- **Option-leg joins:** `index_trade_id` must always be a position in the **full** spot-trade
  list. `simulate_paired_option_trades` numbers whatever list it receives, so any caller
  that filters first (DTE filter) MUST remap afterwards. Join by id or `signal_entry_ts`,
  **never by array position**. (v0.55.1 — this presented as a fake "wrong strike/side
  pairing" bug.)
- **NSE/BSE reuse exchange tokens across expiry cycles**: 2-part canonical instrument keys
  can map to two different contracts (time-disjoint in practice). Any lookup by 2-part key
  must stay time-windowed or expiry-constrained.
- Test fakes for broker interfaces must model the **real two-symbol-space world** (Upstox
  vs Noren strings deliberately different) — self-consistent fakes have hidden production
  bugs here before.

## Tips & tricks learned building this (save yourself the rediscovery)

- **Symptom ≠ location.** Twice now, a "trading logic" bug was a display/join bug
  (0.55.1) or an infrastructure gap (the tick→candle roller not started ⇒ "0 trades all
  day"). Before diving into strategy math, verify the plumbing and the row/id alignment.
- **Reproduce against the real DB before theorizing.** `pymongo` is available in the repo
  venv (`.venv\Scripts\python.exe`); querying `backtest_runs` / `options_1m` directly
  settles most "is the data wrong?" questions in one script. Write throwaway scripts to
  the scratchpad, not the repo.
- **Prove a regression test fails pre-fix.** Stash-free method: `git show HEAD:<file> >
  <file>`, run pytest, then restore. (Do **not** use `git stash` casually here — the repo
  has old stash entries and a mis-typed `stash push` once applied unrelated work.)
- **The optimizer saves multiple sibling run docs per job** (different option configs,
  seconds apart, same display name). When a user reports something odd in a saved run,
  find *all* matching docs — the one they opened may not be the one you inspect.
- **Whole-suite-in-container always fails path-contract tests** — judge container runs by
  motor/route results only; run the full suite on the host.
- **Closed-source binaries can still be interrogated safely.** A `strings` scan of a Go
  binary reveals `json:"…"` struct tags, hostnames and tool names without executing it —
  that is how the MCP's API surface and session schema were established.
- **Go's `json.Unmarshal` ignores unknown fields** — writing a *superset* payload with
  every plausible field alias is a robust way to satisfy an unpublished schema (used by
  `mcp_session_sync.py`; validated first try).
- **Sync/side-effect hooks on the auth path must never raise.** The MCP sync is wrapped so
  a failure only logs — the user's login must always succeed.
- **Docker + host-file interop:** the backend runs in a container, so anything it must
  write to the host filesystem (like the MCP session file) needs an explicit bind mount.
- **`.venv` is pandas 3.0.3**: `date_range` yields µs resolution, so `asi8 // 1_000_000`
  silently gives epoch **seconds** — pin the unit first with `as_unit("ms")`.
- **Tailwind `min-h-0` loses the cascade on flex children** — use inline
  `style={{minHeight: 0}}`.
- **sklearn is load-bearing** via optuna's lazy import even though nothing imports it
  directly — don't "clean it up".
- **Run one optimizer instrument at a time**; the analyzing stage ignores pause/cancel
  (needs a backend restart), and heavy option re-ranks want `opt_workers=1`.

## Current next steps (priority order, unless the user redirects)

1. **Market-hours paper validation** per `docs/phase5b-market-validation-runbook.md`;
   diagnose findings. The Flattrade MCP is now a genuinely useful second channel here —
   use its read-only tools (`get_positions`, `get_order_book`,
   `subscribe_order_updates`) as an **independent broker-truth witness** against
   AlphaForge's own blotter and guard state.
2. After a clean paper day: the **1-lot live validation day**
   (`docs/live-readback-checklist.md` + runbook §6) — the user performs all arming.
3. Known deferred items (CHANGELOG 0.55.0 caveats): per-leg chips on the Live strip
   (needs `_live_status_payload` to surface `sig.premium_momentum`), a direct firing-branch
   test for `exit_time`, `opt_workers>1` for premium_momentum, the declarative
   config-block builder UI.
4. Optional/unscheduled idea: an **AlphaForge-native read-only MCP** exposing intent/guard
   state (which the broker-side MCP structurally cannot know). Complementary, not a
   replacement. Not started.
5. New edge research must follow the pre-registration discipline in
   `docs/PREMIUM_MOMENTUM_EDGE_VERDICT_2026-07.md` (three-way chronological split, costs
   mandatory, untouched holdout, kill criteria written BEFORE running).

Start by reading the docs in the order above, run the host test suite to confirm the
**3486 passed / 4 xfailed** baseline on your machine, check `git log origin/main..main`,
and give the user a short readback of the current state plus your plan for their first
request before changing anything.

---
