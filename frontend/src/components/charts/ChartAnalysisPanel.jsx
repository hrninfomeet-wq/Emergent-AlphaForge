import { ANALYSIS_OVERLAYS, defaultAnalysisSettings, normalizeAnalysisSettings } from "../../lib/chartAnalysisSettings";
import { finiteValue, priceText, timeText } from "./chartPresentation";
import "./chartAnalysis.css";

const metric = value => finiteValue(value) ? Number(value).toFixed(3) : "—";
const probability = value => finiteValue(value) && Number(value) >= 0 && Number(value) <= 1 ? `${(Number(value) * 100).toFixed(1)}%` : "—";
export default function ChartAnalysisPanel({ settings: supplied, onChange, analysis, now = Date.now() }) {
  const settings = normalizeAnalysisSettings(supplied) || defaultAnalysisSettings();
  const { data, catalog, catalogError, loading, stale, error, signals = [] } = analysis;
  const prediction = data?.prediction;
  const logged = prediction?.status === "logged";
  const leading = Object.entries(prediction?.probabilities || {}).filter(([key, value]) => ["up", "flat", "down"].includes(key) && finiteValue(value)).sort((a, b) => b[1] - a[1])[0];
  const latestSignal = signals[signals.length - 1];
  const targetEnd = prediction?.target_end ?? prediction?.target_time;
  const horizonClosed = logged && finiteValue(targetEnd) && Number(targetEnd) * 1000 <= now;
  const scores = data?.scores;
  return <section className="chart-analysis-panel" aria-label="Read-only chart research">
    <div className="chart-analysis-controls" role="group" aria-label="Independent concept overlays">
      {Object.entries(ANALYSIS_OVERLAYS).map(([id, label]) => {
        const item = catalog?.overlays?.find(row => row.id === id);
        return <label key={id} title={item?.reason || (!item ? "Waiting for catalogue availability" : "")}><input type="checkbox" aria-label={label} checked={settings.overlays[id]} disabled={!item?.available} onChange={event => onChange({ ...settings, overlays: { ...settings.overlays, [id]: event.target.checked } })} />{label}{item && !item.available && <small>{item.reason || "Unavailable"}</small>}</label>;
      })}
      <label><input type="checkbox" aria-label="Next-close forecast" checked={settings.forecast} onChange={event => onChange({ ...settings, forecast: event.target.checked })} />Next-close forecast · logs while enabled</label>
      <label>Strategy research<select aria-label="Strategy research markers" value={settings.strategyId || ""} onChange={event => onChange({ ...settings, strategyId: event.target.value || null })}><option value="">Off</option>{(catalog?.strategies || []).map(item => <option key={item.id} value={item.id} disabled={!item.available}>{item.name}{!item.available ? ` — ${item.reason || "Unavailable"}` : ""}</option>)}</select></label>
    </div>
    {(catalogError || error) && <p className="chart-analysis-warning" role="status">{catalogError || error}{stale ? " · STALE" : ""}</p>}
    {loading && <p role="status">Loading closed-bar research…</p>}
    {data && <div className="chart-analysis-meta">{stale ? "Stale research" : "Closed-bar research"} · Through {finiteValue(data.closed_through) ? `${timeText(data.closed_through)} IST` : "unavailable"} · {signals.length} strategy markers · Research only; markers are hypothetical, not fills.</div>}
    {settings.overlays.premium_discount && <p className="chart-analysis-meta">Premium / Discount shows the current confirmed swing range across the chart. The midpoint is 50%. Panning displays this current reference range over earlier candles.</p>}
    {settings.strategyId && data?.strategy?.reason && <p>{data.strategy.reason}</p>}
    {settings.strategyId && latestSignal && <p aria-label="Latest strategy preview">Latest preview: {latestSignal.direction} · {timeText(latestSignal.time)} IST · {(latestSignal.reasons || []).join("; ") || latestSignal.text}</p>}
    {settings.forecast && <article className="chart-research-card" aria-label="Next-close research forecast">
      <strong>AI view · experimental statistical research · model probability, not accuracy</strong>
      {!logged ? <p>{prediction?.reason || "No logged forecast available for this instrument and closed candle."}</p> : <>
        <p>{prediction.model} · Logged before display · {stale ? "STALE" : "Research"} · Samples {finiteValue(prediction.sample_size) ? prediction.sample_size : "—"}</p>
        {horizonClosed && <p role="status" className="chart-analysis-warning">Forecast horizon closed — retained historical forecast; awaiting the next logged forecast or target score.</p>}
        {leading && <p>Leading outcome: <strong>{leading[0]}</strong> · Model probability {probability(leading[1])}</p>}
        <p>Target close {finiteValue(targetEnd) ? `${timeText(targetEnd)} IST` : "unavailable"} · {horizonClosed ? "Historical next-close zone" : "Next-close zone"} {finiteValue(prediction.zone_low) ? priceText(prediction.zone_low) : "—"} – {finiteValue(prediction.zone_high) ? priceText(prediction.zone_high) : "—"}</p>
        <p>Up {probability(prediction.probabilities?.up)} · Flat {probability(prediction.probabilities?.flat)} · Down {probability(prediction.probabilities?.down)} · Flat threshold {finiteValue(prediction.flat_threshold_points) ? `${priceText(prediction.flat_threshold_points)} points` : "unavailable"}</p>
        <p>Predicted close {finiteValue(prediction.predicted_close) ? priceText(prediction.predicted_close) : "—"} · No-change close {finiteValue(prediction.baseline_close) ? priceText(prediction.baseline_close) : "—"}</p>
        <ul>{(prediction.reasons || []).filter(reason => typeof reason === "string").map((reason, i) => <li key={i}>{reason}</li>)}</ul>
      </>}
      <p>Historical analogues; probabilities are not calibrated accuracy. Zone concerns the next close, not intrabar extremes.</p>
      <div aria-label="Forecast scores">Evaluated {scores?.evaluated ?? "—"} · Pending {scores?.pending ?? "—"} · MAE model / no-change {metric(scores?.model_mae)} / {metric(scores?.baseline_mae)} points · Brier model / baseline {metric(scores?.model_brier)} / {metric(scores?.baseline_brier)} · Interval coverage {probability(scores?.coverage)}</div>
    </article>}
    {(data?.warnings || []).length > 0 && <p className="chart-analysis-warning">{data.warnings.filter(value => typeof value === "string").join(" · ")}</p>}
  </section>;
}
