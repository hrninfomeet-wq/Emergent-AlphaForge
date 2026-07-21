# Durable static-IP deployment

## Decision

Run AlphaForge on a small always-on Linux VPS with one reserved public IPv4, but
keep the application private. Bind the frontend, backend, and MongoDB to the
VPS loopback interface and reach them from the trading computer through an SSH
tunnel (or a private WireGuard network). The browser can continue to use
`http://localhost:3000` and OAuth callbacks can continue to use
`http://localhost:8001`, while every broker request originates from the VPS's
registered IPv4.

Do **not** expose the current frontend or API directly to the public Internet.
AlphaForge does not currently have an application-wide authentication and
authorization boundary suitable for that exposure. A public HTTPS deployment
is a later engineering project, not a DNS or reverse-proxy toggle.

The VPS is intended first for market-hours data capture and one-lot paper
forward validation. The recommended real-money path is to pass the complete
promotion policy in `forward-validation-policy.md` and finish the market-hours
live safety drills in `live-readback-checklist.md`. AlphaForge permits an explicit
unvalidated-live override, but that records informed operator authority—not
deployment readiness or evidence of edge.

## Why the current Compose file is not the production deployment

The shipped `docker-compose.yml` is a Windows/local-development stack. It:

- publishes MongoDB, the API, and the frontend on every host interface;
- bakes `localhost` browser URLs into the frontend;
- contains a Windows-specific bind mount for the shared Flattrade MCP session;
- has no host firewall, backup, log-retention, clock, disk, or external health
  policy; and
- assumes secrets are stored in `backend/.env` on the same workstation.

Copying it unchanged to a public server would be unsafe and the Windows bind
mount would not work.

## Target topology

```text
Trading PC / Chrome
  localhost:3000  -\
  localhost:8001  -- encrypted SSH/WireGuard tunnel -- VPS loopback
                                                        |- frontend
Reserved public IPv4 <-- broker and data-provider egress |- FastAPI
                                                        `- MongoDB volume
```

Recommended minimum starting host: two dedicated vCPU, 8 GB RAM, 100 GB SSD,
Ubuntu LTS, and a provider-reserved IPv4. Storage is the likely constraint once
Full market surfaces are retained; monitor it from the first session rather
than sizing from candle data alone.

## Provisioning checklist

1. Reserve the IPv4 at the provider; confirm it remains attached across stop,
   start, and reboot. Register that exact IPv4 with Flattrade.
2. Install current Docker Engine plus the Compose plugin from the vendor
   repository. Enable automatic security updates, NTP/chrony, and an
   Asia/Kolkata display timezone. Store timestamps in UTC in the database.
3. Create a non-root service account. Permit inbound SSH only from the user's
   current administration IP where possible. Deny public access to ports 27017,
   8001, and 3000.
4. Clone a reviewed commit. Create `backend/.env` with mode `0600`; never copy it
   into an image or commit it. Generate a stable Fernet key and preserve it in
   the encrypted backup set.
5. Replace the Windows MCP bind with a Linux directory owned only by the service
   account. AlphaForge remains the sole OAuth owner; do not invoke MCP
   login/logout.
6. Bind published application ports to `127.0.0.1`. Open them from the trading
   PC with SSH local forwarding for 3000 and 8001. Do not publish MongoDB.
7. Leave `LIVE_AUTOPLACE_ARMED=0`. Configure Upstox and Flattrade callback URLs
   exactly as the browser sees them through the tunnel. Complete the daily OAuth
   flow after 06:00 IST.
8. Start MongoDB, then the backend, then the frontend. Confirm backend and
   database health, feed freshness, candle roller, host clock, public IPv4,
   disk headroom, and paper account enforcement before market open.
9. Run one-lot paper forward validation for at least 60 complete sessions and
   120 closed trades. A process restart, data gap, stale quote, or overnight
   position is evidence to investigate, not a result to erase.

## Operational controls

- Use a systemd unit to run `docker compose up` on boot; the container restart
  policy alone is not a complete boot policy.
- Alert on backend health, feed age, guard-loop age, database disk usage,
  container restarts, NTP offset, and an unexpected public IPv4 change.
- Keep daily encrypted MongoDB backups, with one copy outside the VPS. Test a
  restore before trusting the backup. Retain the code commit and redacted
  environment fingerprint with every forward cohort.
- Retain Full-feed ticks for 30 days under the database TTL. Export compact
  decision surfaces and cohort summaries before expiry.
- Reboot and upgrade outside market hours. After every restart, reconcile paper
  and broker state before enabling evaluation.
- Treat SSH/VPN loss as an operator-interface outage, not permission to expose
  the API publicly.

## Go-live boundaries

Static IP and uptime make execution possible; they do not supply trading edge.
Paper readiness requires live Full-feed execution surfaces and the complete
frozen forward gate. Real-money readiness additionally requires market-hours
validation of login, order acknowledgement, partial fills, OCO protection,
restart recovery, kill/stop confirmation, and authenticated broker-flat
readback.

Until those conditions hold, the durable host should collect trustworthy evidence.
If the operator nevertheless enables an unvalidated candidate, use the smallest
capital ceilings and treat it as a high-risk operational experiment, not a
validated trading engine.
