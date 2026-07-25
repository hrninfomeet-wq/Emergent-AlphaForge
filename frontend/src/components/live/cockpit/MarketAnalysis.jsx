import { BarChart3 } from "lucide-react";
import { fmtINR, fmtINRSigned } from "@/lib/fmt";
import { SectionCard } from "@/components/live/liveHelpers";

/**
 * Market Analysis — PCR, max-pain, IV rank, ATM straddle, net greeks + ATM option
 * chain.
 *
 * Purely presentational: fed by GET /market/analysis (`options.*`), with net
 * Δ/Θ merged in from the separately-polled /live-broker/greeks. Renders "—" for
 * every missing value — the backend degrades honestly (nulls + `warnings`)
 * rather than guessing, and this component must never invent a number either.
 */

// Null-safe numeric coercion — never fabricates a number.
const numOrNull = (v) => {
  const n = Number(v);
  return v === null || v === undefined || !Number.isFinite(n) ? null : n;
};

// Null-safe formatter — "—" for missing/NaN, otherwise fixed-point text.
const fmt = (v, digits = 2) => {
  const n = numOrNull(v);
  return n === null ? "—" : n.toFixed(digits);
};

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

// Compact open-interest formatter: 260000 -> "2.6L", 1200000 -> "12.0L".
function fmtOI(v) {
  const n = numOrNull(v);
  if (n === null) return "—";
  const abs = Math.abs(n);
  if (abs >= 100000) return `${(n / 100000).toFixed(1)}L`;
  if (abs >= 1000) return `${(n / 1000).toFixed(1)}K`;
  return String(Math.round(n));
}

function humanizeIvSource(src) {
  if (src === "atm_iv") return "ATM IV";
  if (src === "vix_proxy") return "VIX proxy";
  if (!src) return "unavailable";
  return src;
}

// Humanised forms of the warning codes GET /market/analysis actually emits.
// Anything unmapped renders verbatim rather than being hidden — an unexplained
// blank metric is worse than an unfamiliar code.
const WARNING_LABELS = {
  iv_history_thin: "IV history thin — using VIX proxy",
  iv_unavailable: "IV rank unavailable",
  option_oi_unavailable: "No open interest in the feed — PCR & max-pain unavailable (stream not in full mode)",
  option_chain_unavailable: "Option chain unavailable",
  intraday_candles_thin: "Intraday history thin",
  intraday_unavailable: "Intraday data unavailable",
  daily_history_thin: "Daily history thin — longer timeframes may be blank",
  daily_unavailable: "Daily history unavailable",
  sr_unavailable: "Support/resistance unavailable",
  analysis_unavailable: "Market analysis unavailable",
};

function Tile({ label, value, valueClass = "text-foreground", meterPct, meterClass, sub, subClass = "text-dimmer" }) {
  return (
    <div className="bg-bg-2 border border-line rounded-lg px-2.5 py-2">
      <div className="text-[9.5px] uppercase tracking-wider text-dimmer">{label}</div>
      <div className={`font-mono tabular-nums text-base font-bold mt-0.5 ${valueClass}`}>{value}</div>
      {meterPct !== null && meterPct !== undefined && (
        <div className="h-1 rounded bg-bg-3 mt-1.5 overflow-hidden">
          <div className={`h-full ${meterClass}`} style={{ width: `${clamp(meterPct, 0, 100)}%` }} />
        </div>
      )}
      {sub && <div className={`text-[10px] mt-1 ${subClass}`}>{sub}</div>}
    </div>
  );
}

