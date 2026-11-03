import { INDICATOR_DEFINITIONS } from "./chartIndicators.js";

export const INDICATOR_STORAGE_KEY = "alphaforge:chart-indicators:v1";
export const MAX_INDICATORS = 40;
const PALETTE = ["#fbbf24", "#38bdf8", "#f472b6", "#a78bfa", "#34d399", "#fb923c"];

export function newIndicator(id, rows = []) {
  const definition = INDICATOR_DEFINITIONS.find(item => item.id === id);
  if (!definition || rows.length >= MAX_INDICATORS) return null;
  let number = 1;
  while (rows.some(row => (row.instanceId || row.id) === `${id}-${number}`)) number += 1;
  return { id, instanceId: `${id}-${number}`, enabled: true, params: { ...definition.defaults },
    color: PALETTE[rows.length % PALETTE.length], lineWidth: 1 };
}
export const defaultIndicators = () => [newIndicator("ema")];

function validConfigs(rows) {
  if (!Array.isArray(rows) || rows.length > MAX_INDICATORS) return null;
  const seen = new Set();
  const result = [];
  for (const row of rows) {
    const definition = INDICATOR_DEFINITIONS.find(item => item.id === row?.id);
    const instanceId = row?.instanceId || row?.id;
    if (!definition || typeof instanceId !== "string" || !/^[a-zA-Z0-9_-]{1,80}$/.test(instanceId) || seen.has(instanceId)
      || typeof row.enabled !== "boolean" || !/^#[0-9a-f]{6}$/i.test(row.color || "") || ![1, 2, 3, 4].includes(row.lineWidth)) return null;
    const params = {};
    for (const key of Object.keys(definition.defaults)) {
      const value = row.params?.[key];
      // Retain unfinished/invalid edits; the calculator explicitly refuses them.
      if (value !== "" && (typeof value !== "number" || !Number.isFinite(value))) return null;
      params[key] = value;
    }
    seen.add(instanceId);
    result.push({ id: row.id, instanceId, enabled: row.enabled, params, color: row.color, lineWidth: row.lineWidth });
  }
  return result;
}

export function readIndicatorSettings(storage) {
  try {
    const raw = storage.getItem(INDICATOR_STORAGE_KEY);
    if (!raw) return { configs: defaultIndicators(), writable: true, warning: "" };
    const envelope = JSON.parse(raw);
    const configs = envelope.version === 1 ? validConfigs(envelope.configs) : null;
    if (!configs) throw new Error("Unknown settings");
    return { configs, writable: true, warning: "" };
  } catch {
    return { configs: defaultIndicators(), writable: false, warning: "Saved indicator settings could not be read and were left untouched. Changes stay in this tab." };
  }
}

export function saveIndicatorSettings(storage, configs) {
  const clean = validConfigs(configs);
  if (!clean) return { ok: false, warning: "Indicator settings could not be saved safely. Previous settings were left untouched." };
  try {
    storage.setItem(INDICATOR_STORAGE_KEY, JSON.stringify({ version: 1, configs: clean }));
    return { ok: true, warning: "" };
  } catch { return { ok: false, warning: "Indicator settings could not be saved. Changes stay in this tab." }; }
}
