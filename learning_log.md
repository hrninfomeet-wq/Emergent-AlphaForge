# Learning Log

Orchestrator session log: one core lesson, confirmed approaches, and dead ends per execution,
so the next session starts smarter. Newest entry first.

---

## 2026-07-21 — Codex release-audit triage (Claude Fable 5 session)

**Task:** Assess the ChatGPT-5.6 Codex session's "release audit failed" verdict (5 critical
blockers, 8 high-risk findings) against the real codebase; decide implement vs. neglect,
with focus on paper-trading-in-live-market and real-money readiness.

### Core lesson

**Verify an external agent's audit against the actual code before acting on it.** The Codex
audit cited file paths that don't exist in this repo (`backend/app/api/`,
`backend/app/services/`, `backend/app/live/auto_live.py`), yet most of its capital-safety
findings verified TRUE at the real locations. Neither blind trust nor blind dismissal
survives contact with the code — every claim needed a file:line confirmation, and one
critical claim (the "broken" activation dialog) looks likely to be a test artifact
(stale frontend bundle — a known, documented gotcha of this project) rather than a code bug.

### Confirmed findings (verified inline, file:line)

| ID | Claim | Verdict | Where |
|----|-------|---------|-------|
| C1 | No auth / network isolation | CONFIRMED (predates Codex) | `docker-compose.yml:7,35` publishes Mongo+backend on all interfaces, credential-less Mongo, no API auth |
| C2 | Stop doesn't fence in-flight orders | CONFIRMED (predates Codex) | `executor.py:459` single `allow_fn()` check; `auto_live.py:409` closes over stale doc + frozen `now` |
| C3 | Limits per-deployment, not account-global/atomic | CONFIRMED (predates Codex) | `live_deploy_governor.py:105` queries by `deployment_id` only; `engine.py:264` `guardrail_tick` has test-only callers |
| C4 | Loss-breaker resumable without re-consent | CONFIRMED (predates Codex) | `routers/deployments.py:852` resume→ACTIVE with no pause-reason / mode check |
| C5 | Live activation dialog broken | UNPROVEN | Code reads correct (loaded-flags always set, button disabled matches submit guard). Likely stale-bundle repro or Radix double-modal stacking (`DeployToLivePanel.jsx:241` + `:427` both open simultaneously). Needs browser retest after hard refresh |
| H1 | Enable/stop race, no compare-and-swap | CONFIRMED | `routers/deployments.py:1166` plain `$set` by id |
| H2 | NaN accepted, disables loss breaker | CONFIRMED | `routers/deployments.py:249` no finiteness validator; governor `loss_cap > 0` is False for NaN |
| H3 | Safety config fails open to 20 lots | CONFIRMED | `live_deploy_context.py:264-269` |
| H6 | OCO failure tolerated | CONFIRMED but DELIBERATE | `live_deploy_context.py:167-217` documented design: never unwind a filled+guarded entry |
| H7 | Consent not server-verifiable | TRUE but moot | Single-operator local app; meaningless until an auth layer exists (C1) |
| H4/H5 | Premium-momentum deploy rejection; preset validation parity | NOT YET VERIFIED | Check during lazy-leg / strategy-builder work |

### Key facts that reframe the Codex verdict

- **The user's deployment-freedom request is ALREADY implemented** in the uncommitted diff:
  `routers/deployments.py:1095-1129` makes forward-validation advisory with explicit
  `accept_unvalidated_live` consent override. Broker/capital gates stay hard. Paper deploys
  are gated only by warning acknowledgment (`:404-431`) — never blocked.
- **Most confirmed blockers predate the Codex session** — they are v0.56.0 gaps in files the
  Codex diff never touched (executor.py, live_deploy_governor.py, docker-compose.yml).
  Reverting the Codex diff would fix nothing and lose the consent flow + option-data
  provenance work.
- Nothing found blocks PAPER trading in live market hours: the paper path transmits no
  broker orders; C2/C3/C4-class risks are live-only.

### Confirmed approaches

- Targeted inline verification (Grep for the mechanism → Read ±40 lines) confirmed/refuted
  11 of 13 findings in ~15 tool calls without burning subagent budget.
- Reading the transcript tail-first (verdict section) + grep for section headers beat
  reading 16k lines linearly.
- Checking `git status` provenance per finding ("is the vulnerable code in the uncommitted
  diff or in HEAD?") instantly settled the keep-vs-revert debate.

### Dead ends to avoid

- **23-agent workflow fan-out died on the monthly spend limit** (17/23 agents failed;
  ~1.3M subagent tokens spent for 6 chunk digests). Until the limit resets/raises: work
  inline, single-threaded, lean. Don't retry workflows this month.
- Codex's cited file paths are unreliable — always re-locate by mechanism (Grep), not path.
- Don't judge C5-class UI bugs from code reading alone; this project's frontend serves
  stale bundles after rebuilds (hard-refresh first, then test).

### Addendum (same session, after fixes landed)

- **The Codex audit tested the RUNNING CONTAINERS, not its own patched working
  tree.** Its diff had already fixed H2 at the route level
  (`deployments.py:1029` finite+positive `daily_loss_cap` mandatory) and made
  stop demote live→paper — yet the audit reported both as broken. Corollary: C5
  (the "broken" activation dialog) was very likely reproduced against the old
  bundle; retest in a browser after rebuild + hard refresh before touching the
  component.
- **C4 was real but tiny:** the breach path paused without demoting `mode` —
  while the enable route's docstring already CLAIMED "pauses and demotes". A
  1-line `$set` addition + tests closed it (estimated half a day, took minutes).
  Lesson: when code contradicts its own documented contract, the fix is usually
  the missing line, not a redesign.
- **Dead end:** adding a pydantic `field_validator` for finiteness broke the
  existing test contract (tests construct the body model directly with NaN and
  expect the ROUTE to raise 400). Route-level checks already existed; the right
  defense-in-depth layer was the governor (protects against DB-crafted docs).
  Reverted the validator, kept the governor guard.
- **PowerShell gotcha:** a here-string commit message containing double quotes
  got re-tokenized and split into bogus pathspecs. Write commit messages to a
  temp file and use `git commit -F <file>`.
- Landed: `d301272` (Codex baseline), `4b441fd` (orchestrator docs), `f9a2482`
  (H3 fail-closed + H2 governor guard + C4 breach demotion + C1-lite loopback).
  Suite 3,530/0. Unpushed.

### Open items carried forward

1. Safety-fix sprint (pending user decision on scope): H2+H3 (trivial), C1 loopback
   binding (trivial), C4 resume re-consent, C2 transmit fence, C5 browser verification,
   H1 CAS, C3 account-global wiring.
2. User's items 2-8: lazy-leg Phase 5, strategy-builder audit, live-page redesign, new
   strategy plugins, profit-leverage ideas, end-to-end audit, handover docs.
3. Uncommitted Codex diff (~2.7k lines, 50 files) needs a commit decision + full suite run.
4. H4/H5 verification.
