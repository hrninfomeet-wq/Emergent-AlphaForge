import { TrendingUp, TrendingDown, Shuffle, Activity, HelpCircle } from "lucide-react";

const CFG = {
  TREND:           { cls: "bg-emerald-950 text-emerald-200 border-emerald-900", icon: TrendingUp, label: "TREND" },
  TREND_EXPANDING: { cls: "bg-emerald-900/80 text-emerald-100 border-emerald-700", icon: TrendingUp, label: "TREND+EXP" },
  CHOP:            { cls: "bg-slate-900 text-slate-200 border-slate-700", icon: Shuffle, label: "CHOP" },
  VOLATILE_CHOP:   { cls: "bg-amber-950 text-warning border-amber-900", icon: Activity, label: "VOLATILE" },
  MIXED:           { cls: "bg-blue-950 text-blue-200 border-blue-900", icon: Shuffle, label: "MIXED" },
  UNKNOWN:         { cls: "bg-slate-900 text-slate-300 border-slate-700", icon: HelpCircle, label: "UNKNOWN" },
};

export function RegimeBadge({ regime, count, total }) {
  const cfg = CFG[regime] || CFG.UNKNOWN;
  const pct = total ? Math.round((count / total) * 100) : null;
  const Icon = cfg.icon;
  return (
    <div
      className={`inline-flex items-center gap-1.5 px-2 py-1 rounded-md text-xs font-medium border ${cfg.cls}`}
      data-testid="regime-indicator-badge"
    >
      <Icon className="w-3.5 h-3.5" />
      <span>{cfg.label}</span>
      {pct !== null && (
        <span className="font-mono text-[10px] opacity-80">{pct}%</span>
      )}
      {count !== undefined && pct === null && (
        <span className="font-mono text-[10px] opacity-80">{count}</span>
      )}
    </div>
  );
}
