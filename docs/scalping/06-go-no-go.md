# 06 — Go / no-go, remaining blockers, readiness criteria

## Recommendation: **NO-GO for real money — on both indices.** (Confirmed, on the evidence available)

| Question | Answer | Label |
|---|---|---|
| Is there evidence of positive expectancy after costs for a sub-minute option-buying scalp on NIFTY? | No. The best-supported rule (N1) was KILLED by its pre-registered stage-2 replay gate: −₹15.5 per trade over 39 trades, median −₹36, 1 of 4 sessions positive (corrected simulator). | Confirmed |
| …on SENSEX? | No. Every SENSEX impulse rule lost in both samples and both directions; the expiry-day variant cannot be tested because no SENSEX expiry day has been recorded. | Confirmed |
| Can the infrastructure execute and protect a same-minute scalp safely with real money today? | No. Fills are not pushed (no order-update stream), the LPP band is not known at order time, there is no usable broker-held stop on this account, protection stops when the PC stops, and the existing live guard has a reproduced starvation defect. | Confirmed |
| Is the scalper engine itself safe to run in PAPER? | Yes: it cannot reach a broker (no import path; AST-tested); its invariants hold under fault fuzzing. | Confirmed (simulation) |

**Do not build a real-money adapter now.** Every step toward live (doc 04 §9) is engineering around a hypothesis
with no supporting evidence. The cheapest next move is passive data collection, with kill criteria already fixed.

## Options, in recommended order

1. **Stop here, keep the instruments (recommended default).** Keep `tick_archive` growing (on by default), leave the
   paper runner off. Cost: nothing. Revisit only if new data arrives (option 2 or 3).
2. **Paper-run S1-E only, as a falsification test** (N1 is already killed; paper-running it would test a dead
   rule). Set `SCALP_PAPER_ENABLED=1` (it runs only S1-E by default; `SCALP_PAPER_STRATEGIES` selects presets) and
   rebuild when no live position is open. Stop at 12 SENSEX expiry Thursdays and apply the doc-03 criteria
   mechanically. Expected outcome on current evidence: KILL. Requires the PC (or an always-on host) to run through
   market hours on Thursdays (~3 months).
3. **Measure before hypothesising again:** subscribe read-only to the Flattrade depth WebSocket and record its
   update rate and `le`/`ue`; add NIFTY/SENSEX near-month futures to the recorded universe; record at least one
   SENSEX expiry Thursday. Only a finer or earlier signal source could change the stage-1 picture.
4. **Pivot (outside this brief):** the project's own measurements show the structural edge is on the option
   *selling* side, with tail risk (`docs/INTRADAY_OPTION_BUYING_CANDIDATES_2026-08.md` §14, §16).

## Remaining blockers (all must clear before a real-money pilot could even be reviewed)

| # | Blocker | Owner | Measurable completion check |
|---|---|---|---|
| B1 | No positive evidence (N1 killed at stage 2; S1-E untested) | data | a NEW pre-registered hypothesis, or S1-E, passing stage 2 and stage 3 on sessions recorded after 2026-10-07 |
| B2 | No pushed fills (order-update WebSocket unwired; wrong login frame) | engineering | a read-only connection from the static IP receives `om` events for a manual order placed by the operator |
| B3 | LPP band unknown at order time | engineering | depth WebSocket `le`/`ue` recorded for every held contract; zero LPP rejects in a paper week using it |
| B4 | No usable broker-held protection; app-down = unprotected | broker/policy | Flattrade confirms in writing whether MIS option positions are auto-squared and when; or an always-on host is in place |
| B5 | Live guard starvation defect (existing live path) | engineering | spun-off task merged; test shows ≥ 1 PositionBook read per 1.5 s under 0.5 s ticks |
| B6 | No key-wide order budget (40/min shared with the MCP) | engineering | a shared token bucket with an exit reserve, covered by tests |
| B7 | Fill latency never measured | data | ack→fill and exit-decision→fill distributions from ≥ 100 real fills (requires B2) |
| B8 | App-wide cost constants stale (STT 0.10 %, NSE 0.03503 %) | operator decision | approve the update; re-run saved results that matter |
| B9 | Holiday calendar likely missing 2026-10-20, 11-10, 11-24 | engineering | checked against the official NSE list (spun-off task) |

## Readiness criteria for a separately authorised real-money pilot (all required)

* B1–B9 closed.
* Stage-3 PASS for the specific spec, with its config hash frozen.
* A live pilot plan with: 1 lot, one index, ≤ N sessions, an operator-approved daily loss limit in rupees, the
  operator present for every session, and a pre-registered stop rule for the pilot itself.
* Measured: signal→order ≤ 500 ms p95, order→ack ≤ 300 ms p95, ack→fill ≤ 2 s p95 (from B7); zero unresolved
  reconcile mismatches over the paper period.

## Information needed from you (only what cannot be recovered from the app or docs)

1. Do you want option 1 (stop), option 2 (paper falsification run), or option 3 (measure first)?
2. If option 2: can the PC — or an always-on host on the static IP — run through market hours for ~4 weeks?
3. Approve updating the app-wide statutory constants (STT 0.15 %, NSE 0.03553 %)? It reprices every saved
   backtest/paper/live charge by about +21.6 %.
4. Before ANY real-money activation (not requested now): the rupee risk capital per index, the maximum daily loss,
   and the maximum lots you would authorise. No monetary limit in this work is approved; the ₹2,000 / ₹1,500 values
   are paper placeholders.
