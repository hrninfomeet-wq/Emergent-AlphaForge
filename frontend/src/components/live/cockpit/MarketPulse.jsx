import { Activity } from "lucide-react";

/**
 * Market Pulse — structure + multi-timeframe trend + S/R range bar.
 *
 * Purely presentational: fed by GET /market/analysis (`structure` regime bucket
 * + confidence + why, `trend` per timeframe, and `levels.position_in_range` for
 * the S/R bar). Deliberately COMPACT — this sits at the top of the always-on
 * cockpit core, so it trades height for density. Renders "—" for anything the
 * backend could not compute; it never fabricates a level or a direction.
 */

// Null-safe formatter — never fabricates a number, always "—" for missing/NaN.
const fmt = (v, digits = 2) => {
  const n = Number(v);
  return v === null || v === undefined || !Number.isFinite(n) ? "—" : n.toFixed(digits);
};

const REGIME_LABELS = ["Bearish", "Down", "Choppy", "Up", "Strong"];

// Structure-level direction glyph, driven by the regime bucket (0-4).
function structureGlyph(bucket) {
  if (bucket === null) return { ch: "—", cls: "text-dimmer" };
  if (bucket >= 3) return { ch: "▲", cls: "text-success" };
  if (bucket <= 1) return { ch: "▼", cls: "text-danger" };
  return { ch: "▬", cls: "text-warning" };
}

// Per-timeframe trend glyph, driven by "up" | "down" | "flat".
function TrendGlyph({ trend }) {
  if (trend === "up") return <span className="text-success">▲</span>;
  if (trend === "down") return <span className="text-danger">▼</span>;
  if (trend === "flat") return <span className="text-warning">▬</span>;
  return <span className="text-dimmer">—</span>;
}

const TIMEFRAMES = [
  ["Intraday", "intraday"],
  ["Daily", "daily"],
  ["Weekly", "weekly"],
  ["Monthly", "monthly"],
];

export default function MarketPulse({ analysis }) {
  if (!analysis) {
    return (
      <div className="rounded-lg border border-line bg-bg-1 px-4 py-5 flex items-center gap-3 text-dim">
        <Activity className="w-4 h-4 text-dimmer" />
        <div className="text-xs">
          <div className="font-semibold text-foreground">Market Pulse</div>
          <div className="text-dimmer">Regime, multi-timeframe trend &amp; S/R come online with the analysis engine.</div>
        </div>
      </div>
    );
  }

  const structure = analysis.structure ?? {};
  const trend = analysis.trend ?? {};
  const levels = analysis.levels ?? {};

  const bucketNum = Number(structure.regime_bucket);
  const bucket = Number.isFinite(bucketNum) ? bucketNum : null;
  const glyph = structureGlyph(bucket);

  const confNum = Number(structure.confidence);
  const confPct = Number.isFinite(confNum) ? Math.round(confNum * 100) : null;

  const posNum = Number(levels.position_in_range);
  const posPct = Number.isFinite(posNum) ? Math.min(100, Math.max(0, posNum * 100)) : null;

  return (
    <div className="rounded-lg border border-line bg-bg-1 px-3 py-2.5">
      {/* Header */}
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-semibold text-foreground">
          Market pulse · {analysis.instrument ?? "—"}
        </span>
        <span className="text-[9.5px] uppercase tracking-wider text-dimmer">deterministic</span>
      </div>

      {/* Structure */}
      <div className="flex items-baseline gap-1.5">
        <span className={`text-base font-bold leading-none ${glyph.cls}`}>{glyph.ch}</span>
        <span className="text-base font-bold text-foreground leading-none">{structure.label ?? "—"}</span>
      </div>
      <div className="text-[10.5px] text-dimmer mt-0.5">{structure.kind ?? "—"}</div>

      {/* 5-segment regime meter */}
      <div className="flex gap-1 mt-2">
        {[0, 1, 2, 3, 4].map((i) => {
          const lit = bucket !== null && i === bucket;
          let cls = "bg-bg-3";
          if (lit) cls = i <= 1 ? "bg-danger" : i === 2 ? "bg-warning" : "bg-success";
          return <span key={i} className={`h-1.5 flex-1 rounded ${cls}`} />;
        })}
      </div>
      <div className="flex mt-1">
        {REGIME_LABELS.map((l) => (
          <span key={l} className="flex-1 text-center text-[8.5px] text-dimmer">{l}</span>
        ))}
      </div>

      {/* Confidence */}
      <div className="flex items-center justify-between mt-2.5">
        <span className="text-[9.5px] uppercase tracking-wider text-dimmer">Confidence</span>
        <span className="font-mono tabular-nums text-[10.5px] text-foreground">
          {confPct !== null ? `${confPct}%` : "—"}
        </span>
      </div>
      <div className="h-1 rounded bg-bg-3 mt-1 overflow-hidden">
        <div className="h-full bg-info" style={{ width: `${confPct ?? 0}%` }} />
      </div>

      {/* Trend by timeframe */}
      <div className="grid grid-cols-4 gap-1.5 mt-3">
        {TIMEFRAMES.map(([label, key]) => (
          <div key={key} className="bg-bg-2 border border-line rounded px-1 py-1.5 text-center">
            <div className="text-[8.5px] uppercase tracking-wider text-dimmer">{label}</div>
            <div className="text-base leading-none mt-1">
              <TrendGlyph trend={trend[key]} />
            </div>
          </div>
        ))}
      </div>

      {/* S/R range bar */}
      <div className="mt-3">
        <div className={`relative h-6 rounded border border-line overflow-hidden ${posPct === null ? "bg-bg-3/60" : "bg-bg-2"}`}>
          {posPct !== null ? (
            <span
              className="absolute top-0 bottom-0 w-0.5 bg-sky-400"
              style={{ left: `${posPct}%` }}
            />
          ) : (
            <span className="absolute inset-0 flex items-center justify-center text-[9px] text-dimmer">
              range unavailable
            </span>
          )}
        </div>
        <div className="flex items-center justify-between mt-1 text-[10.5px] font-mono tabular-nums">
          <span className="text-success">S {fmt(levels.supports?.[0], 0)}</span>
          <span className="text-dimmer">{fmt(levels.pivot, 0)}</span>
          <span className="text-danger">R {fmt(levels.resistances?.[0], 0)}</span>
        </div>
      </div>

      {/* Why */}
      <div className="text-[10.5px] text-dimmer mt-2.5">{structure.why ?? "—"}</div>
    </div>
  );
}
