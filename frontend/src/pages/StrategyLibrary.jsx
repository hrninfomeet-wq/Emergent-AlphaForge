import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { Library, CheckCircle2, AlertCircle } from "lucide-react";

export default function StrategyLibrary() {
  const [strategies, setStrategies] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.listStrategies().then((d) => {
      setStrategies(d.items || []);
      setLoading(false);
    });
  }, []);

  if (loading) {
    return (
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        {[1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-40 bg-bg-1" />)}
      </div>
    );
  }

  return (
    <div className="space-y-3" data-testid="strategy-library-page">
      <div className="flex items-center gap-2">
        <div className="text-sm text-dim">{strategies.length} strategies discovered.</div>
        <div className="text-xs text-dimmer">Custom plugins: drop a .py file into <code className="font-mono">backend/app/strategies/plugins/</code> and restart backend.</div>
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        {strategies.map((s) => (
          <StrategyCard key={s.id} s={s} />
        ))}
      </div>
    </div>
  );
}

function StrategyCard({ s }) {
  const loaded = s.is_loaded !== false;
  return (
    <div className="rounded-lg border border-line bg-bg-1 p-3" data-testid={`strategy-card-${s.id}`}>
      <div className="flex items-start gap-3 mb-2">
        <div className="w-9 h-9 rounded-md bg-bg-3 border border-line-strong flex items-center justify-center shrink-0">
          <Library className="w-4 h-4 text-info" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <div className="text-sm font-semibold">{s.name}</div>
            <span className="font-mono text-[10px] text-dimmer">v{s.version}</span>
            {loaded ? (
              <Badge className="bg-emerald-950 text-emerald-200 border-emerald-900"><CheckCircle2 className="w-3 h-3 mr-1" />loaded</Badge>
            ) : (
              <Badge className="bg-rose-950 text-rose-200 border-rose-900"><AlertCircle className="w-3 h-3 mr-1" />failed</Badge>
            )}
            {s.is_builtin && <Badge className="bg-bg-3 text-dim border-line">builtin</Badge>}
          </div>
          <div className="text-[11px] font-mono text-dimmer mt-0.5">{s.id}</div>
        </div>
      </div>
      <div className="text-xs text-dim leading-snug mb-3">{s.description}</div>
      {!loaded && s.error && (
        <div className="text-[11px] text-rose-300 bg-rose-950/50 border border-rose-900 rounded-md p-2 mb-2 font-mono">
          {s.error}
        </div>
      )}
      <div className="grid grid-cols-3 gap-2 mb-3">
        <Pill label="Instruments" items={s.supported_instruments} />
        <Pill label="Modes" items={s.supported_modes} />
        <Pill label="Timeframes" items={s.supported_timeframes} />
      </div>
      {s.parameter_schema && Object.keys(s.parameter_schema).length > 0 && (
        <div>
          <div className="text-[10px] uppercase tracking-wider text-dimmer mb-1">Parameters ({Object.keys(s.parameter_schema).length})</div>
          <div className="flex flex-wrap gap-1">
            {Object.entries(s.parameter_schema).map(([k, def]) => (
              <span key={k} className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-bg-2 border border-line text-dim">
                {k}=<span className="text-foreground">{String(def.default)}</span>
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function Pill({ label, items }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-dimmer mb-1">{label}</div>
      <div className="flex flex-wrap gap-1">
        {(items || []).map((i) => (
          <span key={i} className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-bg-2 border border-line text-dim">{i}</span>
        ))}
      </div>
    </div>
  );
}
