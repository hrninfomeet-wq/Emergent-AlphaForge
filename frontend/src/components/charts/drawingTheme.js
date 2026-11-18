import { chartInk } from "./chartTheme";

function ink(color) {
  return !color || color === "none" || color === "transparent" ? color : chartInk(color, "white");
}

function whiteSceneItem(item) {
  if (item.t === "group") {
    const [first, ...rest] = item.items;
    // The ruler groups its opaque label rectangle and the text painted on it.
    const badge = first?.t === "rect" && first.fill && first.fill !== "transparent"
      && first.fill !== "none" && (first.fillOpacity ?? 1) === 1
      && rest.length > 0 && rest.every(child => child.t === "text");
    return { ...item, items: badge
      ? [whiteSceneItem(first), ...rest.map(child => ({ ...child, fill: "#ffffff" }))]
      : item.items.map(whiteSceneItem) };
  }
  if (item.t === "glyph") return { ...item, color: ink(item.color) };
  if (!item.fill && !item.stroke) return item;
  return { ...item, ...(item.stroke && { stroke: ink(item.stroke) }), ...(item.fill && { fill: ink(item.fill) }) };
}

function whiteAxisLabel(view) {
  return { ...view, backColor: () => ink(view.backColor()), textColor: () => "#ffffff" };
}

// Adapt the library's public paint outputs. Import, edit, hit testing and
// export continue to use the drawing's original coordinates, style and color.
export function installDrawingTheme(manager, getTheme) {
  const { scenes, axisViews } = manager;
  manager.scenes = (width, height) => {
    const rows = scenes.call(manager, width, height);
    return getTheme() === "white"
      ? rows.map(row => ({ ...row, scene: row.scene.map(whiteSceneItem) }))
      : rows;
  };
  manager.axisViews = () => {
    const axes = axisViews.call(manager);
    return getTheme() === "white"
      ? { price: axes.price.map(whiteAxisLabel), time: axes.time.map(whiteAxisLabel) }
      : axes;
  };
}
