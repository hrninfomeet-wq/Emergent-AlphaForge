// Derived backtest performance series + metrics, computed client-side from the
// run result the backend already returns (no new endpoint). Adaptive: when
// option execution + a starting capital are present, everything is in rupees
// (account value, P&L, drawdown); otherwise it falls back to index points.
//
// Sources:
//   result.option_backtest.portfolio.curve  -> rupee equity (starts at capital)
//   result.option_backtest.trades (PAIRED)  -> per-trade rupee P&L + index level
//   result.equity_curve / result.trades     -> points fallback (spot-only runs)

function toSec(ts) {
  return Math.floor(Number(ts) / 1000);
}

/**
 * True when the run was produced by the premium session engine rather than by
 * per-bar evaluate() -> spot trades -> option pairing.
 *
 * For such a run `result.trades` and `result.metrics` are EMPTY BY CONSTRUCTION:
 * the strategy's evaluate() is a deliberate inert stub, so the spot path yields
 * nothing. Everything real lives in `result.option_backtest`. Reading the spot
 * objects is what left the user's Trades pane empty and the Trades / Win Rate /
 * Profit Factor / Net P&L cards blank while "Highest Acct Value" — which already
 * went through buildPerformanceSeries -> option_backtest.portfolio — showed a
 * large number.
 */
export function isPremiumNative(result) {
  return result?.option_backtest?.dispatch === "premium_trigger_config";
}

/**
 * The trade rows the Trades table should render.
 *
 * Ordinary runs: unchanged, the spot trades. Premium-native runs: the executed
 * option trades reshaped into the same row contract, so the existing table,
 * sorting, filters and CSV export all work with no special-casing. The option
 * columns join by `index_trade_id === row index`, which the reshape preserves.
 *
 * Prices/points here are OPTION PREMIUM, which is what a premium-native strategy
 * actually trades — there is no index-points leg to report.
 */
export function displayTrades(result) {
  if (!isPremiumNative(result)) return result?.trades || [];
  return (result?.option_backtest?.trades || [])
    .filter((t) => t.status === "PAIRED")
    .map((t, i) => {
      const entry = Number(t.entry_option_price);
      const exit = Number(t.exit_option_price);
      return {
        index_trade_id: t.index_trade_id ?? i,
        direction: t.direction || t.side || "",
        entry_ts: t.option_entry_ts ?? t.signal_entry_ts ?? null,
        exit_ts: t.option_exit_ts ?? t.signal_exit_ts ?? null,
        entry_datetime: t.signal_entry_datetime || "",
        exit_datetime: t.signal_exit_datetime || "",
        entry_price: Number.isFinite(entry) ? entry : null,
        exit_price: Number.isFinite(exit) ? exit : null,
        exit_reason: t.option_exit_reason || "",
        score: null,
        pnl_pts: t.option_pnl_pts ?? null,
        pnl_pct: Number.isFinite(entry) && Number.isFinite(exit) && entry !== 0
          ? ((exit - entry) / entry) * 100
          : null,
      };
    });
}

/**
 * Flatten each trade's matched option leg onto the row, producing the EXACT
 * projection the Trades pane renders.
 *
 * Extracted so the pane and the CSV export read one contract instead of two.
 * They previously diverged: `TradesTable` computed this join inline while
 * `exportTradesCsv` wrote `result.trades` verbatim, so every option column the
 * user could see on screen — the ₹ P&L included — was silently absent from the
 * download, and a premium-native run (whose spot `trades` is empty by
 * construction) exported a file containing the string "(empty)".
 *
 * Joined by `index_trade_id`, NOT by array position. The two agree for every
 * run currently stored, but position-joining is only accidentally correct: for
 * a premium-native run `displayTrades` filters to PAIRED legs, so a single
 * unpaired leg would shift every subsequent row onto the WRONG option — the
 * exact index-remap class this project has already been bitten by. Position is
 * kept only as a fallback for legacy legs that carry no id.
 */
