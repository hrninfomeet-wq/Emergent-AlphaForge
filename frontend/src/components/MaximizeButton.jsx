import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Maximize2, Minimize2 } from "lucide-react";

/**
 * Browser Fullscreen-API maximize for a panel. Attach `panelRef` to the panel
 * element; `toggleMaximize` enters/exits full screen (Esc also exits, natively).
 * Mirrors the instrument-chart pattern in BacktestChart: full screen resizes the
 * panel WITHOUT restructuring the React tree, so live (lightweight) charts are
 * never disposed + recreated. `fullHeight` is a chart-friendly height to use
 * while maximized; table panels can ignore it.
 */
export function useMaximize(baseHeight = 0) {
  const panelRef = useRef(null);
  const [maximized, setMaximized] = useState(false);
  const [fullHeight, setFullHeight] = useState(baseHeight);

  useEffect(() => {
    const sync = () => {
      const fs = document.fullscreenElement === panelRef.current;
      setMaximized(fs);
      setFullHeight(fs ? Math.max(360, window.innerHeight - 160) : baseHeight);
    };
    document.addEventListener("fullscreenchange", sync);
    window.addEventListener("resize", sync);
    return () => {
      document.removeEventListener("fullscreenchange", sync);
      window.removeEventListener("resize", sync);
    };
  }, [baseHeight]);

  const toggleMaximize = () => {
    const el = panelRef.current;
    if (!el) return;
    if (document.fullscreenElement) document.exitFullscreen?.();
    else el.requestFullscreen?.().catch(() => {});
  };

  return { panelRef, maximized, toggleMaximize, fullHeight };
}

/** Reusable maximize / restore icon button (pairs with `useMaximize`). */
export function MaximizeButton({ maximized, onToggle, label = "panel", testid }) {
  return (
    <Button
      size="icon"
      variant="secondary"
      onClick={onToggle}
      className="h-7 w-7 border border-line bg-bg-2 text-dim"
      title={maximized ? "Exit full screen (Esc)" : `Maximize ${label} (full screen)`}
      aria-label={maximized ? "Exit full screen" : `Maximize ${label}`}
      data-testid={testid}
    >
      {maximized ? <Minimize2 className="w-3.5 h-3.5" /> : <Maximize2 className="w-3.5 h-3.5" />}
    </Button>
  );
}
