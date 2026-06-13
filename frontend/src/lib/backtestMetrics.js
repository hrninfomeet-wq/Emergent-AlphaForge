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
    minAccountValue, maxAccountValue,
    cagr, calmar, years, tradingDays, avgTradesPerDay,
    tradeCount: n,
  };
}
