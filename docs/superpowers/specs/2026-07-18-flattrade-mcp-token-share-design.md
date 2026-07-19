# Flattrade MCP token sharing — single-key coexistence (2026-07-18)

## Problem
Flattrade's official Trading MCP (closed-source Go binary, `C:\tools\flattrade-mcp-windows-amd64.exe`,
9,062,912 bytes) authenticates via its own browser OAuth on `localhost:7080`. Flattrade's API V2
retail-algo policy (enforced 2026-01-12) allows ONE API key per account for non-registered algos, and
that key's redirect URI is registered to AlphaForge's callback (`127.0.0.1:8001/api/flattrade/auth/callback`).
Consequences, verified by binary strings + PiConnect docs + field evidence (repo issue #1):
- The MCP can NEVER complete its own OAuth on this key (redirect always lands on AlphaForge).
- A second key requires the paid registered-algo tier (₹5,000/exchange strategy submission) — rejected.
- Fresh OAuth invalidates the prior token (last-login-wins), so dual independent logins are unsafe.

## Decision (user-approved: Approach A, auto-sync)
AlphaForge remains the single OAuth owner. After its daily token exchange, the backend mirrors the
fresh jKey into the MCP's session file (`~/.flattrade/session.json`), so one login serves both.
PiConnect docs sanction stored-token reuse ("once generated the token can be stored to bypass
authentication for subsequent connects" — endpoints/02, line 62).

## Schema strategy
The binary's session struct is not fully recoverable statically (Go name blob is global), but binary
strings confirm the field vocabulary (`jKey`, `jkey`, `token`, `susertoken`, `uid`, `actid`,
`user_id`, `api_key`, `saved_at`) and the hosts (piconnect.flattrade.in, authapi.flattrade.in — the
exact API AlphaForge uses). Go `json.Unmarshal` ignores unknown fields, so the writer emits a
SUPERSET payload carrying every plausible alias with the same values; only a wrong TYPE could break
parsing (risk accepted: `saved_at` as RFC3339, Go's default time encoding). Escape hatch: env
`FLATTRADE_MCP_SESSION_TEMPLATE` (JSON with `__JKEY__`/`__UID__`/`__ACTID__`/`__API_KEY__`/`__NOW_ISO__`
tokens) overrides the payload without a code change if first validation reveals a different shape.

## Components
1. `backend/app/live/mcp_session_sync.py` — stdlib-only module: `build_session_payload()`,
   `sync_session_file()` (atomic temp+`os.replace`, skip when the existing file already carries the
   same jKey, no-op unless `FLATTRADE_MCP_SESSION_DIR` is set, never raises out of the public fn).
2. Callback hook — `flattrade_auth_callback` (routers/live_broker.py) calls `sync_session_file()`
   right after `save_token()`; failures log a warning and never break the login flow.
3. `backend/scripts/resync_mcp_session.py` — host-side manual/recovery sync: reads the jKey from
   Mongo `live_broker_tokens`, writes `%USERPROFILE%\.flattrade\session.json`; `--clean` deletes the
   file first (the accepted recovery for the MCP's known stale-session lockout, repo issue #1).
4. docker-compose — bind-mounts `C:/Users/haroo/.flattrade` into the backend as `/host-flattrade`
   and sets `FLATTRADE_MCP_SESSION_DIR=/host-flattrade` (machine-specific path is acceptable: this
   compose file is already single-machine).

## Operating rules (accepted by user 2026-07-18)
- Never invoke the MCP's `login`/`logout` tools (login side-effect-refreshes AlphaForge's token and
  kills the previous one; logout wipes the synced session — recover with the resync script).
- MCP write tools are enabled at the user's direction; MCP-placed orders are INVISIBLE to
  AlphaForge's SL-backstop/OCO/kill-switch (user explicitly accepts). Claude reads freely but never
  invokes order-placing/modifying tools itself (hard platform rule).
- Shared per-key rate budget (40 req/s, 200/min; orders 10/s, 40/min): keep MCP queries sparse while
  deployments are armed.
- API secret is NEVER written to the session file (api_key alias only, which is not a credential on
  its own).

## Validation plan (off-market-hours, user-driven finish)
1. User fills real env values in the Claude Desktop config scaffold (placeholders written by Claude)
   and/or registers via `claude mcp add` for Claude Code.
2. User logs in via AlphaForge as usual → session.json appears/updates.
3. In a fresh Claude session, one read-only MCP call (funds/positions) must succeed AND AlphaForge's
   broker status must remain connected (proves shared-token coexistence).
4. If the MCP rejects the session file: adjust via `FLATTRADE_MCP_SESSION_TEMPLATE`, re-run resync.

## Testing
- Unit: payload superset aliases + RFC3339 `saved_at`; atomic write; same-jKey skip; env-unset no-op;
  template override substitution.
- Contract: callback source calls `sync_session_file` after `save_token` (repo's source-pin idiom).
- No live-order tests, ever.
