import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle,
  ClipboardList,
  Layers,
  Shield,
  TrendingUp,
  Wallet,
  Zap,
} from "lucide-react";
import { api } from "@/lib/api";
import { fmtINR } from "@/lib/fmt";

import LiveBanner from "@/components/live/LiveBanner";
import LiveDeploymentStrip from "@/components/live/LiveDeploymentStrip";
import PositionMonitor from "@/components/live/PositionMonitor";
import LiveOrderTicket from "@/components/live/LiveOrderTicket";
import OverallSettingsPanel from "@/components/live/OverallSettingsPanel";
import GttBook from "@/components/live/GttBook";
import GuardPanel from "@/components/live/GuardPanel";
import MetricCard from "@/components/live/MetricCard";

/**
 * LiveDashboard — the assembled Live Trading terminal.
 *
 * OWNS the page state + polling (lifted from the old LiveTrading.jsx page) and
 * the OAuth post-redirect handling, then lays everything out as a cohesive
 * dashboard:
 *
 *   1. Connection banner (+ auth message banner)
 *   2. Hero metric strip (cash / day P&L / positions / orders / mode / guard)
 *   3. Two-column working grid
 *        LEFT  — execution mode · order ticket · approval queue
 *        RIGHT — position monitor · positions blotter · orders blotter · guard
 *   4. Config row — overall controls · GTT / OCO backstop
 *
 * Every existing component is REUSED (never rewritten). The positions / orders
 * blotters + reconcile chip + colour helpers are copied verbatim from the old
 * page so the tables render identically.
 *
 * Polling:
 *   flattradeStatus / liveBrokerLimits / liveBrokerPositions / liveBrokerOrders /
 *   liveBrokerReconcile  — every 15s, each individually .catch()'d.
 *   getGuardStatus       — every 15s for the hero Guard tile only (GuardPanel
 *                          polls its own status independently at 3s).
 *
 * NOTE — PayoffChart was OMITTED. The contract referenced
 * @/components/live/PayoffChart, but no such file exists in the repo; wiring a
 * non-existent import would break the build. A follow-up should add the
 * component (and lift the order-ticket state) before wiring it here.
 */

const POLL_MS = 15_000;

// ─────────────────────────────────────────────────────────────────────────────
// Colour helpers (copied from the old LiveTrading.jsx — identical behaviour)
// ─────────────────────────────────────────────────────────────────────────────
function pnlClass(val) {
  const n = parseFloat(val);
  if (!Number.isFinite(n)) return "text-dimmer";
  if (n > 0) return "text-success";
  if (n < 0) return "text-danger";
  return "text-dim";
}

function signedINR(val) {
  const n = parseFloat(val);
  if (!Number.isFinite(n)) return "–";
  const sign = n >= 0 ? "+" : "";
  return `${sign}${fmtINR(n)}`;
}

// ─────────────────────────────────────────────────────────────────────────────
// Defensive array extraction — Noren returns an array; some wrappers nest it.
// ─────────────────────────────────────────────────────────────────────────────
function asPositionRows(positions) {
  if (positions == null) return null;
  return Array.isArray(positions)
    ? positions
    : (positions.data ?? positions.positions ?? []);
}

function asOrderRows(orders) {
  if (orders == null) return null;
  return Array.isArray(orders) ? orders : (orders.data ?? orders.orders ?? []);
}

// Only genuinely-open positions (netqty != 0) and live (non-terminal) orders
// count as "open" / "working" — a flat position or a COMPLETE/REJECTED order is
// history, not an open exposure.
function isOpenPosition(p) {
  const n = parseFloat(p?.netqty ?? p?.quantity);
  return Number.isFinite(n) && n !== 0;
}

const _TERMINAL_ORDER = new Set(["COMPLETE", "REJECTED", "CANCELED", "CANCELLED"]);
function isWorkingOrder(o) {
  const s = String(o?.status ?? "").trim().toUpperCase();
  return s !== "" && !_TERMINAL_ORDER.has(s);
}

