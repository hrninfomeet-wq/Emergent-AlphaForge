# BLOCKER — live silently discards `risk.exit_controls`

**Found 2026-08-14. Proven against real market data. FIXED in `20c9750`.**

> Replaying the real 27,173-tick path through the PATCHED guard: overlay CARRIED,
> ratchet 10:33:29 IST to stop 69.70, exit 10:33:46 `breakeven_stop`, **+Rs 520.00**.
> The fix DELEGATES to `exit_controls.effective_premium_stop` rather than translating
> into the flat trail schema — that schema dispatches a single `mode` and provably
> cannot express breakeven and trailing together.

## The mechanism

`backend/app/auto_live.py:215-216` (`resolve_live_exit_plan`):

```python
ec = risk.get("exit_controls")
trail = ec if ec else None
```

passes the deployment's `exit_controls` **verbatim** as `levels["trail"]`.
`backend/app/live_deploy_context.py` hands that to
`build_monitor_state(..., trail=levels.get("trail"))`.

`backend/app/live/live_sl_monitor.py:82-148` expects a **flat** trail:

```
{mode: <_VALID_MODES>, trigger, lock_profit, step, raise_by, gap, x, y}
```

`exit_controls` is **nested**:

```
{enabled: bool, unit: "pct"|"pts", breakeven: {trigger, lock}, trailing: {activation, distance}}
```

No `mode` key ⇒ `mode = str(trail.get("mode") or "none")` ⇒ `"none"`, and every trail field
resolves to `None`. **No error is raised**, because `"none"` is a legal mode. The overlay is
discarded in total silence. The only surviving protection is the
`_GUARD_DEFAULT_STOP_PCT = 50.0` deep-default premium stop.

## Why this is a parity break, not just a bug

`backend/app/exit_controls.py` opens with:

> "THE single source both the sim (option_backtest) and the live mark (paper_auto /
> deployment_kill_switch) call, so they can never drift."

- **backtest** — `option_backtest.py` → `ExitControlsConfig` + `effective_premium_stop` ✅
- **paper** — `paper_auto.py:539-544` carries it onto the trade, `:869-876` applies it ✅
- **live** — never calls either. Speaks a different dialect and drops the config ❌

## Proof, with money attached

Deployment `314fb7e8` (New_Confluence_NIFTY · confluence_scalper · NIFTY).
`exit_controls = {enabled: true, unit: "pts", breakeven: {trigger: 10, lock: 8}, trailing: null}`,
and **no** `auto_paper_target_*` / `auto_paper_stop_*` at all, so the premium stop fell to the
50% floor and the premium target stayed `None`.

**Same deployment, same day, same strategy — paper vs live:**

| | entry | stop actually used | outcome |
|---|---|---|---|
| PAPER 04:24Z | 84.6954 | **92.70** = entry **+8** (breakeven lock, ratcheted) | **+₹4,882.69** |
| LIVE 05:01Z | ref 61.35 / fill 61.70 | 30.85 (the 50% default; trail dropped) | ran to −₹1,651-worth, closed by hand |

A third row — same deployment, paper, 04:25Z — is still OPEN at **−₹24,250.98**, so paper is not
uniformly lucky; the point is that the *stop moved* in paper and did not in live.

**Replay of the real live tick path** (27,173 ticks, NIFTY 24300 PE, `NSE_FO|45103`,
10:31:02 → 15:30:01 IST) through the canonical `effective_premium_stop`:

```
stop RATCHETED at 10:33:28 IST (peak 72.00) -> stop 69.70
EXIT        at 10:33:46 IST  ltp 69.25
P&L on 65 qty = Rs +520.00 gross
```

Held to close instead: last tick **36.30** ⇒ **−₹1,651.00** gross.

## What did NOT fire (and why my earlier answer was wrong)

I previously said no strategy rule would have closed the position. Correct on two of three,
wrong on the third:

| exit | level | day's extreme | fired? |
|---|---|---|---|
| spot target | 24162.39 | low **24298.35** | no — 136 pts short |
| spot stop | 24420.05 | high **24405.20** | no — **14.85 pts** short |
| premium 50% stop | 30.85 | low **32.15** | no — 1.30 short |
| **premium breakeven (+10 → lock +8)** | trigger **71.70** | high **72.00** | **YES — and it was discarded** |

So the exits *were* configured and *were* computed; one of them *was* reached. Live threw it away.

## Unit semantics (verified, `exit_controls.py:73-93`)

- `unit == "pts"` → `trigger_level = entry + be_trigger`, `lock_level = entry + be_lock`
- `unit == "pct"` → `trigger_level = entry * (1 + be_trigger)` — **be_trigger is a FRACTION**,
  so the SENSEX deployment's `0.2 / 0.05` mean **+20% / +5%**, not 0.2%.

## Related, still open

- **[17]** nothing shows the operator, before arming, which exits will be in force. This deployment
  went live on `stop = 50% deep default`, `target = None`, `trail = silently discarded` — three
  facts, none of them displayed anywhere.
- **[28]** the exit fields are gated to paper mode and only prefill from an `option_levels` preset,
  which is *how* this deployment ended up with no target/stop in the first place.
