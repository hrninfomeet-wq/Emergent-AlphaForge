import { Badge } from "@/components/ui/badge";
import { CheckCircle2, AlertCircle, XCircle } from "lucide-react";

export function SignificanceBadge({ significance }) {
  if (!significance) return null;
  const badge = significance.badge;
  const ci = significance.ci95_win_rate || [0, 0];
  const map = {
    SIGNIFICANT: { cls: "bg-emerald-950 text-emerald-200 border-emerald-900", icon: CheckCircle2, label: "SIGNIFICANT" },
    TENTATIVE:   { cls: "bg-amber-950 text-warning border-amber-900", icon: AlertCircle, label: "BORDERLINE" },
    INSUFFICIENT:{ cls: "bg-rose-950 text-rose-200 border-rose-900", icon: XCircle, label: "WEAK" },
  };
  const cfg = map[badge] || map.INSUFFICIENT;
  const Icon = cfg.icon;
  return (
    <div
      className={`inline-flex items-center gap-1.5 px-2 py-1 rounded-md text-xs font-medium border ${cfg.cls}`}
      title={significance.note}
      data-testid="backtest-significance-badge"
    >
      <Icon className="w-3.5 h-3.5" />
      <span>{cfg.label}</span>
      <span className="font-mono text-[10px] opacity-80">CI [{ci[0]}–{ci[1]}%]</span>
    </div>
  );
}
