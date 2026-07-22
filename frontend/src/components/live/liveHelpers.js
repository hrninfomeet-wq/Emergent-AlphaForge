import { AlertTriangle, CheckCircle } from "lucide-react";
import { fmtINR } from "@/lib/fmt";

/**
 * Shared live-terminal helpers + blotter components, extracted VERBATIM from the
 * original LiveDashboard.jsx so the cockpit re-uses (never rewrites) them. Colour
 * helpers, defensive Noren row extraction, day-P&L / cash derivations, the
 * positions / orders blotters, the reconcile chip, and the section-card wrapper.
 */

// ── Colour helpers ──────────────────────────────────────────────────────────
export function pnlClass(val) {
  const n = parseFloat(val);
  if (!Number.isFinite(n)) return "text-dimmer";
  if (n > 0) return "text-success";
  if (n < 0) return "text-danger";
  return "text-dim";
}

export function signedINR(val) {
  const n = parseFloat(val);
  if (!Number.isFinite(n)) return "–";
  const sign = n >= 0 ? "+" : "";
  return `${sign}${fmtINR(n)}`;
}

// "HH:MM:SS" of a last-success epoch-ms, or "—".
export function fmtAsOf(ms) {
  if (!Number.isFinite(ms)) return "—";
  try {
    return new Date(ms).toLocaleTimeString();
  } catch {
    return "—";
  }
}

export const SLICE_LABEL = {
  status: "connection", limits: "cash/margin", positions: "positions",
  orders: "orders", reconcile: "reconcile", armState: "arm-state", blotter: "blotter",
  marketAnalysis: "market analysis", holdings: "holdings",
};

// ── Defensive array extraction (Noren returns an array; some wrappers nest it) ─
export function asPositionRows(positions) {
  if (positions == null) return null;
  return Array.isArray(positions)
    ? positions
    : (positions.data ?? positions.positions ?? []);
}

export function asOrderRows(orders) {
  if (orders == null) return null;
  return Array.isArray(orders) ? orders : (orders.data ?? orders.orders ?? []);
}

export function isOpenPosition(p) {
  const n = parseFloat(p?.netqty ?? p?.quantity);
  return Number.isFinite(n) && n !== 0;
}

const _TERMINAL_ORDER = new Set(["COMPLETE", "REJECTED", "CANCELED", "CANCELLED"]);
export function isWorkingOrder(o) {
  const s = String(o?.status ?? "").trim().toUpperCase();
  return s !== "" && !_TERMINAL_ORDER.has(s);
}

// ── Day P&L derivation — Σ position urmtom / rpnl / pnl (finite only) ─────────
export function deriveDayPnl(positions) {
  const rows = asPositionRows(positions);
  if (!rows || rows.length === 0) return null;
  let sum = 0;
  let any = false;
  for (const p of rows) {
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

// ── Available cash — defensive across Noren limits field names ───────────────
export function deriveCash(limits) {
  const v = limits?.cash ?? limits?.net ?? limits?.marginusedtoday ?? null;
  return v == null ? null : Number(v);
}

// ── Section card wrapper ─────────────────────────────────────────────────────
export function SectionCard({ title, badge, children }) {
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

// ── Positions blotter (verbatim) ─────────────────────────────────────────────
export function PositionsBlotter({ positions }) {
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
              <tr key={p.norenordno ?? p.order_id ?? i} className="border-b border-line/50 hover:bg-bg-2/40 transition-colors">
                <td className="py-2 pr-3 pl-0 text-foreground font-semibold">{sym}</td>
                <td className="py-2 px-3 text-right text-foreground">{qty}</td>
                <td className="py-2 px-3 text-right text-dim">{buyAvg != null ? fmtINR(parseFloat(buyAvg)) : "–"}</td>
                <td className="py-2 px-3 text-right text-dim">{netAvg != null ? fmtINR(parseFloat(netAvg)) : "–"}</td>
                <td className="py-2 px-3 text-right text-foreground">{ltp != null ? fmtINR(parseFloat(ltp)) : "–"}</td>
                <td className={`py-2 pl-3 pr-0 text-right font-semibold ${pnlClass(mtm)}`}>{mtm != null ? signedINR(mtm) : "–"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ── Orders blotter (verbatim; `allStatuses` shows history too, for the account tab) ──
const ORDER_SIDE_LABEL = { B: "BUY", S: "SELL", b: "BUY", s: "SELL" };
const ORDER_SIDE_CLASS = { B: "text-success", S: "text-danger", b: "text-success", s: "text-danger" };

export function OrdersBlotter({ orders, allStatuses = false }) {
  if (!orders) {
    return (
      <div className="text-xs text-dimmer font-mono py-4 text-center">
        Loading orders&hellip;
      </div>
    );
  }
  const all = asOrderRows(orders) ?? [];
  const rows = allStatuses ? all : all.filter(isWorkingOrder);
  if (rows.length === 0) {
    return (
      <div className="text-xs text-dimmer font-mono py-6 text-center">
        {allStatuses ? "No orders today" : "No working orders"}
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
              <tr key={orderId} className="border-b border-line/50 hover:bg-bg-2/40 transition-colors">
                <td className="py-2 pr-3 pl-0 text-foreground font-semibold">{sym}</td>
                <td className={`py-2 px-3 text-center font-bold ${ORDER_SIDE_CLASS[side] ?? "text-dim"}`}>{ORDER_SIDE_LABEL[side] ?? side}</td>
                <td className="py-2 px-3 text-right text-foreground">{qty}</td>
                <td className="py-2 px-3 text-right text-dim">{prc != null ? fmtINR(parseFloat(prc)) : "MKT"}</td>
                <td className="py-2 px-3 text-dim">{prctyp}</td>
                <td className="py-2 px-3">
                  <span className="px-1.5 py-0.5 rounded bg-bg-3 border border-line text-dimmer text-[10px] uppercase tracking-wide">{status}</span>
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

// ── Reconciliation chip (verbatim) ───────────────────────────────────────────
export function ReconcileChip({ reconcile }) {
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
  const fmtMismatch = (m) => {
    const type = m?.type ?? "mismatch";
    const tsym = m?.detail?.tsym ?? m?.detail?.norenordno ?? "";
    const qty = m?.detail?.netqty ?? m?.detail?.qty;
    return `${type}${tsym ? ` ${tsym}` : ""}${qty != null ? ` (${qty})` : ""}`;
  };
  return (
    <span
      className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border border-amber-500/40 bg-amber-500/10 text-warning text-xs font-mono"
      title={mismatches.length > 0 ? mismatches.map(fmtMismatch).join("\n") : undefined}
    >
      <AlertTriangle className="w-3.5 h-3.5" />
      {mismatches.length > 0
        ? `${mismatches.length} mismatch${mismatches.length !== 1 ? "es" : ""}: ${mismatches.slice(0, 3).map(fmtMismatch).join(", ")}${mismatches.length > 3 ? "…" : ""}`
        : "Reconciliation mismatch"}
    </span>
  );
}
