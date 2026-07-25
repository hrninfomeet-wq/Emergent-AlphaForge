import { useState } from "react";
import { fmtINR } from "@/lib/fmt";
import { OrdersBlotter, deriveCash } from "@/components/live/liveHelpers";
import LiveBlotter from "@/components/live/LiveBlotter";
import LiveTradeStats from "@/components/live/LiveTradeStats";

/**
 * Professional broker-style account panel with tabs — Funds & Margin, Holdings,
 * Order book, Trade book. Reference data you drill into (the good use of tabs,
 * distinct from the always-on cockpit core). Reads from the existing broker
 * slices; Holdings is wired in Phase 2 via /live-broker/holdings.
 */

function num(v) {
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}
function firstNum(...vals) {
  for (const v of vals) { const n = num(v); if (n != null) return n; }
  return null;
}
function money(n) { return n == null ? "—" : fmtINR(n); }

function FundCell({ label, value, tone }) {
  const cls = tone === "up" ? "text-success" : tone === "down" ? "text-danger" : "text-foreground";
  return (
    <div className="bg-bg-2 border border-line/60 rounded-lg px-3 py-2.5">
      <div className="text-[9.5px] uppercase tracking-wider text-dimmer">{label}</div>
      <div className={`font-mono tabular-nums text-lg font-bold mt-1 ${cls}`}>{value}</div>
    </div>
  );
}

/**
 * DP/demat holdings from GET /live-broker/holdings. Noren nests the symbol in an
 * `exch_tsym` array and reports quantity/price across several fields, so read
 * them defensively — a missing field renders "—" rather than a guessed 0.
 */
function HoldingsTable({ holdings }) {
  if (holdings == null) {
    return <div className="text-xs text-dimmer font-mono py-6 text-center">Loading holdings&hellip;</div>;
  }
  const rows = Array.isArray(holdings) ? holdings : (holdings.holdings ?? holdings.data ?? []);
  if (!rows.length) {
    return <div className="text-xs text-dimmer font-mono py-6 text-center">No holdings</div>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs font-mono tabular-nums">
        <thead>
          <tr className="border-b border-line text-dimmer uppercase tracking-wider text-[10px]">
            <th className="text-left py-2 pr-3 pl-0">Symbol</th>
            <th className="text-left py-2 px-3">Exch</th>
            <th className="text-right py-2 px-3">Qty</th>
            <th className="text-right py-2 px-3">Avg price</th>
            <th className="text-right py-2 pl-3 pr-0">Value</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((h, i) => {
            const sym = h?.exch_tsym?.[0] ?? {};
            const qty = firstNum(h?.holdqty, h?.dpqty, h?.npoadqty, h?.benqty);
            const avg = firstNum(h?.upldprc);
            const value = qty != null && avg != null ? qty * avg : null;
            return (
              <tr key={sym.tsym ?? i} className="border-b border-line/50 hover:bg-bg-2/40 transition-colors">
                <td className="py-2 pr-3 pl-0 text-foreground font-semibold">{sym.tsym ?? "—"}</td>
                <td className="py-2 px-3 text-dim">{sym.exch ?? "—"}</td>
                <td className="py-2 px-3 text-right text-foreground">{qty ?? "—"}</td>
                <td className="py-2 px-3 text-right text-dim">{avg != null ? fmtINR(avg) : "—"}</td>
                <td className="py-2 pl-3 pr-0 text-right text-foreground">{value != null ? fmtINR(value) : "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

const TABS = [
  { k: "fund", label: "Funds & Margin" },
  { k: "hold", label: "Holdings" },
  { k: "ord", label: "Order book" },
  { k: "trd", label: "Trade book" },
];

export default function AccountTabs({ limits, orders, blotter, gtt, holdings }) {
  const [tab, setTab] = useState("fund");

  const availMargin = deriveCash(limits);
  const usedMargin = firstNum(limits?.marginused, limits?.premium, limits?.span);
  const cash = firstNum(limits?.cash);
  const payin = firstNum(limits?.payin);
  const collateral = firstNum(limits?.brkcollamt, limits?.collateral);
  const spanExpo = (() => {
    const s = firstNum(limits?.span); const e = firstNum(limits?.expo, limits?.exposure);
    if (s == null && e == null) return null;
    return (s || 0) + (e || 0);
  })();
  const total = availMargin != null && usedMargin != null ? availMargin + usedMargin : null;
  const utilPct = total ? Math.round((usedMargin / total) * 100) : null;

  return (
    <div className="rounded-lg border border-line bg-bg-1 overflow-hidden">
      <div className="flex gap-0.5 px-1.5 border-b border-line bg-bg-2/40">
        {TABS.map((t) => (
          <button
            key={t.k}
            type="button"
            role="tab"
            aria-selected={tab === t.k}
            onClick={() => setTab(t.k)}
            className={`px-4 py-2.5 text-xs font-semibold rounded-t-md relative top-px border border-transparent border-b-0 ${
              tab === t.k ? "text-foreground bg-bg-1 border-line" : "text-dim hover:text-foreground"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="p-4">
        {tab === "fund" && (
          limits == null ? (
            <div className="text-xs text-dimmer font-mono py-6 text-center">Loading funds&hellip;</div>
          ) : (
            <>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5">
                <FundCell label="Available margin" value={money(availMargin)} tone="up" />
                <FundCell label="Used margin" value={money(usedMargin)} />
                <FundCell label="Cash" value={money(cash)} />
                <FundCell label="Collateral" value={money(collateral)} />
                <FundCell label="Payin today" value={money(payin)} />
                <FundCell label="Span + exposure" value={money(spanExpo)} />
              </div>
              {utilPct != null && (
                <>
                  <div className="text-[9.5px] uppercase tracking-wider text-dimmer mt-3.5">Margin utilisation · {utilPct}% used</div>
                  <div className="flex h-2 rounded overflow-hidden mt-1.5 border border-line/60">
                    <span className="bg-warning" style={{ width: `${Math.min(100, utilPct)}%` }} />
                    <span className="bg-success" style={{ width: `${Math.max(0, 100 - utilPct)}%` }} />
                  </div>
                </>
              )}
              <div className="text-dimmer text-[10.5px] mt-2">Live from the broker limits read; refreshes while connected.</div>
            </>
          )
        )}

        {tab === "hold" && <HoldingsTable holdings={holdings} />}

        {tab === "ord" && <OrdersBlotter orders={orders} allStatuses />}

        {tab === "trd" && (
          <div className="space-y-4">
            <LiveBlotter rows={blotter?.rows} gtt={gtt?.gtt} />
            <LiveTradeStats />
          </div>
        )}
      </div>
    </div>
  );
}