export function joinOptionLegs(trades, optionBacktest) {
  const byId = {};
  for (const ot of (optionBacktest?.trades || [])) {
    if (ot?.index_trade_id != null) byId[ot.index_trade_id] = ot;
  }
  return (trades || []).map((t, i) => {
    // Strictly by id. `index_trade_id` IS the index of the spot trade the leg
    // belongs to, so for a spot row the lookup key is its position; a premium
    // native row carries the id explicitly because displayTrades filters to
    // PAIRED legs and shifts positions.
    //
    // There is deliberately NO positional fallback. Option legs are SPARSE:
    // across the stored corpus 16 runs have far fewer legs than spot trades
    // (835 signals / 317 legs on one), because a signal with no option data
    // never produced a leg. Falling back to `legs[i]` there stamps ANOTHER
    // trade's strike and rupee P&L onto a row that had no leg at all — 1,100
    // rows corpus-wide, and it broke the export's reconciliation (one run
    // summed to -104,324.65 against a true -63,181.45). A row with no leg must
    // stay blank.
    const opt = (t?.index_trade_id != null ? byId[t.index_trade_id] : byId[i]) ?? null;
    const paired = opt?.status === "PAIRED";
    return {
      ...t,
      idx: i + 1,
      opt_symbol: opt?.trading_symbol || opt?.instrument_key || null,
      opt_strike: opt?.strike ?? null,
      opt_side: opt?.side ?? null,
      opt_entry: opt?.entry_option_price ?? null,
      opt_exit: opt?.exit_option_price ?? null,
      // Premium move in % — long-option (buying) semantics: exit vs entry.
      opt_pnl_pct:
        opt?.entry_option_price != null && opt?.exit_option_price != null && Number(opt.entry_option_price) !== 0
          ? ((Number(opt.exit_option_price) - Number(opt.entry_option_price)) / Number(opt.entry_option_price)) * 100
          : null,
      // Gross premium move in points (exit - entry), taken from the engine's
      // stored figure rather than recomputed: it is already the canonical value
      // and stays consistent with how the engine books the leg. GROSS — the
      // rupee column `opt_pnl_value` is NET of round-trip charges, so the two
      // deliberately do not tie out by a simple x quantity.
      opt_pnl_pts: opt?.option_pnl_pts ?? null,
      opt_pnl_value: opt?.option_pnl_value ?? null,
      opt_exit_reason: opt?.option_exit_reason ?? null,
      opt_status: opt?.status ?? null,
      opt_lots: opt?.lots ?? null,
      opt_qty: opt?.quantity ?? null,
      opt_charges: paired ? Number(opt.total_charges || 0) : null,
      // Buy value loads all charges on entry so Sell - Buy = net option P&L
      // (entry premium x qty + round-trip charges; sell = exit premium x qty).
      opt_buy_value: paired
        ? Number(opt.entry_option_price) * Number(opt.quantity) + Number(opt.total_charges || 0)
        : null,
      opt_sell_value: paired
        ? Number(opt.exit_option_price) * Number(opt.quantity)
        : null,
    };
  });
}

/**
 * The headline KPIs, read from whichever envelope actually describes the run.
 *
 * Returns a unit-tagged object rather than raw metrics because the two families
 * are in DIFFERENT UNITS: a spot run is index points, a premium-native run is
 * rupee-native (premium x quantity). Rendering a rupee figure under the existing
 * "Net P&L (pts)" heading would be the same units lie this audit found in
 * optimizer.py, where a rupee drawdown is stored as `max_dd_pts`.
 *
 * Newer option results persist profit_factor in the metrics envelope so trimmed
 * list/summary responses do not need to carry every trade.  Older full results
 * can still derive it from their trades as a compatibility fallback.
 */
export function resultKpis(result) {
  if (!isPremiumNative(result)) {
    const m = result?.metrics || {};
    return {
      currency: false,
      unit: "pts",
      tradeCount: m.trade_count ?? null,
      winRate: m.win_rate ?? null,
      profitFactor: m.profit_factor ?? null,
      netPnl: m.total_pnl_pts ?? null,
      maxDd: m.max_dd_pts ?? null,
      sharpe: m.sharpe ?? null,
    };
  }
  const ob = result.option_backtest || {};
  const m = ob.metrics || {};
  const port = ob.portfolio || {};
  const paired = (ob.trades || []).filter((t) => t.status === "PAIRED");
  const pnls = paired.map((t) => Number(t.option_pnl_value)).filter((v) => Number.isFinite(v));
  const grossWin = pnls.filter((p) => p > 0).reduce((s, v) => s + v, 0);
  const grossLoss = Math.abs(pnls.filter((p) => p < 0).reduce((s, v) => s + v, 0));
  return {
    currency: true,
    unit: "₹",
    tradeCount: m.paired_trade_count ?? paired.length,
    winRate: m.win_rate ?? null,
    profitFactor: m.profit_factor ?? (grossLoss > 0 ? grossWin / grossLoss : null),
    netPnl: port.net_pnl_value ?? m.total_option_pnl_value ?? null,
    maxDd: port.max_drawdown_value ?? null,
    sharpe: port.sharpe_daily ?? null,
  };
}

