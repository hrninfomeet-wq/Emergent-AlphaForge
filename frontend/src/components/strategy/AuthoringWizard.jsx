import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { toast } from "sonner";
import { Plus, X } from "lucide-react";

const ID_RE = /^[a-z][a-z0-9_]*$/;

// Exit fields we render as number inputs. Anything else from the catalog is
// ignored here (v1 only supports these scalar point/percent/minute exits).
const EXIT_FIELDS = [
  { key: "spot_target_pts", label: "Target (spot pts)" },
  { key: "spot_stop_pts", label: "Stop (spot pts)" },
  { key: "time_stop_minutes", label: "Time stop (minutes)" },
  { key: "target_pct", label: "Target %" },
  { key: "stop_pct", label: "Stop %" },
];

const emptyParam = () => ({ name: "", type: "float", min: "", max: "", default: "" });
const emptyCond = () => ({ left: "", op: "", right: "", label: "" });

function extractIdFromCode(code) {
  const m = /id\s*=\s*["']([a-z][a-z0-9_]*)["']/.exec(code || "");
  return m ? m[1] : "";
}

// Coerce a `right`/value field: a finite number string -> Number; else the raw
// trimmed string (so "ema9" / "param:rsi_thr" pass through unchanged).
function coerceValue(v) {
  const s = String(v ?? "").trim();
  if (s !== "" && !Number.isNaN(Number(s)) && Number.isFinite(Number(s))) return Number(s);
  return s;
}

const inputCls =
  "text-xs px-2 py-1.5 rounded-md bg-bg-2 border border-line text-foreground focus:outline-none focus:ring-1 focus:ring-info w-full";
const labelCls = "text-[10px] uppercase tracking-wider text-dimmer mb-1 block";
const sectionCls = "rounded-lg border border-line bg-bg-1 p-3 space-y-2";

export default function AuthoringWizard({ open, onOpenChange, onInstalled }) {
  const [catalog, setCatalog] = useState(null);
  const [catalogError, setCatalogError] = useState(null);

  // Identity
  const [id, setId] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  // Parameters / conditions
  const [params, setParams] = useState([]);
  const [entryCe, setEntryCe] = useState([emptyCond()]);
  const [entryPe, setEntryPe] = useState([]);

  // Gates
  const [skipRegimes, setSkipRegimes] = useState([]);
  const [cooldownBars, setCooldownBars] = useState("");

  // Exits
  const [exits, setExits] = useState({});

  // Footer state
  const [preview, setPreview] = useState(null); // { ok, code, errors }
  const [busy, setBusy] = useState(false);
  const [overwritePending, setOverwritePending] = useState(false);

  // AI section state
  const [aiSource, setAiSource] = useState("");
  const [aiBusy, setAiBusy] = useState(false);
  const [fidelity, setFidelity] = useState(null); // { captured, couldnt_map, ambiguous }
  const [aiErrors, setAiErrors] = useState([]);
  const [providers, setProviders] = useState([]);          // [{id,label,configured}]
  const [provider, setProvider] = useState("");            // selected id

  // Feasibility / converse state
  const [ruleSet, setRuleSet] = useState(null);   // { decision, rules, summary }
  const [conversing, setConversing] = useState(false);
  const [converseError, setConverseError] = useState(null); // persistent (not a flash toast)
  const [genError, setGenError] = useState(null);           // persistent generate error
  const [showCaps, setShowCaps] = useState(false);          // engine-capabilities panel

  // Mode toggle + Full-Python state
  const [mode, setMode] = useState("spec"); // "spec" | "python"
  const [pyCode, setPyCode] = useState("");
  const [pyNotes, setPyNotes] = useState("");
  const [pyBusy, setPyBusy] = useState(false);
  const [pyValidation, setPyValidation] = useState(null); // { ok, violations, smoke }
  const validationTokenRef = useRef(0);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await api.getStrategyCatalog();
        if (!cancelled) {
          setCatalog(res);
          setCatalogError(null);
        }
      } catch (e) {
        if (!cancelled) setCatalogError(e?.response?.data?.detail || e?.message || "Failed to load catalog");
      }
      try {
        const prov = await api.getAuthorProviders();
        if (!cancelled) {
          setProviders(prov.providers || []);
          const firstConfigured = (prov.providers || []).find((p) => p.configured);
          setProvider(prov.active || (firstConfigured ? firstConfigured.id : ""));
        }
      } catch (e) {
        if (!cancelled) setProviders([]);
      }
    })();
    return () => { cancelled = true; };
  }, [open]);

  const columns = catalog?.columns || [];
  const ops = catalog?.ops || [];
  const regimes = catalog?.regimes || [];
  const paramTypes = catalog?.param_types || ["int", "float", "bool"];
  const configuredProviders = providers.filter((p) => p.configured);
  const aiReady = configuredProviders.length > 0;

  const idValid = id === "" || ID_RE.test(id);

  function buildSpec() {
    const cleanParams = params
      .filter((p) => String(p.name).trim() !== "")
      .map((p) => {
        const row = { name: String(p.name).trim(), type: p.type };
        if (p.type === "bool") {
          row.default = !!p.default;
        } else {
          if (String(p.min).trim() !== "") row.min = Number(p.min);
          if (String(p.max).trim() !== "") row.max = Number(p.max);
          if (String(p.default).trim() !== "") row.default = Number(p.default);
        }
        return row;
      });

    const cleanConds = (list) =>
      list
        .filter((c) => !(String(c.left).trim() === "" && String(c.op).trim() === "" && String(c.right).trim() === ""))
        .map((c) => {
          const cond = { left: c.left, op: c.op, right: coerceValue(c.right) };
          if (String(c.label).trim() !== "") cond.label = String(c.label).trim();
          return cond;
        });

    const exitsOut = {};
    for (const f of EXIT_FIELDS) {
      const raw = exits[f.key];
      if (raw !== undefined && String(raw).trim() !== "") exitsOut[f.key] = Number(raw);
    }

    const spec = {
      id: id.trim(),
      name: name.trim(),
      version: "1.0.0",
      description: description.trim(),
      params: cleanParams,
      entry_ce: cleanConds(entryCe),
      entry_pe: cleanConds(entryPe),
      gate_skip_regimes: skipRegimes,
      exits: exitsOut,
    };
    if (String(cooldownBars).trim() !== "") spec.cooldown_bars = Number(cooldownBars);
    return spec;
  }

  // Reverse of buildSpec: populate all form state from a StrategySpec dict returned
  // by the AI endpoint. Stringifies numbers so the inputs stay in their text-state
  // form exactly as they would be if the user typed them.
  function loadFromSpec(spec) {
    if (!spec) return;
    setId(spec.id ?? "");
    setName(spec.name ?? "");
    setDescription(spec.description ?? "");

    setParams(
      (spec.params || []).map((p) => ({
        name: p.name ?? "",
        type: p.type ?? "float",
        min: p.min != null ? String(p.min) : "",
        max: p.max != null ? String(p.max) : "",
        default: p.default != null ? String(p.default) : "",
      }))
    );

    const mapConds = (list) =>
      (list || []).map((c) => ({
        left: c.left ?? "",
        op: c.op ?? "",
        right: c.right != null ? String(c.right) : "",
        label: c.label ?? "",
      }));
    setEntryCe(mapConds(spec.entry_ce).length > 0 ? mapConds(spec.entry_ce) : [emptyCond()]);
    setEntryPe(mapConds(spec.entry_pe));

    setSkipRegimes(spec.gate_skip_regimes || []);
    setCooldownBars(spec.cooldown_bars != null ? String(spec.cooldown_bars) : "");

    const exitsIn = spec.exits || {};
    const newExits = {};
    for (const f of EXIT_FIELDS) {
      if (exitsIn[f.key] != null) newExits[f.key] = String(exitsIn[f.key]);
    }
    setExits(newExits);

    // Clear any prior compile preview so the user knows the form changed
    setPreview(null);
    setOverwritePending(false);
  }

  async function runConverse() {
    setConversing(true);
    setConverseError(null);
    try {
      const res = await api.authorConverse(aiSource, provider);
      setRuleSet(res);
    } catch (e) {
      // Persist the error in a panel the user can read — NOT a flash toast that
      // disappears before they can see what the feasibility problem was.
      const detail = e.response?.data?.detail || e.message || "Feasibility check failed";
      setConverseError({ status: e.response?.status, detail });
      setRuleSet(null);
    } finally {
      setConversing(false);
    }
  }

  async function onGenerateWithAi() {
    if (!aiSource.trim()) return;
    setAiBusy(true);
    setGenError(null);
    try {
      const res = await api.authorFromSource(aiSource, provider || undefined);
      loadFromSpec(res.spec);
      setFidelity(res.fidelity);
      setAiErrors(res.errors || []);
      toast.success("AI filled the form — review below");
    } catch (e) {
      setGenError(e?.response?.data?.detail || e?.message || "AI generation failed");
    } finally {
      setAiBusy(false);
    }
  }

  async function onGeneratePython() {
    if (!aiSource.trim()) return;
    setPyBusy(true);
    setGenError(null);
    try {
      const res = await api.authorPythonFromSource(aiSource, provider || undefined);
      setPyCode(res.code || "");
      setPyNotes(res.notes || "");
      setFidelity(res.fidelity || null);
      setPyValidation(null);
      validationTokenRef.current += 1;
      toast.success("AI wrote a strategy — review, validate, then install");
    } catch (e) {
      setGenError(e?.response?.data?.detail || e?.message || "AI generation failed");
    } finally { setPyBusy(false); }
  }

  function onPyCodeEdit(v) {
    setPyCode(v);
    validationTokenRef.current += 1;
    setPyValidation(null);
  }

  async function onValidatePython() {
    const token = validationTokenRef.current;
    setPyBusy(true);
    try {
      const res = await api.validatePython(pyCode);
      if (token === validationTokenRef.current) setPyValidation(res);
    } catch (e) {
      if (token === validationTokenRef.current)
        setPyValidation({ ok: false, violations: [e?.response?.data?.detail || e?.message || "validate failed"], smoke: null });
    } finally { setPyBusy(false); }
  }

  async function onInstallPython() {
    setPyBusy(true);
    try {
      const id = extractIdFromCode(pyCode);
      const res = await api.installPython(pyCode, id);
      toast.success("Installed " + res.strategy_id);
      onInstalled?.(); onOpenChange(false);
    } catch (e) {
      toast.error("Install failed: " + (e?.response?.data?.detail || e?.message || "unknown error"));
    } finally { setPyBusy(false); }
  }

  async function onPreview() {
    setBusy(true);
    setOverwritePending(false);
    try {
      const res = await api.authorCompile(buildSpec());
      setPreview(res);
    } catch (e) {
      setPreview({ ok: false, errors: [e?.response?.data?.detail || e?.message || "Compile failed"], code: null });
    } finally {
      setBusy(false);
    }
  }

  async function doInstall(overwrite) {
    setBusy(true);
    try {
      const res = await api.authorInstall(buildSpec(), overwrite);
      toast.success("Installed " + res.strategy_id);
      onInstalled?.();
      onOpenChange(false);
    } catch (e) {
      const detail = e?.response?.data?.detail || e?.message || "unknown error";
      toast.error("Install failed: " + detail);
      if (/already exists/i.test(String(detail))) setOverwritePending(true);
    } finally {
      setBusy(false);
    }
  }

  // ---- Parameter row helpers ----
  const addParam = () => setParams((p) => [...p, emptyParam()]);
  const removeParam = (i) => setParams((p) => p.filter((_, idx) => idx !== i));
  const setParamField = (i, field, value) =>
    setParams((p) => p.map((row, idx) => (idx === i ? { ...row, [field]: value } : row)));

  // ---- Condition row helpers (shared by CE/PE) ----
  const condOps = (list, setList) => ({
    add: () => setList([...list, emptyCond()]),
    remove: (i) => setList(list.filter((_, idx) => idx !== i)),
    set: (i, field, value) => setList(list.map((row, idx) => (idx === i ? { ...row, [field]: value } : row))),
  });
  const ceOps = condOps(entryCe, setEntryCe);
  const peOps = condOps(entryPe, setEntryPe);

  const toggleRegime = (r) =>
    setSkipRegimes((cur) => (cur.includes(r) ? cur.filter((x) => x !== r) : [...cur, r]));

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="max-w-3xl max-h-[85vh] overflow-y-auto bg-bg-1 border-line"
        data-testid="authoring-wizard"
      >
        <DialogHeader>
          <DialogTitle className="text-sm font-semibold uppercase tracking-wider text-dim">
            New Strategy
          </DialogTitle>
        </DialogHeader>

        {catalogError && (
          <div className="text-[11px] text-rose-300 bg-rose-950/50 border border-rose-900 rounded-md p-2">
            Could not load the authoring catalog: {catalogError}. Dropdowns may be empty.
          </div>
        )}

        {/* Mode toggle */}
        <div className="flex gap-2">
          {[["spec", "Spec (fast)"], ["python", "Full Python (powerful)"]].map(([m, label]) => (
            <button key={m} type="button" onClick={() => setMode(m)} data-testid={`author-mode-${m}`}
              className={`text-xs px-3 py-1.5 rounded-md border ${mode === m ? "bg-info/15 border-info/50 text-foreground" : "bg-bg-2 border-line text-dim"}`}>
              {label}
            </button>
          ))}
        </div>

        {/* ✨ Describe with AI */}
        <div className={sectionCls}>
          <div className="text-[10px] uppercase tracking-wider text-dim">✨ Describe with AI</div>
          <div className="text-[11px] text-dimmer leading-relaxed">
            Paste your strategy rules (or a YouTube link). <b className="text-dim">Check feasibility</b> tells you,
            rule by rule, what this engine can and can't build from it — read it before generating.
            <b className="text-dim"> Generate with AI</b> then fills the form (or writes Python), and shows which
            rules it captured vs. dropped so you can verify the result.
          </div>
          {aiReady ? (
            <div className="flex items-center gap-2">
              <label className={labelCls + " mb-0"}>Provider</label>
              <select
                value={provider}
                onChange={(e) => setProvider(e.target.value)}
                className={inputCls + " w-44"}
                data-testid="author-ai-provider"
              >
                {configuredProviders.map((p) => (
                  <option key={p.id} value={p.id}>{p.label}</option>
                ))}
              </select>
            </div>
          ) : (
            <div className="text-[11px] text-amber-300">
              No AI provider configured — set GEMINI_API_KEY or ANTHROPIC_API_KEY in backend/.env.
            </div>
          )}
          <textarea
            value={aiSource}
            onChange={(e) => setAiSource(e.target.value)}
            rows={3}
            placeholder="Paste the strategy rules / transcript — or a YouTube link — and I'll fill the form below."
            className={inputCls}
            data-testid="author-ai-source"
          />
          <div className="flex items-center gap-2">
            <button
              onClick={() => (mode === "python" ? onGeneratePython() : onGenerateWithAi())}
              disabled={aiBusy || pyBusy || !aiSource.trim() || !aiReady}
              className="text-xs font-medium px-3 py-1.5 rounded-md bg-bg-2 border border-line text-foreground disabled:opacity-50"
              data-testid="author-ai-generate-btn"
            >
              {(aiBusy || pyBusy) ? "Generating…" : "Generate with AI"}
            </button>
            <button
              onClick={runConverse}
              disabled={conversing || !aiSource.trim() || !aiReady}
              className="text-xs font-medium px-3 py-1.5 rounded-md bg-bg-2 border border-line text-foreground disabled:opacity-50"
              data-testid="author-ai-converse-btn"
            >
              {conversing ? "Checking…" : "Check feasibility"}
            </button>
            <button
              type="button"
              onClick={() => setShowCaps((s) => !s)}
              className="ml-auto text-[11px] text-info hover:underline"
              data-testid="author-caps-toggle"
            >
              {showCaps ? "Hide" : "What can this engine build?"}
            </button>
          </div>

          {/* Persistent AI error panel — stays until the next run (was a flash toast) */}
          {genError && (
            <div className="rounded-md border border-rose-900 bg-rose-950/50 p-2 space-y-1" data-testid="author-gen-error">
              <div className="flex items-center justify-between">
                <span className="text-[11px] font-semibold text-rose-300">Generation failed</span>
                <button onClick={() => setGenError(null)} className="text-dimmer hover:text-rose-300" aria-label="Dismiss">
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
              <div className="text-[11px] text-rose-200 leading-relaxed whitespace-pre-wrap">{genError}</div>
            </div>
          )}
          {converseError && (
            <div className="rounded-md border border-rose-900 bg-rose-950/50 p-2 space-y-1" data-testid="author-converse-error">
              <div className="flex items-center justify-between">
                <span className="text-[11px] font-semibold text-rose-300">
                  Feasibility check failed{converseError.status ? ` (${converseError.status})` : ""}
                </span>
                <button onClick={() => setConverseError(null)} className="text-dimmer hover:text-rose-300" aria-label="Dismiss">
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
              <div className="text-[11px] text-rose-200 leading-relaxed whitespace-pre-wrap">{converseError.detail}</div>
            </div>
          )}

          {/* Engine capabilities & limits — honest tiers, set expectations up front */}
          {showCaps && catalog?.capability && (() => {
            const cap = catalog.capability;
            const featNames = (arr) => (arr || []).map((f) => f.name).join(", ");
            return (
              <div className="rounded-md border border-line bg-bg-0 p-2 space-y-2.5 text-[11px]" data-testid="author-caps-panel">
                <div className="text-dim">
                  Every rule is checked against what the engine can actually compute. There are
                  four tiers — read them so you know what will work where:
                </div>

                {/* Tier 1 — build now (backtest + live) */}
                <div data-testid="cap-tier-build-now">
                  <div className="text-emerald-300 font-semibold">✓ Buildable now — backtest AND live</div>
                  <div className="text-dimmer">{cap.build_now?.note}</div>
                  <div className="text-dimmer mt-0.5">
                    Indicator columns ({(cap.build_now?.columns || []).length}): {(cap.build_now?.columns || []).join(", ")}
                  </div>
                  {(cap.build_now?.features || []).length > 0 && (
                    <div className="text-dimmer mt-0.5">Structural features: <span className="text-emerald-400">{featNames(cap.build_now.features)}</span></div>
                  )}
                </div>

                {/* Tier 2 — backtest-only (live fidelity not guaranteed) */}
                {(cap.backtest_only?.features || []).length > 0 && (
                  <div data-testid="cap-tier-backtest-only">
                    <div className="text-amber-300 font-semibold">◑ Backtest-only — live fidelity not guaranteed yet</div>
                    <div className="text-dimmer">
                      <span className="text-amber-400 font-mono">{featNames(cap.backtest_only.features)}</span>. {cap.backtest_only.note}
                    </div>
                  </div>
                )}

                {/* Tier 3 — addable with data (roadmap) */}
                <div data-testid="cap-tier-addable">
                  <div className="text-sky-300 font-semibold">◔ Not yet, but addable — needs data or engine work</div>
                  <ul className="text-dimmer list-disc pl-4 space-y-0.5">
                    {(cap.addable_data?.items || []).map((c, i) => <li key={`a${i}`}>{c}</li>)}
                    {(cap.needs_engine?.items || []).map((c, i) => <li key={`n${i}`}>{c}</li>)}
                  </ul>
                  <div className="text-dimmer mt-0.5">{cap.addable_data?.note}</div>
                  {cap.needs_engine?.note && <div className="text-dimmer mt-0.5">{cap.needs_engine.note}</div>}
                </div>

                {/* Tier 4 — truly infeasible */}
                <div data-testid="cap-tier-infeasible">
                  <div className="text-rose-300 font-semibold">✗ Out of reach on this infrastructure</div>
                  <ul className="text-dimmer list-disc pl-4 space-y-0.5">
                    {(cap.infeasible?.items || []).map((c, i) => <li key={i}>{c}</li>)}
                  </ul>
                  <div className="text-dimmer mt-0.5">{cap.infeasible?.note}</div>
                </div>

                <div>
                  <div className="text-dim font-semibold">Data limits</div>
                  <ul className="text-dimmer list-disc pl-4 space-y-0.5">
                    {(cap.data_limits || []).map((c, i) => <li key={i}>{c}</li>)}
                  </ul>
                </div>
              </div>
            );
          })()}

          {/* Fidelity readback */}
          {fidelity && (
            <div className="space-y-1.5 mt-1" data-testid="author-ai-fidelity">
              {(fidelity.captured || []).length > 0 && (
                <ul className="text-[11px] text-emerald-300 space-y-0.5">
                  {fidelity.captured.map((item, i) => (
                    <li key={i}>✓ {item}</li>
                  ))}
                </ul>
              )}
              {(fidelity.couldnt_map || []).length > 0 && (
                <ul className="text-[11px] text-amber-300 space-y-0.5">
                  {fidelity.couldnt_map.map((item, i) => (
                    <li key={i}>⚠ couldn't map: {item}</li>
                  ))}
                </ul>
              )}
              {(fidelity.ambiguous || []).length > 0 && (
                <ul className="text-[11px] text-amber-300 space-y-0.5">
                  {fidelity.ambiguous.map((item, i) => (
                    <li key={i}>⚠ ambiguous: {item}</li>
                  ))}
                </ul>
              )}
              {aiErrors.length > 0 && (
                <ul className="text-[11px] text-rose-300 space-y-0.5">
                  {aiErrors.map((err, i) => (
                    <li key={i}>validation: {err}</li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>

        {mode === "spec" && (<>

        {/* RuleSet feasibility panel */}
        {ruleSet && (
          <div className={sectionCls} data-testid="ruleset-panel">
            <div className="flex items-center gap-2 mb-2">
              <span className="text-xs font-semibold uppercase tracking-wider text-dim">Feasibility</span>
              <span data-testid="ruleset-decision"
                className={`text-[10px] px-2 py-0.5 rounded-full border font-mono ${
                  ruleSet.decision === "BUILD" ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
                  : ruleSet.decision === "ADVISE" ? "border-amber-500/30 bg-amber-500/10 text-amber-300"
                  : ruleSet.decision === "ASK" ? "border-sky-500/30 bg-sky-500/10 text-sky-300"
                  : "border-rose-500/30 bg-rose-500/10 text-rose-300"}`}>
                {ruleSet.decision}
              </span>
              <span className="text-[11px] text-dimmer">{ruleSet.summary}</span>
            </div>
            <div className="space-y-1">
              {ruleSet.rules.map((r) => (
                <div key={r.id} className="flex items-start gap-2 text-[11px]" data-testid="ruleset-rule">
                  <span className={`mt-0.5 w-2 h-2 rounded-full shrink-0 ${
                    r.decision_class === "BUILDABLE_NOW" ? "bg-emerald-500"
                    : r.decision_class === "BUILDABLE_WITH_FEATURE" ? (r.live_feasible === false ? "bg-amber-500" : "bg-emerald-500")
                    : r.decision_class === "AMBIGUOUS" ? "bg-sky-500" : "bg-rose-500"}`} />
                  <div className="min-w-0">
                    <div className="text-foreground">{r.text} <span className="text-dimmer">· {r.kind}/{r.criticality}</span></div>
                    <div className="text-dimmer">{r.question || r.message}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* a. Identity */}
        <div className={sectionCls}>
          <div className="text-[10px] uppercase tracking-wider text-dim">Identity</div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            <div>
              <label className={labelCls}>ID (slug)</label>
              <input
                value={id}
                onChange={(e) => setId(e.target.value)}
                placeholder="ema_rsi_demo"
                className={inputCls}
                data-testid="author-id"
              />
              {!idValid && (
                <div className="text-[10px] text-rose-300 mt-1">
                  must match ^[a-z][a-z0-9_]* (lowercase, start with a letter)
                </div>
              )}
            </div>
            <div>
              <label className={labelCls}>Name</label>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="EMA RSI Demo"
                className={inputCls}
                data-testid="author-name"
              />
            </div>
          </div>
          <div>
            <label className={labelCls}>Description</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={2}
              placeholder="What this strategy does…"
              className={inputCls}
              data-testid="author-description"
            />
          </div>
        </div>

        {/* b. Parameters */}
        <div className={sectionCls}>
          <div className="flex items-center justify-between">
            <div className="text-[10px] uppercase tracking-wider text-dim">Parameters</div>
            <button onClick={addParam} className="text-[11px] text-info flex items-center gap-1" data-testid="author-add-param">
              <Plus className="w-3 h-3" /> Add parameter
            </button>
          </div>
          {params.length === 0 && <div className="text-[11px] text-dimmer">No parameters.</div>}
          {params.map((p, i) => (
            <div key={i} className="flex items-end gap-2" data-testid={`author-param-${i}`}>
              <div className="flex-1">
                <label className={labelCls}>name</label>
                <input value={p.name} onChange={(e) => setParamField(i, "name", e.target.value)} className={inputCls} />
              </div>
              <div className="w-24">
                <label className={labelCls}>type</label>
                <select value={p.type} onChange={(e) => setParamField(i, "type", e.target.value)} className={inputCls}>
                  {paramTypes.map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
              </div>
              {p.type === "bool" ? (
                <div className="flex items-center h-[30px] px-1">
                  <label className="flex items-center gap-1 text-[11px] text-dim">
                    <input
                      type="checkbox"
                      checked={!!p.default}
                      onChange={(e) => setParamField(i, "default", e.target.checked)}
                    />
                    default
                  </label>
                </div>
              ) : (
                <>
                  <div className="w-20">
                    <label className={labelCls}>min</label>
                    <input type="number" value={p.min} onChange={(e) => setParamField(i, "min", e.target.value)} className={inputCls} />
                  </div>
                  <div className="w-20">
                    <label className={labelCls}>max</label>
                    <input type="number" value={p.max} onChange={(e) => setParamField(i, "max", e.target.value)} className={inputCls} />
                  </div>
                  <div className="w-20">
                    <label className={labelCls}>default</label>
                    <input type="number" value={p.default} onChange={(e) => setParamField(i, "default", e.target.value)} className={inputCls} />
                  </div>
                </>
              )}
              <button onClick={() => removeParam(i)} className="text-dimmer hover:text-rose-300 h-[30px] px-1" aria-label="Remove parameter">
                <X className="w-4 h-4" />
              </button>
            </div>
          ))}
        </div>

        {/* c. Entry CE */}
        <ConditionSection
          title="Entry — Calls (CE)"
          testid="ce"
          list={entryCe}
          ops={ceOps}
          columns={columns}
          opsList={ops}
        />

        {/* d. Entry PE */}
        <ConditionSection
          title="Entry — Puts (PE)"
          testid="pe"
          list={entryPe}
          ops={peOps}
          columns={columns}
          opsList={ops}
        />

        {/* e. Gates */}
        <div className={sectionCls}>
          <div className="text-[10px] uppercase tracking-wider text-dim">Gates</div>
          <div>
            <label className={labelCls}>Skip these regimes</label>
            <div className="flex flex-wrap gap-1.5">
              {regimes.length === 0 && <span className="text-[11px] text-dimmer">No regimes in catalog.</span>}
              {regimes.map((r) => {
                const on = skipRegimes.includes(r);
                return (
                  <button
                    key={r}
                    onClick={() => toggleRegime(r)}
                    className={`text-[11px] px-2.5 py-1 rounded-full border ${
                      on ? "bg-info/15 border-info/50 text-foreground" : "bg-bg-2 border-line text-dim"
                    }`}
                    data-testid={`author-regime-${r}`}
                  >
                    {r}
                  </button>
                );
              })}
            </div>
          </div>
          <div className="w-40">
            <label className={labelCls}>Cooldown bars</label>
            <input
              type="number"
              value={cooldownBars}
              onChange={(e) => setCooldownBars(e.target.value)}
              className={inputCls}
              data-testid="author-cooldown"
            />
          </div>
        </div>

        {/* f. Exits */}
        <div className={sectionCls}>
          <div className="text-[10px] uppercase tracking-wider text-dim">Exits</div>
          <div className="text-[10px] text-dimmer">Only filled fields are sent.</div>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
            {EXIT_FIELDS.map((f) => (
              <div key={f.key}>
                <label className={labelCls}>{f.label}</label>
                <input
                  type="number"
                  value={exits[f.key] ?? ""}
                  onChange={(e) => setExits((cur) => ({ ...cur, [f.key]: e.target.value }))}
                  className={inputCls}
                  data-testid={`author-exit-${f.key}`}
                />
              </div>
            ))}
          </div>
        </div>

        {/* Preview panel */}
        {preview && (
          <div className={sectionCls}>
            <div className="text-[10px] uppercase tracking-wider text-dim">Compile preview</div>
            {preview.ok ? (
              <pre
                className="text-[11px] font-mono bg-bg-0 border border-line rounded-md p-2 overflow-auto max-h-72 text-foreground"
                data-testid="author-preview-code"
              >
                {preview.code}
              </pre>
            ) : (
              <ul className="text-[11px] text-rose-300 list-disc pl-4 space-y-0.5" data-testid="author-preview-errors">
                {(preview.errors || ["Unknown compile error"]).map((err, i) => (
                  <li key={i}>{err}</li>
                ))}
              </ul>
            )}
          </div>
        )}

        {/* Install gate caveat note */}
        {ruleSet && ruleSet.decision !== "BUILD" && (
          <div className="text-[11px] text-amber-300" data-testid="install-gate-note">
            {ruleSet.decision === "REJECT"
              ? "Can't install — a core rule isn't buildable. See Feasibility above."
              : ruleSet.decision === "ASK"
              ? "Answer the clarifying question(s) above, then re-check."
              : "Installing with caveats (some rules are backtest-only)."}
          </div>
        )}

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 pt-1">
          <button
            onClick={onPreview}
            disabled={busy}
            className="text-xs font-medium px-3 py-1.5 rounded-md bg-bg-2 border border-line text-foreground disabled:opacity-50"
            data-testid="author-preview-btn"
          >
            Preview code
          </button>
          {overwritePending ? (
            <button
              onClick={() => doInstall(true)}
              disabled={busy}
              className="text-xs font-semibold px-3 py-1.5 rounded-md bg-amber-500/15 border border-amber-500/50 text-foreground disabled:opacity-50"
              data-testid="author-overwrite-btn"
            >
              Overwrite existing?
            </button>
          ) : (
            <button
              onClick={() => doInstall(false)}
              disabled={busy || (ruleSet && ruleSet.decision === "REJECT")}
              className="text-xs font-semibold px-3 py-1.5 rounded-md bg-info/15 border border-info/50 text-foreground disabled:opacity-50"
              data-testid="author-install-btn"
            >
              Install
            </button>
          )}
        </div>

        </>)}

        {/* Full-Python panel */}
        {mode === "python" && (
          <div className={sectionCls}>
            <div className="text-[10px] uppercase tracking-wider text-dim">Generated Python</div>
            <textarea data-testid="author-py-code" value={pyCode}
              onChange={(e) => onPyCodeEdit(e.target.value)} rows={16}
              spellCheck={false}
              className={inputCls + " font-mono text-[11px]"}
              placeholder="Generate from a description above, or paste a StrategyBase module here." />
            {pyNotes ? <div className="text-[11px] text-dim">{pyNotes}</div> : null}
            {pyValidation && (
              <div className="text-[11px]" data-testid="author-py-validation">
                {pyValidation.ok
                  ? <div className="text-emerald-300">✓ passed validation{pyValidation.smoke?.signal_repr ? ` — ${pyValidation.smoke.signal_repr}` : ""}</div>
                  : <ul className="text-rose-300 list-disc pl-4">
                      {(pyValidation.violations?.length ? pyValidation.violations : [pyValidation.smoke?.error || "validation failed"]).map((v, i) => <li key={i}>{v}</li>)}
                    </ul>}
              </div>
            )}
            <div className="flex items-center justify-end gap-2 pt-1">
              <button type="button" onClick={onValidatePython} disabled={pyBusy || !pyCode.trim()}
                data-testid="author-py-validate"
                className="text-xs font-medium px-3 py-1.5 rounded-md bg-bg-2 border border-line text-foreground disabled:opacity-50">
                Validate
              </button>
              <button type="button" onClick={onInstallPython} disabled={!pyValidation?.ok || pyBusy}
                data-testid="author-py-install"
                className="text-xs font-semibold px-3 py-1.5 rounded-md bg-info/15 border border-info/50 text-foreground disabled:opacity-50">
                Install
              </button>
            </div>
          </div>
        )}

      </DialogContent>
    </Dialog>
  );
}

function ConditionSection({ title, testid, list, ops, columns, opsList }) {
  return (
    <div className={sectionCls}>
      <div className="flex items-center justify-between">
        <div className="text-[10px] uppercase tracking-wider text-dim">{title}</div>
        <button onClick={ops.add} className="text-[11px] text-info flex items-center gap-1" data-testid={`author-add-${testid}`}>
          <Plus className="w-3 h-3" /> Add condition
        </button>
      </div>
      {list.length === 0 && <div className="text-[11px] text-dimmer">No conditions.</div>}
      {list.map((c, i) => (
        <div key={i} className="flex items-end gap-2" data-testid={`author-cond-${testid}-${i}`}>
          <div className="flex-1">
            <label className={labelCls}>left</label>
            <select value={c.left} onChange={(e) => ops.set(i, "left", e.target.value)} className={inputCls}>
              <option value="">—</option>
              {columns.map((col) => <option key={col} value={col}>{col}</option>)}
            </select>
          </div>
          <div className="w-24">
            <label className={labelCls}>op</label>
            <select value={c.op} onChange={(e) => ops.set(i, "op", e.target.value)} className={inputCls}>
              <option value="">—</option>
              {opsList.map((op) => <option key={op} value={op}>{op}</option>)}
            </select>
          </div>
          <div className="flex-1">
            <label className={labelCls}>right</label>
            <input
              value={c.right}
              onChange={(e) => ops.set(i, "right", e.target.value)}
              placeholder="ema9 / 55 / param:rsi_thr"
              className={inputCls}
            />
            <div className="text-[9px] text-dimmer mt-0.5">a number, a column name, or param:NAME</div>
          </div>
          <div className="flex-1">
            <label className={labelCls}>label</label>
            <input value={c.label} onChange={(e) => ops.set(i, "label", e.target.value)} className={inputCls} />
          </div>
          <button onClick={() => ops.remove(i)} className="text-dimmer hover:text-rose-300 h-[30px] px-1" aria-label="Remove condition">
            <X className="w-4 h-4" />
          </button>
        </div>
      ))}
    </div>
  );
}
