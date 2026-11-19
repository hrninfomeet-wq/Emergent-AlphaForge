const hasOwn = (value, key) => Object.prototype.hasOwnProperty.call(value || {}, key);
const isSet = (value, key) => value?.[key] !== undefined;
const hasBound = (value) => isSet(value, "min") || isSet(value, "max");

/**
 * Audit saved UI overrides without guessing backend-only pinning decisions.
 * Declared strategy schemas and the indicator catalog come from GET /strategies.
 */
export function auditOptimizerBoundOverrides({
  parameterSchema = {},
  indicatorCatalog = {},
  overrides = {},
  expandIndicatorPeriods = false,
} = {}) {
  const savedBounds = [];
  const activeFixed = [];
  const foreign = [];
  const ignored = [];
  const savedCatalog = [];

  for (const [name, override] of Object.entries(overrides || {})) {
    const hasBounds = hasBound(override);
    const hasFixed = isSet(override, "fixed");
    if (!hasBounds && !hasFixed) continue;

    const declared = parameterSchema?.[name];
    const catalog = indicatorCatalog?.[name];
    if (!declared && !catalog) {
      foreign.push(name);
      continue;
    }

    // Declared schema wins over the catalog exactly as _build_param_space does.
    if (!declared) {
      savedCatalog.push(name);
      continue;
    }

    if (!["int", "float", "bool"].includes(declared.type)) {
      ignored.push({ name, reason: `parameter type '${declared.type || "unknown"}' is not searched` });
      continue;
    }

    if (hasFixed) {
      activeFixed.push(name);
      if (hasBounds) ignored.push({ name, reason: "fixed value takes precedence over min/max bounds" });
      continue;
    }

    // A schema-level fixed value is retained even when bounds are overridden.
    if (hasOwn(declared, "fixed")) {
      if (hasBounds) ignored.push({ name, reason: "strategy schema fixes this parameter; min/max bounds do not make it searchable" });
      continue;
    }

    if (!hasBounds) continue;
    if (declared.type === "bool") {
      ignored.push({ name, reason: "boolean search uses both values; min/max bounds do not apply" });
      continue;
    }

    const hasMin = isSet(override, "min") || declared.min !== undefined;
    const hasMax = isSet(override, "max") || declared.max !== undefined;
    if (!hasMin || !hasMax) {
      ignored.push({ name, reason: "incomplete effective range; optimizer pins the parameter to its default" });
      continue;
    }

    // NON_ALPHA_PARAM_NAMES and premium-native restrictions are not included in
    // this UI metadata. Report the user's saved bounds, never claim they are active.
    savedBounds.push(name);
  }

  return {
    savedBounds: savedBounds.sort(),
    savedCatalog: savedCatalog.sort(),
    activeFixed: activeFixed.sort(),
    foreign: foreign.sort(),
    ignored: ignored.sort((a, b) => a.name.localeCompare(b.name)),
    expandIndicatorPeriods: !!expandIndicatorPeriods,
  };
}
