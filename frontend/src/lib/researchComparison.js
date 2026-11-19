// Read-only comparisons of saved evidence. No defaults from today's forms or
// strategy catalog: those would silently fill holes in historical records.
const own = (o, k) => Object.prototype.hasOwnProperty.call(o || {}, k);
const object = v => v != null && typeof v === "object" && !Array.isArray(v);
const available = v => v !== undefined && !(typeof v === "number" && !Number.isFinite(v));
const pick = (o, keys) => Object.fromEntries(keys.map(k => [k, o?.[k]]));

/** A known difference wins over unknown siblings; two missing values never equal. */
export function evidenceStatus(left, right) {
  if (!available(left) || !available(right)) return "unknown";
  if (left === null || right === null) return left === right ? "same" : "different";
  if (Array.isArray(left) || Array.isArray(right)) {
    if (!Array.isArray(left) || !Array.isArray(right) || left.length !== right.length) return "different";
    return combine(left.map((v, i) => evidenceStatus(v, right[i])));
  }
  if (object(left) || object(right)) {
    if (!object(left) || !object(right)) return "different";
    return combine([...new Set([...Object.keys(left), ...Object.keys(right)])]
      .map(k => evidenceStatus(left[k], right[k])));
  }
  return left === right ? "same" : "different";
}
function combine(statuses) {
  return statuses.includes("different") ? "different" : statuses.includes("unknown") ? "unknown" : "same";
}
function dte(value) {
  if (value === undefined) return undefined;
  if (value === null || value === "all") return [];
  const tokens = (Array.isArray(value) ? value : [value]).map(v =>
    typeof v === "string" ? v.trim().toLowerCase().replace(/^dte/, "") : v);
  if (tokens.some(v => v === "" || typeof v === "boolean" || !Number.isInteger(Number(v)) || Number(v) < 0)) return undefined;
  return [...new Set(tokens.map(Number))].sort((a, b) => a - b);
}
function sizing(ob) {
  const s = ob?.sizing_config;
  if (!object(s) || !own(s, "enabled")) return undefined;
  if (!s.enabled) return { mode: "fixed_lots", fixed_lots: ob.request?.lots };
  if (s.mode === "fixed_lots") return pick(s, ["mode", "fixed_lots"]);
  if (s.mode === "premium_at_risk") return pick(s, ["mode", "capital", "risk_per_trade_pct", "max_lots", "assumed_stop_pct_of_premium"]);
  return undefined;
}
function costs(ob, premium) {
  const c = ob?.cost_config;
  if (premium && c === null) return { enabled: false };
  if (!object(c) || typeof c.enabled !== "boolean") return undefined;
  if (!c.enabled) return { enabled: false };
  return pick(c, ["enabled", "brokerage_per_order", "stt_sell_rate", "exchange_txn_rate", "sebi_rate", "gst_rate", "stamp_buy_rate", "spread_pct_of_premium", "spread_min_pts"]);
}
function runEvidence(run) {
  const cfg = run?.config || {}, ob = run?.option_backtest;
  const premium = ob?.dispatch === "premium_trigger_config";
  const enabled = typeof ob?.enabled === "boolean" ? ob.enabled
    : cfg.option_backtest?.enabled === false ? false : undefined;
  const engine = ob?.engine_params;
  const option = value => enabled === false ? "Not applicable (spot only)" : enabled === true ? value : undefined;
  return {
    instrument: run?.instrument ?? cfg.instrument,
    strategy: run?.strategy_id ?? cfg.strategy_id,
    timeframe: cfg.timeframe,
    interval: [cfg.start_ts ?? undefined, cfg.end_ts ?? undefined],
    spotCosts: premium ? "Not applied by premium engine" : cfg.costs_enabled,
    filters: premium ? "Controlled by premium engine parameters" : cfg.pretrade_filters,
    engine: premium ? "Premium native" : enabled === true ? "Spot + paired options" : enabled === false ? "Spot only" : undefined,
    window: premium ? [engine?.reference_time, engine?.entry_cutoff] : [cfg.trade_window_start, cfg.trade_window_end],
    costs: option(costs(ob, premium)), sizing: option(sizing(ob)),
    capital: option(ob?.portfolio?.starting_capital),
    dte: option(premium ? "Not applied by premium engine" : dte(ob?.request?.dte_filter)),
    moneyness: option(ob?.moneyness),
    expiry: option(premium ? "Selected by premium engine" : pick(ob?.request, ["expiry_mode", "expiry_date"])),
    candleAge: option(premium ? "Premium session engine" : pick(ob?.request, ["entry_max_age_sec", "exit_max_age_sec"])),
    slippage: option(premium ? "No point slippage / expiry-tail widening" : ob?.slippage_config),
    exits: option(premium ? ob?.option_exit_config : ob?.exit_mode === "spot_exit" ? "Spot exit" : {
      mode: ob?.exit_mode, levels: ob?.option_exit_config }),
    overlay: option(premium ? "Controlled by premium engine parameters" : pick(ob?.request, ["exit_controls", "daily_caps"])),
    params: run?.params_applied ?? cfg.params,
    premium,
  };
}

function performance(run) {
  const ob = run?.option_backtest, premium = ob?.dispatch === "premium_trigger_config";
  return {
    spotNetPnl: premium ? null : run?.metrics?.total_pnl_pts ?? null,
    optionNetPnl: ob?.portfolio?.net_pnl_value ?? ob?.metrics?.total_option_pnl_value ?? null,
    tradeCount: premium ? ob?.metrics?.paired_trade_count ?? null : run?.metrics?.trade_count ?? null,
    unit: premium ? "option trades" : "spot trades",
  };
}

