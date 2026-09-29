/**
 * What the Portfolio Greeks card may claim. No imports, no JSX — loads under node
 * (tests/test_greeks_view.py executes it).
 *
 * The card used to print "No open live positions." whenever the poll returned no
 * priced positions — which is what the route returned for an empty guard registry
 * AND for any failure. After a restart with an expired token the registry is empty
 * while a broker position is still open, so the card affirmatively said the account
 * was flat in the state where it was most exposed. `Number(null)` is 0, so a null
 * figure also rendered as a calm "₹0".
 *
 * The route now carries `book` = {state: flat|open|unknown, open_count, unguarded,
 * error} read from the BROKER's position book. Three states stay distinct:
 *
 *   flat     the broker book was read and nothing is open  -> "no positions" is a fact
 *   open     something is open; `unguarded` lists what the software guard is NOT watching
 *   unknown  the book could not be read (expired session / broker down / stale)
 *
 * Nothing renders as zero, and nothing says "no positions", unless the book was read.
 */

/** null / undefined / "" / NaN -> null; otherwise the finite number. */
export function finiteOrNull(v) {
  if (v === null || v === undefined || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

/**
 * @param {object|null} greeks   GET /live-broker/greeks (null until the first read)
 * @param {*} [pollError]        the poll's last error (its data is then the LAST reading)
 * @returns {{state: "loading"|"flat"|"open"|"unknown", netDelta: number|null,
 *            netTheta: number|null, priced: string|null,
 *            notes: Array<{tone: "dim"|"warn"|"danger", text: string}>}}
 */
export function greeksView(greeks, pollError) {
  const notes = [];
  const pollNote = () => notes.push({
    tone: "warn",
    text: "Greeks poll is failing — the figures above are the last reading, not live.",
  });

  if (greeks === null || greeks === undefined || typeof greeks !== "object") {
    if (pollError) {
      notes.push({ tone: "warn", text: "Greeks unavailable — the poll is failing." });
    }
    return { state: "loading", netDelta: null, netTheta: null, priced: null, notes };
  }

  const nComputed = Number(greeks.n_computed) || 0;
  const nSkipped = Number(greeks.n_skipped) || 0;
  const total = nComputed + nSkipped;
  const priced = total > 0 ? `${nComputed} of ${total} priced` : null;
  const netDelta = finiteOrNull(greeks.net_delta_rupees_per_point);
  const netTheta = finiteOrNull(greeks.net_theta_rupees_per_day);
  const book = greeks.book && typeof greeks.book === "object" ? greeks.book : null;

  // An older backend that does not report the book cannot support "no positions".
  if (!book || !["flat", "open", "unknown"].includes(book.state)) {
    notes.push({
      tone: "warn",
      text: "The broker book status was not reported — whether any position is open cannot be stated.",
    });
    if (pollError) pollNote();
    return { state: "unknown", netDelta: nComputed > 0 ? netDelta : null,
             netTheta: nComputed > 0 ? netTheta : null, priced, notes };
  }

  if (book.state === "flat") {
    notes.push({ tone: "dim", text: "No open positions — the broker book was read and is empty." });
    if (pollError) pollNote();
    return { state: "flat", netDelta, netTheta, priced: null, notes };
  }

  if (book.state === "open") {
    const unguarded = Array.isArray(book.unguarded) ? book.unguarded : [];
    if (unguarded.length > 0) {
      const shown = unguarded.slice(0, 4).join(", ") + (unguarded.length > 4 ? "…" : "");
      notes.push({
        tone: "danger",
        text: `${unguarded.length} open broker position${unguarded.length === 1 ? "" : "s"} `
          + `NOT under the software guard (${shown}) — included in these figures.`,
      });
    }
    if (greeks.error) {
      notes.push({ tone: "warn", text: String(greeks.error) });
    } else if (netDelta === null && netTheta === null) {
      notes.push({ tone: "warn",
                   text: "Open positions could not be priced — the figures are unknown, not zero." });
    } else if (nSkipped > 0) {
      notes.push({ tone: "dim", text: `${nSkipped} open position${nSkipped === 1 ? "" : "s"} could not be priced.` });
    }
    if (pollError) pollNote();
    return { state: "open", netDelta, netTheta, priced, notes };
  }

  // unknown: the book could not be read.
  const why = book.error ? String(book.error) : "broker book unreadable";
  notes.push({
    tone: "warn",
    text: `Broker book unreadable (${why}) — whether any position is open cannot be stated.`,
  });
  const partial = nComputed > 0;
  if (partial) {
    notes.push({ tone: "warn",
                 text: "Figures cover only the positions the software guard can see." });
  }
  if (pollError) pollNote();
  return { state: "unknown", netDelta: partial ? netDelta : null,
           netTheta: partial ? netTheta : null, priced, notes };
}

/** Tailwind text class for a note tone. */
export function noteToneClass(tone) {
  switch (tone) {
    case "danger": return "text-danger";
    case "warn": return "text-warning";
    default: return "text-dimmer/70";
  }
}