export default function MarketAnalysis({ analysis }) {
  if (!analysis) {
    return (
      <div className="rounded-lg border border-line bg-bg-1 px-4 py-5 flex items-center gap-3 text-dim">
        <BarChart3 className="w-4 h-4 text-dimmer" />
        <div className="text-xs">
          <div className="font-semibold text-foreground">Market Analysis</div>
          <div className="text-dimmer">PCR, max-pain, IV rank, ATM straddle &amp; the option chain come online with the analysis engine.</div>
        </div>
      </div>
    );
  }

  const options = analysis.options ?? {};
  const spot = numOrNull(analysis.spot ?? analysis.levels?.spot);
  const warnings = Array.isArray(analysis.warnings) ? analysis.warnings : [];
  const chain = Array.isArray(options.chain) ? options.chain : [];

  // PCR (OI)
  const pcr = numOrNull(options.pcr_oi);
  const pcrMeterPct = pcr !== null ? (pcr / 2) * 100 : null;
  const pcrMeterClass = pcr !== null && pcr > 1 ? "bg-success" : "bg-danger";

  // Max pain — signed distance (points) from spot.
  const maxPain = numOrNull(options.max_pain);
  let maxPainSub = "—";
  if (maxPain !== null && spot !== null) {
    const diff = Math.round(maxPain - spot);
    maxPainSub = diff === 0 ? "at spot" : `${Math.abs(diff)} pts ${diff > 0 ? "above" : "below"} spot`;
  }

  // IV rank (30d)
  const ivRank = numOrNull(options.iv_rank_30d);
  const ivRankPct = ivRank !== null ? Math.round(ivRank * 100) : null;
  const ivSourceLabel = humanizeIvSource(options.iv_rank_source);
  const ivSourceClass = options.iv_rank_source === "vix_proxy" ? "text-warning" : "text-dimmer";

  // ATM straddle
  const straddle = numOrNull(options.atm_straddle);
  const impliedMove = numOrNull(options.implied_move_pct);

  // Net delta / theta
  const netDelta = numOrNull(options.net_delta_rupee);
  const netTheta = numOrNull(options.net_theta_rupee);

  // Nearest-to-spot strike, for chain-row highlighting.
  let nearestStrike = null;
  if (chain.length > 0 && spot !== null) {
    let bestDiff = Infinity;
    for (const row of chain) {
      const k = numOrNull(row?.strike);
      if (k === null) continue;
      const d = Math.abs(k - spot);
      if (d < bestDiff) { bestDiff = d; nearestStrike = k; }
    }
  }

  return (
    <SectionCard title={`Market analysis · ${analysis.instrument ?? "—"}`}>
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
        <Tile
          label="PCR (OI)"
          value={fmt(pcr, 2)}
          meterPct={pcrMeterPct}
          meterClass={pcrMeterClass}
        />
        <Tile
          label="Max pain"
          value={fmt(maxPain, 0)}
          sub={maxPainSub}
        />
        <Tile
          label="IV rank 30d"
          value={ivRankPct !== null ? `${ivRankPct}%` : "—"}
          meterPct={ivRankPct}
          meterClass="bg-warning"
          sub={ivSourceLabel}
          subClass={ivSourceClass}
        />
        <Tile
          label="ATM straddle"
          value={straddle !== null ? fmtINR(straddle) : "—"}
          sub={impliedMove !== null ? `±${fmt(impliedMove, 2)}% implied` : "—"}
        />
        <Tile
          label="Net Δ"
          value={netDelta !== null ? fmtINRSigned(netDelta) : "—"}
          valueClass={netDelta === null ? "text-dimmer" : netDelta >= 0 ? "text-success" : "text-danger"}
          sub="₹/point"
        />
        <Tile
          label="Net Θ"
          value={netTheta !== null ? fmtINRSigned(netTheta) : "—"}
          valueClass={netTheta === null ? "text-dimmer" : netTheta >= 0 ? "text-success" : "text-danger"}
          sub="₹/day"
        />
      </div>

      <div className="text-[9.5px] uppercase tracking-wider text-dimmer mt-3 mb-1.5">Option chain · ATM ± 2</div>
      {chain.length === 0 ? (
        <div className="text-[10.5px] text-dimmer">Option chain unavailable</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs font-mono tabular-nums">
            <thead>
              <tr className="border-b border-line text-dimmer uppercase tracking-wider text-[10px]">
                <th className="text-right py-2 pr-3 pl-0">CE OI</th>
                <th className="text-right py-2 px-3">CE LTP</th>
                <th className="text-center py-2 px-3">Strike</th>
                <th className="text-right py-2 px-3">PE LTP</th>
                <th className="text-right py-2 pl-3 pr-0">PE OI</th>
              </tr>
            </thead>
            <tbody>
              {chain.map((row, i) => {
                const strike = numOrNull(row?.strike);
                const isNearest = nearestStrike !== null && strike === nearestStrike;
                return (
                  <tr
                    key={strike ?? i}
                    className={`border-b border-line/50 ${isNearest ? "bg-sky-500/10" : ""}`}
                  >
                    <td className="py-1.5 pr-3 pl-0 text-right text-dimmer">{fmtOI(row?.ce_oi)}</td>
                    <td className="py-1.5 px-3 text-right text-success">{fmt(row?.ce_ltp, 2)}</td>
                    <td className="py-1.5 px-3 text-center font-bold text-foreground">{fmt(strike, 0)}</td>
                    <td className="py-1.5 px-3 text-right text-danger">{fmt(row?.pe_ltp, 2)}</td>
                    <td className="py-1.5 pl-3 pr-0 text-right text-dimmer">{fmtOI(row?.pe_oi)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {warnings.length > 0 && (
        <div className="text-[10.5px] text-warning mt-3">
          {warnings.map((w) => WARNING_LABELS[w] ?? w).join(" · ")}
        </div>
      )}
    </SectionCard>
  );
}
