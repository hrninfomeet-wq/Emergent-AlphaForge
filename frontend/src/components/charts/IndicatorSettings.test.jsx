import React, { act, useState } from "react";
import { createRoot } from "react-dom/client";
import IndicatorSettings, { defaultIndicators } from "./IndicatorSettings";

let container, root, current;
function Harness() {
  const [configs, setConfigs] = useState(defaultIndicators); current = configs;
  return <IndicatorSettings configs={configs} setConfigs={setConfigs} volumeAvailable={true} timeframe="1m" onClose={() => {}} />;
}
beforeEach(async () => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container);
  await act(async () => root.render(<Harness />));
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });

test("add, edit and remove one EMA without changing its peer", async () => {
  await act(async () => [...container.querySelectorAll("button")].find(button => button.textContent.includes("Add indicator")).click());
  expect(current).toHaveLength(2);
  const firstId = current[0].instanceId, secondId = current[1].instanceId;
  const period = container.querySelector('[aria-label="EMA 2 period"]');
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(period, "50");
    period.dispatchEvent(new Event("input", { bubbles: true }));
  });
  expect(current.map(row => row.params.period)).toEqual([20, 50]);
  const width = container.querySelector('[aria-label="EMA 2 line width"]');
  await act(async () => { width.value = "3"; width.dispatchEvent(new Event("change", { bubbles: true })); });
  expect(current.map(row => row.lineWidth)).toEqual([1, 3]);
  await act(async () => container.querySelector('[aria-label="Remove EMA 1"]').click());
  expect(current.map(row => row.instanceId)).toEqual([secondId]);
  expect(current[0].instanceId).not.toEqual(firstId);
  expect(current[0].params.period).toBe(50);
});
