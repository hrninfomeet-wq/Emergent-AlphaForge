import React, { useEffect, useRef, useState } from "react";
import { conditionalApi } from "@/lib/conditionalApi";

const fieldClass = "w-full rounded border border-border bg-background px-2 py-1.5 text-sm";
const buttonClass = "rounded border border-border px-3 py-2 text-sm disabled:opacity-40";
const initial = {
  mode: "paper", underlying: "NIFTY", option_type: "CE", expiry: "",
  strike_kind: "ATM", strike_offset: "1", strike_price: "", lots: "1", band_pct: "3", max_entry_premium: "",
  spot_enabled: false, spot_direction: "below", spot_price: "", spot_mode: "cross",
  premium_enabled: false, premium_direction: "rise", premium_value: "", premium_unit: "points",
  validity: "minutes", valid_minutes: "30", valid_until: "14:50",
  stop_value: "10", stop_unit: "percent", target_enabled: false, target_value: "20", target_unit: "percent",
  trailing_enabled: false, trail_activation: "10", trail_distance: "5", trail_step: "1",
  lock_enabled: false, lock_activation: "15", lock_profit: "5", lock_step: "5", lock_raise: "5",
  square_off: "15:00", entry_timeout_seconds: "15", max_reprices: "1", stale_seconds: "5", failsafe: "flatten",
};

function number(value, label) {
  if (String(value).trim() === "" || !Number.isFinite(Number(value))) throw new Error(`${label} must be a number.`);
  return Number(value);
}

export function conditionalSpec(f) {
  return {
    mode: f.mode, underlying: f.underlying, option_type: f.option_type, expiry: f.expiry,
    strike: { kind: f.strike_kind, offset: ["ITM", "OTM"].includes(f.strike_kind) ? number(f.strike_offset, "Strike offset") : 0,
      ...(f.strike_kind === "DIRECT" ? { price: number(f.strike_price, "Strike price") } : {}) },
    lots: number(f.lots, "Lots"), band_pct: number(f.band_pct, "Band"),
    ...(String(f.max_entry_premium ?? "").trim() !== "" ? { max_entry_premium: number(f.max_entry_premium, "Maximum entry premium") } : {}),
    spot_condition: f.spot_enabled ? { direction: f.spot_direction, price: number(f.spot_price, "Spot level"), mode: f.spot_mode } : null,
    premium_condition: f.premium_enabled ? { direction: f.premium_direction, value: number(f.premium_value, "Premium move"), unit: f.premium_unit } : null,
    ...(f.validity === "minutes" ? { valid_minutes: number(f.valid_minutes, "Validity") } : { valid_until: f.valid_until }),
    exits: {
      stop: { value: number(f.stop_value, "Stop loss"), unit: f.stop_unit },
      target: f.target_enabled ? { value: number(f.target_value, "Target"), unit: f.target_unit } : null,
      trailing: f.trailing_enabled ? { activation: number(f.trail_activation, "Trail activation"), distance: number(f.trail_distance, "Trail distance"), step: number(f.trail_step, "Trail step") } : null,
      profit_lock: f.lock_enabled ? { activation: number(f.lock_activation, "Profit-lock activation"), profit: number(f.lock_profit, "Locked profit"), step: number(f.lock_step, "Profit-lock step"), raise_by: number(f.lock_raise, "Profit-lock raise") } : null,
      square_off: f.square_off,
    },
    entry_timeout_seconds: number(f.entry_timeout_seconds, "Entry timeout"), max_reprices: number(f.max_reprices, "Maximum reprices"),
    stale_seconds: number(f.stale_seconds, "Stale-feed threshold"), failsafe: f.failsafe,
  };
}