export function compareResearch(source, job, winner) {
  const preset = source?.kind === "preset", record = source?.record;
  const cfg = record?.config || {};
  // Presets are declarations. Their execution block is displayed separately;
  // it cannot supply effective settings or a measured P&L.
  const a = preset ? { instrument: cfg.instrument, strategy: cfg.strategy_id,
    timeframe: cfg.timeframe, params: cfg.params } : runEvidence(record);
  const b = runEvidence(winner);
  const space = object(job?.param_space) ? job.param_space : null;
  const searched = keys => !!space && keys.some(k => own(space, k));
  const definitions = [
    ["instrument", "Instrument"], ["strategy", "Strategy"], ["timeframe", "Timeframe"],
    ["interval", "Data interval (epoch ms; inclusive request)"], ["engine", "Evaluation engine"],
    ["spotCosts", "Spot costs enabled"], ["filters", "Pre-trade filters"],
    ["window", "Entry window (IST)", a.premium || b.premium ? ["reference_time", "entry_cutoff"] : []],
    ["costs", "Effective option costs / spread"],
    ["sizing", "Effective sizing", a.premium || b.premium ? ["lots"] : []],
    ["capital", "Starting capital (₹)"], ["dte", "DTE sessions"],
    ["moneyness", "Option moneyness", a.premium || b.premium ? ["moneyness"] : []],
    ["expiry", "Expiry selection"], ["candleAge", "Maximum option candle age (seconds)"],
    ["slippage", "Saved point-slippage / expiry-tail policy"],
    ["exits", "Exit policy", a.premium || b.premium ? ["target_pts", "stop_pts", "target_pct", "stop_pct"] : []],
    ["overlay", "Exit overlay / daily caps", job?.config?.search_exit_controls ? ["__overlay_search"] : []],
  ];
  const settings = definitions.map(([key, label, keys = []]) => ({ key, label, left: a[key], right: b[key],
    role: searched(keys) || (key === "overlay" && job?.config?.search_exit_controls) ? "parameter" : "common",
    status: evidenceStatus(a[key], b[key]) }));
  const rightParams = b.params ?? job?.best_params;
  const parameters = [...new Set([...Object.keys(a.params || {}), ...Object.keys(rightParams || {})])].sort()
    .map(key => ({ key, left: a.params?.[key], right: rightParams?.[key],
      status: evidenceStatus(a.params?.[key], rightParams?.[key]), bounds: space?.[key] }));
  const sourceLabel = preset ? `preset:${record?.name}` : record?.config?.optimization_job_id
    ? `job:${record.config.optimization_job_id.slice(0, 8)}` : null;
  const seeds = Array.isArray(job?.incumbent_seeds) ? job.incumbent_seeds : [];
  const matchingSeeds = sourceLabel ? seeds.filter(s => s.source === sourceLabel) : [];
  // Matching a partial parameter dict is not proof of an identical candidate.
  const ranked = Array.isArray(job?.rerank?.ranked) ? job.rerank.ranked : [];
  const sourceParams = preset ? cfg.params : record?.config?.params;
  const matchingRanked = object(sourceParams) ? ranked.filter(r => object(r.params)
    && evidenceStatus(sourceParams, r.params) === "same") : [];
  return {
    settings, settingsStatus: combine(settings.filter(r => r.role === "common").map(r => r.status)),
    dataset: { status: "unknown", note: "Dataset identity unknown: saved records do not provide a verifiable candle fingerprint. Equal intervals or candle counts do not prove identical data." },
    parameters, bounds: space, declaredPolicy: preset ? cfg.execution : null,
    performance: { left: preset ? performance(null) : performance(record), right: performance(winner) },
    inclusion: {
      seeds,
      seed: { status: matchingSeeds.length ? "recorded" : "unknown", records: matchingSeeds,
        note: matchingSeeds.length ? "Named incumbent enqueued; this is not proof of trial execution or exact parameter coverage. Inspect dropped values."
          : "No matching named incumbent evidence; inclusion is unknown." },
      rerank: { status: matchingRanked.length ? "recorded" : "unknown", records: matchingRanked,
        note: matchingRanked.length ? "Exact requested parameter map appears in saved option-rerank rows. Data/policy parity still requires the settings above."
          : "No exact parameter map in saved option-rerank rows; inclusion is unknown because saved rows can be truncated or parameters expanded." },
    },
  };
}

/** Selection cleanup suppresses stale results/errors at every dependent read. */
export function loadResearchComparison(selection, fetchers, publish) {
  let cancelled = false;
  publish({ status: "loading" });
  Promise.resolve().then(async () => {
    const [record, job] = await Promise.all([
      selection.source.kind === "preset" ? selection.source.record : fetchers.getBacktestRun(selection.source.id),
      fetchers.getOptJob(selection.jobId),
    ]);
    if (cancelled) return;
    if (job?.id !== selection.jobId || (selection.source.kind === "run" && record?.id !== selection.source.id)) {
      throw new Error("Saved detail does not match the selected source/job.");
    }
    const winner = job.best_backtest_run_id ? await fetchers.getBacktestRun(job.best_backtest_run_id) : null;
    if (cancelled) return;
    if (winner && winner.id !== job.best_backtest_run_id) throw new Error("Saved winner does not match this job's linked run.");
    publish({ status: "ready", source: { kind: selection.source.kind, record }, job, winner,
      comparison: compareResearch({ kind: selection.source.kind, record }, job, winner) });
  }).catch(error => { if (!cancelled) publish({ status: "error", error: error?.message || "Saved comparison could not be loaded." }); });
  return () => { cancelled = true; };
}
