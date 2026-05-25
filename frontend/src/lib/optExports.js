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
    pretrade_filters: job?.config?.pretrade_filters,
    saved_from: "AlphaForge Auto-Optimizer",
    saved_at: new Date().toISOString(),
  };
  exportJson(cfg, `alphaforge_optimizer_config_${safeName(stamp)}.json`);
};

export const exportOptJob = (job) => {
  const stamp = (job?.config?.name || "opt") + "_" + (job?.id?.slice(0, 8) || "");
  // Strip the param_space to keep file size manageable
  const out = { ...job, param_space: undefined };
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
