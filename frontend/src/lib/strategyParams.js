/**
 * Strategy-parameter form rules shared by direct deployment controls.
 *
 * Backend parity is deliberate: runtime._validate_strategy_deployment_config
 * accepts null only when the schema default is null (or omitted).  Keeping the
 * rule pure makes it executable in Node regression tests instead of relying on
 * JSX source checks.
 */
export function isNullableStrategyParam(spec = {}) {
  return spec?.default == null;
}

export function strategyParamInputValue(rawValue, spec = {}) {
  if (rawValue === "") {
    return isNullableStrategyParam(spec) ? null : "";
  }
  if (spec?.type === "int" || spec?.type === "float") {
    return Number(rawValue);
  }
  return rawValue;
}

export function isStrategyParamValueValid(value, spec = {}) {
  if (value == null) return isNullableStrategyParam(spec);

  if (spec?.type === "bool") return typeof value === "boolean";
  if (spec?.type === "str") {
    return typeof value === "string" && value.trim() !== "";
  }
  if (spec?.type !== "int" && spec?.type !== "float") return true;
  if (value === "" || typeof value === "boolean") return false;

  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return false;
  if (spec.type === "int" && !Number.isInteger(numeric)) return false;
  if (spec.min != null && numeric < Number(spec.min)) return false;
  if (spec.max != null && numeric > Number(spec.max)) return false;
  return true;
}

export function areStrategyParamsValid(schema = {}, params = {}) {
  return Object.entries(schema || {}).every(([name, spec]) => (
    isStrategyParamValueValid(params?.[name], spec)
  ));
}
