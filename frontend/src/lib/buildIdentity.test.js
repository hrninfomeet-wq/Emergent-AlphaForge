import { describeBuildIdentity, normalizeBuildIdentity } from "./buildIdentity";

const manifest = (char = "a") => ({
  schema: 1, scope: "alphaforge-source-v1", status: "known",
  source_sha256: char.repeat(64), built_at: "2026-10-24T00:00:00+00:00",
  git_revision: "b".repeat(40), git_dirty: true,
});

test("matching sources do not claim current checkout freshness", () => {
  const view = describeBuildIdentity(manifest(), { phase: "ready", metadata: manifest() });
  expect(view.state).toBe("match");
  expect(view.label).toBe("Build sources match");
  expect(view.note).toMatch(/Checkout freshness unverified/);
  expect(view.note).toMatch(/mounted plugins/);
});

test("dirty builds on the same commit still mismatch when captured bytes differ", () => {
  const view = describeBuildIdentity(manifest("a"), { phase: "ready", metadata: manifest("c") });
  expect(view.state).toBe("mismatch");
  expect(view.label).toBe("Build source mismatch");
});

test.each([null, {}, { ...manifest(), scope: "other" }, { ...manifest(), source_sha256: "v1.0" }])("missing/malformed frontend provenance stays unknown", (metadata) => {
  expect(normalizeBuildIdentity(metadata).status).toBe("unknown");
  expect(describeBuildIdentity(metadata, { phase: "ready", metadata: manifest() }).state).toBe("unknown");
});

test("legacy backend and an unavailable endpoint never become a source match", () => {
  expect(describeBuildIdentity(manifest(), { phase: "legacy" }).state).toBe("unknown");
  expect(describeBuildIdentity(manifest(), { phase: "error" }).state).toBe("unavailable");
  expect(describeBuildIdentity(manifest(), { phase: "loading" }).state).toBe("checking");
});

test("unknown git metadata is not presented as clean", () => {
  const parsed = normalizeBuildIdentity({ ...manifest(), git_dirty: "false", git_revision: "garbage" });
  expect(parsed.git_dirty).toBeNull();
  expect(parsed.git_revision).toBeNull();
});
