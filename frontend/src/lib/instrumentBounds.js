/**
 * Guard: point-denominated search bounds do not transfer across instruments.
 *
 * WHY THIS EXISTS
 * ---------------
 * On 2026-09-01 two `explosive_reversal` optimizer jobs ran with BYTE-IDENTICAL
 * `param_overrides` — `spot_target_pts` max 200, `spot_stop_pts` max 80 — one on
 * NIFTY and one on SENSEX. NIFTY found a +Rs 514,052 optimum comfortably inside
 * that box (stop 77.16, a true interior peak). SENSEX returned -Rs 932,976, with
 * all 50 of its re-ranked candidates negative.
 *
 * The two indices carry the SAME relative volatility (median 1-minute true range
 * 0.0320% of index on NIFTY vs 0.0328% on SENSEX) and differ only by a ~3.2x
 * point scale. So the identical box is ~3.2x TIGHTER on SENSEX: NIFTY's winning
 * stop of 0.317% of index needs ~250 SENSEX points and the ceiling was 80, while
 * its 0.742% target needs ~580 and the ceiling was 200. The profitable geometry
 * was not merely unfound — it was unreachable, and the search fell into sub-noise
 * stops (0.72x of a single 1m bar range) that lost 348% of the gross edge to
 * bid-ask friction across 9.2x as many trades.
 *
 * Nothing on screen said so. Changing the instrument only regenerates the run
 * NAME; overrides carry silently, and the existing bounds audit reports only
 * overrides belonging to a DIFFERENT STRATEGY.
 *
 * WHAT THIS DOES — AND DELIBERATELY DOES NOT DO
 * ---------------------------------------------
 * Warns, never restricts, matching this app's stated rule for out-of-range
 * params. It reports the carry-over, the scale ratio and the rescaled bound; the
 * operator decides. It never rewrites `param_overrides` on its own.
 */

/**
 * Approximate index levels, for SCALE GUIDANCE ONLY.
 *
 * Medians measured over the full warehouse on 2026-09-01: NIFTY 24,468,
 * BANKNIFTY 55,734, SENSEX 80,176. These are used to compute a RATIO between
 * two instruments, so slow index drift cancels out and only a large relative
 * re-rating would change the guidance. They are not, and must not become, an
 * input to any backtest, sizing or order path.
 */
export const REFERENCE_INDEX_LEVEL = {
  NIFTY: 24500,
  BANKNIFTY: 55700,
  SENSEX: 80200,
};

/**
 * Is this parameter denominated in absolute index POINTS?
 *
 * Points are the only unit that fails to transfer. ATR multiples (`*_atr`,
 * `*_atr_mult`) and percentages (`*_pct`) are already scale-free, and bar counts
 * / lookbacks / thresholds have no price dimension at all. Flagging those too
 * would train the operator to dismiss the warning, so the predicate stays narrow:
 * a trailing `_pts`, which is this codebase's consistent convention for the unit
 * (`spot_target_pts`, `spot_stop_pts`, `option_target_pts`, `option_stop_pts`).
 */
export const isPointDenominatedParam = (name) => /_pts$/.test(String(name || ""));

const levelOf = (instrument, levels) => {
  const table = levels || REFERENCE_INDEX_LEVEL;
  const v = table[String(instrument || "").toUpperCase()];
  return Number.isFinite(v) && v > 0 ? v : null;
};

/** Ratio to multiply a `from`-instrument point value by to reach `to`. */
export const scaleRatio = (from, to, levels) => {
  const a = levelOf(from, levels);
  const b = levelOf(to, levels);
  return a && b ? b / a : null;
};

/**
 * Preview what a percent-of-index bound becomes in points on `instrument`.
 *
 * APPROXIMATE BY DESIGN. The job converts against the run window's real median
 * close (`app/bounds_unit.reference_index_price`); this uses the static table
 * above, so the two agree to within the level's drift. Its job is to catch a
 * typo — a "3.17" that should have been "0.317" — before a run is launched, not
 * to predict the exact bound. Label it with a "~" wherever it is rendered.
 *
 * Returns null (never a guess) for an unknown instrument or an unusable value,
 * so the caller shows nothing rather than a fabricated number.
 */
export const pctToPointsPreview = (pct, instrument, levels) => {
  const level = levelOf(instrument, levels);
  if (level === null) return null;
  if (pct === null || pct === undefined || pct === "") return null;
  const n = Number(pct);
  if (!Number.isFinite(n) || n < 0) return null;
  return Math.round((n / 100) * level);
};

/** An override is only live when it actually sets a bound. */
const isSet = (o) => !!o && (o.min !== undefined || o.max !== undefined);

const rescale = (value, ratio) => {
  if (value === undefined || value === null || ratio === null) return null;
  const n = Number(value);
  if (!Number.isFinite(n)) return null;
  // Two significant-ish figures: this is guidance, and a bound printed as
  // 654.8163 would imply a precision the reference levels do not have.
  const scaled = n * ratio;
  return scaled >= 100 ? Math.round(scaled / 5) * 5 : Math.round(scaled);
};

/**
 * Audit whether point-denominated overrides were authored for a different
 * instrument than the one now selected.
 *
 * @param {object}  args.overrides         `config.param_overrides`
 * @param {?string} args.authoredOn        instrument the overrides were last edited under
 * @param {string}  args.instrument        currently selected instrument
 * @param {object}  args.parameterSchema   selected strategy's declared schema
 * @param {?object} args.referenceLevels   override the level table (tests / future feed)
 * @returns {{mismatch, fromInstrument, toInstrument, ratio, params}}
 */
export const auditInstrumentScale = ({
  overrides,
  authoredOn,
  instrument,
  parameterSchema,
  referenceLevels,
} = {}) => {
  const none = {
    mismatch: false,
    fromInstrument: null,
    toInstrument: null,
    ratio: null,
    params: [],
  };

  const from = authoredOn ? String(authoredOn).toUpperCase() : null;
  const to = instrument ? String(instrument).toUpperCase() : null;
  // No recorded authorship => every job saved before this guard existed. Staying
  // silent is correct: we cannot tell a carry-over from a deliberate choice, and
  // a warning that fires on load would be dismissed by reflex.
  if (!from || !to || from === to) return none;

  const declared = parameterSchema || {};
  const ratio = scaleRatio(from, to, referenceLevels);

  // Guard on SCALE, not on the name. Two instruments at the same index level
  // need no rescale however different they are called. An UNKNOWN instrument
  // (ratio null) still warns — the carry-over is real, we just cannot size it.
  if (ratio !== null && Math.abs(ratio - 1) < 0.1) return none;

  const params = Object.entries(overrides || {})
    .filter(([name, ov]) =>
      isSet(ov) &&
      isPointDenominatedParam(name) &&
      // A param the strategy does not declare cannot affect the search; that is
      // the existing foreign-override audit's business, not this one's.
      Object.prototype.hasOwnProperty.call(declared, name))
    .map(([name, ov]) => ({
      name,
      min: ov.min === undefined ? null : ov.min,
      max: ov.max === undefined ? null : ov.max,
      suggestedMin: rescale(ov.min, ratio),
      suggestedMax: rescale(ov.max, ratio),
    }))
    .sort((a, b) => a.name.localeCompare(b.name));

  if (!params.length) return none;

  return {
    mismatch: true,
    fromInstrument: from,
    toInstrument: to,
    ratio: ratio === null ? null : Math.round(ratio * 100) / 100,
    params,
  };
};
