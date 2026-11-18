import { defaultStyleFor, sceneOf } from "lightweight-charts-drawing";
import { chartInk } from "./chartTheme";
import { installDrawingTheme } from "./drawingTheme";

function scene(kind) {
  const style = { ...defaultStyleFor(kind), color: "#ffffff" };
  if (style.levels) style.levels = style.levels.map(level => ({ ...level, color: "#ffffff" }));
  const drawing = { id: "drawing", kind, points: [{ time: 60, price: 100 }, { time: 120, price: 200 }], style };
  return sceneOf(drawing, [{ x: 25, y: 75 }, { x: 225, y: 175 }], { w: 500, h: 300, coords: null, selected: false });
}

function themeFixture(sceneItems) {
  const rows = [{ selected: false, hoveredAnchor: -1, scene: sceneItems }];
  const view = { coordinate: () => 70, text: () => "100", backColor: () => "#ffffff", textColor: () => "#101010" };
  const axes = { price: [view], time: [view] };
  const manager = { scenes: () => rows, axisViews: () => axes };
  let theme = "default";
  installDrawingTheme(manager, () => theme);
  return { manager, rows, axes, setTheme(value) { theme = value; } };
}

beforeEach(() => jest.spyOn(HTMLCanvasElement.prototype, "getContext")
  .mockReturnValue({ measureText: text => ({ width: text.length * 7 }) }));
afterEach(() => jest.restoreAllMocks());

test("saved white lines and arrows become visible without altering the default drawing or its hit area", () => {
  const { manager, rows, setTheme } = themeFixture([...scene("horizontal-line"), ...scene("arrow-mark-up")]);
  expect(manager.scenes(500, 300)).toBe(rows);
  setTheme("white");
  const items = manager.scenes(500, 300)[0].scene;
  const darkInk = chartInk("#ffffff", "white");
  expect(darkInk).not.toBe("#ffffff");
  expect(items.find(item => item.t === "line").stroke).toBe(darkInk);
  expect(items.find(item => item.t === "path").fill).toBe(darkInk);
  expect(items.find(item => item.t === "hit")).toBe(rows[0].scene.find(item => item.t === "hit"));
  setTheme("default");
  expect(manager.scenes(500, 300)).toBe(rows);
  expect(rows[0].scene.find(item => item.t === "line").stroke).toBe("#ffffff");
});

test("Fibonacci levels retain their geometry and fill opacity, while a ruler badge keeps contrasting text", () => {
  const fib = scene("fib-retracement");
  const ruler = scene("date-and-price-range");
  const { manager, setTheme } = themeFixture([...fib, ...ruler]);
  setTheme("white");
  const items = manager.scenes(500, 300)[0].scene;
  const paintedLevel = items.find(item => item.t === "line" && item.stroke === chartInk("#ffffff", "white"));
  expect(paintedLevel).toBeDefined();
  const fillIndex = fib.findIndex(item => item.fillOpacity > 0);
  expect(fillIndex).toBeGreaterThanOrEqual(0);
  expect(items[fillIndex]).toEqual({ ...fib[fillIndex], fill: chartInk(fib[fillIndex].fill, "white") });
  const badge = items.find(item => item.t === "group" && item.items[0]?.t === "rect");
  expect(badge.items[0].fill).not.toBe("#ffffff");
  expect(badge.items.slice(1).every(text => text.fill === "#ffffff")).toBe(true);
});

test("price and time badges keep coordinates, text and the original colors on return to Default", () => {
  const { manager, axes, setTheme } = themeFixture([]);
  setTheme("white");
  for (const [axis] of Object.values(manager.axisViews())) {
    expect(axis.backColor()).toBe(chartInk("#ffffff", "white"));
    expect(axis.textColor()).toBe("#ffffff");
    expect(axis.coordinate()).toBe(70);
    expect(axis.text()).toBe("100");
  }
  setTheme("default");
  expect(manager.axisViews()).toBe(axes);
  expect(axes.price[0].backColor()).toBe("#ffffff");
});
