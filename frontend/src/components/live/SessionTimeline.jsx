import { useCallback, useEffect, useState } from "react";
import { ChevronDown, ChevronRight, Loader2, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import { getApiErrorMessage } from "@/lib/apiError";
import { formatTimeline } from "@/lib/liveTimelineView";

const TONE_CLASS = {
  danger: "text-rose-300", warn: "text-warning", dim: "text-dimmer", default: "text-dim",
};

/**
 * "Today's timeline" for ONE live deployment — collapsed by default, fetched only
 * when opened (never polled), refreshed on demand. Read-only. The gaps footnote is
 * part of the answer: it lists what is not recorded anywhere, so a quiet stretch of
 * the day is not mistaken for "nothing happened".
 */
export default function SessionTimeline({ depId }) {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState({ loading: false, error: null, data: null });

  const load = useCallback(async () => {
    setState((s) => ({ ...s, loading: true, error: null }));
    try {
      const data = await api.deploymentTimeline(depId);
      setState({ loading: false, error: null, data });
    } catch (e) {
      setState((s) => ({ loading: false, error: getApiErrorMessage(e, e.message), data: s.data }));
    }
  }, [depId]);

  useEffect(() => {
    if (open) load();
  }, [open, load]);

  const view = state.data ? formatTimeline(state.data) : null;

  return (
    <div data-testid="live-deploy-timeline">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="inline-flex items-center gap-1 text-dimmer hover:text-foreground"
        data-testid="live-deploy-timeline-toggle"
      >
        {open ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
        Today&apos;s timeline
      </button>
      {open && (
        <div className="mt-1 pl-4 space-y-1">
          {state.loading && <Loader2 className="w-3 h-3 animate-spin text-dimmer" />}
          {state.error && (
            <div className="text-rose-300" data-testid="live-deploy-timeline-error">
              Timeline unavailable: {state.error}
            </div>
          )}
          {view && (
            <>
              {view.empty && (
                <div className="text-dimmer" data-testid="live-deploy-timeline-empty">
                  No recorded events{view.date ? ` for ${view.date}` : ""} — see the notes below for what is never recorded.
                </div>
              )}
              {view.rows.length > 0 && (
                <ul className="space-y-0.5" data-testid="live-deploy-timeline-list">
                  {view.rows.map((r) => (
                    <li key={r.key} className={`flex gap-2 ${TONE_CLASS[r.tone] || "text-dim"}`}>
                      <span className="tabular-nums text-dimmer shrink-0">{r.time}</span>
                      <span className="shrink-0 font-medium">{r.label}</span>
                      {r.detail && <span className="text-dimmer break-words min-w-0">{r.detail}</span>}
                    </li>
                  ))}
                </ul>
              )}
              {view.gaps.length > 0 && (
                <ul className="text-[10px] text-dimmer list-disc pl-4" data-testid="live-deploy-timeline-gaps">
                  {view.gaps.map((g) => <li key={g}>{g}</li>)}
                </ul>
              )}
            </>
          )}
          <button
            type="button"
            onClick={load}
            disabled={state.loading}
            className="inline-flex items-center gap-1 text-dimmer hover:text-foreground disabled:opacity-50"
            data-testid="live-deploy-timeline-refresh"
          >
            <RefreshCw className="w-3 h-3" />
            refresh
          </button>
        </div>
      )}
    </div>
  );
}
