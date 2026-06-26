import { useCallback, useEffect, useRef, useState } from "react";

/**
 * usePoll — poll an async `fetcher` immediately on mount and then every
 * `intervalMs`, with per-call error isolation and unmount safety.
 *
 * Returns `{ data, error, loading, refetch }`:
 *   - data    — last successful result (null until the first success)
 *   - error   — last error (null after a success); a failed call NEVER clears data
 *   - loading — true until the first call settles
 *   - refetch — fire an out-of-band fetch now (returns the promise)
 *
 * The latest `fetcher` is always used (kept in a ref) so a changing closure does
 * NOT restart the interval — only `intervalMs` / `enabled` changes do. This lets a
 * caller vary the fetcher (e.g. a batched call keyed on a changing id set) without
 * tearing down and recreating the timer each render.
 */
export function usePoll(fetcher, intervalMs, { enabled = true } = {}) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const mountedRef = useRef(true);

  const refetch = useCallback(async () => {
    try {
      const result = await fetcherRef.current();
      if (mountedRef.current) {
        setData(result);
        setError(null);
      }
      return result;
    } catch (e) {
      if (mountedRef.current) setError(e);
      return undefined;
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    if (!enabled) {
      setLoading(false);
      return undefined;
    }
    refetch();
    const id = window.setInterval(refetch, intervalMs);
    return () => {
      mountedRef.current = false;
      window.clearInterval(id);
    };
  }, [refetch, intervalMs, enabled]);

  return { data, error, loading, refetch };
}
