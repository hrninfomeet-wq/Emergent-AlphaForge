import { useEffect } from "react";
import { X } from "lucide-react";
import LiveDeploymentStrip from "@/components/live/LiveDeploymentStrip";
import GttBook from "@/components/live/GttBook";
import OverallSettingsPanel from "@/components/live/OverallSettingsPanel";

/**
 * Right slide-over drawer holding the set-and-forget config the trader does NOT
 * watch tick-by-tick: deployment control (enable/disable/stop with the typed
 * consent flow), the GTT/OCO PC-down backstop book, and the basket
 * SL/target/trailing overall controls. Every panel is the EXISTING component,
 * just relocated off the main cockpit.
 */
function DrawerSection({ title, badge, children }) {
  return (
    <div className="border border-line rounded-lg overflow-hidden">
      <div className="flex items-center justify-between px-3.5 py-2.5 border-b border-line bg-bg-2/50">
        <span className="text-xs font-semibold text-foreground">{title}</span>
        {badge}
      </div>
      <div className="p-3">{children}</div>
    </div>
  );
}

export default function ConfigDrawer({ open, onClose, onArmedSummaryChange }) {
  useEffect(() => {
    function onEsc(e) { if (e.key === "Escape") onClose?.(); }
    if (open) document.addEventListener("keydown", onEsc);
    return () => document.removeEventListener("keydown", onEsc);
  }, [open, onClose]);

  return (
    <>
      <div
        onClick={onClose}
        className={`fixed inset-0 bg-black/50 z-40 transition-opacity motion-reduce:transition-none ${open ? "opacity-100" : "opacity-0 pointer-events-none"}`}
        aria-hidden="true"
      />
      <aside
        aria-label="Configure and deploy"
        className={`fixed top-0 right-0 h-full w-[min(460px,94vw)] bg-bg-1 border-l border-line z-50 flex flex-col transition-transform motion-reduce:transition-none ${open ? "translate-x-0" : "translate-x-full"}`}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-line bg-bg-2/50">
          <span className="text-sm font-semibold text-foreground">⚙ Configure &amp; deploy</span>
          <button type="button" onClick={onClose} className="w-7 h-7 rounded-md border border-line bg-bg-3 text-dim hover:text-foreground flex items-center justify-center" aria-label="Close">
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="p-4 overflow-auto flex flex-col gap-4">
          <p className="text-[11px] text-dimmer">Set-and-forget controls — you don't watch these tick by tick, so they live off the main cockpit.</p>
          <DrawerSection title="Deployment control" badge={<span className="text-[9px] uppercase tracking-wider text-dimmer border border-line bg-bg-3 rounded-full px-2 py-0.5">enable / disable / stop</span>}>
            <LiveDeploymentStrip onArmedSummaryChange={onArmedSummaryChange} />
          </DrawerSection>
          <DrawerSection title="GTT / OCO backstop" badge={<span className="text-[9px] uppercase tracking-wider text-dimmer border border-line bg-bg-3 rounded-full px-2 py-0.5">PC-down net</span>}>
            <GttBook />
          </DrawerSection>
          <DrawerSection title="Overall controls" badge={<span className="text-[9px] uppercase tracking-wider text-dimmer border border-line bg-bg-3 rounded-full px-2 py-0.5">basket SL / trail</span>}>
            <OverallSettingsPanel scope="overall" />
          </DrawerSection>
        </div>
      </aside>
    </>
  );
}
