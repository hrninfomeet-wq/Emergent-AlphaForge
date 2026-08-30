/**
 * Client-side export helpers.
 * No backend dependency — all download triggered via Blob URLs.
 */
import { displayTrades, isPremiumNative, joinOptionLegs } from "@/lib/backtestMetrics";

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

export const exportCsv = (rows, filename, preamble = null) => {
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
    ...(preamble || []),
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

/**
 * Trades CSV = exactly what the Trades pane renders, in the pane's column order.
 *
 * Was `exportCsv(result.trades)`, which read the RAW SPOT list and so disagreed
 * with the pane on every option run: all 14 option columns — `opt_pnl_value`,
 * the actual rupee P&L, among them — were missing, and a premium-native run
 * (spot `trades` empty by construction) downloaded a file containing the single
 * word "(empty)" despite showing trades and a large ₹ P&L on screen.
 *
 * Column order mirrors the pane: row number, the spot/base fields, then the
 * option leg. Any remaining raw trade fields (mfe_pts, mae_pts, reasons, regime,
 * vix, ...) are appended rather than dropped, so existing header-keyed consumers
 * keep working and gain the option columns.
 */
const BASE_TRADE_KEYS = [
  "direction", "entry_ts", "entry_datetime", "entry_price",
  "exit_ts", "exit_datetime", "exit_price", "exit_reason",
  "score", "pnl_pts", "pnl_pct",
];

const OPTION_TRADE_KEYS = [
  "opt_symbol", "opt_strike", "opt_side", "opt_status", "opt_lots", "opt_qty",
  "opt_entry", "opt_exit", "opt_pnl_pts", "opt_pnl_pct", "opt_exit_reason",
  "opt_buy_value", "opt_sell_value", "opt_charges", "opt_pnl_value",
];

const round2 = (n) => (Number.isFinite(n) ? Math.round(n * 100) / 100 : null);

/**
 * Summary lines for a FILTERED export, as leading `#` comments.
 *
 * Deliberately limited to metrics that are EXACTLY ADDITIVE over a subset of
 * trades — counts, win rate, gross win/loss, profit factor, net P&L, charges.
 * Summing the monthly slices of a stored run reproduces its full-run trade count
 * and net P&L to the rupee, so these are exact, not estimates.
 *
 * Max drawdown, Sharpe and the significance CI are deliberately ABSENT. They are
 * path/sample dependent, not additive: summing the per-month drawdowns of one
 * stored run gave Rs 1,512,246 against a true full-run figure of Rs 208,748 — a
 * 7.2x overstatement. Anything derived from the equity path has to be recomputed
 * against that path, so rather than print a wrong number this says why it is not
 * here.
 */
const filterSummaryLines = (rows, result, filters, total) => {
  const optionEnabled = !!result?.option_backtest?.enabled;
  const premium = isPremiumNative(result);
  // `Number(null)` is 0 and `Number.isFinite(0)` is true, so mapping before
  // discarding blanks counted every trade WITHOUT an option leg as a zero-P&L
  // loss. On a sparse run (835 signals, 314 fills) that reported 733 losses and
  // a 12.22% win rate instead of 102/314 = 32.48%. Discard the blanks first.
  const numeric = (key) => rows
    .filter((r) => r[key] != null && r[key] !== "")
    .map((r) => Number(r[key]))
    .filter(Number.isFinite);
  const money = optionEnabled ? numeric("opt_pnl_value") : [];
  const outcomes = optionEnabled ? money : numeric("pnl_pts");
  const wins = outcomes.filter((v) => v > 0);
  const losses = outcomes.filter((v) => v <= 0);
  const grossWin = wins.reduce((s, v) => s + v, 0);
  const grossLoss = Math.abs(losses.reduce((s, v) => s + v, 0));
  const spotPts = rows.reduce((s, r) => s + (Number(r.pnl_pts) || 0), 0);

  // Run names are free text and routinely contain commas ("... net_pnl_inr, 404%"),
  // which would split a comment line across columns when opened in a spreadsheet.
  const clean = (v) => String(v ?? "").replace(/[\r\n,]+/g, " ").trim();
  const active = Object.entries(filters || {})
    .filter(([, v]) => v != null && v !== "")
    .map(([k, v]) => `${k}=${clean(v)}`);

  const out = [
    "# AlphaForge - FILTERED trades export",
    `# run: ${clean(result?.name) || "(unnamed)"}`,
    `# run_id: ${clean(result?.id)}`,
    `# instrument: ${clean(result?.instrument)}  strategy: ${clean(result?.strategy_id)}`,
    `# filters: ${active.length ? active.join("; ") : "(none)"}`,
    `# trades_in_view: ${rows.length} of ${total ?? rows.length}`,
    // Only meaningful when they differ: a signal with no option data never
    // produced a leg, so it has no rupee outcome and cannot be a win or a loss.
    ...(optionEnabled && outcomes.length !== rows.length
      ? [`# trades_with_option_fill: ${outcomes.length} (win/loss and P&L below are over these)`]
      : []),
    `# wins: ${wins.length}  losses: ${losses.length}  win_rate_pct: ${round2(outcomes.length ? (100 * wins.length) / outcomes.length : 0)}`,
    `# profit_factor: ${grossLoss > 0 ? round2(grossWin / grossLoss) : ""}`,
  ];
  if (optionEnabled) {
    out.push(`# gross_win_inr: ${round2(grossWin)}  gross_loss_inr: ${round2(grossLoss)}`);
    out.push(`# net_pnl_inr: ${round2(money.reduce((s, v) => s + v, 0))}`);
    out.push(`# total_charges_inr: ${round2(rows.reduce((s, r) => s + (Number(r.opt_charges) || 0), 0))}`);
  }
  out.push(`# ${premium ? "net_pnl_premium_pts" : "net_pnl_spot_pts"}: ${round2(spotPts)}`);
  out.push("# NOTE: max drawdown, Sharpe and the significance CI are NOT recomputed for a");
  out.push("#       filtered subset - they depend on the full equity path, and summing or");
  out.push("#       re-basing them over a slice materially misstates them.");
  out.push("# NOTE: a hand-picked date range is an in-sample slice, not a validated result.");
  out.push("# (data rows begin below; these '#' lines are comments - pandas: comment='#')");
  return out;
};

export const exportTradesCsv = (result, view = null) => {
  // Export the VIEW when the table published one, so the download matches the
  // screen (direction / exit reason / outcome / date range). Falls back to the
  // whole run, which keeps an unfiltered export byte-identical to before.
  const all = joinOptionLegs(displayTrades(result), result?.option_backtest);
  const rows = Array.isArray(view?.rows) ? view.rows : all;
  const filters = view?.filters || {};
  const isFiltered = Object.values(filters).some((v) => v != null && v !== "")
    || (Array.isArray(view?.rows) && view.rows.length !== all.length);

  const range = [filters.date_from, filters.date_to].filter(Boolean).join("_to_");
  const stamp = (result?.name || "run") + "_" + (result?.id?.slice(0, 8) || "")
    + (range ? `_${range}` : "") + (isFiltered && !range ? "_filtered" : "");
  const filename = `alphaforge_trades_${safeName(stamp)}.csv`;


  // A premium-native run has no spot leg: its base prices ARE option premium.
  // Relabel rather than ship rupees under a spot heading — the same units lie
  // the pane already avoids by renaming these columns on screen.
  const premium = isPremiumNative(result);
  const rename = {
    entry_price: premium ? "entry_premium" : "entry_price",
    exit_price: premium ? "exit_premium" : "exit_price",
    pnl_pts: premium ? "pnl_premium_pts" : "pnl_pts",
  };

  const optionEnabled = !!result?.option_backtest?.enabled;
  // A premium-native run has no spot leg, so its base columns ALREADY hold the
  // option premium: entry_premium/exit_premium/pnl_premium_pts were byte-for-byte
  // identical to opt_entry/opt_exit/opt_pnl_pts in the export. The pane drops the
  // repeats for exactly this reason; the CSV now matches it.
  const optionKeys = premium
    ? OPTION_TRADE_KEYS.filter((k) => !["opt_entry", "opt_exit", "opt_pnl_pts", "opt_pnl_pct"].includes(k))
    : OPTION_TRADE_KEYS;
  const ordered = [
    "trade_no",
    ...BASE_TRADE_KEYS,
    ...(optionEnabled ? optionKeys : []),
  ];
  // Anything the run carries that the pane does not column-ise (mfe/mae, reasons,
  // regime, vix, ...) is preserved after the pane's columns.
  const known = new Set([...ordered, ...OPTION_TRADE_KEYS, "idx"]);
  const extras = Array.from(
    rows.reduce((set, r) => {
      Object.keys(r || {}).forEach((k) => { if (!known.has(k)) set.add(k); });
      return set;
    }, new Set())
  );

  const shape = (r) => {
    const out = {};
    out.trade_no = r.idx;
    for (const k of BASE_TRADE_KEYS) out[rename[k] || k] = r[k] ?? null;
    if (optionEnabled) for (const k of optionKeys) out[k] = r[k] ?? null;
    for (const k of extras) out[k] = r[k] ?? null;
    return out;
  };
  const preamble = isFiltered
    ? filterSummaryLines(rows, result, filters, view?.total ?? all.length)
    : null;

  // Filtered down to nothing: emit the summary and the header row rather than
  // the bare string "(empty)". A file that says only "(empty)" is exactly the
  // failure this export was fixed for, and a user who narrowed a date range too
  // far cannot tell it apart from a broken download. Headers + a preamble that
  // names the filters say plainly "your filter matched no trades".
  if (!rows.length) {
    const keys = all.length ? Object.keys(shape(all[0])) : [];
    if (!keys.length) {
      exportCsv([], filename);   // the run genuinely has no trades at all
      return;
    }
    const text = [...(preamble || []), keys.join(",")].join("\n");
    triggerDownload(new Blob([text], { type: "text/csv" }), filename);
    return;
  }

  exportCsv(rows.map(shape), filename, preamble);
};
