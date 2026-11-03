import React, { act } from "react";
import { createRoot } from "react-dom/client";
import ChartOrderLines, { orderLevels } from "./ChartOrderLines";
const order = { id:"1", mode:"paper", active:true, chart_owned:true, quantity:0, limit_price:190, trigger_price:205, stop_price:150, target_price:250 };
test("levels reflect fill, ownership, and terminal state", () => {
  expect(orderLevels([order]).map(x => x.field)).toEqual(["limit_price", "trigger_price"]);
  const filled = orderLevels([{...order, quantity:65, entry_price:190}]);
  expect(filled.map(x => x.field)).toEqual(["entry_price", "stop_price", "target_price"]);
  expect(filled[0].locked).toBe(true);
  expect(orderLevels([{...order, active:false}, {...order, chart_owned:false}])).toEqual([]);
  expect(orderLevels([{...order, pending_change:true}]).every(x => x.locked)).toBe(true);
});
describe("dragging", () => {
  let container, root, chart, series, request;
  async function render(rows) { await act(async () => root.render(<ChartOrderLines chart={chart} series={series} rows={rows} onRequestChange={request} />)); }
  function pointer(button, name, y) { button.dispatchEvent(new MouseEvent(name, {bubbles:true, button:0, clientY:y})); }
  beforeEach(async () => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container);
    chart = {applyOptions:jest.fn()}; series = {priceToCoordinate:jest.fn(() => 50), coordinateToPrice:jest.fn(y => y), getPane:() => ({getHeight:() => 100})}; request = jest.fn();
    await render([order]); container.querySelector(".chart-order-handles").getBoundingClientRect = () => ({top:0});
  });
  afterEach(async () => { await act(async () => root.unmount()); container.remove(); });
  test("release proposes a price only after dragging", async () => {
    const button = container.querySelector('[aria-label^="Paper Buy limit"]');
    await act(async () => pointer(button, "pointerdown", 50)); await act(async () => pointer(button, "pointermove", 80));
    expect(request).not.toHaveBeenCalled(); await act(async () => pointer(button, "pointerup", 80));
    expect(request).toHaveBeenCalledWith(order, "limit_price", 80);
    expect(chart.applyOptions).toHaveBeenLastCalledWith({handleScroll:true, handleScale:true});
  });
  test("concurrent fill invalidates an entry drag", async () => {
    const button = container.querySelector('[aria-label^="Paper Buy limit"]');
    await act(async () => pointer(button, "pointerdown", 50)); await render([{...order, quantity:65, entry_price:190}]);
    await act(async () => pointer(button, "pointerup", 80)); expect(request).not.toHaveBeenCalled();
  });
  test("release outside price pane is rejected", async () => {
    const button = container.querySelector('[aria-label^="Paper Buy limit"]');
    await act(async () => pointer(button, "pointerdown", 50)); await act(async () => pointer(button, "pointerup", 101));
    expect(request).not.toHaveBeenCalled(); expect(series.coordinateToPrice).not.toHaveBeenCalled();
  });
});
