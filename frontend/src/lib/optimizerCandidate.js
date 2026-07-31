const isObject = (value) => value !== null && typeof value === "object" && !Array.isArray(value);

const hasNonFiniteNumber = (value) => {
  if (typeof value === "number") return !Number.isFinite(value);
  if (Array.isArray(value)) return value.some(hasNonFiniteNumber);
  if (isObject(value)) return Object.values(value).some(hasNonFiniteNumber);
  return false;
};

const candidateIsAvailable = (explicit, value) =>
  explicit === true || (explicit == null && typeof value === "number" && Number.isFinite(value));

/** Mirror the backend's immutable finite-candidate contract, including {} params. */
export const hasFiniteOptimizerCandidate = (job) => {
  const bsf = job?.best_so_far;
  const candidates = [
    {
      available: candidateIsAvailable(job?.finite_candidate_available, job?.best_value),
      params: job?.best_params,
      metrics: job?.best_metrics || {},
    },
    {
      available: candidateIsAvailable(bsf?.finite_candidate_available, bsf?.value),
      params: bsf?.params,
      metrics: bsf?.metrics || {},
    },
  ];
  return candidates.some(({ available, params, metrics }) =>
    available && isObject(params) && !hasNonFiniteNumber({ params, metrics }));
};
