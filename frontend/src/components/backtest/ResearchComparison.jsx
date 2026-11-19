import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { loadResearchComparison } from "@/lib/researchComparison";

const statusLabel = { same: "Same recorded settings", different: "Settings mismatch", unknown: "Unknown — evidence missing" };
const tone = status => status === "different" ? "text-warning" : status === "same" ? "text-positive" : "text-dimmer";
const display = value => value === undefined ? "Unknown (not recorded)"
  : value === null ? "None (explicit)" : typeof value === "object"
    ? JSON.stringify(value, (_, v) => v === undefined ? "Unknown (not recorded)" : v, 2) : String(value);
const money = value => value == null ? "Unknown (not recorded)" : `₹${value.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;

function EvidenceTable({ rows }) {
  return <div className="overflow-x-auto"><table className="w-full text-xs text-left">
    <thead className="text-dimmer"><tr><th className="p-2">Setting</th><th className="p-2">Source</th><th className="p-2">Saved winner</th><th className="p-2">Evidence</th></tr></thead>
    <tbody>{rows.map(row => <tr key={row.key} className="border-t border-line align-top">
      <th className="p-2 font-normal text-dim">{row.label || row.key}{row.role === "parameter" && <span className="block text-dimmer">Searched parameter</span>}</th>
      <td className="p-2"><pre className="whitespace-pre-wrap break-words font-mono max-w-sm">{display(row.left)}</pre></td>
      <td className="p-2"><pre className="whitespace-pre-wrap break-words font-mono max-w-sm">{display(row.right)}</pre></td>
      <td className={`p-2 ${tone(row.status)}`}>{row.status === "same" ? "Same" : row.status === "different" ? "Different" : "Unknown"}</td>
    </tr>)}</tbody>
  </table></div>;
}

/** On-demand saved-record reads only. Does not submit/replay or change Lab inputs. */
export default function ResearchComparison({ pastRuns = [], presets = [] }) {
  const [open, setOpen] = useState(false);
  const [jobs, setJobs] = useState([]);
  const [jobsState, setJobsState] = useState("idle");
  const [jobsError, setJobsError] = useState("");
  const [sourceKey, setSourceKey] = useState("");
  const [jobId, setJobId] = useState("");
  const [detail, setDetail] = useState({ status: "idle" });
  const options = useMemo(() => [
    ...pastRuns.map(run => ({ key: `run:${run.id}`, kind: "run", id: run.id, label: run.name || run.id })),
    ...presets.map(record => ({ key: `preset:${record.name}`, kind: "preset", record, label: record.name })),
  ], [pastRuns, presets]);
  const selectedSource = options.find(source => source.key === sourceKey);
  const selectionKey = `${sourceKey}\n${jobId}`;

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setJobsState("loading"); setJobsError("");
    api.listOptJobs(100).then(data => {
      if (!cancelled) { setJobs(data.items || []); setJobsState("ready"); }
    }).catch(error => {
      if (!cancelled) { setJobsState("error"); setJobsError(error?.message || "Could not load saved optimization jobs."); }
    });
    return () => { cancelled = true; };
  }, [open]);

  useEffect(() => {
    if (!open || !selectedSource || !jobId) return;
    return loadResearchComparison({ source: selectedSource, jobId }, api,
      state => setDetail({ ...state, selectionKey }));
  }, [open, selectedSource, jobId, selectionKey]);

  const current = detail.selectionKey === selectionKey ? detail : { status: "idle" };
  const c = current.comparison;
  const selectClass = "w-full rounded border border-line bg-bg-2 p-2 text-xs text-foreground";
  return <section className="rounded-lg border border-line bg-bg-1" data-testid="research-comparison">
    <button type="button" className="w-full p-3 text-left text-sm font-semibold text-foreground"
      aria-expanded={open} onClick={() => setOpen(value => !value)}>
      {open ? "▾" : "▸"} Compare saved preset / backtest with optimizer winner
    </button>
    {open && <div className="p-3 pt-0 space-y-3">
      <p className="text-xs text-dimmer">Select saved evidence before interpreting a profit difference. This comparison reads records; it does not run a backtest or change the setup above.</p>
      <div className="grid md:grid-cols-2 gap-3">
        <label className="text-xs text-dim">Source preset or backtest
          <select aria-label="Comparison source" className={selectClass} value={sourceKey} onChange={event => setSourceKey(event.target.value)}>
            <option value="">Select source…</option>
            <optgroup label="Saved backtests (recent 50)">{options.filter(o => o.kind === "run").map(o => <option key={o.key} value={o.key}>{o.label} · {o.id.slice(0, 8)}</option>)}</optgroup>
            <optgroup label="Saved presets">{options.filter(o => o.kind === "preset").map(o => <option key={o.key} value={o.key}>{o.label}</option>)}</optgroup>
          </select>
        </label>
        <label className="text-xs text-dim">Optimization job (recent 100)
          <select aria-label="Comparison optimization job" className={selectClass} value={jobId} onChange={event => setJobId(event.target.value)} disabled={jobsState === "loading"}>
            <option value="">{jobsState === "loading" ? "Loading saved jobs…" : "Select job…"}</option>
            {jobs.map(job => <option key={job.id} value={job.id}>{job.name || job.strategy_id || job.id} · {job.status} · {job.id.slice(0, 8)}</option>)}
          </select>
        </label>
      </div>
      {jobsError && <p className="text-xs text-warning" role="alert">{jobsError} Close and reopen to retry.</p>}
      {jobsState === "ready" && jobs.length === 0 && <p className="text-xs text-dimmer">No saved optimization jobs found.</p>}
      {current.status === "loading" && <p role="status" className="text-xs text-dim">Loading full source, job and linked winner…</p>}
      {current.status === "error" && <p role="alert" className="text-xs text-warning">{current.error}</p>}
      {current.status === "ready" && c && <div className="space-y-3" data-testid="research-comparison-result">
        <p className={`text-sm ${tone(c.settingsStatus)}`} data-testid="research-comparison-status">{statusLabel[c.settingsStatus]}</p>
        <p className="text-xs text-warning">{c.dataset.note}</p>
        <p className="text-xs text-dimmer">Source: {current.source.record.name || current.source.record.id}. Job: {current.job.id} ({current.job.status}). Winner run: {current.winner?.id || "Unknown — job has no linked saved backtest"}.</p>
        {current.job.status !== "done" && <p className="text-xs text-warning">This job is not complete. The saved candidate may be partial; no final-winner claim is made.</p>}
        {current.source.kind === "preset" && <p className="text-xs text-warning">A preset records a declared policy, not an executed dataset or result. Select its saved backtest to compare effective evaluation settings.</p>}
        <details open className="rounded border border-line">
          <summary className="p-2 text-xs text-dim cursor-pointer">Effective evaluation settings</summary>
          <EvidenceTable rows={c.settings} />
          <p className="p-2 text-xs text-dimmer">Searched parameter differences are shown separately from common settings. Null is explicit; missing fields remain unknown. Option spread can replace point slippage; the saved slippage policy also controls expiry-tail widening on ordinary paired runs.</p>
        </details>
        <div className="overflow-x-auto"><table className="w-full text-xs text-left">
          <thead><tr className="text-dimmer"><th className="p-2">Saved result</th><th className="p-2">Source</th><th className="p-2">Winner</th></tr></thead>
          <tbody>
            <tr><th className="p-2 font-normal">Option net P&amp;L (₹)</th><td className="p-2 font-mono">{money(c.performance.left.optionNetPnl)}</td><td className="p-2 font-mono">{money(c.performance.right.optionNetPnl)}</td></tr>
            <tr><th className="p-2 font-normal">Spot net P&amp;L (points)</th><td className="p-2 font-mono">{c.performance.left.spotNetPnl ?? "Unknown / not applicable"}</td><td className="p-2 font-mono">{c.performance.right.spotNetPnl ?? "Unknown / not applicable"}</td></tr>
            <tr><th className="p-2 font-normal">Trade count</th><td className="p-2">{c.performance.left.tradeCount ?? "Unknown"} {c.performance.left.unit}</td><td className="p-2">{c.performance.right.tradeCount ?? "Unknown"} {c.performance.right.unit}</td></tr>
          </tbody>
        </table></div>
        <details className="rounded border border-line"><summary className="p-2 text-xs text-dim cursor-pointer">Strategy / indicator parameters and effective searched bounds</summary>
          <EvidenceTable rows={c.parameters} />
          <p className="p-2 text-xs text-dimmer">Parameter differences are the subject of optimization. Missing parameters may reflect unrecorded defaults; they are not assumed equal.</p>
          <pre className="p-2 text-xs font-mono whitespace-pre-wrap break-words" data-testid="comparison-bounds">{c.bounds ? display(c.bounds) : "Unknown — effective param_space was not saved. Requested overrides are not proof of searched bounds."}</pre>
        </details>
        <details className="rounded border border-line"><summary className="p-2 text-xs text-dim cursor-pointer">Incumbent / option-rerank inclusion evidence</summary>
          <div className="p-2 text-xs space-y-2">
            <p>{c.inclusion.seed.note}</p><pre className="font-mono whitespace-pre-wrap break-words">{display(c.inclusion.seeds)}</pre>
            <p>{c.inclusion.rerank.note}</p>
            <pre className="font-mono whitespace-pre-wrap break-words">{display(c.inclusion.rerank.records)}</pre>
            <p>Saved rerank evaluated count: {current.job.rerank?.evaluated ?? "Unknown"}. Stored ranking rows: {current.job.rerank?.ranked?.length ?? "Unknown"}.</p>
            {current.job.rerank_coverage && <pre className="font-mono whitespace-pre-wrap break-words">{display(current.job.rerank_coverage)}</pre>}
          </div>
        </details>
        {current.source.kind === "preset" && <details className="rounded border border-line"><summary className="p-2 text-xs text-dim cursor-pointer">Source preset's declared execution policy</summary>
          <pre className="p-2 text-xs font-mono whitespace-pre-wrap break-words">{display(c.declaredPolicy)}</pre>
        </details>}
      </div>}
    </div>}
  </section>;
}
