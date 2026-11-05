# Item 5 implementation contract — 2026-11-05

Critical risk: analysis must never invoke execution. Root owns router/journal,
causal input and final dependency review; only explicitly reviewed pure strategy
classes are evaluated. No deployment, broker, runtime or optimizer imports.

Backend pure module `chart_analysis.py` exports:
- `analysis_catalog(instrument, timeframe)` => `{overlays:[{id,label,available,reason}],
  strategies:[{id,name,available,reason}]}`.
- `analyze_closed_bars(bars, instrument, timeframe, strategy_id=None)` =>
  `{overlays:{id:{lines:[],zones:[],markers:[],warnings:[]}},
  strategy:{id,status,reason,signals:[]},warnings:[]}`.
- Bars are validated, chronological CLOSED OHLCV candles, `time` in seconds.
  Module does not fetch history or write data. Root filters closed candles.
- Overlay IDs: `support_resistance`, `fvg`, `order_blocks`, `structure_breaks`,
  `change_of_character`, `displacement`, `patterns`, `premium_discount`, `volume_imbalance`.
  Last is explicitly unavailable without order-flow data; never relabel FVG as it.
- Lines `{id,price,label,color}`; zones `{id,start,end,low,high,label,color}`;
  markers/signals `{id,time,price,direction,text,reasons}`. Time is when known,
  not an earlier unconfirmed pivot. Cap lines 20, zones 30, markers 100 per concept.
- Latest-only strategy evaluation, no historical replay pretending to be live.
  Return no marker for NONE. Reuse existing indicator/feature/context builders;
  enforce instrument/timeframe/data/warmup/session requirements. No arbitrary
  plugin discovery, generated code, premium-trigger evaluator or order imports.

Root router/service:
- GET `/charts/analysis/catalog?instrument_key=&timeframe=` returns catalog above.
- POST `/charts/analysis/observe` body `{instrument_key,timeframe,strategy_id:null,
  forecast:false}` uses authoritative existing chart history; no client candles.
- Returns `{instrument,timeframe,as_of_ms,closed_through,overlays,strategy,warnings,
  prediction,scores}`. `prediction` nullable or `{status,reason}` until eligible.
- Forecast available only on eligible intraday closed bars; predicts NEXT CLOSE,
  not intrabar high/low. Statistical historical-analogue model, not an LLM, not
  calibrated accuracy. Fields: `{id,status:'logged',model,source_time,target_time,
  target_end,created_at_ms,baseline_close,predicted_close,zone_low,zone_high,
  probabilities:{up,flat,down},confidence,sample_size,flat_threshold_points,reasons}`.
- Every displayed prediction is durably inserted before exposure. Immutable unique
  identity per instrument/expiry/timeframe/source candle/model. No retrospective
  predictions. Fail logging => no display as a logged forecast. Score later only
  against an actually closed target bar: MAE, Brier, interval coverage and matching
  no-change baseline. `scores:{evaluated,pending,model_mae,baseline_mae,model_brier,
  baseline_brier,coverage}`. New forecasts require an enabled, visible view.
  Existing pending records can settle after disconnect: a data-only worker leases
  due records, checks the exact contract and re-reads the target candle. Missing
  outcomes stay pending. The scoring worker does not create predictions.

UI:
- Per-tile optional validated `analysis:{overlays:{id:boolean},forecast:boolean,
  strategyId:string|null}`; missing legacy settings default all off.
- Separate `useChartAnalysis(instrument,timeframe,settings)` polls observe while
  enabled/visible; one in-flight request, cancel/ignore replaced context, bounded
  retries. Retain last valid data with explicit stale state on errors. Poll 5s to
  pick up new closed history; server coalesces analysis per closed bar.
- Independent panel toggles; unsupported controls disabled with reason. Accumulate
  unique strategy markers for current identity/timeframe/strategy, cap 100.
- Separate read-only canvas primitive/marker lifecycle; no drawing persistence or
  order callbacks. AI card labels model probability/sample size, next-close zone,
  reasons, logging state and baseline scores; never call it proven accuracy.
- Tests: causal prefix, unfinished candle exclusion, no-order import/call sentinel,
  durable dedup/scoring, independent toggle persistence and isolated browser signal.
