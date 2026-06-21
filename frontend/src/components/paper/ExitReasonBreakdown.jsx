import { EXIT_BUCKETS } from "@/lib/paperAgg";

const LABEL = { target: "target", stop: "stop", eod: "end-of-day", manual: "manual", other: "other" };
const COLOR = {
  target: "var(--color-success)", stop: "var(--color-danger)",
  eod: "var(--text-3)", manual: "var(--color-info)", other: "var(--text-2)",
};

// breakdown: { pct: {bucket: int}, counts?: {...}, total?: int }
export default function ExitReasonBreakdown({ breakdown, variant = "full" }) {
  const pct = breakdown?.pct || breakdown || {};
  const total = breakdown?.total;
  if (variant === "compact") {
    const segs = EXIT_BUCKETS.filter((b) => (pct[b] || 0) > 0);
    if (segs.length === 0) return <span className="text-[10px] text-dimmer">—</span>;
    return (
      <div className="flex h-2.5 w-24 rounded-sm overflow-hidden" data-testid="exit-mix-compact"
        title={EXIT_BUCKETS.map((b) => `${LABEL[b]} ${pct[b] || 0}%`).join(" · ")}>
        {segs.map((b) => (
          <div key={b} style={{ width: `${pct[b]}%`, backgroundColor: COLOR[b] }} />
        ))}
      </div>
    );
  }
  return (
    <div className="rounded-lg border border-line bg-bg-1 p-3" data-testid="paper-exit-breakdown">
      <div className="text-xs font-semibold uppercase tracking-wider text-dim mb-2">
        Exit reasons {total != null ? <span className="text-dimmer font-normal">· {total} closed</span> : null}
      </div>
      {(total === 0) ? (
        <div className="text-[11px] text-dimmer font-mono">No closed trades for this filter.</div>
      ) : (
        <div className="flex flex-col gap-2">
          {EXIT_BUCKETS.map((b) => (
            <div key={b}>
              <div className="flex justify-between text-[11px]"><span className="text-dim">{LABEL[b]}</span><span className="font-mono text-dimmer">{pct[b] || 0}%</span></div>
              <div className="h-2 rounded-sm bg-bg-3"><div className="h-2 rounded-sm" style={{ width: `${pct[b] || 0}%`, backgroundColor: COLOR[b] }} /></div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
