/**
 * Pure view logic for the Live Deployments pane and the risk cards around it.
 *
 * No imports, no JSX, no alias — this module loads under plain node, so every
 * decision the pane shows is tested by EXECUTING it (tests/test_live_deployment_view.py),
 * never by grepping JSX.
 *
 * The rule this module exists to keep: render the governor's own answer, never a
 * second derivation of it. The backend's `governor` block (live_deploy_governor.
 * describe_live_caps) carries the caps, what is consumed against them, and the
 * verdicts the entry path would reach. Loss consumed is realized PLUS open
 * unrealized there; a realized-only figure computed here would read "₹0 of ₹3,000
 * used" beside an open −₹3,200 loser. Unknown values arrive as null and stay null —
 * rendered "—", never 0.
 */

const fin = (v) => {
  if (v === null || v === undefined || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};

export function isLiveDeployment(dep) {
  return String(dep?.mode || "").toLowerCase() === "live";
}

/** The governor block, or {available:false} — a missing one never reads as OK. */
export function readGovernor(status) {
  const g = status?.governor;
  if (!g || typeof g !== "object") return { available: false, reason: "governor unavailable" };
  if (g.error) return { available: false, reason: String(g.error) };
  return { available: true, ...g };
}

/**
 * Cap headroom rows: lots today, positions open now, loss today.
 * `configured` is false for an unset cap (never implies a limit that is not
 * there); `unknown` is true when the governor could not measure the number.
 */
export function capHeadroom(gov) {
  if (!gov?.available) return [];
  const caps = gov.caps || {};
  const c = gov.consumed || {};
  const row = (key, label, used, max, unknown) => {
    const m = fin(max);
    const configured = m !== null && m > 0;
    const u = unknown ? null : fin(used);
    return {
      key, label, used: u, max: configured ? m : null, configured,
      unknown: Boolean(unknown),
      ratio: configured && u !== null ? u / m : null,
    };
  };
  const dayPnl = fin(c.day_pnl);
  const lossUnknown = Boolean(c.exposure_unknown) || dayPnl === null;
  return [
    row("lots", "lots today", c.lots_today, caps.max_lots_per_day, c.lots_today === null),
    row("concurrent", "open now", c.concurrent_now, caps.max_concurrent, false),
    row("loss", "loss today", lossUnknown ? null : Math.max(0, -dayPnl),
        caps.daily_loss_cap, lossUnknown),
  ];
}

const REASON_TEXT = {
  // authorization — checked first by the entry path
  no_deployment: "no deployment",
  not_live_mode: "not in live mode",
  live_paused: "held — no new entries",
  not_connected: "broker session not connected (or expired)",
  entry_cutoff_unresolvable: "entry cutoff unresolvable",
  after_entry_cutoff: "after the 15:00 IST entry cutoff",
  market_closed_today: "market closed today",
  before_market_open: "before the 09:15 open",
  // account layer
  account_latched: "account safety latch is set — reset required",
  account_max_open_block: "account max open positions reached",
  account_exposure_invalid: "account exposure unknown — a position mark is stale",
  account_exposure_unavailable: "account exposure unreadable",
  account_broker_stop_loss: "account daily loss limit hit",
  account_profit_lock: "account profit lock reached",
  account_config_unavailable: "account safety config unreadable",
  account_max_lots_per_order_unset: "account max lots per order not set — live disabled",
  // deployment layer
  live_caps_missing: "live mode with no caps configured",
  invalid_daily_loss_cap: "daily loss cap is not a number",
  exposure_unknown: "exposure unknown — a position mark is stale",
  daily_loss_cap: "daily loss cap hit",
  lots_unmeasurable: "today's lots unmeasurable (bad data)",
  max_lots_per_day: "daily lot cap reached",
  max_concurrent: "max concurrent positions open",
};

/** Human text for a governor refusal reason (prefix-matched for `x:detail`). */
export function reasonText(reason) {
  if (!reason) return null;
  const s = String(reason);
  const head = s.split(":")[0];
  return REASON_TEXT[s] || REASON_TEXT[head] || s.replace(/[_:]/g, " ").trim();
}

/**
 * What stops this deployment from placing an entry right now.
 * `canTrade` is true ONLY when the governor answered and nothing binds.
 */
export function bindingView(gov) {
  if (!gov?.available) {
    return { canTrade: false, tone: "warn", layer: null,
             text: "entry state unknown", title: gov?.reason || "governor unavailable" };
  }
  const b = gov.binding;
  if (!b) return { canTrade: true, tone: "ok", layer: null, text: "can trade now", title: "" };
  const tone = b.pause || b.layer === "account" ? "danger" : "warn";
  return {
    canTrade: false, tone, layer: b.layer,
    text: reasonText(b.reason),
    title: `${b.layer}: ${b.reason}${b.pause ? " (pauses the deployment)" : ""}`,
  };
}

/**
 * The Day Stop card: the WORST live deployment, not a pooled sum.
 *
 * Caps are enforced PER deployment, so pooling was a lie in the dangerous
 * direction: one deployment at 95% of its cap beside one at 0% pooled to ~48%
 * and read as safe. Loss used comes from the governor's day P&L (realized + open).
 */
export function worstDayStop(deployments, deployLive) {
  const live = (deployments || []).filter(isLiveDeployment);
  const rows = live.map((d) => {
    const gov = readGovernor((deployLive || {})[d.id]);
    const cap = gov.available ? fin(gov.caps?.daily_loss_cap) : null;
    const dayPnl = gov.available ? fin(gov.consumed?.day_pnl) : null;
    const unknown = !gov.available || Boolean(gov.consumed?.exposure_unknown) || dayPnl === null;
    const used = unknown ? null : Math.max(0, -dayPnl);
    return {
      id: d.id, name: d.name || d.id, cap, used, unknown,
      ratio: cap !== null && cap > 0 && used !== null ? used / cap : null,
    };
  });
  let worst = null;
  for (const r of rows) {
    if (r.ratio === null) continue;
    if (!worst || r.ratio > worst.ratio) worst = r;
  }
  const anyUnknown = rows.some((r) => r.cap !== null && r.unknown);
  const anyCap = rows.some((r) => r.cap !== null);
  let tone = "default";
  if (worst && worst.ratio >= 0.75) tone = "danger";
  else if (anyUnknown) tone = "warn";
  return { any: live.length > 0, anyCap, anyUnknown, worst, rows, tone };
}

/**
 * Row order: live deployments holding positions first, then held, then idle;
 * not-live deployments separately. Stable within a group. The deployment holding
 * real money must never render seventh.
 */
export function sortDeploymentRows(deployments, deployLive) {
  const live = [];
  const notLive = [];
  (deployments || []).forEach((d, i) => (isLiveDeployment(d) ? live : notLive).push([d, i]));
  const rank = (d) => {
    const st = (deployLive || {})[d.id] || {};
    const open = Array.isArray(st.open_positions) ? st.open_positions.length : 0;
    const gov = readGovernor(st);
    const concurrent = gov.available ? fin(gov.consumed?.concurrent_now) || 0 : 0;
    if (open > 0 || concurrent > 0) return 0;
    if (st.live_paused) return 1;
    return 2;
  };
  live.sort((a, b) => rank(a[0]) - rank(b[0]) || a[1] - b[1]);
  return { live: live.map((p) => p[0]), notLive: notLive.map((p) => p[0]) };
}

/**
 * Open positions joined to the marked book by trading symbol.
 * Distance to stop is from the LAST PRICE (room left before the stop fires), for
 * a long option: ltp − stop. `markStale` when no live tick backs the price.
 */
export function openPositionRows(openPositions, markedRows) {
  const byTsym = new Map();
  for (const r of markedRows || []) if (r?.tsym) byTsym.set(String(r.tsym), r);
  return (Array.isArray(openPositions) ? openPositions : []).map((op) => {
    const mark = byTsym.get(String(op?.tsym || ""));
    const ltp = mark ? fin(mark.lp) : null;
    const stop = fin(op?.stop_level);
    const pts = ltp !== null && stop !== null ? ltp - stop : null;
    return {
      tsym: op?.tsym || "—",
      qty: fin(op?.qty),
      entry: fin(op?.entry_price),
      stop,
      target: fin(op?.target_level),
      ltp,
      markStale: mark ? String(mark.mark_source || "") !== "tick" : true,
      distToStopPts: pts,
      distToStopPct: pts !== null && ltp ? (pts / ltp) * 100 : null,
      filled: Boolean(op?.seen_filled),
    };
  });
}

/**
 * The last entry the deployment INTENDED, in one line. Two shapes exist:
 * the premium-trigger audit {ref_premium, premium_at_entry} and the executor
 * dry-run {would_send, ref_ltp, lots}.
 */
export function describeIntended(intended) {
  if (!intended || typeof intended !== "object") return null;
  const parts = [];
  const lots = fin(intended.lots);
  if (lots !== null) parts.push(`${lots} lot${lots === 1 ? "" : "s"}`);
  const ref = fin(intended.ref_ltp ?? intended.ref_premium);
  if (ref !== null) parts.push(`ref ₹${ref.toFixed(2)}`);
  const at = fin(intended.premium_at_entry);
  if (at !== null) parts.push(`premium ₹${at.toFixed(2)}`);
  if (intended.would_send === false) parts.push("dry-run — not sent");
  return parts.length ? parts.join(" · ") : null;
}

const IST_MS = 5.5 * 3600 * 1000;
const istDay = (ms) => new Date(ms + IST_MS).toISOString().slice(0, 10);

/**
 * The entry-refused chip. The latest signal's refusal carries no date bound, so
 * a refusal from a previous session read as current. `stale` = not today (IST).
 */
export function entryRefusalView(lastEntry, nowMs) {
  if (!lastEntry || !lastEntry.error) return null;
  const atMs = lastEntry.at ? Date.parse(lastEntry.at) : NaN;
  const known = Number.isFinite(atMs);
  const stale = !known || istDay(atMs) !== istDay(nowMs);
  return {
    reason: lastEntry.error,
    stale,
    dateLabel: known ? istDay(atMs) : "date unknown",
  };
}

/**
 * The execution strip's two legs. "dry-run" means the gate chose not to send;
 * when the broker session is unusable NOTHING can be sent, which is a different
 * and far worse state — it must never be labelled as a harmless dry-run. An
 * absent `connected` (a payload older than this field) keeps the old reading.
 */
export function executionLegs(armState) {
  if (!armState) return null;
  const noBroker = armState.connected === false;
  const leg = (tx) => (noBroker
    ? { text: "BLOCKED — no broker", tone: "danger" }
    : tx ? { text: "TRANSMIT", tone: "danger" } : { text: "dry-run", tone: "dim" });
  return {
    entries: leg(Boolean(armState.would_transmit_entry)),
    // Absent must NOT read as dry-run: the guard always transmits when it can.
    squares: leg(armState.would_transmit_exit !== false),
    sessionExpired: Boolean(armState.session_expired),
  };
}

/**
 * The guard card, from the guard's own health — never from the constant `armed`.
 * An old backend without `health` degrades to "unknown", never to ARMED.
 */
export function guardHealthView(guard) {
  const h = guard?.health;
  if (!h || !h.state) {
    return { label: guard == null ? null : "UNKNOWN", tone: "warn",
             title: "guard health not reported" };
  }
  const tone = {
    watching: "success", idle: "default",
    blind: "danger", stalled: "danger", not_running: "danger",
  }[h.state] || "warn";
  return { label: h.label || String(h.state).toUpperCase(), tone, title: h.reason || "" };
}

/**
 * An exit report (/live/stop, /live/flatten, stop-all's per-deployment reports)
 * in one honest line. A broker ACCEPTANCE is not a fill, so nothing here ever
 * says "flattened"; anything failed, deferred, skipped or unguarded makes the
 * result not-ok. The old Stop toast said "Stopped" whatever the report held.
 */
export function summarizeExitReport(r) {
  if (!r || typeof r !== "object") return { ok: false, tone: "danger", message: "no response from the exit request" };
  const list = (k) => (Array.isArray(r[k]) ? r[k] : []);
  const done = [];
  const problems = [];
  const s = list("exit_submitted_tsyms");
  if (s.length) done.push(`exit submitted: ${s.join(", ")} — awaiting fill confirmation`);
  const f = list("already_flat_tsyms");
  if (f.length) done.push(`already flat: ${f.join(", ")}`);
  const c = list("cancel_confirmed_tsyms");
  if (c.length) done.push(`unfilled entry cancelled: ${c.join(", ")}`);
  const q = list("already_squaring_tsyms");
  if (q.length) done.push(`already exiting: ${q.join(", ")}`);
  const failed = list("failed_tsyms");
  if (failed.length) problems.push(`FAILED — still open: ${failed.join(", ")}`);
  const deferred = list("deferred_tsyms");
  if (deferred.length) problems.push(`deferred (another exit in flight): ${deferred.join(", ")}`);
  const shared = list("skipped_shared_tsyms");
  if (shared.length) problems.push(`NOT sent — contract shared with another position: ${shared.join(", ")}`);
  const ung = list("unguarded_open_tsyms");
  if (ung.length) problems.push(`open in the journal but not held by the guard: ${ung.join(", ")} — check the broker`);
  const ok = problems.length === 0;
  if (!done.length && ok) done.push("nothing was open to exit");
  return { ok, tone: ok ? "success" : "danger", message: [...done, ...problems].join(" · ") };
}

/**
 * Approximate value of an open position row (from openPositionRows): last price ×
 * quantity, or null when either is unknown. Null renders "value unknown" — never 0,
 * so a position with no price cannot read as a position worth nothing.
 */
export function positionValue(p) {
  const ltp = fin(p?.ltp);
  const qty = fin(p?.qty);
  return ltp !== null && qty !== null ? ltp * qty : null;
}

/**
 * The LIVE half of a Stop ALL response, judged per deployment.
 *
 * /deployments/stop-all returns `live_exit_reports` ({deployment_id: exit report}).
 * The old toast said "N live deployment(s) disabled" whatever those reports held, so
 * a failed exit — a position still open on the broker — arrived under a green
 * banner. Here EVERY deployment stop-all touched (the disarmed ids AND any report
 * keys) must have a report, and each report must be clean by summarizeExitReport's
 * rule; a missing report is a problem, not a pass. `nameById` names them for the
 * operator. `submitted` counts exits sent (an acceptance, not a fill).
 */
export function stopAllLiveVerdict(res, nameById) {
  const names = nameById || {};
  const reports = res && typeof res.live_exit_reports === "object" && res.live_exit_reports
    ? res.live_exit_reports : {};
  const disarmed = Array.isArray(res?.disarmed_live_deployment_ids)
    ? res.disarmed_live_deployment_ids : [];
  const ids = [...new Set([...disarmed, ...Object.keys(reports)])];
  const problems = [];
  let submitted = 0;
  for (const id of ids) {
    const name = names[id] || String(id);
    const report = reports[id];
    if (report === undefined || report === null) {
      problems.push({ id, name, message: "no exit report returned — check the broker" });
      continue;
    }
    const s = summarizeExitReport(report);
    if (!s.ok) problems.push({ id, name, message: s.message });
    if (Array.isArray(report.exit_submitted_tsyms)) submitted += report.exit_submitted_tsyms.length;
  }
  return {
    ok: problems.length === 0,
    problems,
    submitted,
    message: problems.map((p) => `${p.name}: ${p.message}`).join(" · "),
  };
}