function errorText(error) {
  const detail = error?.response?.data?.detail;
  return typeof detail === "string" ? detail : detail ? JSON.stringify(detail) : error?.message || "Request failed. Refresh status before trying again.";
}
function timeText(value) {
  if (!value) return "Not supplied";
  const date = new Date(typeof value === "number" ? value * 1000 : value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString("en-IN", { timeZone: "Asia/Kolkata" }) + " IST";
}
function textValue(value) {
  if (value === null || value === undefined) return "Not supplied";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}
function contractName(contract) {
  if (!contract) return "Not supplied";
  return textValue(contract.trading_symbol || contract.tsym || [contract.underlying, contract.strike, contract.option_type, contract.expiry].filter(Boolean).join(" ") || contract.instrument_key || contract);
}
function eventText(record) {
  if (!record || typeof record !== "object") return textValue(record);
  const details = Object.entries(record).filter(([key]) => !["event", "at"].includes(key))
    .map(([key, value]) => `${key.replaceAll("_", " ")}: ${textValue(value)}`).join(" · ");
  return `${record.at != null ? `${timeText(record.at)} — ` : ""}${textValue(record.event)}${details ? ` · ${details}` : ""}`;
}
function alertText(record) {
  if (!record || typeof record !== "object") return textValue(record);
  return `${record.at != null ? `${timeText(record.at)} — ` : ""}${textValue(record.message ?? record)}`;
}

export default function ConditionalOrderPanel() {
  const [form, setForm] = useState(initial);
  const [metadata, setMetadata] = useState(null);
  const [snapshot, setSnapshot] = useState(null);
  const [preview, setPreview] = useState(null);
  const [previewSpec, setPreviewSpec] = useState(null);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [statusError, setStatusError] = useState("");
  const [notice, setNotice] = useState("");
  const mounted = useRef(true);
  const revision = useRef(0);
  const mutating = useRef(false);

  useEffect(() => {
    mounted.current = true;
    let active = true, timer;
    const refresh = async () => {
      try {
        const next = await conditionalApi.list();
        if (active) { setSnapshot(next); setStatusError(""); }
      } catch (e) { if (active) setStatusError(errorText(e)); }
      finally { if (active) timer = setTimeout(refresh, 3000); }
    };
    refresh();
    return () => { active = false; mounted.current = false; clearTimeout(timer); };
  }, []);

  useEffect(() => {
    let active = true;
    setMetadata(null);
    conditionalApi.metadata(form.underlying).then(next => { if (active) setMetadata(next); })
      .catch(e => { if (active) setError(errorText(e)); });
    return () => { active = false; };
  }, [form.underlying]);

  function update(name, value) {
    revision.current += 1;
    setForm(old => ({ ...old, [name]: value, ...(name === "underlying" ? { expiry: "", strike_price: "" } : {}) }));
    setPreview(null); setPreviewSpec(null); setConfirmed(false); setNotice("");
  }
  const control = snapshot?.controls?.[form.mode];
  const latched = control === true || Boolean(control?.latched || control?.blocked || control?.blocked_until_reset);
  const liveAvailable = (snapshot?.live_available ?? metadata?.live_available) === true;
  const canPreview = metadata && form.expiry && !latched && (form.mode !== "live" || (liveAvailable && String(form.max_entry_premium).trim() !== ""));

  async function mutate(fn, success) {
    if (mutating.current) return;
    mutating.current = true; setBusy(true); setError(""); setNotice("");
    try {
      const result = await fn();
      if (mounted.current) {
        setNotice(success);
        try {
          const next = await conditionalApi.list();
          if (mounted.current) { setSnapshot(next); setStatusError(""); }
        } catch (e) { if (mounted.current) setStatusError(errorText(e)); }
      }
      return result;
    } catch (e) {
      if (mounted.current) setError(errorText(e));
    } finally {
      mutating.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  async function prepare(event) {
    event.preventDefault();
    if (!canPreview || mutating.current) return;
    let spec;
    try { spec = conditionalSpec(form); } catch (e) { setError(errorText(e)); return; }
    const version = revision.current;
    setPreview(null); setConfirmed(false);
    const result = await mutate(() => conditionalApi.preview(spec), "");
    if (mounted.current && result && version === revision.current) { setPreview(result); setPreviewSpec(spec); }
  }
  async function arm() {
    if (!preview || !previewSpec || latched || (previewSpec.mode === "live" && (!confirmed || preview.live_available !== true))) return;
    // Discard the preview before await: no double-click or automatic retry can redeem it.
    const id = preview.preview_id;
    const live = previewSpec.mode === "live";
    setPreview(null); setPreviewSpec(null); setConfirmed(false);
    await mutate(() => conditionalApi.arm(id, live), "Arm request acknowledged. See the server order status below.");
  }
  function input(name, label, type = "number", attrs = {}) {
    return <label className="block text-xs space-y-1" key={name}><span>{label}</span><input className={fieldClass} type={type} value={form[name]} onChange={e => update(name, e.target.value)} {...attrs} /></label>;
  }
  function select(name, label, options) {
    return <label className="block text-xs space-y-1" key={name}><span>{label}</span><select className={fieldClass} value={form[name]} onChange={e => update(name, e.target.value)}>{options.map(o => <option key={Array.isArray(o) ? o[0] : o} value={Array.isArray(o) ? o[0] : o}>{Array.isArray(o) ? o[1] : o}</option>)}</select></label>;
  }
  function toggle(name, label) {
    return <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form[name]} onChange={e => update(name, e.target.checked)} />{label}</label>;
  }
  const units = [["points", "Premium points (₹/unit)"], ["percent", "% of entry fill"]];
  const contracts = metadata?.contracts || [];
  const strikes = [...new Set(contracts.filter(c => c.expiry === form.expiry && c.option_type === form.option_type).map(c => c.strike))].sort((a, b) => a - b);
  const orders = snapshot?.orders || [];
  const health = snapshot?.health;
  const healthLabel = health?.stale ? "Heartbeat stale — monitoring unverified" : health?.running ? "Running" : "Not running";

  return <div className="space-y-4">
    <p className="text-sm text-muted-foreground">Conditions and exits run on the server. Paper is the default. Flattrade executes live orders; Upstox supplies market data.</p>
    <p className="text-xs text-muted-foreground">New conditional-order workflow — live market validation pending.</p>
    <p className="rounded border border-amber-500/60 bg-amber-500/10 p-3 text-sm">Software-only protection may be required. A stop is not a guaranteed maximum loss. A stopped backend or unusable broker session can prevent flattening; monitor unresolved alerts.</p>
    {metadata?.warning && <p className="text-sm text-amber-500">{textValue(metadata.warning)}</p>}
    {error && <p role="alert" className="text-sm text-red-500">{error}</p>}
    {statusError && <p role="alert" className="text-sm text-red-500">Server status unavailable: {statusError}. Last displayed status may be stale.</p>}
    {notice && <p role="status" className="text-sm">{notice}</p>}
    <form onSubmit={prepare} className="space-y-4">
      <fieldset disabled={busy} className="space-y-4">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {select("mode", "Execution mode", [["paper", "Paper — simulated"], ["live", "Live — real money"]])}
          {select("underlying", "Underlying", [["NIFTY", "NIFTY 50"], ["BANKNIFTY", "BANK NIFTY"], ["SENSEX", "SENSEX"]])}
          {select("option_type", "Option type", ["CE", "PE"])}
          {select("expiry", "Expiry from instrument master", [["", metadata ? "Select expiry" : "Loading master…"], ...(metadata?.expiries || [])])}
          {select("strike_kind", "Strike selection", [["ATM", "ATM at trigger"], ["ITM", "ITM at trigger"], ["OTM", "OTM at trigger"], ["DIRECT", "Choose strike"]])}
          {["ITM", "OTM"].includes(form.strike_kind) && input("strike_offset", "Strike steps", "number", { min: 1, step: 1, required: true })}
          {form.strike_kind === "DIRECT" && select("strike_price", "Strike price from master", [["", "Select strike"], ...strikes.map(v => [String(v), String(v)])])}
          {input("lots", "Lots", "number", { min: 1, step: 1, required: true })}
          {input("band_pct", "Band % of execution premium", "number", { min: 0, step: "any", required: true })}
          {input("max_entry_premium", "Maximum entry premium (₹/unit)", "number", { min: 0, step: "any", required: form.mode === "live" })}
        </div>
        <p className="text-xs text-muted-foreground">BUY marketable LIMIT uses a 0.5% buffer capped by Band %, rounded to the contract tick. No market order is sent. A maximum entry premium is required for live orders and caps the debit even when the trigger resolves a different strike.</p>
        <fieldset className="rounded border border-border p-3 space-y-3"><legend className="text-sm px-1">Entry conditions — all selected conditions must hold</legend>
          {toggle("spot_enabled", "Use underlying level")}
          {form.spot_enabled && <div className="grid grid-cols-3 gap-3">{select("spot_direction", "Spot direction", [["above", "Above"], ["below", "Below"]])}{input("spot_price", "Underlying spot level", "number", { min: 0, step: "any", required: true })}{select("spot_mode", "Spot condition mode", [["cross", "Cross — edge triggered"], ["level", "Level — already true allowed"]])}</div>}
          {toggle("premium_enabled", "Use option premium move")}
          {form.premium_enabled && <><div className="grid grid-cols-3 gap-3">{select("premium_direction", "Premium direction", [["rise", "Rises"], ["fall", "Falls"]])}{input("premium_value", "Premium move", "number", { min: 0, step: "any", required: true })}{select("premium_unit", "Premium move units", [["points", "Premium points"], ["percent", "% of arm reference"]])}</div><p className="text-xs">Watches the named arm-reference contract below. The execution contract can differ when ATM/ITM/OTM is resolved at trigger.</p></>}
          {!form.spot_enabled && !form.premium_enabled && <p className="text-xs">With no price condition selected, the next eligible server evaluation can enter immediately.</p>}
          <div className="grid grid-cols-2 gap-3">{select("validity", "Entry validity", [["minutes", "Minutes after arm"], ["until", "Until time (IST)"]])}{form.validity === "minutes" ? input("valid_minutes", "Valid for minutes", "number", { min: 1, step: 1, required: true }) : input("valid_until", "Valid until (IST)", "time", { required: true })}</div>
        </fieldset>
        <fieldset className="rounded border border-border p-3 space-y-3"><legend className="text-sm px-1">Exits — measured from actual entry fill</legend>
          <div className="grid grid-cols-2 gap-3">{input("stop_value", "Stop loss", "number", { min: 0, step: "any", required: true })}{select("stop_unit", "Stop loss units", units)}</div>
          {toggle("target_enabled", "Use profit target")}
          {form.target_enabled && <div className="grid grid-cols-2 gap-3">{input("target_value", "Target", "number", { min: 0, step: "any", required: true })}{select("target_unit", "Target units", units)}</div>}
          {toggle("trailing_enabled", "Use trailing stop")}
          {form.trailing_enabled && <div className="grid grid-cols-3 gap-3">{input("trail_activation", "Trail activation (profit points)", "number", { min: 0, step: "any", required: true })}{input("trail_distance", "Trail distance (points)", "number", { min: 0, step: "any", required: true })}{input("trail_step", "Trail step (points)", "number", { min: 0, step: "any", required: true })}</div>}
          {toggle("lock_enabled", "Use profit-lock trailing")}
          {form.lock_enabled && <div className="grid grid-cols-2 md:grid-cols-4 gap-3">{input("lock_activation", "Profit-lock activation (points)", "number", { min: 0, step: "any", required: true })}{input("lock_profit", "Profit to lock (points)", "number", { min: 0, step: "any", required: true })}{input("lock_step", "Profit-lock step (points)", "number", { min: 0, step: "any", required: true })}{input("lock_raise", "Raise lock by (points)", "number", { min: 0, step: "any", required: true })}</div>}
          {input("square_off", "Forced square-off (IST)", "time", { required: true })}
          <p className="text-xs">Stops only tighten. Stop and target are mutually exclusive exit winners; orders remain open until exit fills are confirmed.</p>
        </fieldset>
        <details className="rounded border border-border p-3"><summary className="text-sm cursor-pointer">Timeout and failsafe policy</summary><div className="grid grid-cols-2 gap-3 mt-3">
          {input("entry_timeout_seconds", "Unfilled entry timeout (seconds)", "number", { min: 1, step: 1, required: true })}
          {input("max_reprices", "Maximum cancel/reprices", "number", { min: 0, step: 1, required: true })}
          {input("stale_seconds", "Stale-feed threshold (seconds)", "number", { min: 1, step: 1, required: true })}
          {select("failsafe", "Feed/session failure action", [["flatten", "Alert + attempt flatten"], ["alert", "Alert only — exposure remains"]])}
        </div></details>
        {form.mode === "live" && !liveAvailable && <p role="alert" className="text-sm text-red-500">Live arming unavailable: {snapshot?.live_reason || metadata?.live_reason || "server readiness has not been established"}.</p>}
        {latched && <p role="alert" className="text-sm text-red-500">{form.mode} conditional orders are latched. Arming is blocked.</p>}
        <button type="submit" className={buttonClass} disabled={!canPreview || busy}>Preview conditional order</button>
      </fieldset>
    </form>
    {preview && previewSpec && <section aria-label="Conditional order confirmation" className="rounded border border-border p-4 space-y-3">
      <h3 className="font-semibold">Review before arming {previewSpec.mode === "live" ? "— REAL MONEY" : "— PAPER"}</h3>
      <p>{previewSpec.underlying} {previewSpec.option_type} · expiry {previewSpec.expiry} · {previewSpec.lots} lot(s) · {previewSpec.strike.kind}{["ITM", "OTM"].includes(previewSpec.strike.kind) ? previewSpec.strike.offset : ""}{previewSpec.strike.kind === "DIRECT" ? ` ${previewSpec.strike.price}` : " resolved at trigger"}</p>
      <p>Indicative reference contract: <strong>{contractName(preview.reference?.contract)}</strong> · preview premium ₹{textValue(preview.reference?.premium)} · spot {textValue(preview.reference?.spot)}</p>
      <p className="text-sm">The server captures a fresh reference when this order is armed. That actual reference is then fixed for the premium condition and shown in the order below. An expired preview is rejected.</p>
      <p>Underlying condition: {previewSpec.spot_condition ? `${previewSpec.spot_condition.mode} ${previewSpec.spot_condition.direction} ${previewSpec.spot_condition.price}` : "None"}. Premium condition: {previewSpec.premium_condition ? `${previewSpec.premium_condition.direction} ${previewSpec.premium_condition.value} ${previewSpec.premium_condition.unit} from reference` : "None"}.</p>
      <p>Entry window: {previewSpec.valid_minutes ? `${previewSpec.valid_minutes} minutes from arm` : `until ${previewSpec.valid_until} IST`}; forced exit {previewSpec.exits.square_off} IST. Preview expires: {timeText(preview.expires_at)}.</p>
      <p>Stop: {previewSpec.exits.stop.value} {previewSpec.exits.stop.unit}; target: {previewSpec.exits.target ? `${previewSpec.exits.target.value} ${previewSpec.exits.target.unit}` : "none"}. Trailing: {previewSpec.exits.trailing ? textValue(previewSpec.exits.trailing) : "off"}. Profit-lock: {previewSpec.exits.profit_lock ? textValue(previewSpec.exits.profit_lock) : "off"}.</p>
      <p>Maximum entry premium: {previewSpec.max_entry_premium ? `₹${previewSpec.max_entry_premium}/unit` : "Not set (paper)"}. Planned stop loss: ₹{textValue(preview.summary?.planned_loss ?? preview.summary?.planned_loss_inr)}. Full premium exposure: ₹{textValue(preview.summary?.premium_exposure ?? preview.summary?.premium_exposure_inr)} plus costs. {previewSpec.mode === "live" ? "Exposure is bounded by the maximum entry premium; stop-based loss is not guaranteed." : "Paper values are reference estimates, not guaranteed maximum loss."}</p>
      {preview.summary && <details><summary>Server-confirmed summary</summary><dl className="text-xs space-y-1">{Object.entries(preview.summary).map(([key, value]) => <div key={key}><dt className="inline font-semibold">{key.replaceAll("_", " ")}: </dt><dd className="inline">{textValue(value)}</dd></div>)}</dl></details>}
      {previewSpec.mode === "live" && <label className="flex gap-2 text-sm"><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} />I confirm this live order, its full premium exposure and software-protection risk.</label>}
      <button type="button" className={buttonClass} disabled={busy || latched || (previewSpec.mode === "live" && (!confirmed || preview.live_available !== true))} onClick={arm}>Arm {previewSpec.mode === "live" ? "LIVE order — real money" : "paper order"}</button>
    </section>}
    <section aria-label="Conditional order status" className="space-y-3">
      <div className="flex flex-wrap items-center gap-2"><h3 className="font-semibold mr-auto">Server orders</h3>
        <button type="button" className={buttonClass} disabled={busy} onClick={() => mutate(() => conditionalApi.kill(form.mode), `${form.mode} kill requested. Check remaining exposure below.`)}>Kill {form.mode} conditional orders</button>
        {latched && <button type="button" className={buttonClass} disabled={busy} onClick={() => mutate(() => conditionalApi.reset(form.mode), `${form.mode} reset requested.`)}>Reset {form.mode} conditional latch</button>}
      </div>
      {health && <div className="text-xs space-y-1">
        <p>Engine: {healthLabel}. Last heartbeat: {timeText(health.heartbeat)}.</p>
        {health.watchdog && <p className="text-amber-500">Independent watchdog has taken over recovery. New entries are blocked in this worker.</p>}
        {health.last_error && <p role="alert" className="text-red-500">Engine error: {textValue(health.last_error)}</p>}
      </div>}
      {!snapshot ? <p className="text-sm">Waiting for server status…</p> : orders.length === 0 ? <p className="text-sm">No conditional orders.</p> : <div className="space-y-2">{orders.map(order => {
        const id = order.id || order._id, spec = order.spec || {};
        const terminal = ["CLOSED", "EXPIRED", "CANCELLED", "CANCELED", "REJECTED", "FAILED"].includes(order.status);
        return <article key={id} className="rounded border border-border p-3 text-sm space-y-2">
          <p><strong>{order.status}</strong> · {spec.mode || order.mode} · {spec.underlying} {spec.option_type} · {spec.lots} lot(s)</p>
          <p>Resolved contract: {contractName(order.resolved_contract || order.contract)} · deadline {timeText(order.deadline)}</p>
          <p>Entry filled: {order.filled_quantity ?? 0} units · closed: {order.closed_quantity ?? 0} units · remaining: {order.external_flat ? "Broker reports flat; exit attribution unverified" : Math.max(0, (order.filled_quantity ?? 0) - (order.closed_quantity ?? 0))} · entry fill ₹{textValue(order.entry_fill_price)}</p>
          <p>Protection: {textValue(order.protection || order.summary?.protection)}.</p>
          {order.reference && <p>Actual arm reference: {contractName(order.reference.contract)} · premium ₹{textValue(order.reference.premium)} · spot {textValue(order.reference.spot)}</p>}
          {(Array.isArray(order.alerts) ? order.alerts : []).map((alert, i) => <p role="alert" className="text-red-500" key={i}>{alertText(alert)}</p>)}
          {order.last_error && <p role="alert" className="text-red-500">{textValue(order.last_error)}</p>}
          <details><summary>Order events and protection</summary><p>Protection: {textValue(order.exit_state || order.protection)}</p><ol className="list-decimal pl-5">{(Array.isArray(order.events) ? order.events : []).map((event, i) => <li key={i}>{eventText(event)}</li>)}</ol></details>
          {!terminal && <button type="button" className={buttonClass} disabled={busy} onClick={() => mutate(() => conditionalApi.cancel(id), "Cancel requested. Filled exposure remains tracked until exit confirmation.")}>Cancel / close {id}</button>}
        </article>;
      })}</div>}
    </section>
  </div>;
}
