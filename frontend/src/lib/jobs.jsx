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
const HYGIENE_STORAGE_KEY = "alphaforge.activeHygiene";
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

function loadHygiene() {
  try {
    const raw = localStorage.getItem(HYGIENE_STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function saveHygiene(batch) {
  try {
    if (batch) localStorage.setItem(HYGIENE_STORAGE_KEY, JSON.stringify(batch));
    else localStorage.removeItem(HYGIENE_STORAGE_KEY);
  } catch {
    /* ignore */
  }
}

export function JobsProvider({ children }) {
  // jobs[kind] = latest job document (or null)
  const [jobs, setJobs] = useState({});
  // Aggregated state of the most recent Data Hygiene execute batch.
  const [hygiene, setHygiene] = useState(null);
  // Active run IDs being polled, kept in a ref so the poll loop reads fresh values.
  const activeRef = useRef({});
  // Completion listeners: { kind: Set<fn> }
  const listenersRef = useRef({});
  const pollingRef = useRef(false);
  // Active hygiene batch tracking: { runIds: [...], submittedAt, total } or null.
  const hygieneRef = useRef(null);
  const hygienePollingRef = useRef(false);

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

  // ---- Data Hygiene batch tracking -----------------------------------------
  // A hygiene "execute" submits several warehouse_runs (one per instrument x
  // kind). We poll /data-hygiene/status, match our submitted run IDs, and
  // aggregate progress. The batch (run IDs) is persisted so progress survives
  // navigation and reload, like the single-job kinds above.
  const ensureHygienePolling = useCallback(() => {
    if (hygienePollingRef.current) return;
    hygienePollingRef.current = true;

    const tick = async () => {
      const batch = hygieneRef.current;
      if (!batch || !batch.runIds?.length) {
        hygienePollingRef.current = false;
        return;
      }
      try {
        const res = await api.dataHygieneStatus();
        const all = res.items || [];
        const tracked = all.filter((r) => batch.runIds.includes(r.id));
        const done = tracked.filter((r) => !TERMINAL_EXCLUDED.includes(r.status));
        const failed = tracked.filter((r) => r.status === "failed");
        const total = batch.runIds.length;
        const avgPct = tracked.length
          ? Math.round(tracked.reduce((acc, r) => acc + (Number(r.progress_pct) || 0), 0) / total)
          : 0;
        const allDone = done.length >= total && total > 0;

        setHygiene({
          runIds: batch.runIds,
          total,
          completed: done.length,
          failed: failed.length,
          progress_pct: allDone ? 100 : avgPct,
          runs: tracked,
          done: allDone,
        });

        if (allDone) {
          hygieneRef.current = null;
          saveHygiene(null);
          hygienePollingRef.current = false;
          if (failed.length) {
            toast.error(`Data hygiene finished with ${failed.length} failed job(s) of ${total}`);
          } else {
            toast.success(`Data hygiene complete: ${total} job(s) finished`);
          }
          emitComplete("data_hygiene", { total, failed: failed.length });
          return;
        }
      } catch {
        // Transient status error: keep trying on the next tick.
      }
      setTimeout(tick, POLL_INTERVAL_MS);
    };

    tick();
  }, [emitComplete]);

  const startHygieneBatch = useCallback((submitResult) => {
    const runIds = (submitResult?.submitted || [])
      .map((s) => s.run_id)
      .filter(Boolean);
    if (runIds.length === 0) return 0;
    const batch = {
      runIds,
      submittedAt: new Date().toISOString(),
      total: runIds.length,
    };
    hygieneRef.current = batch;
    saveHygiene(batch);
    setHygiene({
      runIds,
      total: runIds.length,
      completed: 0,
      failed: 0,
      progress_pct: 0,
      runs: [],
      done: false,
    });
    ensureHygienePolling();
    return runIds.length;
  }, [ensureHygienePolling]);

  // On mount: rehydrate persisted active jobs and resume polling.
  useEffect(() => {
    const persisted = loadPersisted();
    const resumeKinds = Object.keys(persisted).filter((k) => JOB_KINDS[k] && persisted[k]);
    for (const kind of resumeKinds) {
      activeRef.current[kind] = persisted[kind];
    }
    if (resumeKinds.length > 0) ensurePolling();

    // Resume an in-flight hygiene batch if one was persisted.
    const savedHygiene = loadHygiene();
    if (savedHygiene?.runIds?.length) {
      hygieneRef.current = savedHygiene;
      setHygiene({
        runIds: savedHygiene.runIds,
        total: savedHygiene.total || savedHygiene.runIds.length,
        completed: 0,
        failed: 0,
        progress_pct: 0,
        runs: [],
        done: false,
      });
      ensureHygienePolling();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const value = {
    jobs,
    hygiene,
    startJob,
    startHygieneBatch,
    onJobComplete,
    isJobActive: (kind) => Boolean(activeRef.current[kind]),
    isHygieneActive: () => Boolean(hygieneRef.current),
  };

  return <JobsContext.Provider value={value}>{children}</JobsContext.Provider>;
}

export function useJobs() {
  const ctx = useContext(JobsContext);
  if (!ctx) throw new Error("useJobs must be used within a JobsProvider");
  return ctx;
}
