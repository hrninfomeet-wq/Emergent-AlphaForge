import React, { act } from "react";
import { createRoot } from "react-dom/client";
import BuildIdentity from "./BuildIdentity";
import { apiClient } from "@/lib/api";
import frontendManifest from "@/build-manifest.json";

jest.mock("@/lib/api", () => ({ apiClient: { get: jest.fn() } }));
jest.mock("@/build-manifest.json", () => ({
  schema: 1, scope: "alphaforge-source-v1", status: "known",
  source_sha256: "a".repeat(64), built_at: "2026-10-24T00:00:00+00:00",
  git_revision: "b".repeat(40), git_dirty: true,
}));

let root, container;
beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  jest.useFakeTimers();
  jest.clearAllMocks();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  jest.useRealTimers();
});
async function render() {
  await act(async () => root.render(<BuildIdentity />));
}

test("shows captured hashes and dirty revision, then detects backend replacement by read-only polling", async () => {
  apiClient.get.mockResolvedValueOnce({ data: frontendManifest });
  await render();
  expect(container.textContent).toContain("Build sources match");
  expect(container.textContent).toContain("Frontend: aaaaaaaaaaaa");
  expect(container.textContent).toContain("Git: bbbbbbbbbbbb · dirty");
  expect(container.textContent).toContain("SHA-256: " + "a".repeat(64));
  expect(container.textContent).toContain("Checkout freshness unverified");
  apiClient.get.mockResolvedValueOnce({ data: { ...frontendManifest, source_sha256: "c".repeat(64) } });
  await act(async () => jest.advanceTimersByTime(60000));
  expect(container.textContent).toContain("Build source mismatch");
  expect(apiClient.get.mock.calls).toEqual([
    ["/build-info", { timeout: 5000 }], ["/build-info", { timeout: 5000 }],
  ]);
});

test("legacy missing endpoint is explicit unknown", async () => {
  apiClient.get.mockRejectedValue({ response: { status: 404 } });
  await render();
  expect(container.textContent).toContain("Build identity unknown");
  expect(container.textContent).toContain("Backend: unknown");
  expect(container.textContent).toContain("Backend has no build metadata endpoint");
});

test("failed poll removes last-good match evidence", async () => {
  apiClient.get.mockResolvedValueOnce({ data: frontendManifest });
  await render();
  apiClient.get.mockRejectedValueOnce(new Error("offline"));
  await act(async () => jest.advanceTimersByTime(60000));
  expect(container.textContent).toContain("Backend build unavailable");
  expect(container.textContent).not.toContain("Build sources match");
});

test("unmount clears polling and discards delayed response", async () => {
  let resolve;
  apiClient.get.mockImplementation(() => new Promise((done) => { resolve = done; }));
  await render();
  expect(container.textContent).toContain("Checking build identity");
  await act(async () => root.unmount());
  root = createRoot(container);
  await act(async () => { resolve({ data: frontendManifest }); jest.advanceTimersByTime(120000); });
  expect(apiClient.get).toHaveBeenCalledTimes(1);
});
