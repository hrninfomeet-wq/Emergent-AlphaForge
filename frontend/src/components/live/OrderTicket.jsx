import { useState } from "react";
import { CheckCircle, XCircle, Loader2, AlertTriangle } from "lucide-react";
import { api } from "@/lib/api";
import { fmtINR } from "@/lib/fmt";

/**
 * OrderTicket — dry-run + place for a single LIVE_TEST 1-lot option-buy order.
 *
 * Lot is LOCKED to 1.
 * The Place button is disabled unless:
 *   - mode === "LIVE_TEST"
 *   - every dry-run verdict has ok === true
 * Clicking Place triggers a second confirm modal before the real API call.
 */

const UNDERLYINGS = ["NIFTY", "BANKNIFTY", "SENSEX"];
const OPTION_SIDES = ["CE", "PE"];

const LOT_SIZES = { NIFTY: 75, BANKNIFTY: 30, SENSEX: 20 };

export default function OrderTicket({ mode, disabled }) {
  // Form state
  const [underlying, setUnderlying] = useState("NIFTY");
  const [strike, setStrike] = useState("");
  const [optionSide, setOptionSide] = useState("CE");
  const [expiryDate, setExpiryDate] = useState("");
  const [refLtp, setRefLtp] = useState("");
  const [bandPct, setBandPct] = useState("3");

  // Dry-run state
  const [dryRunBusy, setDryRunBusy] = useState(false);
  const [dryRunResult, setDryRunResult] = useState(null); // {would_send, verdicts, client_order_id}
  const [dryRunError, setDryRunError] = useState(null);

  // Place state
  const [showPlaceConfirm, setShowPlaceConfirm] = useState(false);
  const [placeBusy, setPlaceBusy] = useState(false);
  const [placeResult, setPlaceResult] = useState(null);
  const [placeError, setPlaceError] = useState(null);

  const lotSize = LOT_SIZES[underlying] ?? 1;

  const buildContract = () => ({
    underlying,
    strike: parseInt(strike, 10),
    side: optionSide,   // CE / PE
    expiry_date: expiryDate,
    lot_size: lotSize,
  });

  const canDryRun = strike && expiryDate && refLtp && bandPct;

  const handleDryRun = async () => {
    if (!canDryRun) return;
    setDryRunBusy(true);
    setDryRunResult(null);
    setDryRunError(null);
    setShowPlaceConfirm(false);
    setPlaceResult(null);
    setPlaceError(null);
    try {
      const res = await api.dryRunLiveOrder({
        contract: buildContract(),
        side: "B",
        order_kind: "entry",
        lots: 1,
        ref_ltp: parseFloat(refLtp),
        band_pct: parseFloat(bandPct),
        fat_finger_cap: 1,
        levels: {},
      });
      setDryRunResult(res);
    } catch (e) {
      setDryRunError(e?.response?.data?.detail ?? e?.message ?? "Dry-run failed");
    } finally {
      setDryRunBusy(false);
    }
  };

  const allVerdictsPass =
    dryRunResult != null &&
    Array.isArray(dryRunResult.verdicts) &&
    dryRunResult.verdicts.length > 0 &&
    dryRunResult.verdicts.every((v) => v.ok);

  const canPlace = mode === "LIVE_TEST" && allVerdictsPass && !disabled;

  const handlePlaceConfirmed = async () => {
    setPlaceBusy(true);
    setPlaceResult(null);
    setPlaceError(null);
    try {
      const res = await api.placeLiveTestOrder({
        contract: buildContract(),
        side: "B",
        ref_ltp: parseFloat(refLtp),
        band_pct: parseFloat(bandPct),
        levels: {},
      });
      setPlaceResult(res);
      setShowPlaceConfirm(false);
    } catch (e) {
      setPlaceError(e?.response?.data?.detail ?? e?.message ?? "Place order failed");
      setShowPlaceConfirm(false);
    } finally {
      setPlaceBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      {/* Form grid */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        {/* Underlying */}
        <div className="flex flex-col gap-1">
          <label className="text-[10px] uppercase tracking-wider text-dimmer font-semibold">
            Underlying
          </label>
          <select
            value={underlying}
            onChange={(e) => { setUnderlying(e.target.value); setDryRunResult(null); }}
            disabled={disabled}
            className="bg-bg-2 border border-line rounded-md px-2 py-1.5 text-xs font-mono text-foreground focus:outline-none focus:ring-1 focus:ring-info/50 disabled:opacity-50"
          >
            {UNDERLYINGS.map((u) => (
              <option key={u} value={u}>{u}</option>
            ))}
          </select>
        </div>

        {/* Strike */}
        <div className="flex flex-col gap-1">
          <label className="text-[10px] uppercase tracking-wider text-dimmer font-semibold">
            Strike
          </label>
          <input
            type="number"
            value={strike}
            onChange={(e) => { setStrike(e.target.value); setDryRunResult(null); }}
            placeholder="e.g. 23000"
            disabled={disabled}
            className="bg-bg-2 border border-line rounded-md px-2 py-1.5 text-xs font-mono text-foreground placeholder:text-dimmer focus:outline-none focus:ring-1 focus:ring-info/50 disabled:opacity-50"
          />
        </div>

        {/* CE / PE */}
        <div className="flex flex-col gap-1">
          <label className="text-[10px] uppercase tracking-wider text-dimmer font-semibold">
            Type
          </label>
          <div className="flex gap-1">
            {OPTION_SIDES.map((s) => (
              <button
                key={s}
                type="button"
                disabled={disabled}
                onClick={() => { setOptionSide(s); setDryRunResult(null); }}
                className={`flex-1 py-1.5 text-xs font-mono font-semibold rounded-md border transition-colors disabled:opacity-50 ${
                  optionSide === s
                    ? s === "CE"
                      ? "border-emerald-500/60 bg-emerald-500/15 text-emerald-300"
                      : "border-danger/60 bg-danger/15 text-danger"
                    : "border-line bg-bg-2 text-dimmer hover:bg-bg-3"
                }`}
              >
                {s}
              </button>
            ))}
          </div>
        </div>

        {/* Expiry date */}
        <div className="flex flex-col gap-1">
          <label className="text-[10px] uppercase tracking-wider text-dimmer font-semibold">
            Expiry (YYYY-MM-DD)
          </label>
          <input
            type="date"
            value={expiryDate}
            onChange={(e) => { setExpiryDate(e.target.value); setDryRunResult(null); }}
            disabled={disabled}
            className="bg-bg-2 border border-line rounded-md px-2 py-1.5 text-xs font-mono text-foreground focus:outline-none focus:ring-1 focus:ring-info/50 disabled:opacity-50"
          />
        </div>

        {/* Ref LTP (premium) */}
        <div className="flex flex-col gap-1">
          <label className="text-[10px] uppercase tracking-wider text-dimmer font-semibold">
            Ref LTP (premium ₹)
          </label>
          <input
            type="number"
            step="0.05"
            value={refLtp}
            onChange={(e) => { setRefLtp(e.target.value); setDryRunResult(null); }}
            placeholder="e.g. 85.00"
            disabled={disabled}
            className="bg-bg-2 border border-line rounded-md px-2 py-1.5 text-xs font-mono text-foreground placeholder:text-dimmer focus:outline-none focus:ring-1 focus:ring-info/50 disabled:opacity-50"
          />
        </div>

        {/* Band % */}
        <div className="flex flex-col gap-1">
          <label className="text-[10px] uppercase tracking-wider text-dimmer font-semibold">
            Band %
          </label>
          <input
            type="number"
            step="0.5"
            min="0"
            value={bandPct}
            onChange={(e) => { setBandPct(e.target.value); setDryRunResult(null); }}
            disabled={disabled}
            className="bg-bg-2 border border-line rounded-md px-2 py-1.5 text-xs font-mono text-foreground focus:outline-none focus:ring-1 focus:ring-info/50 disabled:opacity-50"
          />
        </div>
      </div>

      {/* Lot size — locked display */}
      <div className="flex items-center gap-3 text-xs font-mono">
        <span className="text-dimmer uppercase tracking-wider text-[10px] font-semibold">Lots</span>
        <span className="px-3 py-1 rounded-md border border-line bg-bg-3 text-dim font-semibold">
          1 (locked)
        </span>
        <span className="text-dimmer">
          = {lotSize} qty @ {refLtp ? fmtINR(parseFloat(refLtp) * lotSize) : "–"} premium
        </span>
      </div>

      {/* Dry-run button */}
      <div className="flex items-center gap-2 flex-wrap">
        <button
          type="button"
          disabled={!canDryRun || dryRunBusy || disabled}
          onClick={handleDryRun}
          className="inline-flex items-center gap-1.5 px-4 py-1.5 rounded-md border border-info/50 bg-info/10 text-info text-xs font-mono font-semibold hover:bg-info/20 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {dryRunBusy ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : (
            <CheckCircle className="w-3.5 h-3.5" />
          )}
          {dryRunBusy ? "Running…" : "Dry-run"}
        </button>

        {dryRunError && (
          <span className="text-xs text-danger font-mono">{dryRunError}</span>
        )}
      </div>

      {/* Verdict list */}
      {dryRunResult && (
        <div className="rounded-lg border border-line bg-bg-2/50 px-3 py-2.5 space-y-1.5">
          <div className="text-[10px] uppercase tracking-wider text-dimmer font-semibold mb-1">
            Dry-run verdicts
            {dryRunResult.client_order_id && (
              <span className="ml-2 text-dimmer normal-case font-mono font-normal">
                ref: {dryRunResult.client_order_id}
              </span>
            )}
          </div>
          {dryRunResult.verdicts?.map((v, i) => (
            <div
              key={i}
              className={`flex items-start gap-2 text-xs font-mono ${v.ok ? "text-emerald-300" : "text-danger"}`}
            >
              {v.ok ? (
                <CheckCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
              ) : (
                <XCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
              )}
              <span>
                <span className="font-semibold">{v.check}</span>
                {v.detail ? <span className="text-dimmer ml-1">— {v.detail}</span> : null}
              </span>
            </div>
          ))}
          {dryRunResult.verdicts?.length === 0 && (
            <div className="text-xs text-dimmer font-mono">No verdicts returned.</div>
          )}
        </div>
      )}

      {/* Place button — disabled unless LIVE_TEST + all verdicts pass */}
      {dryRunResult && (
        <div className="space-y-2">
          {!canPlace && (
            <div className="flex items-center gap-1.5 text-xs text-dimmer font-mono">
              <AlertTriangle className="w-3.5 h-3.5 text-amber-400 shrink-0" />
              {mode !== "LIVE_TEST"
                ? "Switch to LIVE_TEST mode above to enable placing."
                : "All verdicts must pass to place an order."}
            </div>
          )}
          <button
            type="button"
            disabled={!canPlace || placeBusy}
            onClick={() => { setShowPlaceConfirm(true); setPlaceError(null); }}
            className="inline-flex items-center gap-1.5 px-5 py-2 rounded-md border-2 border-danger/70 bg-danger text-white text-sm font-mono font-bold hover:bg-danger/90 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            Place 1 lot — REAL MONEY
          </button>
        </div>
      )}

      {/* Second confirm modal */}
      {showPlaceConfirm && (
        <div className="rounded-lg border-2 border-danger bg-danger/10 px-4 py-3 space-y-3">
          <div className="text-sm font-bold text-danger flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 shrink-0" />
            Final confirm — placing a REAL order
          </div>
          <div className="text-xs text-danger/80 font-mono space-y-0.5">
            <div>
              Underlying: <span className="font-bold">{underlying}</span>{" "}
              Strike: <span className="font-bold">{strike}</span>{" "}
              {optionSide}{" "}
              Expiry: <span className="font-bold">{expiryDate}</span>
            </div>
            <div>
              1 lot ({lotSize} qty) @ ~{refLtp ? fmtINR(parseFloat(refLtp)) : "–"} LTP ±{bandPct}%
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              disabled={placeBusy}
              onClick={handlePlaceConfirmed}
              className="inline-flex items-center gap-1.5 px-4 py-1.5 rounded-md border border-danger/60 bg-danger text-white text-xs font-mono font-bold hover:bg-danger/90 disabled:opacity-50 transition-colors"
            >
              {placeBusy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : null}
              {placeBusy ? "Placing…" : "Confirm — Place Order"}
            </button>
            <button
              type="button"
              disabled={placeBusy}
              onClick={() => setShowPlaceConfirm(false)}
              className="px-3 py-1.5 rounded-md border border-line bg-bg-2 text-dim text-xs font-mono hover:bg-bg-3 transition-colors"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Place result */}
      {placeResult && (
        <div
          className={`rounded-lg border px-3 py-2.5 text-xs font-mono space-y-1 ${
            placeResult.placed
              ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
              : "border-danger/40 bg-danger/10 text-danger"
          }`}
        >
          <div className="font-bold">
            {placeResult.placed ? "Order placed" : placeResult.halted ? "Halted" : "Not placed"}
          </div>
          {placeResult.norenordno && (
            <div>Order ID: {placeResult.norenordno}</div>
          )}
          {placeResult.reason && (
            <div className="text-dimmer">Reason: {placeResult.reason}</div>
          )}
          {placeResult.verdicts?.map((v, i) => (
            <div key={i} className={`flex items-start gap-1.5 ${v.ok ? "text-emerald-300" : "text-danger"}`}>
              {v.ok ? <CheckCircle className="w-3 h-3 shrink-0 mt-0.5" /> : <XCircle className="w-3 h-3 shrink-0 mt-0.5" />}
              <span>{v.check}{v.detail ? ` — ${v.detail}` : ""}</span>
            </div>
          ))}
        </div>
      )}

      {placeError && (
        <div className="text-xs text-danger font-mono px-2 py-1 rounded border border-danger/30 bg-danger/10">
          {placeError}
        </div>
      )}
    </div>
  );
}
