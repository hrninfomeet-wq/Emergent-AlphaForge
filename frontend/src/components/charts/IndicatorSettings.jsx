import { useState } from "react";
import { X, Plus, Trash2 } from "lucide-react";
import { INDICATOR_DEFINITIONS } from "../../lib/chartIndicators";
import { MAX_INDICATORS, newIndicator } from "../../lib/chartIndicatorConfig";
export { defaultIndicators } from "../../lib/chartIndicatorConfig";

export default function IndicatorSettings({ configs, setConfigs, volumeAvailable, timeframe, onClose }) {
  const [type, setType] = useState("ema");
  const update = (instance, patch) => setConfigs(rows => rows.map(row => (row.instanceId || row.id) === instance ? { ...row, ...patch } : row));
  const add = () => setConfigs(rows => { const next = newIndicator(type, rows); return next ? [...rows, next] : rows; });
  const counts = {};
  return <section className="chart-indicator-settings" aria-label="Indicator settings">
    <div className="chart-section-title"><div><strong>Indicators</strong><small>Each instance has its own period and style</small></div><button type="button" aria-label="Close indicator settings" onClick={onClose}><X size={17} /></button></div>
    <div className="chart-add-indicator"><select aria-label="Indicator to add" value={type} onChange={event => setType(event.target.value)}>{INDICATOR_DEFINITIONS.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select><button type="button" onClick={add} disabled={configs.length >= MAX_INDICATORS}><Plus size={14} />Add indicator</button><span>For a crossover, add EMA twice and set the periods to 20 and 50.</span></div>
    <div className="chart-indicator-grid">{configs.map(config => {
      const definition = INDICATOR_DEFINITIONS.find(row => row.id === config.id);
      if (!definition) return null;
      counts[config.id] = (counts[config.id] || 0) + 1;
      const label = `${definition.name} ${counts[config.id]}`;
      const instance = config.instanceId || config.id;
      const dailyVwap = config.id === "vwap" && ["1d", "1w"].includes(timeframe);
      const unavailable = dailyVwap || (!volumeAvailable && ["volume", "vwap"].includes(config.id));
      return <div key={instance} className={`chart-indicator-setting ${config.enabled ? "enabled" : ""}`}>
        <div className="chart-indicator-instance-header"><label className="chart-indicator-switch"><input type="checkbox" aria-label={label} checked={config.enabled} disabled={unavailable} onChange={event => update(instance, { enabled: event.target.checked })} /><strong>{label}</strong></label><button type="button" aria-label={`Remove ${label}`} onClick={() => setConfigs(rows => rows.filter(row => (row.instanceId || row.id) !== instance))}><Trash2 size={13} /></button></div>
        {unavailable && <small>{dailyVwap ? "Requires intraday candles" : "Volume unavailable for this instrument"}</small>}
        <div className="chart-indicator-params">{Object.entries(config.params).map(([key, value]) => <label key={key}>{key}<input type="number" aria-label={`${label} ${key}`} min={key === "multiplier" ? "0.1" : "1"} max={key === "multiplier" ? "1000" : "10000"} step={key === "multiplier" ? "0.1" : "1"} value={value} onChange={event => update(instance, { params: { ...config.params, [key]: event.target.value === "" ? "" : Number(event.target.value) } })} /></label>)}</div>
        <div className="chart-instance-style"><label>Color<input type="color" aria-label={`${label} color`} value={config.color || "#fbbf24"} onChange={event => update(instance, { color: event.target.value })} /></label><label>Width<select aria-label={`${label} line width`} value={config.lineWidth || 1} onChange={event => update(instance, { lineWidth: Number(event.target.value) })}>{[1, 2, 3, 4].map(width => <option key={width} value={width}>{width} px</option>)}</select></label></div>
      </div>;
    })}</div>
    {!configs.length && <p>No indicators added. Choose an indicator above to start.</p>}
    <p>Settings are saved in this browser. Warm-up periods remain blank. Calculations use loaded candles and do not change strategy parameters.</p>
  </section>;
}
