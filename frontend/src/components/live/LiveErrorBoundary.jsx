import { Component } from "react";
import KillSwitchPanel from "@/components/live/KillSwitchPanel";

/**
 * LiveErrorBoundary — a render crash on the Live page must NEVER take away the
 * kill switch. It sits INSIDE <LiveDataProvider> (so the fallback's
 * <KillSwitchPanel/> still has live broker data via context) and wraps
 * <LiveDashboard/>. On any child render error it shows the error + a reload
 * button + the always-available kill switch, instead of white-screening the
 * whole route (which previously happened when a FastAPI 422 detail array was
 * rendered as a raw React child).
 */
export default class LiveErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    // eslint-disable-next-line no-console
    console.error("Live page render error:", error, info);
  }

  render() {
    if (this.state.error) {
      const msg = String(this.state.error?.message || this.state.error || "Unknown error");
      return (
        <div className="space-y-4" data-testid="live-error-boundary">
          <div className="rounded-lg border-2 border-danger bg-danger/10 text-danger px-4 py-3 font-mono text-sm">
            <div className="font-bold">The Live page hit a render error.</div>
            <div className="text-xs mt-1 break-words">{msg}</div>
            <div className="text-xs mt-2 text-dim">
              Your positions are unaffected. The kill switch below still works — reload
              to recover the full page.
            </div>
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="mt-2 h-8 px-3 rounded border border-danger/60 text-danger hover:bg-danger/20 text-xs font-bold"
              data-testid="live-error-reload"
            >
              Reload Live page
            </button>
          </div>
          <KillSwitchPanel />
        </div>
      );
    }
    return this.props.children;
  }
}
