/** A top-layer series primitive follows its RSI pane through moves and resizes.
 * Lightweight Charts repaints this layer on both crosshair and data changes. */
export function createRsiPaneLegend(series, cursor) {
  let title, latestValue, color;
  const renderer = {
    draw(target) {
      const value = cursor.logical == null ? latestValue : series.dataByIndex(cursor.logical)?.value;
      const text = `${title}  ${Number.isFinite(value) ? value.toFixed(2) : "—"}`;
      target.useMediaCoordinateSpace(({ context }) => {
        context.save();
        context.font = "600 12px Inter, system-ui, sans-serif";
        context.textBaseline = "top";
        context.fillStyle = "#0c111b";
        context.fillRect(4, 3, context.measureText(text).width + 14, 19);
        context.fillStyle = color;
        context.fillRect(7, 7, 3, 10);
        context.fillStyle = "#e7edf6";
        context.fillText(text, 14, 6);
        context.restore();
      });
    },
  };
  const views = [{ zOrder: () => "top", renderer: () => renderer }];
  return {
    paneViews: () => views,
    update(item) {
      title = item.name;
      color = item.color;
      latestValue = item.data.findLast(point => Number.isFinite(point.value))?.value;
    },
  };
}