// ─────────────────────────────────────────────────────────────────────────────
// Positions blotter (copied verbatim from the old LiveTrading.jsx)
// ─────────────────────────────────────────────────────────────────────────────
function PositionsBlotter({ positions }) {
  if (!positions) {
    return (
      <div className="text-xs text-dimmer font-mono py-4 text-center">
        Loading positions&hellip;
      </div>
    );
  }

  const rows = (asPositionRows(positions) ?? []).filter(isOpenPosition);

  if (rows.length === 0) {
    return (
      <div className="text-xs text-dimmer font-mono py-6 text-center">
        No open positions
      </div>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs font-mono tabular-nums">
        <thead>
          <tr className="border-b border-line text-dimmer uppercase tracking-wider text-[10px]">
            <th className="text-left py-2 pr-3 pl-0">Symbol</th>
            <th className="text-right py-2 px-3">Qty</th>
            <th className="text-right py-2 px-3">Buy Avg</th>
            <th className="text-right py-2 px-3">Net Avg</th>
            <th className="text-right py-2 px-3">LTP</th>
            <th className="text-right py-2 pl-3 pr-0">MTM P&amp;L</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((p, i) => {
            const sym = p.tsym ?? p.tradingsymbol ?? "–";
            const qty = p.netqty ?? p.quantity ?? "–";
            const buyAvg = p.daybuyavgprc ?? p.buy_price ?? null;
            const netAvg = p.netavgprc ?? p.average_price ?? null;
            const ltp = p.lp ?? p.last_price ?? null;
            const mtm = p.urmtom ?? p.rpnl ?? p.pnl ?? null;
            return (
              <tr
                key={p.norenordno ?? p.order_id ?? i}
                className="border-b border-line/50 hover:bg-bg-2/40 transition-colors"
              >
                <td className="py-2 pr-3 pl-0 text-foreground font-semibold">{sym}</td>
                <td className="py-2 px-3 text-right text-foreground">{qty}</td>
                <td className="py-2 px-3 text-right text-dim">
                  {buyAvg != null ? fmtINR(parseFloat(buyAvg)) : "–"}
                </td>
                <td className="py-2 px-3 text-right text-dim">
                  {netAvg != null ? fmtINR(parseFloat(netAvg)) : "–"}
                </td>
                <td className="py-2 px-3 text-right text-foreground">
                  {ltp != null ? fmtINR(parseFloat(ltp)) : "–"}
                </td>
                <td className={`py-2 pl-3 pr-0 text-right font-semibold ${pnlClass(mtm)}`}>
                  {mtm != null ? signedINR(mtm) : "–"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Working orders blotter (copied verbatim from the old LiveTrading.jsx)
// ─────────────────────────────────────────────────────────────────────────────
const ORDER_SIDE_LABEL = { B: "BUY", S: "SELL", b: "BUY", s: "SELL" };
const ORDER_SIDE_CLASS = {
  B: "text-success",
  S: "text-danger",
  b: "text-success",
  s: "text-danger",
};

function OrdersBlotter({ orders }) {
  if (!orders) {
    return (
      <div className="text-xs text-dimmer font-mono py-4 text-center">
        Loading orders&hellip;
      </div>
    );
  }

  const rows = (asOrderRows(orders) ?? []).filter(isWorkingOrder);

  if (rows.length === 0) {
    return (
      <div className="text-xs text-dimmer font-mono py-6 text-center">
        No working orders
      </div>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs font-mono tabular-nums">
        <thead>
          <tr className="border-b border-line text-dimmer uppercase tracking-wider text-[10px]">
            <th className="text-left py-2 pr-3 pl-0">Symbol</th>
            <th className="text-center py-2 px-3">Side</th>
            <th className="text-right py-2 px-3">Qty</th>
            <th className="text-right py-2 px-3">Price</th>
            <th className="text-left py-2 px-3">Type</th>
            <th className="text-left py-2 px-3">Status</th>
            <th className="text-left py-2 pl-3 pr-0">Order ID</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((o, i) => {
            const sym = o.tsym ?? o.tradingsymbol ?? "–";
            const side = o.trantype ?? o.transaction_type ?? "–";
            const qty = o.qty ?? o.quantity ?? "–";
            const prc = o.prc ?? o.price ?? null;
            const prctyp = o.prctyp ?? o.order_type ?? "–";
            const status = o.status ?? "–";
            const orderId = o.norenordno ?? o.order_id ?? String(i);
            return (
              <tr
                key={orderId}
                className="border-b border-line/50 hover:bg-bg-2/40 transition-colors"
              >
                <td className="py-2 pr-3 pl-0 text-foreground font-semibold">{sym}</td>
                <td className={`py-2 px-3 text-center font-bold ${ORDER_SIDE_CLASS[side] ?? "text-dim"}`}>
                  {ORDER_SIDE_LABEL[side] ?? side}
                </td>
                <td className="py-2 px-3 text-right text-foreground">{qty}</td>
                <td className="py-2 px-3 text-right text-dim">
                  {prc != null ? fmtINR(parseFloat(prc)) : "MKT"}
                </td>
                <td className="py-2 px-3 text-dim">{prctyp}</td>
                <td className="py-2 px-3">
                  <span className="px-1.5 py-0.5 rounded bg-bg-3 border border-line text-dimmer text-[10px] uppercase tracking-wide">
                    {status}
                  </span>
                </td>
                <td className="py-2 pl-3 pr-0 text-dimmer truncate max-w-[120px]">{orderId}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Reconciliation chip (copied verbatim from the old LiveTrading.jsx)
// ─────────────────────────────────────────────────────────────────────────────
function ReconcileChip({ reconcile }) {
  if (!reconcile) return null;

  if (reconcile.ok) {
    return (
      <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border border-emerald-500/40 bg-emerald-500/10 text-emerald-300 text-xs font-mono">
        <CheckCircle className="w-3.5 h-3.5" />
        Reconciled &#10003;
      </span>
    );
  }

  const mismatches = reconcile.mismatches ?? [];
  return (
    <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border border-amber-500/40 bg-amber-500/10 text-amber-300 text-xs font-mono">
      <AlertTriangle className="w-3.5 h-3.5" />
      {mismatches.length > 0
        ? `${mismatches.length} mismatch${mismatches.length !== 1 ? "es" : ""}: ${mismatches.slice(0, 3).join(", ")}${mismatches.length > 3 ? "…" : ""}`
        : "Reconciliation mismatch"}
    </span>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Section card wrapper (identical to LiveOrderPanel's local SectionCard)
// ─────────────────────────────────────────────────────────────────────────────
function SectionCard({ title, badge, children }) {
  return (
    <div className="rounded-lg border border-line bg-bg-1 overflow-hidden">
      <div className="px-4 py-2.5 border-b border-line bg-bg-2/40 flex items-center gap-2">
        <span className="text-sm font-semibold text-foreground">{title}</span>
        {badge}
      </div>
      <div className="px-4 py-3">{children}</div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Day P&L derivation — Σ position urmtom / rpnl / pnl (parseFloat, finite only)
// ─────────────────────────────────────────────────────────────────────────────
function deriveDayPnl(positions) {
  const rows = asPositionRows(positions);
  if (!rows || rows.length === 0) return null;
  let sum = 0;
  let any = false;
  for (const p of rows) {
    // Day P&L per position = unrealised MTM (urmtom) + realised (rpnl). A closed
    // position has urmtom 0 but its rpnl carries the day's realised gain/loss.
    const u = parseFloat(p?.urmtom);
    const r = parseFloat(p?.rpnl);
    if (Number.isFinite(u)) { sum += u; any = true; }
    if (Number.isFinite(r)) { sum += r; any = true; }
    if (!Number.isFinite(u) && !Number.isFinite(r)) {
      const g = parseFloat(p?.pnl);
      if (Number.isFinite(g)) { sum += g; any = true; }
    }
  }
  return any ? sum : null;
}

// ─────────────────────────────────────────────────────────────────────────────
// Available cash — defensive across Noren limits field names (mirrors AccountStrip)
// ─────────────────────────────────────────────────────────────────────────────
function deriveCash(limits) {
  const v = limits?.cash ?? limits?.net ?? limits?.marginusedtoday ?? null;
  return v == null ? null : Number(v);
}

// ─────────────────────────────────────────────────────────────────────────────
// Main dashboard
// ─────────────────────────────────────────────────────────────────────────────
export default function LiveDashboard() {
  // ── Broker state ──────────────────────────────────────────────────────────
  const [status, setStatus] = useState(null);
  const [limits, setLimits] = useState(null);
  const [positions, setPositions] = useState(null);
  const [orders, setOrders] = useState(null);
  const [reconcile, setReconcile] = useState(null);
  const [authMsg, setAuthMsg] = useState(null);

  // ── Execution mode (for the hero tile; the order ticket auto-arms on Place) ──
  const [mode, setMode] = useState(null); // null = loading

  // ── Hero guard summary (GuardPanel polls its own copy; this is just the tile) ─
  const [guard, setGuard] = useState(null);

  // ── Deployments for the Live Deployment strip ─────────────────────────────
  const [deployments, setDeployments] = useState([]);
  const [armedSummary, setArmedSummary] = useState({ armedCount: 0, autoplaceArmed: null });

  const timerRef = useRef(null);

  // ── Poll all broker endpoints (each individually .catch'd) ────────────────
  const fetchAll = useCallback(() => {
    api.flattradeStatus().then(setStatus).catch(() => null);
    api.liveBrokerLimits().then(setLimits).catch(() => null);
    api.liveBrokerPositions().then(setPositions).catch(() => null);
    api.liveBrokerOrders().then(setOrders).catch(() => null);
    api.liveBrokerReconcile().then(setReconcile).catch(() => null);
    api.getGuardStatus().then(setGuard).catch(() => null);
    // Poll mode too so the hero tile reflects the auto-arm/revert (LIVE_TEST is
    // single-shot — armed on Place, reverted after the order).
    api.getLiveMode().then((d) => setMode(d?.mode ?? null)).catch(() => null);
    // Deployments — for the Live Deployment strip.
    api.listDeployments({ limit: 200 })
      .then((d) => setDeployments((d.items || []).filter((dep) => String(dep.status || "").toUpperCase() !== "ARCHIVED")))
      .catch(() => null);
  }, []);

  useEffect(() => {
    fetchAll();
    timerRef.current = setInterval(fetchAll, POLL_MS);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [fetchAll]);

  // ── Execution mode (load once; ModeSwitch updates it on change) ───────────
  useEffect(() => {
    let cancelled = false;
    api
      .getLiveMode()
      .then((data) => {
        if (!cancelled) setMode(data?.mode ?? null);
      })
      .catch(() => {
        /* backend not wired yet — stay null */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // ── OAuth post-redirect handling ──────────────────────────────────────────
  // Flattrade bounces back to /live-trading?flattrade_connected=1 (or
  // ?flattrade_error=...). Surface it, refresh status, then strip the param.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.has("flattrade_connected")) {
      setAuthMsg({ ok: true, text: "Flattrade login successful — connected." });
      fetchAll();
    } else if (params.has("flattrade_error")) {
      setAuthMsg({
        ok: false,
        text: `Flattrade login failed: ${params.get("flattrade_error")}`,
      });
    }
    if (params.has("flattrade_connected") || params.has("flattrade_error")) {
      window.history.replaceState({}, "", window.location.pathname);
    }
  }, [fetchAll]);

  // ── Derived hero values ───────────────────────────────────────────────────
  const positionRows = asPositionRows(positions);
  const orderRows = asOrderRows(orders);
  const positionCount = positionRows != null ? positionRows.filter(isOpenPosition).length : null;
  const orderCount = orderRows != null ? orderRows.filter(isWorkingOrder).length : null;

  const dayPnl = deriveDayPnl(positions);
  const cash = deriveCash(limits);

  const isLiveTest = mode === "LIVE_TEST";

  // Armed-live deployment count + autoplace state for the banner.
  // Lifted up from LiveDeploymentStrip via onArmedSummaryChange callback.
  const { armedCount, autoplaceArmed } = armedSummary;

  // Guard tile: ARMED (danger) vs DRY-RUN (warn) + guarded count.
  const guardArmed = !!guard?.armed;
  const guardCount = (() => {
    if (guard == null) return null;
    const raw = Number(guard?.count);
    if (Number.isFinite(raw)) return raw;
    return Array.isArray(guard?.guarded) ? guard.guarded.length : 0;
  })();

  return (
    <div className="space-y-4">
      {/* ── 1. Connection banner + auth message ─────────────────────────── */}
      <LiveBanner status={status} onRefresh={fetchAll} armedCount={armedCount} autoplaceArmed={autoplaceArmed} />

      {authMsg && (
        <div
          className={`text-sm font-mono px-3 py-2 rounded border ${
            authMsg.ok
              ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
              : "border-danger/40 bg-danger/10 text-danger"
          }`}
        >
          {authMsg.text}
        </div>
      )}

      {/* ── 1b. Live Deployment strip ───────────────────────────────────── */}
      <LiveDeploymentStrip deployments={deployments} onRefresh={fetchAll} onArmedSummaryChange={setArmedSummary} />

      {/* ── 2. Hero metric strip ────────────────────────────────────────── */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
        <MetricCard
          label="Available Cash"
          value={cash != null ? fmtINR(cash) : null}
          loading={limits == null}
          icon={<Wallet className="w-3.5 h-3.5" />}
          sub="broker net"
        />
        <MetricCard
          label="Day P&L"
          value={dayPnl != null ? signedINR(dayPnl) : null}
          tone={dayPnl == null ? "default" : dayPnl >= 0 ? "success" : "danger"}
          loading={positions == null}
          icon={<TrendingUp className="w-3.5 h-3.5" />}
          sub="open MTM + realised"
        />
        <MetricCard
          label="Open Positions"
          value={positionCount != null ? String(positionCount) : null}
          loading={positions == null}
          icon={<Layers className="w-3.5 h-3.5" />}
          sub="from broker"
        />
        <MetricCard
          label="Working Orders"
          value={orderCount != null ? String(orderCount) : null}
          loading={orders == null}
          icon={<ClipboardList className="w-3.5 h-3.5" />}
          sub="from broker"
        />
        <MetricCard
          label="Mode"
          value={mode ?? "—"}
          tone={isLiveTest ? "info" : "default"}
          loading={mode === null}
          icon={<Zap className="w-3.5 h-3.5" />}
          sub={isLiveTest ? "real orders armed" : "execution mode"}
        />
        <MetricCard
          label="Guard"
          value={
            guard == null
              ? null
              : guardArmed
              ? "ARMED"
              : "DRY-RUN"
          }
          tone={guard == null ? "default" : guardArmed ? "danger" : "warn"}
          loading={guard == null}
          icon={<Shield className="w-3.5 h-3.5" />}
          sub={guardCount != null ? `${guardCount} guarded` : "auto-exit"}
        />
      </div>

      {/* ── 3. Two-column working grid ──────────────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* LEFT column — the direct order ticket (one-click place; the
            mode-switch + approval-queue steps are folded into the Place button) */}
        <div className="space-y-4">
          <SectionCard
            title="Order Ticket"
            badge={
              <span className="text-[10px] font-mono text-danger px-2 py-0.5 rounded-full border border-danger/40 bg-danger/10 uppercase tracking-wider font-bold">
                REAL MONEY · 1-CLICK
              </span>
            }
          >
            <LiveOrderTicket mode={mode} disabled={false} />
          </SectionCard>
        </div>

        {/* RIGHT column — monitor · positions · orders · guard */}
        <div className="space-y-4">
          {/* PositionMonitor self-polls + self-renders (no card chrome). */}
          <PositionMonitor />

          <SectionCard title="Open Positions" badge={<ReconcileChip reconcile={reconcile} />}>
            <PositionsBlotter positions={positions} />
          </SectionCard>

          <SectionCard title="Working Orders">
            <OrdersBlotter orders={orders} />
          </SectionCard>

          {/* GuardPanel brings its own card chrome + header. */}
          <GuardPanel />
        </div>
      </div>

      {/* ── 4. Config row — overall controls · GTT / OCO backstop ───────── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <SectionCard
          title="Overall Controls"
          badge={
            <span className="text-[10px] font-mono text-dimmer px-2 py-0.5 rounded-full border border-line bg-bg-3 uppercase tracking-wider">
              basket SL / target / trailing
            </span>
          }
        >
          <OverallSettingsPanel scope="overall" />
        </SectionCard>

        <SectionCard
          title="GTT / OCO Backstop"
          badge={
            <span className="text-[10px] font-mono text-dimmer px-2 py-0.5 rounded-full border border-line bg-bg-3 uppercase tracking-wider">
              NRML PC-died net
            </span>
          }
        >
          <GttBook />
        </SectionCard>
      </div>
    </div>
  );
}
