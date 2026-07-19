# Flattrade MCP integration — operations guide

_How the official Flattrade Trading MCP server shares AlphaForge's single Flattrade API key,
what it can do, and the rules that keep it from breaking live execution._

**Status:** built + live-validated 2026-07-19 (v0.55.2, commit `f67f463`).
**Design spec:** [`superpowers/specs/2026-07-18-flattrade-mcp-token-share-design.md`](superpowers/specs/2026-07-18-flattrade-mcp-token-share-design.md).

---

## 1. What this is (and what it is not)

Flattrade ships an official **Model Context Protocol** server that exposes the user's live
Flattrade account to any MCP-capable AI client (Claude Desktop, Claude Code, etc.). It is a
**separate product from AlphaForge** — a closed-source binary that talks to the same PiConnect
(Noren) API AlphaForge uses. It is installed on the user's machine and gives the AI assistant a
conversational read/write surface onto the real broker account.

It is **not** part of AlphaForge's execution path. AlphaForge's own live stack (executor, guard,
kill switches, caps) is untouched by it. The only code AlphaForge contributes is a small
**token-sharing** module so both can run off one login.

### Provenance / trust notes (verified 2026-07-18)

| Claim (marketing) | Reality (verified from primary sources) |
|---|---|
| "Open source, MIT licensed" | Repo `github.com/flattrade/flattrademcp` contains **only a README + 6 precompiled binaries**. No source, `license: null` via the GitHub API. |
| `npx @flattrade/mcp` | **No such npm package** (`npm view` → E404). Real install = download the platform binary from `dist/`. |
| "17+ tools" | README documents ~13; the **running server actually exposes 44 tools** (see §4). |
| Signed binary | **Unsigned** — the README itself documents overriding macOS Gatekeeper. |

The binary was **not** decompiled or executed during analysis; the capability surface below was
read from the live MCP handshake after install, and the transport/API host facts from a static
strings scan (`piconnect.flattrade.in`, `authapi.flattrade.in`, `jData/jKey` conventions —
i.e. it is a PiConnect client, same API as `live/flattrade_client.py`).

---

## 2. Why token sharing was necessary (the one-key constraint)

Flattrade's **API V2 / retail-algo standards, enforced 2026-01-12**, allow **one API key per
account** for non-registered algos. A second key exists only on the *registered-algo* tier, which
requires submitting a strategy for exchange approval — a **₹5,000 + GST non-refundable fee per
exchange**, and only relevant above **10 orders/second** (AlphaForge is orders of magnitude below
that; it must never be pushed onto this tier for convenience).

That single key can hold exactly **one registered redirect URI**, and AlphaForge already owns it:

```
FLATTRADE_REDIRECT_URI = http://127.0.0.1:8001/api/flattrade/auth/callback
```

The MCP binary expects its own callback on `http://localhost:7080`. Consequences:

1. **The MCP can never complete its own OAuth on this key** — Flattrade always redirects the
   browser to AlphaForge's callback, never to the MCP's listener.
