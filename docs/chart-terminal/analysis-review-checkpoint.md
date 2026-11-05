# Independent stage 5 research review

Scope: `chart_research.py`, `chart_analysis.py`, research router, their tests, and the actual reachable indicator/feature/strategy functions. Read-only source review plus pure local reproductions; root owns implementation changes. No network, broker, authentication, restart, or execution actions.

## Ranked findings

1. **P1: cached unfinished values become closed inputs and irreversible score outcomes.** In the reviewed version, `closed_bars` compared candle end only with observation wall time. `ChartDataService.history` caches snapshots for 15 seconds, including the then-forming candle. A snapshot captured at epoch second `1793853958`, whose last candle ended at `1793853960`, was accepted at observation time `1793853962`; `PredictionJournal.score` then stored `status=scored`, `actual_close=106.08`, although that value was captured before the target closed. Snapshot completion time alone also fails if a network fetch crosses candle end. Fix: preserve request-start watermark and require candle closure before both current time and snapshot request start. Root confirmed and is implementing `requested_at_ms` filtering.

2. **P2: concurrent deduplicated logger can expose a forecast later classified late.** Task A upserts pending and awaits delayed acknowledgement. Task B starts before target end, matches the pending document, and finishes its duplicate upsert after target end. The late check is conditional on `upserted_id is not None`, so B returns `logged` while A later marks that same record `late`. Pure async-Event reproduction: duplicate response `logged` at `1793853961` for target end `1793853960`, storage pending; release A => original response `waiting`, storage late. Fix: re-check horizon on every certification path and prevent a duplicate from certifying an in-flight unacknowledged insert; use a timely-certification state/conditional transition if required.

3. **P2: option/future overlays use cash/index CAS suppression.** `analyze_closed_bars` calls `precompute_all_indicators(frame)` with default `segment='spot'` regardless of instrument kind. An option series at 2026-11-05 15:19 IST with last candle OHLC 104/106/103/105 following 100/101/99/100 produces no FVG zone. The identical frame passed through the existing builder with `segment='options'` yields `fvg='UP'`. Catalog advertises these overlays for options. Fix: select derivative indicator segment for futures/options, retaining cash/index suppression for cash/index.

Root independently found session-calendar/CAS eligibility defects in forecast generation and is correcting them; this review does not duplicate that finding.

## Critical execution boundary

Confirmed by actual function/import review: the two allowlisted classes inherit a pure no-op `session_precompute`; their `evaluate` methods access only provided frames/parameters. The analyzer imports pure indicator/feature/context helpers, instantiates only those explicit classes, and does not call strategy discovery. Local transitive guarded-import test exercised evaluation without importing runtime/live/deployment/optimizer/broker modules or invoking discovery. This establishes the reviewed research-analysis path, not a claim about broker acceptance or live safety.

## Verification

`PYTHONPATH=backend python -m pytest tests/test_chart_research.py tests/test_chart_analysis.py -q`: **14 passed in 5.67 seconds** against the version reviewed before root's fixes. The three cases above were additional pure local stdin Python reproductions using the existing test fake collection/history and async Events; no production database was contacted.

## Lesson and boundary

Core lesson: candle closure is a property of the data snapshot as well as the current clock. Confirmed approach: independently trace actual evaluated methods and combine targeted tests with adversarial boundary/concurrency reproductions. Dead end to avoid: a passing top-level AST import test or wall-clock-only closure filter does not establish transitive execution isolation or finalized scoring data.

One bounded review pass completed. Source fixes and post-fix regression verification remain with root.
