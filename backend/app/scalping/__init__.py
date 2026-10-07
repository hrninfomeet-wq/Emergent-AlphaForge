"""Sub-minute option-buying scalping lab (NIFTY / SENSEX) — PAPER and REPLAY only.

Status (2026-10-07): research capability. No scalping hypothesis has passed a pre-registered
holdout (see docs/scalping/). Real-money execution is NOT implemented here by design: the only
execution adapter is the deterministic simulator in ``sim_broker``; ``config.validate`` refuses
any mode other than ``paper`` / ``replay``. Wiring a live adapter is a separate, operator-authorized
piece of work (docs/scalping/04-execution-and-protection.md §9).

Modules
-------
config       per-index parameter sets with units + provenance (measured / provisional / policy)
costs        current statutory schedule (STT 0.15 % from 2026-04-01) on top of app.option_costs
market       1-second feature state: quotes, freshness, synthetic forward, z-scores
signals      pure entry-signal functions (one per pre-registered hypothesis)
engine       deterministic event-driven position/order state machine (no I/O)
sim_broker   deterministic execution model: latency, marketable limits vs depth, partial fills, LPP, faults
replay       recorded-tape replay: tape -> market -> engine <-> sim_broker -> trades + latency report
recorder     tape preservation: archive Upstox full-mode ticks past the 30-day TTL
paper_runner live-tick paper loop (env-gated, default OFF)
journal      Mongo persistence for scalper events and paper trades
"""
