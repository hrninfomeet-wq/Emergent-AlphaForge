const dark = {
  background: "#0c111b", text: "#8d9bb1", legendText: "#e7edf6", separator: "#253046", separatorHover: "#344560",
  gridX: "#141e2d", gridY: "#192333", border: "#263146", cursor: "#64748b", cursorLabel: "#334155",
  up: "#35c69a", down: "#f16d7c",
};
const white = {
  background: "#ffffff", text: "#475569", legendText: "#172438", separator: "#bbc7d6", separatorHover: "#8898ad",
  gridX: "#e0e6ed", gridY: "#d9e1ea", border: "#a6b4c5", cursor: "#53657d", cursorLabel: "#334155",
  up: "#08745d", down: "#b52742",
};
export const chartPalette = theme => theme === "white" ? white : dark;

const inkCache = new Map();
// Chart and user swatch colors are six-digit sRGB. Keep the chosen hue,
// darkening bright ink just enough for small labels on a white background.
export function chartInk(color, theme) {
  if (theme !== "white" || !/^#[\da-f]{6}$/i.test(color)) return color;
  if (inkCache.has(color)) return inkCache.get(color);
  let rgb = color.slice(1).match(/../g).map(byte => parseInt(byte, 16));
  const linear = byte => byte <= 10 ? byte / 3294.6 : ((byte / 255 + .055) / 1.055) ** 2.4;
  while (.2126 * linear(rgb[0]) + .7152 * linear(rgb[1]) + .0722 * linear(rgb[2]) > 1.05 / 4.5 - .05) {
    rgb = rgb.map(byte => Math.floor(byte * .94));
  }
  const result = "#" + rgb.map(byte => byte.toString(16).padStart(2, "0")).join("");
  inkCache.set(color, result);
  return result;
}

export function chartPaint(theme) {
  const p = chartPalette(theme);
  const crosshairLine = { color: p.cursor, labelBackgroundColor: p.cursorLabel };
  return {
    layout: { background: { type: "solid", color: p.background }, textColor: p.text,
      panes: { separatorColor: p.separator, separatorHoverColor: p.separatorHover } },
    grid: { vertLines: { color: p.gridX }, horzLines: { color: p.gridY } },
    rightPriceScale: { borderColor: p.border }, timeScale: { borderColor: p.border },
    crosshair: { vertLine: { ...crosshairLine }, horzLine: { ...crosshairLine } },
  };
}

export function candlePaint(theme) {
  const { up, down } = chartPalette(theme);
  return { upColor: up, downColor: down, wickUpColor: up, wickDownColor: down };
}