function dedupeAscending(points) {
  const seen = new Set();
  const out = [];
  for (const p of points) {
    if (p.time == null || !Number.isFinite(p.value)) continue;
    if (seen.has(p.time)) continue;
    seen.add(p.time);
    out.push(p);
  }
  return out.sort((a, b) => a.time - b.time);
}

/** Per-trade net BUY value = entry premium × quantity + round-trip charges.
 * Loading all charges on the buy keeps the table consistent: Sell − Buy = net
 * P&L (sell = exit premium × qty). Matches the user's ₹165k + ₹5k = ₹170k. */
export function tradeBuyValue(t) {
  return Number(t.entry_option_price) * Number(t.quantity) + Number(t.total_charges || 0);
}
export function tradeSellValue(t) {
  return Number(t.exit_option_price) * Number(t.quantity);
}

/**
 * Build the chartable series for the Performance section.
 * Returns { currency, unit, capital, cumPnl[], buyValue[], accountValue[],
 * drawdown[], rightLabel } — each series [{time(sec), value}] ascending & deduped.
 *
 * Rupee mode (option execution): cumPnl = cumulative ₹ P&L from 0; buyValue =
 * per-trade net buy value (the algotest-style right-axis series, per the user's
 * definition — capital deployed per trade incl. charges, NOT the index level);
 * accountValue = capital growth (capital + cumulative P&L); drawdown in ₹.
 */
export function buildPerformanceSeries(result) {
  const ob = result?.option_backtest;
  const portfolio = ob?.enabled ? ob.portfolio : null;
  const curve = portfolio?.curve || [];
  const paired = (ob?.trades || [])
    .filter((t) => t.status === "PAIRED")
    .sort((a, b) => Number(a.option_exit_ts || a.signal_exit_ts || 0) - Number(b.option_exit_ts || b.signal_exit_ts || 0));

  if (curve.length && portfolio?.starting_capital != null) {
    const capital = Number(portfolio.starting_capital);
    const accountValue = dedupeAscending(curve.map((p) => ({ time: toSec(p.ts), value: Number(p.equity_value) })));
    const drawdown = dedupeAscending(curve.map((p) => ({ time: toSec(p.ts), value: Number(p.drawdown_value) })));
    const cumPnl = dedupeAscending(curve.map((p) => ({ time: toSec(p.ts), value: Number(p.equity_value) - capital })));
    const buyValue = dedupeAscending(
      paired.map((t) => ({ time: toSec(t.option_exit_ts || t.signal_exit_ts), value: tradeBuyValue(t) }))
    );
    return { currency: true, unit: "₹", capital, cumPnl, buyValue, accountValue, drawdown, rightLabel: "Trade buy value" };
  }

  // Points fallback (spot-only run): no option premiums to value a position, so
  // the right-axis context is the index level at each trade.
  const ec = result?.equity_curve || [];
  const accountValue = dedupeAscending(ec.map((p) => ({ time: toSec(p.ts), value: Number(p.equity_pts) })));
  const drawdown = dedupeAscending(ec.map((p) => ({ time: toSec(p.ts), value: Number(p.drawdown_pts) })));
  const cumPnl = accountValue;
  const buyValue = dedupeAscending(
    (result?.trades || []).map((t) => ({ time: toSec(t.exit_ts), value: Number(t.exit_price) }))
  );
  return { currency: false, unit: "pts", capital: null, cumPnl, buyValue, accountValue, drawdown, rightLabel: "Index level" };
}

/**
 * Net P&L per calendar month for the monthly calendar. Rupee when option
 * execution ran, else index points. Returns { currency, byMonth: Map<"YYYY-MM", pnl> }.
 */
export function monthlyPnl(result) {
  const ob = result?.option_backtest;
  const byMonth = new Map();
  const add = (iso, pnl) => {
    if (!iso || !Number.isFinite(pnl)) return;
    const key = String(iso).slice(0, 7);
    byMonth.set(key, (byMonth.get(key) || 0) + pnl);
  };
  if (ob?.enabled) {
    for (const t of ob.trades || []) {
      if (t.status !== "PAIRED") continue;
      add(t.signal_exit_datetime || isoFromTs(t.option_exit_ts), Number(t.option_pnl_value));
    }
    return { currency: true, unit: "₹", byMonth };
  }
  for (const t of result?.trades || []) {
    add(t.exit_datetime || isoFromTs(t.exit_ts), Number(t.pnl_pts));
  }
  return { currency: false, unit: "pts", byMonth };
}

