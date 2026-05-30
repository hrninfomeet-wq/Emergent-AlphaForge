import { createContext, useContext, useEffect, useRef, useState, useCallback } from "react";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { fmtInt } from "@/lib/fmt";

/**
 * Global background-job tracker.
 *
 * Long-running warehouse fetches (Upstox index ingest, option-candle fetch) run
 * as fire-and-forget tasks on the backend and expose a pollable job document.
 * Previously the polling loop lived in the Data Warehouse page's local state, so
 * navigating away unmounted the component and the progress bar was lost on
 * return even though the backend job kept running.
 *
 * This provider is mounted ABOVE the router, so it survives route changes. It:
 *   - tracks one active job per "kind" (upstox_ingest, option_fetch)
 *   - persists active run IDs to localStorage so a full reload resumes them
 *   - polls each active job and exposes its latest state via context
 *   - fires terminal toasts once, centrally
 *   - lets pages subscribe to completion events to refresh derived views
 */

const JobsContext = createContext(null);

const STORAGE_KEY = "alphaforge.activeJobs";
const POLL_INTERVAL_MS = 2500;
const TERMINAL_EXCLUDED = ["queued", "running"];

// Per-kind configuration: how to fetch the job and how to describe it.
const JOB_KINDS = {
  upstox_ingest: {
    fetch: (id) => api.getUpstoxIngestJob(id),
    completeToast: (job) => {
      const chunk = job.chunk_days ? ` · chunk ${job.chunk_days}d` : "";
      if (job.status === "ok" || job.status === "empty") {
        return {
          type: "success",
          msg: `Upstox ${job.instrument}: +${fmtInt(job.candles_added || 0)} / ~${fmtInt(job.candles_updated || 0)} updated${chunk}`,
        };
      }
      return {
        type: "error",
        msg: `Upstox ingest ${job.status}: ${(job.failed_chunks || [])[0]?.error || "check run details"}`,
      };
    },
  },
  option_fetch: {
    fetch: (id) => api.getOptionWarehouseFetchJob(id),
    completeToast: (job) => ({
      type: job.status === "failed" ? "error" : "success",
      msg: `Option fetch ${job.status}: +${fmtInt(job.candles_added || 0)} / ~${fmtInt(job.candles_updated || 0)} updated`,
    }),
  },
};

function isTerminal(job) {
  return job && !TERMINAL_EXCLUDED.includes(job.status);
}

function loadPersisted() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

function savePersisted(map) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(map));
  } catch {
    /* ignore quota / unavailable storage */
  }
}

export function JobsProvider({ children }) {
  // jobs[kind] = latest job document (or null)
  const [jobs, setJobs] = useState({});
  // Active run IDs being polled, kept in a ref so the poll loop reads fresh values.
  const activeRef = useRef({});
  // Completion listeners: { kind: Set<fn> }
  const listenersRef = useRef({});
  const pollingRef = useRef(false);

  const persist = useCallback(() => {
    const map = {};
    for (const [kind, runId] of Object.entries(activeRef.current)) {
      if (runId) map[kind] = runId;
    }
    savePersisted(map);
  }, []);

  const emitComplete = useCallback((kind, job) => {
    const set = listenersRef.current[kind];
    if (set) {
      for (const fn of set) {
        try {
          fn(job);
        } catch {
          /* listener errors must not break polling */
        }
      }
    }
  }, []);

  // The single polling loop. Runs while any job is active.
  const ensurePolling = useCallback(() => {
    if (pollingRef.current) return;
    pollingRef.current = true;

    const tick = async () => {
      const entries = Object.entries(activeRef.current).filter(([, id]) => id);
      if (entries.length === 0) {
        pollingRef.current = false;
        return;
      }

      await Promise.all(
        entries.map(async ([kind, runId]) => {
          const cfg = JOB_KINDS[kind];
          if (!cfg) return;
          try {
            const job = await cfg.fetch(runId);
            setJobs((prev) => ({ ...prev, [kind]: job }));
            if (isTerminal(job)) {
              // Stop tracking; keep the final state visible.
              delete activeRef.current[kind];
              persist();
              const t = cfg.completeToast(job);
              if (t?.type === "error") toast.error(t.msg);
              else toast.success(t.msg);
              emitComplete(kind, job);
            }
          } catch (e) {
            // Job vanished (e.g. backend restarted and lost the run) or transient
            // error. Drop it so we don't poll forever; surface once.
            delete activeRef.current[kind];
            persist();
            setJobs((prev) => ({ ...prev, [kind]: null }));
          }
        }),
      );

      if (Object.values(activeRef.current).some(Boolean)) {
        setTimeout(tick, POLL_INTERVAL_MS);
      } else {
        pollingRef.current = false;
      }
    };

    tick();
  }, [emitComplete, persist]);

  // Begin (or resume) tracking a job for a kind.
  const startJob = useCallback((kind, initialJob) => {
    if (!JOB_KINDS[kind] || !initialJob?.id) return;
    activeRef.current[kind] = initialJob.id;
    persist();
    setJobs((prev) => ({ ...prev, [kind]: initialJob }));
    ensurePolling();
  }, [ensurePolling, persist]);

  // Subscribe to terminal completion of a kind. Returns an unsubscribe fn.
  const onJobComplete = useCallback((kind, handler) => {
    if (!listenersRef.current[kind]) listenersRef.current[kind] = new Set();
    listenersRef.current[kind].add(handler);
    return () => listenersRef.current[kind]?.delete(handler);
  }, []);

  // On mount: rehydrate persisted active jobs and resume polling.
  useEffect(() => {
    const persisted = loadPersisted();
    const resumeKinds = Object.keys(persisted).filter((k) => JOB_KINDS[k] && persisted[k]);
    if (resumeKinds.length === 0) return;
    for (const kind of resumeKinds) {
      activeRef.current[kind] = persisted[kind];
    }
    ensurePolling();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const value = {
    jobs,
    startJob,
    onJobComplete,
    isJobActive: (kind) => Boolean(activeRef.current[kind]),
  };

  return <JobsContext.Provider value={value}>{children}</JobsContext.Provider>;
}

export function useJobs() {
  const ctx = useContext(JobsContext);
  if (!ctx) throw new Error("useJobs must be used within a JobsProvider");
  return ctx;
}
