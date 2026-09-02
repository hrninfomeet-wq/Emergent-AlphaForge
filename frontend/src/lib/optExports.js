/**
 * Optimizer-specific export helpers.
 */
import { exportJson, exportCsv } from "@/lib/exports";

const safeName = (s) =>
  String(s || "untitled").replace(/[^a-zA-Z0-9_\-]+/g, "_").slice(0, 80);

export const exportOptConfig = (job) => {
  const stamp = (job?.config?.name || "opt") + "_" + (job?.id?.slice(0, 8) || "");
  const cfg = {
    name: job?.config?.name,
    instrument: job?.instrument,
    strategy_id: job?.strategy_id,
    method: job?.method,
    objective: job?.objective,
    n_trials_total: job?.n_trials_total,
    param_overrides: job?.config?.param_overrides,
    // The bounds are meaningless without their UNIT. An exported config that
    // said `spot_stop_pts: {max: 0.32}` with no unit would be re-imported as a
    // 0.32-POINT stop. `bounds_resolution` records the reference price the
    // percentages were resolved against, so the exported file explains the
    // point bounds in `param_space` rather than just asserting them.
    bounds_unit: job?.config?.bounds_unit || "points",
    bounds_pct_params: job?.config?.bounds_pct_params || [],
    bounds_resolution: job?.bounds_resolution,
    pretrade_filters: job?.config?.pretrade_filters,
    saved_from: "AlphaForge Auto-Optimizer",
    saved_at: new Date().toISOString(),
  };
  exportJson(cfg, `alphaforge_optimizer_config_${safeName(stamp)}.json`);
};

export const exportOptJob = (job) => {
  const stamp = (job?.config?.name || "opt") + "_" + (job?.id?.slice(0, 8) || "");
  // `param_space` is KEPT. It was stripped "to keep file size manageable", but
  // measured on a real job it is 1,779 of 77,359 bytes (2%) — while being the
  // only record of the bounds the search actually ran under. Dropping it made
  // an exported result impossible to audit: a leftover override had widened
  // spot_target_pts from the declared 200 to 300 and nothing in the file said
  // so. `trial_log` is the genuinely large field and is still omitted.
  const out = { ...job, trial_log: undefined };
  exportJson(out, `alphaforge_optimizer_result_${safeName(stamp)}.json`);
};

export const exportOptAlternatives = (job) => {
  const stamp = (job?.config?.name || "opt") + "_" + (job?.id?.slice(0, 8) || "");
  const rows = (job?.top_n_alternatives || []).map((alt, i) => ({
    rank: i + 1,
    objective_value: alt.objective_value,
    trade_count: alt.metrics?.trade_count,
    win_rate_pct: alt.metrics?.win_rate,
    profit_factor: alt.metrics?.profit_factor,
    total_pnl_pts: alt.metrics?.total_pnl_pts,
    max_dd_pts: alt.metrics?.max_dd_pts,
    sharpe: alt.metrics?.sharpe,
    ...alt.params,
  }));
  exportCsv(rows, `alphaforge_optimizer_alternatives_${safeName(stamp)}.csv`);
};
