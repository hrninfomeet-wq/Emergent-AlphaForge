import { useEffect, useRef, useState } from "react";
import { makeAnchor } from "@/lib/sessionClock";

/**
 * Anchor the server's trading clock to the local MONOTONIC clock and tick once a
 * second. Re-anchors on every new `session` (each arm-state poll), so the
 * countdown is recomputed from a fresh server instant every 15 s and never
 * accumulates drift — and the browser's wall clock is never consulted at all.
 *
 * Returns {anchor, perfNow}; feed both to sessionClock.sessionCountdown().
 */
export default function useServerClock(session) {
  const anchorRef = useRef(null);
  const [perfNow, setPerfNow] = useState(() => performance.now());

  useEffect(() => {
    const ms = Number(session?.server_now_ms);
    if (Number.isFinite(ms)) anchorRef.current = makeAnchor(ms, performance.now());
  }, [session]);

  useEffect(() => {
    const id = setInterval(() => setPerfNow(performance.now()), 1000);
    return () => clearInterval(id);
  }, []);

  return { anchor: anchorRef.current, perfNow };
}
