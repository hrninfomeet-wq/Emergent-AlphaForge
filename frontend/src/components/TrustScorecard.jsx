import { AlertTriangle, ShieldCheck } from "lucide-react";

/**
 * Advisory trust verdict — never blocks. Green when no warnings, amber otherwise.
 * `quality` is the object from deployment_quality.evaluate_source_quality
 * ({ acknowledgment_required, warnings: [{id,label,detail}], ... }).
 */
export function TrustScorecard({ quality }) {
  if (!quality) return null;
  const warnings = quality.warnings || [];
  const ok = warnings.length === 0;
  return (
    <div
      className={`rounded-lg border p-3 ${ok ? "border-success/40 bg-success/5" : "border-amber-400/40 bg-amber-400/5"}`}
      data-testid="trust-scorecard"
    >
      <div className="flex items-center gap-2 mb-2">
        {ok ? <ShieldCheck className="w-4 h-4 text-success" /> : <AlertTriangle className="w-4 h-4 text-warning" />}
        <span className="text-[11px] font-semibold uppercase tracking-wider text-dim">
          Trust {ok ? "· no warnings" : `· ${warnings.length} warning${warnings.length > 1 ? "s" : ""}`}
        </span>
      </div>
      {ok ? (
        <div className="text-[11px] text-dimmer">No trust warnings on this result.</div>
      ) : (
        <ul className="space-y-1.5">
          {warnings.map((w) => (
            <li key={w.id} className="text-[11px]">
              <span className="text-warning font-medium">{w.label}</span>
              <span className="text-dimmer"> — {w.detail}</span>
            </li>
          ))}
        </ul>
      )}
      <div className="text-[10px] text-dimmer mt-2 leading-snug">
        Advisory only — nothing is blocked. Option-₹ headline figures are full-window, not walk-forward validated.
      </div>
    </div>
  );
}
