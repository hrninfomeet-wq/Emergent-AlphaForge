/** Use fresh market ticks only with a recent, authoritative quantity snapshot. */
export function markChartOrders(rows, snapshot, asOf, now, unavailable) {
  const tick = snapshot?.tick;
  const fresh = !unavailable && typeof asOf === "number" && now - asOf <= 10000 && now >= asOf - 5000;
  return rows.map(row => {
    const validTick = fresh && snapshot?.connected === true && row.instrument_key === snapshot.instrument_key &&
      typeof tick?.last_price === "number" && Number.isFinite(tick.last_price) && tick.last_price > 0 &&
      [tick.ts, tick.ingest_ts].every(at => typeof at === "number" && at > 0 && now - at <= 10000 && at <= now + 5000);
    const oldFresh = fresh && row.mark_fresh && typeof row.mark_age_ms === "number" && row.mark_age_ms + Math.max(0, now - asOf) <= 10000;
    if (!validTick || !(row.quantity > 0) || !Number.isFinite(row.entry_price)) return { ...row, mark_fresh: !!oldFresh };
    return { ...row, mark_price: tick.last_price, mark_fresh: true, pnl: (tick.last_price - row.entry_price) * row.quantity };
  });
}
