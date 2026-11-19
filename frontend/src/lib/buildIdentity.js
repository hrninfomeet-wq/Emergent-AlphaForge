const SCOPE = "alphaforge-source-v1";

export function normalizeBuildIdentity(value) {
  const known = value?.schema === 1 && value?.scope === SCOPE && value?.status === "known"
    && /^[0-9a-f]{64}$/.test(value?.source_sha256 || "") && typeof value?.built_at === "string";
  return {
    status: known ? "known" : "unknown",
    source_sha256: known ? value.source_sha256 : null,
    built_at: known ? value.built_at : null,
    git_revision: known && /^[0-9a-f]{40,64}$/.test(value?.git_revision || "") ? value.git_revision : null,
    git_dirty: known && typeof value?.git_dirty === "boolean" ? value.git_dirty : null,
  };
}

export function describeBuildIdentity(frontendValue, backendState) {
  const frontend = normalizeBuildIdentity(frontendValue);
  const backend = normalizeBuildIdentity(backendState?.metadata);
  let state = "unknown";
  let label = "Build identity unknown";
  if (backendState?.phase === "loading") {
    state = "checking";
    label = "Checking build identity";
  } else if (backendState?.phase === "error") {
    state = "unavailable";
    label = "Backend build unavailable";
  } else if (frontend.status === "known" && backend.status === "known") {
    state = frontend.source_sha256 === backend.source_sha256 ? "match" : "mismatch";
    label = state === "match" ? "Build sources match" : "Build source mismatch";
  }
  return {
    state, label, frontend, backend,
    note: "Checkout freshness unverified. Source fingerprint covers captured app source, assets and build configuration; dependencies, image identity and runtime mounted plugins are not verified.",
  };
}
