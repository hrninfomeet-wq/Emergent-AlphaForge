/**
 * Client-side export helpers.
 * No backend dependency — all download triggered via Blob URLs.
 */

const triggerDownload = (blob, filename) => {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 500);
};

const safeName = (s) =>
  String(s || "untitled")
    .replace(/[^a-zA-Z0-9_\-]+/g, "_")
    .slice(0, 80);

export const exportJson = (data, filename) => {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  triggerDownload(blob, filename);
};

export const exportCsv = (rows, filename) => {
  if (!rows || rows.length === 0) {
    triggerDownload(new Blob(["(empty)"], { type: "text/csv" }), filename);
    return;
  }
  const keys = Array.from(
    rows.reduce((set, r) => {
      Object.keys(r || {}).forEach((k) => set.add(k));
      return set;
    }, new Set())
  );
  const escape = (v) => {
    if (v === null || v === undefined) return "";
    const s = typeof v === "object" ? JSON.stringify(v) : String(v);
    if (s.includes(",") || s.includes("\"") || s.includes("\n")) {
      return `"${s.replace(/"/g, '""')}"`;
    }
    return s;
  };
  const lines = [
    keys.join(","),
    ...rows.map((r) => keys.map((k) => escape(r[k])).join(",")),
  ];
  const blob = new Blob([lines.join("\n")], { type: "text/csv" });
  triggerDownload(blob, filename);
};

export const exportBacktestConfig = (result) => {
  const stamp = (result?.name || "run") + "_" + (result?.id?.slice(0, 8) || "");
  const cfg = {
    name: result?.name,
    instrument: result?.instrument,
    strategy_id: result?.strategy_id,
    config: result?.config,
    params_applied: result?.params_applied,
    saved_from: "AlphaForge Backtest Lab",
    saved_at: new Date().toISOString(),
  };
  exportJson(cfg, `alphaforge_config_${safeName(stamp)}.json`);
};

export const exportBacktestResult = (result) => {
  const stamp = (result?.name || "run") + "_" + (result?.id?.slice(0, 8) || "");
  exportJson(result, `alphaforge_result_${safeName(stamp)}.json`);
};

export const exportTradesCsv = (result) => {
  const stamp = (result?.name || "run") + "_" + (result?.id?.slice(0, 8) || "");
  exportCsv(result?.trades || [], `alphaforge_trades_${safeName(stamp)}.csv`);
};
