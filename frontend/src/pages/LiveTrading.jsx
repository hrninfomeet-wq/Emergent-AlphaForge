import { useCallback, useEffect, useRef, useState } from "react";
import { CheckCircle, AlertTriangle } from "lucide-react";
import { api } from "@/lib/api";
import { fmtINR, colorPnL } from "@/lib/fmt";
import LiveBanner from "@/components/live/LiveBanner";
import AccountStrip from "@/components/live/AccountStrip";
import LiveOrderPanel from "@/components/live/LiveOrderPanel";

/**
 * Live Trading page — real-money broker state + L3 Live-Test order panel.
 *
 * Shows real-money broker state (Flattrade / Noren) in read-only mode (L0)
 * plus the L3 execution controls: mode switch, dry-run order ticket,
 * 1-lot place (LIVE_TEST only), and a position monitor with countdown.
 *
 * Polls:
 *   flattradeStatus     — connection / session health  → LiveBanner
 *   liveBrokerLimits    — margin / cash card           → AccountStrip
 *   liveBrokerPositions — open positions blotter       → PositionsBlotter
 *   liveBrokerOrders    — working orders blotter       → OrdersBlotter
 *   liveBrokerReconcile — reconciliation chip
 *
 * Every fetch is individually .catch()'d so a not-yet-connected backend
 * never crashes the page.
 */

const POLL_MS = 15_000;

// ─────────────────────────────────────────────────────────────────────────────
// Colour helpers
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
// Positions blotter
// ─────────────────────────────────────────────────────────────────────────────
function PositionsBlotter({ positions }) {
  if (!positions) {
    return (
      <div className="text-xs text-dimmer font-mono py-4 text-center">
        Loading positions&hellip;
      </div>
    );
  }

  // Noren returns an array; some API wrappers nest it under .data or .positions
  const rows = Array.isArray(positions)
    ? positions
    : (positions.data ?? positions.positions ?? []);

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
// Working orders blotter
// ─────────────────────────────────────────────────────────────────────────────
const ORDER_SIDE_LABEL = { B: "BUY", S: "SELL", b: "BUY", s: "SELL" };
const ORDER_SIDE_CLASS = { B: "text-success", S: "text-danger", b: "text-success", s: "text-danger" };

function OrdersBlotter({ orders }) {
  if (!orders) {
    return (
      <div className="text-xs text-dimmer font-mono py-4 text-center">
        Loading orders&hellip;
      </div>
    );
  }

  const rows = Array.isArray(orders)
    ? orders
    : (orders.data ?? orders.orders ?? []);

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
// Reconciliation chip
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
// Section card wrapper
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
// Main page
// ─────────────────────────────────────────────────────────────────────────────
export default function LiveTrading() {
  const [status, setStatus] = useState(null);
  const [limits, setLimits] = useState(null);
  const [positions, setPositions] = useState(null);
  const [orders, setOrders] = useState(null);
  const [reconcile, setReconcile] = useState(null);
  const [authMsg, setAuthMsg] = useState(null);

  const timerRef = useRef(null);

  const fetchAll = useCallback(() => {
    api.flattradeStatus().then(setStatus).catch(() => null);
    api.liveBrokerLimits().then(setLimits).catch(() => null);
    api.liveBrokerPositions().then(setPositions).catch(() => null);
    api.liveBrokerOrders().then(setOrders).catch(() => null);
    api.liveBrokerReconcile().then(setReconcile).catch(() => null);
  }, []);

  useEffect(() => {
    fetchAll();
    timerRef.current = setInterval(fetchAll, POLL_MS);
    return () => clearInterval(timerRef.current);
  }, [fetchAll]);

  // OAuth post-redirect handling: Flattrade bounces back to
  // /live-trading?flattrade_connected=1 (or ?flattrade_error=...). Surface it,
  // refresh status, then strip the param from the URL.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.has("flattrade_connected")) {
      setAuthMsg({ ok: true, text: "Flattrade login successful — connected." });
      fetchAll();
    } else if (params.has("flattrade_error")) {
      setAuthMsg({ ok: false, text: `Flattrade login failed: ${params.get("flattrade_error")}` });
    }
    if (params.has("flattrade_connected") || params.has("flattrade_error")) {
      window.history.replaceState({}, "", window.location.pathname);
    }
  }, [fetchAll]);

  // Derive counts defensively for AccountStrip
  const positionCount = positions != null
    ? (Array.isArray(positions) ? positions : (positions.data ?? positions.positions ?? [])).length
    : null;

  const orderCount = orders != null
    ? (Array.isArray(orders) ? orders : (orders.data ?? orders.orders ?? [])).length
    : null;

  return (
    <div className="space-y-4">
      {/* Bold live banner — connection state + Login / Logout buttons */}
      <LiveBanner status={status} onRefresh={fetchAll} />

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

      {/* Account metrics strip */}
      <SectionCard
        title="Account Overview"
        badge={<ReconcileChip reconcile={reconcile} />}
      >
        <AccountStrip
          limits={limits}
          positionCount={positionCount}
          orderCount={orderCount}
        />
      </SectionCard>

      {/* Positions blotter */}
      <SectionCard title="Open Positions">
        <PositionsBlotter positions={positions} />
      </SectionCard>

      {/* Working orders blotter */}
      <SectionCard title="Working Orders">
        <OrdersBlotter orders={orders} />
      </SectionCard>

      {/* Live order panel — mode switch, position monitor, order ticket, approval queue */}
      <LiveOrderPanel />
    </div>
  );
}