function isoFromTs(ts) {
  if (ts == null) return null;
  // IST date (UTC+5:30) so months bucket on the trading calendar, not UTC.
  const d = new Date(Number(ts) + (5 * 60 + 30) * 60 * 1000);
  return d.toISOString().slice(0, 10);
}

function streaks(pnls) {
  let maxWin = 0;
  let maxLoss = 0;
  let curWin = 0;
  let curLoss = 0;
  for (const p of pnls) {
    if (p > 0) {
      curWin += 1;
      curLoss = 0;
      maxWin = Math.max(maxWin, curWin);
    } else if (p < 0) {
      curLoss += 1;
      curWin = 0;
      maxLoss = Math.max(maxLoss, curLoss);
    } else {
      curWin = 0;
      curLoss = 0;
    }
  }
  return { maxWin, maxLoss };
}

// Longest stretch the account spent below its running peak, in calendar days,
// plus whether it ended back at a fresh high (recovered).
function drawdownDuration(equity) {
  if (equity.length < 2) return { days: 0, recovered: true };
  let peakTime = equity[0].time;
  let peakVal = equity[0].value;
  let longest = 0;
  let underwaterSince = null;
  for (const p of equity) {
    if (p.value >= peakVal) {
      peakVal = p.value;
      peakTime = p.time;
      underwaterSince = null;
    } else {
      if (underwaterSince == null) underwaterSince = peakTime;
      longest = Math.max(longest, p.time - underwaterSince);
    }
  }
  const last = equity[equity.length - 1];
  const recovered = last.value >= peakVal;
  return { days: Math.round(longest / 86400), recovered };
}

/**
 * High-value decision metrics not in the backend metrics dict, computed from
 * the trades + the equity series. Adaptive rupee/points via buildPerformanceSeries.
 * Returns numbers (or null when not derivable) — formatting is the caller's job.
 */
