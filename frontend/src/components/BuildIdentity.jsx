import { useEffect, useState } from "react";
import { apiClient } from "@/lib/api";
import { describeBuildIdentity } from "@/lib/buildIdentity";
import frontendManifest from "@/build-manifest.json";

function IdentityRow({ name, metadata }) {
  const dirty = metadata.git_dirty === true ? "dirty" : metadata.git_dirty === false ? "clean" : "dirty state unknown";
  return (
    <div className="mt-1 break-all" title={metadata.source_sha256 || "No baked source metadata"}>
      <div className="font-mono">{name}: {metadata.source_sha256?.slice(0, 12) || "unknown"}</div>
      <div>Git: {metadata.git_revision?.slice(0, 12) || "unknown"} · {dirty}</div>
      {metadata.built_at && <div>Captured: {metadata.built_at}</div>}
      {metadata.source_sha256 && <div className="font-mono">SHA-256: {metadata.source_sha256}</div>}
    </div>
  );
}

export default function BuildIdentity() {
  const [backend, setBackend] = useState({ phase: "loading" });
  useEffect(() => {
    let cancelled = false;
    async function check() {
      let next;
      try {
        const response = await apiClient.get("/build-info", { timeout: 5000 });
        next = { phase: "ready", metadata: response.data };
      } catch (error) {
        next = { phase: error?.response?.status === 404 ? "legacy" : "error" };
      }
      if (!cancelled) setBackend(next);
    }
    check();
    const timer = window.setInterval(check, 60000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, []);
  const view = describeBuildIdentity(frontendManifest, backend);
  const warning = ["mismatch", "unknown", "unavailable"].includes(view.state);
  return (
    <details className="mt-2" data-testid="build-identity" data-build-state={view.state}>
      <summary className={`cursor-pointer ${warning ? "text-warning" : "text-dim"}`}>
        <span>{view.label}</span>
        <span className="block font-mono text-[10px]">
          FE {view.frontend.source_sha256?.slice(0, 12) || "unknown"} · BE {view.backend.source_sha256?.slice(0, 12) || "unknown"}
        </span>
      </summary>
      <IdentityRow name="Frontend" metadata={view.frontend} />
      <IdentityRow name="Backend" metadata={view.backend} />
      {backend.phase === "legacy" && <p className="mt-1">Backend has no build metadata endpoint.</p>}
      <p className="mt-1">{view.note}</p>
    </details>
  );
}