2. Even if it could, Flattrade appears to be **last-login-wins**: a fresh OAuth invalidates the
   prior token (field evidence: flattrademcp GitHub issue #1). A mid-day MCP login would silently
   kill AlphaForge's live session, and AlphaForge's expiry heuristic is purely time-based
   (06:00 IST cutoff in `flattrade_token.py`) so it would **misdiagnose** the failure.

**Therefore:** AlphaForge is the sole OAuth owner, and it hands its token to the MCP. The
PiConnect docs explicitly sanction stored-token reuse
(`Resources/flattrade-pi-api/endpoints/02-login-flow-session-token.md`: *"once generated the token
can be stored to bypass authentication for subsequent connects"*).

---

## 3. How the sharing works

```
User clicks "Connect Flattrade" (Live page)
  → browser OAuth on Flattrade
  → GET /api/flattrade/auth/callback  (routers/live_broker.py)
      → exchange_code_for_token()
      → save_token()                       → Mongo live_broker_tokens
      → sync_session_file()                → ~/.flattrade/session.json   ← NEW
      → maybe_run_live_recovery(force=True)
  → MCP is now authenticated, no separate login
```

| Piece | Path | Role |
|---|---|---|
| Sync module | `backend/app/live/mcp_session_sync.py` | Builds the session payload; atomic write; skips when the token is unchanged; **never raises** (a sync failure must not break login) |
| Callback hook | `backend/app/routers/live_broker.py` (`flattrade_auth_callback`) | Calls `sync_session_file()` immediately after `save_token()` |
| Recovery script | `backend/scripts/resync_mcp_session.py` | Host-side manual re-sync from Mongo; `--clean` deletes the file first |
| Container plumbing | `docker-compose.yml` | Bind-mounts the host `.flattrade` dir → `/host-flattrade`; sets `FLATTRADE_MCP_SESSION_DIR` |
| Tests | `tests/test_mcp_session_sync.py` | Payload aliases, atomic write, skip-same-token, env-unset no-op, never-raises, callback ordering pin |

### The superset-payload trick

The binary's session schema is **not public**. `build_session_payload()` therefore writes a
**superset** of every plausible field alias observed in the binary's string table:

```json
{ "uid": …, "actid": …, "user_id": …, "client_id": …,
  "jKey": …, "jkey": …, "token": …, "susertoken": …,
  "api_key": …,            // only when FLATTRADE_API_KEY is set — omitted otherwise
  "saved_at": "<RFC3339>" }
```

The file holds a **live session token in plaintext** (as does its Mongo source,
`live_broker_tokens`). Treat it as a credential: it is bind-mounted into the backend
container, must never be committed, and is worth deleting if you hand the machine to anyone.

Go's `json.Unmarshal` **ignores unknown fields**, so extra aliases are free; only a wrong *type*
could break parsing (hence `saved_at` in RFC3339, Go's default time encoding). This was validated
first-try against the real binary — no override was needed.

**Escape hatch** if a future binary version changes the schema: set
`FLATTRADE_MCP_SESSION_TEMPLATE` to a JSON template using the tokens `__JKEY__`, `__UID__`,
`__ACTID__`, `__API_KEY__`, `__NOW_ISO__`. No code change required.

**The API secret is never written to the session file** — only the session token and the
(non-credential) api_key alias.

### Environment variables

| Variable | Where | Meaning |
|---|---|---|
| `FLATTRADE_MCP_SESSION_DIR` | backend container (set in compose) | Target dir for `session.json`. **Unset ⇒ sync is a no-op** (the feature is opt-in). |
| `FLATTRADE_MCP_SESSION_TEMPLATE` | backend (optional) | JSON template override for the session payload. |
| `FLATTRADE_USER_ID` / `FLATTRADE_API_KEY` / `FLATTRADE_API_SECRET` | **MCP client config**, not AlphaForge | The MCP binary's own env. Same key values AlphaForge uses. |

---

## 4. Capability surface (44 tools, from the live handshake)

| Group | Tools |
|---|---|
| Auth | `login`, `logout`, `check_login` |
| Quotes / reference | `get_quote`, `get_tick`, `get_ticks`, `search_scrip`, `get_security_info`, `get_option_chain` |
| History | `get_candles`, `get_daily_candles` |
| Portfolio | `get_positions`, `get_holdings`, `get_limits`, `get_order_margin` |
| Books | `get_order_book`, `get_order_history`, `get_trade_book` |
| **Orders (write)** | `place_order`, `modify_order`, `cancel_order`, `convert_product` |
| **GTT / OCO (write)** | `place_gtt_order`, `modify_gtt_order`, `cancel_gtt_order`, `get_pending_gtt_orders`, `get_enabled_gtts`, `place_oco_order`, `modify_oco_order`, `cancel_oco_order` |
| **Alerts (write)** | `set_alert`, `modify_alert`, `cancel_alert`, `get_pending_alerts` |
| Streaming | `subscribe_ticks`, `unsubscribe_ticks`, `watch_price`, `subscribe_order_updates`, `unsubscribe_order_updates`, `get_order_updates`, `subscribe_position_updates`, `unsubscribe_position_updates`, `get_position_updates`, `ws_debug` |

Capabilities AlphaForge does **not** otherwise wrap: native `get_option_chain`, real-time
**order/position update streams**, per-order **margin probe**, price **alerts**, and `search_scrip`.

---

## 5. Operating rules (NON-NEGOTIABLE)

1. **Never call the MCP's `login` or `logout` tools.**
   - `login` cannot succeed on this key (redirect mismatch) **and** side-effect-refreshes the
     account token, which would kill AlphaForge's live session.
   - `logout` wipes the synced `session.json`.
   - The user's AlphaForge login is the **only** login. Recover with the resync script.
2. **The AI assistant never places, modifies, or cancels orders.** The write tools exist for the
   *user's* direct use. This mirrors AlphaForge's own standing rule (`HANDOFF.md` §4) — the
   assistant never personally transmits a real order, regardless of which surface is available.
3. **MCP-placed positions are invisible to AlphaForge's protections.** The guard, OCO backstop,
   SL monitor and kill switch reconcile against AlphaForge's own intent store; a foreign order has
   no matching intent record. The user has explicitly accepted this trade-off — but never assume a
   position seen in the MCP is protected by AlphaForge.
4. **Rate budget is shared.** PiConnect caps are per key: **40 req/s, 200 req/min** globally and
   **10 req/s, 40 req/min** on order endpoints
   (`Resources/flattrade-pi-api/endpoints/57-api-rate-limits.md`). AlphaForge's reconcile/guard
   polling is safety-critical — **keep MCP queries sparse while deployments are armed.**
5. **Never create a second API key** to "fix" a conflict — that path leads to the ₹5,000 tier and
   the user has declined it. See §2.

---

## 6. Runbook

### Daily (normal use)
Log in through AlphaForge as usual (Live page → Connect Flattrade). `session.json` is rewritten
automatically at that instant. Nothing else to do. Tokens clear ~05:00–06:00 IST daily, so this is
a once-per-trading-day action, exactly as before.

### Verify the sync worked
```bash
# host
python -c "import json;d=json.load(open(r'C:\Users\<you>\.flattrade\session.json'));print(sorted(d))"
curl -s localhost:8001/api/flattrade/status      # connected:true, expired:false
```
Then, from an MCP-enabled session: `check_login` → "Authenticated", and `get_limits` → `stat: Ok`.

### The MCP is stuck / says logged out (issue #1 stale-session lockout)
```bash
.venv/Scripts/python.exe backend/scripts/resync_mcp_session.py --clean
```
Then restart the MCP client session. If AlphaForge itself reports `expired`, do the AlphaForge
login first — the script syncs a token, it cannot mint one.

### The MCP rejects the injected session (future binary version)
1. Inspect the new schema: `strings` the binary for `json:"…"` tags near session fields.
2. Set `FLATTRADE_MCP_SESSION_TEMPLATE` in `backend/.env` to a matching JSON template.
3. `docker compose up -d --build backend`, re-login (or run the resync script).

### Client registration (one-time, user-performed)
- **Claude Desktop** — `%APPDATA%\Claude\claude_desktop_config.json`, an `mcpServers.flattrade`
  block with `command` = binary path and the three `env` credentials.
- **Claude Code** — `claude mcp add flattrade --scope user --env FLATTRADE_USER_ID=… --env
  FLATTRADE_API_KEY=… --env FLATTRADE_API_SECRET=… -- "C:\tools\flattrade-mcp-windows-amd64.exe"`
  (writes `~/.claude.json`). Verify with `claude mcp list` → `✓ Connected`.
- Restart the client afterwards — MCP servers attach at session start.
- Credentials live in plain text in those client configs (same posture as `backend/.env`).
  **Never commit them.**

---

## 7. Recommended uses (highest value first)

1. **Independent broker-truth witness during live/paper validation** — cross-check AlphaForge's
   blotter and guard state against the broker's own books through a second, independent channel.
   The `subscribe_order_updates` / `subscribe_position_updates` streams make this near-real-time.
2. **Conversational broker console** — positions, funds, order book, margin probes without opening
   the UI; pre-session margin checks.
3. **Ad-hoc option-chain / quote intelligence** — a second source next to AlphaForge's
   Upstox-derived Greeks.
4. **Live-integration debugging** — inspect real order states and rejection reasons interactively
   instead of writing one-off scripts.

A future **AlphaForge-native read-only MCP** (exposing intent/guard state, which the broker cannot
know) remains an open idea — complementary, not a replacement. Not built; not scheduled.