export function computeKeyMetrics(result) {
  const series = buildPerformanceSeries(result);
  const cur = series.currency;
  const ob = result?.option_backtest;
  const portfolio = ob?.enabled ? ob.portfolio : null;

  // Per-trade P&L in the active unit.
  let pnls = [];
  if (series.currency) {
    pnls = (ob?.trades || [])
      .filter((t) => t.status === "PAIRED")
      .map((t) => Number(t.option_pnl_value))
      .filter((v) => Number.isFinite(v));
  } else {
    pnls = (result?.trades || []).map((t) => Number(t.pnl_pts)).filter((v) => Number.isFinite(v));
  }

  const n = pnls.length;
  const wins = pnls.filter((p) => p > 0);
  const losses = pnls.filter((p) => p < 0);
  const sum = (a) => a.reduce((s, v) => s + v, 0);
  const avgWin = wins.length ? sum(wins) / wins.length : 0;
  const avgLoss = losses.length ? sum(losses) / losses.length : 0;
  const payoff = avgLoss !== 0 ? Math.abs(avgWin / avgLoss) : null;
  const expectancy = n ? sum(pnls) / n : 0;
  const largestWin = wins.length ? Math.max(...wins) : 0;
  const largestLoss = losses.length ? Math.min(...losses) : 0;
  const { maxWin, maxLoss } = streaks(pnls);
  const dd = drawdownDuration(series.accountValue);

  // Time span. CAGR/Calmar are ONLY meaningful over a multi-year window —
  // annualizing a large return over a few months produces absurd, misleading
  // numbers (e.g. 1900% CAGR), so they are suppressed (null) under ~1 year.
  const tEq = series.accountValue;
  let years = null;
  if (tEq.length >= 2) years = (tEq[tEq.length - 1].time - tEq[0].time) / (365.25 * 86400);
  let cagr = null;
  let calmar = null;
  if (series.currency && series.capital > 0 && years && years >= 1.0 && portfolio?.ending_equity != null) {
    const growth = Number(portfolio.ending_equity) / series.capital;
    if (growth > 0) cagr = (Math.pow(growth, 1 / years) * 100) - 100;
    const maxDdPct = Math.abs(Number(portfolio.max_drawdown_pct) || 0);
    if (maxDdPct > 0 && cagr != null) calmar = cagr / maxDdPct;
  }

  // Return per unit of worst drawdown — span-INDEPENDENT and honest (how much
  // you made for each rupee/point of the deepest peak-to-trough decline). The
  // headline risk-vs-reward number that works on any window length.
  const netForRatio = cur
    ? (portfolio?.net_pnl_value ?? 0)
    : (result?.metrics?.total_pnl_pts ?? 0);
  const ddForRatio = cur
    ? Math.abs(Number(portfolio?.max_drawdown_value) || 0)
    : Math.abs(Number(result?.metrics?.max_dd_pts) || 0);
  const returnOverMaxDd = ddForRatio > 0 ? netForRatio / ddForRatio : null;
  const sharpe = cur ? (portfolio?.sharpe_daily ?? null) : (result?.metrics?.sharpe ?? null);

  // Lowest / highest the account (capital growth) ever reached.
  const accVals = series.accountValue.map((p) => p.value).filter((v) => Number.isFinite(v));
  const minAccountValue = accVals.length ? Math.min(...accVals) : null;
  const maxAccountValue = accVals.length ? Math.max(...accVals) : null;

  // Peak cash a SINGLE trade would have tied up — entry premium x quantity plus
  // round-trip charges, i.e. the Trades table's "Buy ₹" column, maximised. Net
  // P&L and return % say nothing about the cash that must be free at the worst
  // moment, and a strategy whose peak outlay exceeds the account cannot be
  // traded at all however good the backtest looks. Uses `tradeBuyValue` so the
  // card and the column can never disagree. Null (not 0) in points mode: a
  // spot-only run has no premium outlay, and 0 would read as "costs nothing".
  const buyValues = (ob?.trades || [])
    .filter((t) => t.status === "PAIRED")
    .map((t) => tradeBuyValue(t))
    .filter((v) => Number.isFinite(v));
  const maxBuyValue = series.currency && buyValues.length ? Math.max(...buyValues) : null;

  // PEAK CONCURRENT outlay — the number that actually decides whether an account
  // can run this strategy. The single-trade max understates it whenever legs
  // overlap, and `leg_mode: "both"` opens CE and PE together plus a lazy
  // re-entry, so overlap is the normal case, not the exception. Sweep line over
  // (entry, exit) with +buy / -buy events; ties release before they acquire, so a
  // position closing on the same bar another opens does not double-count.
  let peakConcurrentCapital = null;
  if (series.currency) {
    const events = [];
    for (const t of (ob?.trades || [])) {
      if (t.status !== "PAIRED") continue;
      const v = tradeBuyValue(t);
      const a = Number(t.option_entry_ts ?? t.signal_entry_ts);
      const b = Number(t.option_exit_ts ?? t.signal_exit_ts);
      if (!Number.isFinite(v) || !Number.isFinite(a)) continue;
      events.push({ t: a, d: v });
      events.push({ t: Number.isFinite(b) ? b : a, d: -v });
    }
    if (events.length) {
      events.sort((x, y) => (x.t - y.t) || (x.d - y.d));
      let cur = 0;
      let peak = 0;
      for (const e of events) {
        cur += e.d;
        if (cur > peak) peak = cur;
      }
      peakConcurrentCapital = peak;
    }
  }
  // Does the configured account actually cover it? A return computed on a base
  // that could not have funded the trades is not a return.
  const capitalShortfall = (series.currency && peakConcurrentCapital != null
    && series.capital > 0)
    ? peakConcurrentCapital > series.capital
    : false;

  const tradingDays = portfolio?.trading_days
    || new Set((result?.trades || []).map((t) => String(t.exit_datetime || "").slice(0, 10)).filter(Boolean)).size
    || 0;
  const avgTradesPerDay = tradingDays ? n / tradingDays : null;

  return {
    currency: series.currency,
    unit: series.unit,
    capital: series.capital,
    endingEquity: portfolio?.ending_equity ?? null,
    netPnl: series.currency ? (portfolio?.net_pnl_value ?? null) : (result?.metrics?.total_pnl_pts ?? null),
    returnPct: series.currency ? (portfolio?.total_return_pct ?? null) : null,
    maxDdValue: series.currency ? (portfolio?.max_drawdown_value ?? null) : (result?.metrics?.max_dd_pts ?? null),
    maxDdPct: series.currency ? (portfolio?.max_drawdown_pct ?? null) : null,
    avgWin, avgLoss, payoff, expectancy, largestWin, largestLoss,
    maxWinStreak: maxWin, maxLossStreak: maxLoss,
    ddDurationDays: dd.days, recovered: dd.recovered,
    returnOverMaxDd, sharpe,
    minAccountValue, maxAccountValue, maxBuyValue,
    peakConcurrentCapital, capitalShortfall,
    cagr, calmar, years, tradingDays, avgTradesPerDay,
    tradeCount: n,
  };
}
