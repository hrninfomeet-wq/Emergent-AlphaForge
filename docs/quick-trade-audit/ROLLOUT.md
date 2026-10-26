# Conditional Quick Trade rollout — 2026-10-26

Confirmed: the conditional feature is committed on local `main` as `91a5977289a607fedbe3ac895fb9122122b01fb4`, and the rebuilt application displays the new Conditional Orders tab with Paper selected. Local main and origin/main matched before the feature commit; no merge conflict existed. Remote origin/main was not pushed.

The commit includes the feature, its paper-stop integration and required shared build support. Other existing checkout edits remain intact and uncommitted. The rebuilt images retain those already-local app changes; their build identity correctly reports a dirty checkout. This is not a clean-checkout release claim.

## Evidence

- `docker compose build backend frontend conditional-watchdog` exited 0; frontend production compilation completed. Build output: `rollout-build.log`.
- `docker compose up -d --no-deps backend frontend conditional-watchdog` recreated only those app services. Mongo's container ID and start timestamp remained unchanged.
- Health returns database OK. Conditional API returns a running worker, fresh heartbeat and no error. The separate watchdog is running and reads the primary heartbeat.
- Actual running conditional Python files match the host files by SHA-256. Frontend/backend report the same captured-source fingerprint `99fc7ff8793c04d00faaeade1e019a0c26ca27795a09ce16069a2bf498ef7f5f`.
- Metadata API loads contracts/expiries for NIFTY, BANKNIFTY and SENSEX. Counts and first expiry values are captured in `rollout-verification.json`.
- Chrome and the embedded browser visibly showed Conditional orders, Manual live ticket, Paper selected, entry/exit settings, loaded expiry choices and engine status. The user subsequently navigated the embedded tab into Upstox login; that flow was left untouched.
- Before rollout: LIVE_OFFLINE, no live deployments/open live records/pending entry intents/conditional orders, and no guarded positions. After rollout: LIVE_OFFLINE and no conditional orders. Broker exposure could not be independently established with the expired session; that limitation was disclosed before restarting.

## Limits

No order was armed, preview submitted or trade placed as part of deployment. No login, logout, kill or live-enable control was invoked. Behavioural acceptance tests, paper trades, stop/fill/OCO/restart/failover correctness and live broker acceptance remain unverified. Configuration readiness is not authenticated broker-session readiness. This rollout proves source/build/API/UI integration, not live-trading safety.

Machine-readable evidence: `rollout-verification.json`. Previous implementation-only/deployment-pending notes are historical and superseded by this rollout for deployment status only.
