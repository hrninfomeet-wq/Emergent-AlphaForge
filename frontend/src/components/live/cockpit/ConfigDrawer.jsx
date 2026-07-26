import { useEffect, useRef } from "react";
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
    // shrink-0 is load-bearing: as a flex child these sections default to
    // flex-shrink:1, so with overflow-hidden they SQUASH and silently clip their
    // own content (measured: 231px shown of 653px) instead of letting the body
    // scroll. The user could not reach the deployment controls at all.
    <div className="border border-line rounded-lg overflow-hidden shrink-0">
      <div className="flex items-center justify-between px-3.5 py-2.5 border-b border-line bg-bg-2/50">
        <span className="text-xs font-semibold text-foreground">{title}</span>
        {badge}
      </div>
      <div className="p-3">{children}</div>
    </div>
  );
}

export default function ConfigDrawer({ open, onClose, onArmedSummaryChange }) {
  const panelRef = useRef(null);
  const restoreFocusRef = useRef(null);

  useEffect(() => {
    function onEsc(e) { if (e.key === "Escape") onClose?.(); }
    if (open) document.addEventListener("keydown", onEsc);
    return () => document.removeEventListener("keydown", onEsc);
  }, [open, onClose]);

  // Move focus into the drawer on open and hand it back to whatever opened it on
  // close. Without this, keyboard focus stays on the page behind the drawer.
  useEffect(() => {
    if (open) {
      restoreFocusRef.current = document.activeElement;
      // Deferred: focusing in the same commit races the inert effect below (a
      // still-inert element silently refuses focus) and the slide transition.
      const raf = window.requestAnimationFrame(() => panelRef.current?.focus());
      return () => window.cancelAnimationFrame(raf);
    }
    if (restoreFocusRef.current instanceof HTMLElement) {
      restoreFocusRef.current.focus();
      restoreFocusRef.current = null;
    }
    return undefined;
  }, [open]);

  // `inert` must be set on the DOM node directly — passing it as a JSX attribute
  // is silently dropped by this React version (verified in the browser: the
  // closed drawer still had 16 tabbable controls). Setting the property removes
  // the whole subtree from the tab order and the accessibility tree.
  useEffect(() => {
    const el = panelRef.current;
    if (!el) return;
    if ("inert" in el) el.inert = !open;
    else if (!open) el.setAttribute("inert", "");
    else el.removeAttribute("inert");
  }, [open]);

  return (
    <>
      <div
        onClick={onClose}
        className={`fixed inset-0 bg-black/50 z-40 transition-opacity motion-reduce:transition-none ${open ? "opacity-100" : "opacity-0 pointer-events-none"}`}
        aria-hidden="true"
      />
      <aside
        ref={panelRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label="Configure and deploy"
        // A closed drawer is only translated off-screen, so without aria-hidden +
        // pointer-events-none every control inside stays in the tab order and the
        // accessibility tree — keyboard users could tab into an invisible panel
        // and operate deployment controls they cannot see.
        aria-hidden={!open}
        className={`fixed top-0 right-0 h-full w-[min(460px,94vw)] bg-bg-1 border-l border-line z-50 flex flex-col transition-transform motion-reduce:transition-none focus:outline-none ${open ? "translate-x-0" : "translate-x-full pointer-events-none"}`}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-line bg-bg-2/50">
          <span className="text-sm font-semibold text-foreground">⚙ Configure &amp; deploy</span>
          <button type="button" onClick={onClose} className="w-7 h-7 rounded-md border border-line bg-bg-3 text-dim hover:text-foreground flex items-center justify-center" aria-label="Close">
            <X className="w-4 h-4" />
          </button>
        </div>
        {/* flex-1 + inline minHeight:0 make THIS the scroll container. Tailwind's
            min-h-0 is unreliable on flex children in this codebase (documented
            gotcha), so the inline style is deliberate — without it the body
            cannot shrink below its content and no scrollbar ever appears. */}
        <div className="p-4 overflow-y-auto flex-1 flex flex-col gap-4" style={{ minHeight: 0 }}>
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
