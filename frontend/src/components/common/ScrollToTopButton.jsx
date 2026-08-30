import { useCallback, useEffect, useState } from "react";
import { ArrowUp } from "lucide-react";

/**
 * Floating "back to top" control for the long research pages.
 *
 * WHICH ELEMENT ACTUALLY SCROLLS IS NOT OBVIOUS HERE — it is resolved at runtime
 * rather than assumed. `Layout` wraps every page in
 * `<div class="flex-1 min-w-0 overflow-y-auto" data-testid="page-content">`, which
 * reads like the scroller, but the flex chain above it is not height-constrained,
 * so that div GROWS to fit its content and never overflows. Measured on /backtest
 * at 1280x800: page-content scrollHeight 5690 == clientHeight 5690 (0 scrollable),
 * while documentElement had 5239px of scroll. Binding to the container alone would
 * have produced a button that renders, clicks, and does nothing.
 *
 * So: use the container when it genuinely scrolls, otherwise the document. That
 * keeps working if the layout is ever given a real height constraint (the
 * `min-h-0` fix this codebase has needed elsewhere), without needing a change here.
 */
export default function ScrollToTopButton({
  scrollRef,
  threshold = 320,
  testid = "scroll-to-top",
}) {
  const [visible, setVisible] = useState(false);

  // The element that is really scrolling right now.
  const resolveScroller = useCallback(() => {
    const el = scrollRef?.current;
    if (el && el.scrollHeight - el.clientHeight > 1) return el;
    return document.scrollingElement || document.documentElement;
  }, [scrollRef]);

  useEffect(() => {
    const update = () => {
      const el = resolveScroller();
      if (!el) return;
      // Require somewhere to scroll BACK from, so a page that overflows by a few
      // pixels never shows the button.
      const scrollable = el.scrollHeight - el.clientHeight > threshold;
      setVisible(scrollable && el.scrollTop > threshold);
    };

    update();
    // Listen on both candidates: which one scrolls depends on the page and on
    // whether the layout is height-constrained.
    window.addEventListener("scroll", update, { passive: true });
    const container = scrollRef?.current;
    if (container) container.addEventListener("scroll", update, { passive: true });

    // Route changes swap the children without firing a scroll event, and panels
    // that expand/collapse change scrollHeight; without this the button could
    // linger after navigating to a short page, or stay hidden on a newly-tall one.
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(update) : null;
    if (ro) {
      if (container) ro.observe(container);
      if (document.body) ro.observe(document.body);
    }

    return () => {
      window.removeEventListener("scroll", update);
      if (container) container.removeEventListener("scroll", update);
      if (ro) ro.disconnect();
    };
  }, [scrollRef, threshold, resolveScroller]);

  const toTop = () => {
    const el = resolveScroller();
    if (!el) return;
    // Honour the OS "reduce motion" setting — a long smooth scroll is exactly the
    // kind of movement that setting exists to suppress.
    const reduce = typeof window !== "undefined"
      && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const behavior = reduce ? "auto" : "smooth";
    if (typeof el.scrollTo === "function") el.scrollTo({ top: 0, behavior });
    else el.scrollTop = 0;
  };

  return (
    <button
      type="button"
      onClick={toTop}
      aria-label="Scroll back to top"
      title="Back to top"
      data-testid={testid}
      aria-hidden={!visible}
      tabIndex={visible ? 0 : -1}
      className={[
        "fixed bottom-5 right-5 z-40 h-9 w-9 rounded-full",
        "border border-line bg-bg-2/90 backdrop-blur text-dim shadow-lg",
        "flex items-center justify-center",
        "hover:text-foreground hover:border-info hover:bg-bg-3",
        "focus:outline-none focus:ring-1 focus:ring-ring",
        "transition-opacity duration-150",
        visible ? "opacity-100" : "opacity-0 pointer-events-none",
      ].join(" ")}
    >
      <ArrowUp className="w-4 h-4" />
    </button>
  );
}
