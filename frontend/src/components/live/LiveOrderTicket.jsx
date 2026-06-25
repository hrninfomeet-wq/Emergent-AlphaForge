import { useCallback, useEffect, useState } from "react";
import { CheckCircle, XCircle, Loader2, AlertTriangle, Send } from "lucide-react";
import { api } from "@/lib/api";
import { fmtINR } from "@/lib/fmt";
import PayoffChart from "./PayoffChart";

/**
 * LiveOrderTicket — exchange-aware order ticket that builds a multi-lot option
 * order, previews it through the backend choke-point, and queues it for the
 * two-man approval flow. This component NEVER places a real order itself — the
 * parent owns the approval queue and the explicit approve step.
 *
 * Props:
 *   mode      — the live execution mode string (informational; the queue/approve
 *               gate is enforced server-side, so this ticket stays usable for
 *               building + queueing regardless of mode).
 *   disabled  — when true, every input + button is disabled.
 *   onQueued(res) — called after a SUCCESSFUL createOrderApproval with
 *               res = { approval_id, token, summary }. The parent adds it to its
 *               pending-approval queue (the token is shown ONCE here and never
 *               returned by the list endpoint).
 *
 * Exchange rules drive the Order-Type and Product selects (CO/BO are not offered
 * because the backend rules exclude them). The rules also surface the lot size,
 * freeze qty, tick, and expiry cadence as read-only chips.
 */

const UNDERLYINGS = ["NIFTY", "BANKNIFTY", "SENSEX"];
const OPTION_SIDES = ["CE", "PE"];

// Local fallback while server rules are loading or unavailable. PREFER server rules.
// Values MIRROR the backend flattrade_symbol.EXCHANGE_RULES (incl. the snake_case
// expiry_cadence tokens the server returns) so the offline preview matches.
const DEFAULT_RULES = {
  NIFTY: { exch: "NFO", lot_size: 65, freeze_qty: 1800, tick: 0.05, products: ["NRML", "MIS"], price_types: ["LIMIT", "MARKET", "SL-LMT"], expiry_cadence: "weekly_tue" },
  BANKNIFTY: { exch: "NFO", lot_size: 30, freeze_qty: 600, tick: 0.05, products: ["NRML", "MIS"], price_types: ["LIMIT", "MARKET", "SL-LMT"], expiry_cadence: "monthly_last_tue" },
  SENSEX: { exch: "BFO", lot_size: 20, freeze_qty: 1000, tick: 0.05, products: ["NRML", "MIS"], price_types: ["LIMIT", "MARKET", "SL-LMT"], expiry_cadence: "weekly_thu" },
};

// Human labels for the snake_case expiry_cadence tokens the backend returns.
const CADENCE_LABELS = {
  weekly_tue: "weekly (Tue)",
  monthly_last_tue: "monthly (last Tue)",
  weekly_thu: "weekly (Thu)",
};

function cadenceLabel(token) {
  return CADENCE_LABELS[token] ?? token ?? "–";
}

function defaultRulesFor(underlying) {
  return DEFAULT_RULES[underlying] ?? DEFAULT_RULES.NIFTY;
}

const FAT_FINGER_CAP = 50;
const BUFFER_PCT = 0.5;

export default function LiveOrderTicket({ mode, disabled, onQueued }) {
  // ── Form state ──────────────────────────────────────────────────────────
  const [underlying, setUnderlying] = useState("NIFTY");
  const [strike, setStrike] = useState("");
  const [optionSide, setOptionSide] = useState("CE");
  const [expiryDate, setExpiryDate] = useState("");
  const [side, setSide] = useState("B"); // B = Buy, S = Sell
  const [orderType, setOrderType] = useState("LIMIT");
  const [product, setProduct] = useState("MIS");
  const [lots, setLots] = useState("1");
  const [refLtp, setRefLtp] = useState("");
  const [bandPct, setBandPct] = useState("3");
  // Protective stop % (software guard + the SL-LMT trigger). Defaults to 50% so a
  // filled position is never left unguarded.
  const [stopPct, setStopPct] = useState("50");
  const [targetPct, setTargetPct] = useState(""); // optional guard target %

  // ── Exchange rules ──────────────────────────────────────────────────────
  const [rules, setRules] = useState(() => defaultRulesFor("NIFTY"));
  const [rulesLoading, setRulesLoading] = useState(false);
  const [rulesError, setRulesError] = useState(null);

  // ── ATM-suggest state ───────────────────────────────────────────────────
  const [atmBusy, setAtmBusy] = useState(false);
  const [atmNote, setAtmNote] = useState(null);

  // ── Fetch-premium state ─────────────────────────────────────────────────
  const [fetchBusy, setFetchBusy] = useState(false);
  const [premiumBadge, setPremiumBadge] = useState(null); // "live" | "last_candle" | null
  const [premiumNote, setPremiumNote] = useState(null);

  // ── Preview state ───────────────────────────────────────────────────────
  const [previewBusy, setPreviewBusy] = useState(false);
  const [previewResult, setPreviewResult] = useState(null); // { ok, children, verdicts, client_order_id }
  const [previewError, setPreviewError] = useState(null);

  // ── Place state (direct one-click place) ──────────────────────────────────
  const [queueBusy, setQueueBusy] = useState(false);  // "placing…" busy flag
  const [queueResult, setQueueResult] = useState(null); // { ok:false, verdicts } re-check failure
  const [queueError, setQueueError] = useState(null);
  const [queuedConfirm, setQueuedConfirm] = useState(false);
  const [showPlaceConfirm, setShowPlaceConfirm] = useState(false);
  const [placeResult, setPlaceResult] = useState(null); // executor result {placed, norenordno, reason, ...}

  // Any form change invalidates a prior preview/queue result so a stale green
  // preview can never be queued.
  const resetTransient = useCallback(() => {
    setPreviewResult(null);
    setPreviewError(null);
    setQueueResult(null);
    setQueueError(null);
    setQueuedConfirm(false);
    setShowPlaceConfirm(false);
    setPlaceResult(null);
  }, []);

  // ── Load exchange rules on mount + whenever underlying changes ───────────
  useEffect(() => {
    let cancelled = false;
    setRulesLoading(true);
    setRulesError(null);
    api
      .getOrderRules(underlying)
      .then((res) => {
        if (cancelled || !res) return;
        setRules(res);
        // Reconcile current selects against the server price_types/products.
        const priceTypes = Array.isArray(res.price_types) ? res.price_types : [];
        const products = Array.isArray(res.products) ? res.products : [];
        if (priceTypes.length > 0) {
          setOrderType((cur) => (priceTypes.includes(cur) ? cur : priceTypes[0]));
        }
        if (products.length > 0) {
          setProduct((cur) => (products.includes(cur) ? cur : products[0]));
        }
      })
      .catch((e) => {
        if (cancelled) return;
        // Fall back to a sensible default but keep a note for the chip strip.
        setRules(defaultRulesFor(underlying));
        setRulesError(
          e?.response?.data?.detail ?? e?.message ?? "rules unavailable — using defaults"
        );
      })
      .finally(() => {
        if (!cancelled) setRulesLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [underlying]);

  // Effective rules + derived option lists (always non-empty so selects render).
  const priceTypes =
    Array.isArray(rules?.price_types) && rules.price_types.length > 0
      ? rules.price_types
      : defaultRulesFor(underlying).price_types;
  const products =
    Array.isArray(rules?.products) && rules.products.length > 0
      ? rules.products
      : defaultRulesFor(underlying).products;

  const lotSize = Number(rules?.lot_size) || defaultRulesFor(underlying).lot_size;
  const freezeQty = Number(rules?.freeze_qty) || 0;

  const lotsNum = parseInt(lots, 10);
  const qty = Number.isFinite(lotsNum) && lotsNum > 0 ? lotsNum * lotSize : 0;
  const willSplit = freezeQty > 0 && qty > freezeQty;
  const childCount = willSplit ? Math.ceil(qty / freezeQty) : 1;

  const isMarket = orderType === "MARKET";
  const isSlLmt = orderType === "SL-LMT";

  // ── ATM suggest ─────────────────────────────────────────────────────────
  const handleAtmSuggest = async () => {
    if (disabled) return;
    setAtmBusy(true);
    setAtmNote(null);
    setPremiumBadge(null);
    setPremiumNote(null);
    resetTransient();
    try {
      const res = await api.getAtmSuggest({ underlying, side: optionSide });
      if (res && res.atm_strike != null) {
        setStrike(String(res.atm_strike));
        if (res.expiry) setExpiryDate(res.expiry);
        if (res.premium != null) {
          setRefLtp(res.premium.toFixed(2));
          setPremiumBadge(res.premium_source === "live_tick" ? "live" : "last_candle");
        }
        const spotStr = res.spot != null ? ` @ spot ${Math.round(res.spot)}` : "";
        setAtmNote(`ATM${spotStr}`);
      } else {
        const reason = res.reason ?? "no market data";
        setAtmNote(
          reason === "no_spot"
            ? "no spot / market data unavailable — pick a strike manually"
            : reason === "no_contracts"
            ? "no contracts found — pick a strike manually"
            : `${reason} — pick a strike manually`
        );
      }
    } catch (e) {
      setAtmNote(
        e?.response?.data?.detail ?? e?.message ?? "ATM lookup failed — enter manually"
      );
    } finally {
      setAtmBusy(false);
    }
  };

  // ── Fetch premium ───────────────────────────────────────────────────────
  const canFetchPremium = !!(underlying && strike && expiryDate);

  const handleFetchPremium = async () => {
    if (!canFetchPremium || disabled) return;
    setFetchBusy(true);
    setPremiumBadge(null);
    setPremiumNote(null);
    try {
      const res = await api.getOptionPremium({
        underlying,
        strike: parseInt(strike, 10),
        expiry_date: expiryDate,
        side: optionSide,
      });
      if (res && res.premium != null) {
        setRefLtp(res.premium.toFixed(2));
        setPremiumBadge(res.source === "live_tick" ? "live" : "last_candle");
        setPremiumNote(null);
        resetTransient();
      } else {
        setPremiumBadge(null);
        setPremiumNote(
          res.reason === "contract_not_found"
            ? "no premium found — contract not found, enter manually"
            : "no premium found — enter manually"
        );
      }
    } catch (e) {
      setPremiumBadge(null);
      setPremiumNote(
        e?.response?.data?.detail ?? e?.message ?? "fetch failed — enter manually"
      );
    } finally {
      setFetchBusy(false);
    }
  };

  // ── Payload builder (shared by preview + place) ─────────────────────────
  // levels carry BOTH the SL-LMT order trigger (validate_and_build) AND the
  // software guard's protective stop/target (_make_arm → build_monitor_state).
  // stop_pct defaults to the guard default (50%) so a position is never unguarded.
  const buildLevels = () => {
    const sp = parseFloat(stopPct);
    const tp = parseFloat(targetPct);
    const levels = { stop_pct: Number.isFinite(sp) && sp > 0 ? sp : 50 };
    if (Number.isFinite(tp) && tp > 0) levels.target_pct = tp;
    return levels;
  };

  const buildPayload = () => ({
    underlying,
    strike: parseInt(strike, 10),
    option_type: optionSide,
    side,
    expiry_date: expiryDate,
    lots: parseInt(lots, 10),
    order_type: orderType,
    product,
    ref_ltp: isMarket ? null : parseFloat(refLtp),
    band_pct: parseFloat(bandPct),
    fat_finger_cap: FAT_FINGER_CAP,
    levels: buildLevels(),
    buffer_pct: BUFFER_PCT,
  });

  // Required: strike, expiryDate, lots; refLtp required only when not MARKET.
  const hasRequired =
    !!strike &&
    !!expiryDate &&
    Number.isFinite(lotsNum) &&
    lotsNum > 0 &&
    (isMarket || (refLtp !== "" && Number.isFinite(parseFloat(refLtp))));

  const canPreview = hasRequired && !previewBusy && !disabled;

  const handlePreview = async () => {
    if (!canPreview) return;
    setPreviewBusy(true);
    setPreviewResult(null);
    setPreviewError(null);
    setQueueResult(null);
    setQueueError(null);
    setQueuedConfirm(false);
    try {
      const res = await api.previewLiveOrder(buildPayload());
      setPreviewResult(res);
    } catch (e) {
      setPreviewError(e?.response?.data?.detail ?? e?.message ?? "Preview failed");
    } finally {
      setPreviewBusy(false);
    }
  };

  // Place is only enabled after a preview that returned ok === true.
  const canPlace =
    previewResult != null && previewResult.ok === true && !queueBusy && !disabled;

  // DIRECT PLACE (no separate approval-queue / mode-switch step): on confirm we
  // auto-arm LIVE_TEST, then create + redeem a one-shot approval in one shot. The
  // backend safety chain (choke-point validation, margin, fat-finger, the single
  // executor chokepoint, software-guard registration) is unchanged — only the UI
  // friction (manual mode switch + a separate approval queue) is removed. The
  // REAL-MONEY confirm dialog is the per-trade gate.
  const handlePlaceConfirmed = async () => {
    setQueueBusy(true);
    setQueueResult(null);
    setQueueError(null);
    setQueuedConfirm(false);
    // Track whether we armed LIVE_TEST and whether a real order actually placed, so
    // the `finally` can STAND DOWN (revert to LIVE_OFFLINE) when we armed but the
    // order did NOT place — otherwise a network failure between arm and place would
    // leave the system armed with no UI to disarm. A SUCCESSFUL place self-reverts
    // (the executor consumes the single-shot), so we only revert on non-placement.
    let armed = false;
    let placedOk = false;
    try {
      // 1. arm LIVE_TEST (single-shot)
      try {
        await api.setLiveMode("LIVE_TEST", true);
        armed = true;
      } catch (e) {
        setQueueError(
          `Could not arm LIVE_TEST: ${e?.response?.data?.detail ?? e?.message ?? "mode error"}`
        );
        return;
      }
      // 2. create the approval (re-validates server-side)
      const created = await api.createOrderApproval(buildPayload());
      if (!created?.ok) {
        setQueueResult(created ?? { ok: false, verdicts: [] });
        return;
      }
      // 3. redeem the one-shot token → place via the executor chokepoint
      const placed = await api.approveOrder(created.approval_id, created.token);
      setPlaceResult(placed);
      placedOk = !!placed?.placed;
      if (placedOk) setPreviewResult(null); // clear so it can't double-fire
    } catch (e) {
      setQueueError(e?.response?.data?.detail ?? e?.message ?? "Place failed");
    } finally {
      setShowPlaceConfirm(false);
      // Stand down: armed but nothing placed → revert to a safe mode (best-effort).
      if (armed && !placedOk) {
        try {
          await api.setLiveMode("LIVE_OFFLINE");
        } catch {
          /* best-effort stand-down; the hero Mode tile will still reflect reality on the next poll */
        }
      }
      setQueueBusy(false);
    }
  };

  const inputCls =
    "bg-bg-2 border border-line rounded-md px-2 py-1.5 text-xs font-mono text-foreground placeholder:text-dimmer focus:outline-none focus:ring-1 focus:ring-info/50 disabled:opacity-50";
  const labelCls =
    "text-[10px] uppercase tracking-wider text-dimmer font-semibold";

  return (
    <div className="space-y-4">
      {/* ── Rules strip (read-only chips) ──────────────────────────────── */}
      <div className="flex items-center gap-1.5 flex-wrap text-[10px] font-mono">
        <span className="text-dimmer uppercase tracking-wider font-semibold mr-1">
          Rules
        </span>
        {rulesLoading ? (
          <span className="inline-flex items-center gap-1 text-dimmer">
            <Loader2 className="w-3 h-3 animate-spin" /> loading…
          </span>
        ) : (
          <>
            <RuleChip label="exch" value={rules?.exch ?? "–"} />
            <RuleChip label="lot" value={lotSize} />
            <RuleChip label="freeze" value={freezeQty || "–"} />
            <RuleChip label="tick" value={rules?.tick ?? "–"} />
            <RuleChip label="expiry" value={cadenceLabel(rules?.expiry_cadence)} />
          </>
        )}
        {rulesError && (
          <span className="inline-flex items-center gap-1 text-amber-400" title={rulesError}>
            <AlertTriangle className="w-3 h-3 shrink-0" />
            defaults
          </span>
        )}
      </div>

      {/* ── Form grid ──────────────────────────────────────────────────── */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        {/* Underlying */}
        <div className="flex flex-col gap-1">
          <label className={labelCls}>Underlying</label>
          <select
            value={underlying}
            onChange={(e) => {
              setUnderlying(e.target.value);
              setPremiumBadge(null);
              setPremiumNote(null);
              setAtmNote(null);
              resetTransient();
            }}
            disabled={disabled}
            className={inputCls}
          >
            {UNDERLYINGS.map((u) => (
              <option key={u} value={u}>{u}</option>
            ))}
          </select>
        </div>

        {/* Strike + ATM button */}
        <div className="flex flex-col gap-1">
          <label className={labelCls}>Strike</label>
          <div className="flex items-center gap-1.5">
            <input
              type="number"
              value={strike}
              onChange={(e) => {
                setStrike(e.target.value);
                setPremiumBadge(null);
                setPremiumNote(null);
                setAtmNote(null);
                resetTransient();
              }}
              placeholder="e.g. 23000"
              disabled={disabled}
              className={`min-w-0 flex-1 ${inputCls}`}
            />
            <button
              type="button"
              disabled={atmBusy || disabled}
              onClick={handleAtmSuggest}
              title="Resolve nearest ATM strike, front expiry, and premium"
              className="shrink-0 inline-flex items-center gap-1 px-2 py-1.5 rounded-md border border-info/40 bg-info/10 text-info text-[10px] font-mono font-semibold hover:bg-info/20 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {atmBusy ? <Loader2 className="w-3 h-3 animate-spin" /> : null}
              {atmBusy ? "…" : "ATM"}
            </button>
          </div>
          {atmNote && (
            <span
              className={`text-[10px] font-mono leading-tight ${
                atmNote.startsWith("ATM") ? "text-info/80" : "text-dimmer"
              }`}
            >
              {atmNote}
            </span>
          )}
        </div>

        {/* CE / PE */}
        <div className="flex flex-col gap-1">
          <label className={labelCls}>Type</label>
          <div className="flex gap-1">
            {OPTION_SIDES.map((s) => (
              <button
                key={s}
                type="button"
                disabled={disabled}
                onClick={() => {
                  setOptionSide(s);
                  setPremiumBadge(null);
                  setPremiumNote(null);
                  setAtmNote(null);
                  resetTransient();
                }}
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

        {/* Buy / Sell side */}
        <div className="flex flex-col gap-1">
          <label className={labelCls}>Side</label>
          <div className="flex gap-1">
            {[
              { v: "B", label: "Buy" },
              { v: "S", label: "Sell" },
            ].map((s) => {
              // Long-only: live ENTRIES are option BUYS. SELL is disabled here to
              // match the backend (executor Gate 0 + approve gate both reject
              // side != "B"); exits go through the separate square/exit route, not
              // this ticket, so disabling SELL does not touch the exit path.
              const sellDisabled = s.v === "S";
              return (
                <button
                  key={s.v}
                  type="button"
                  disabled={disabled || sellDisabled}
                  title={
                    sellDisabled
                      ? "Long-only: live entries are option BUYS. To exit, use the square / exit route."
                      : undefined
                  }
                  onClick={() => {
                    setSide(s.v);
                    resetTransient();
                  }}
                  className={`flex-1 py-1.5 text-xs font-mono font-semibold rounded-md border transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
                    side === s.v
                      ? s.v === "B"
                        ? "border-emerald-500/60 bg-emerald-500/15 text-emerald-300"
                        : "border-danger/60 bg-danger/15 text-danger"
                      : "border-line bg-bg-2 text-dimmer hover:bg-bg-3"
                  }`}
                >
                  {s.label}
                </button>
              );
            })}
          </div>
          <span className="text-[10px] font-mono text-dimmer">Long-only — entries are option buys; exit via square route</span>
        </div>

        {/* Expiry date */}
        <div className="flex flex-col gap-1">
          <label className={labelCls}>Expiry (YYYY-MM-DD)</label>
          <input
            type="date"
            value={expiryDate}
            onChange={(e) => {
              setExpiryDate(e.target.value);
              setPremiumBadge(null);
              setPremiumNote(null);
              setAtmNote(null);
              resetTransient();
            }}
            disabled={disabled}
            className={inputCls}
          />
        </div>

        {/* Order Type — options from rules.price_types */}
        <div className="flex flex-col gap-1">
          <label className={labelCls}>Order Type</label>
          <select
            value={orderType}
            onChange={(e) => {
              setOrderType(e.target.value);
              resetTransient();
            }}
            disabled={disabled}
            className={inputCls}
          >
            {priceTypes.map((pt) => (
              <option key={pt} value={pt}>{pt}</option>
            ))}
          </select>
        </div>

        {/* Product — options from rules.products */}
        <div className="flex flex-col gap-1">
          <label className={labelCls}>Product</label>
          <select
            value={product}
            onChange={(e) => {
              setProduct(e.target.value);
              resetTransient();
            }}
            disabled={disabled}
            className={inputCls}
          >
            {products.map((pr) => (
              <option key={pr} value={pr}>{pr}</option>
            ))}
          </select>
        </div>

        {/* Lots */}
        <div className="flex flex-col gap-1">
          <label className={labelCls}>Lots</label>
          <input
            type="number"
            min="1"
            step="1"
            value={lots}
            onChange={(e) => {
              setLots(e.target.value);
              resetTransient();
            }}
            placeholder="1"
            disabled={disabled}
            className={inputCls}
          />
        </div>

        {/* Ref LTP (premium) */}
        <div className="flex flex-col gap-1">
          <label className={labelCls}>
            Ref LTP (premium ₹)
            {isMarket && (
              <span className="ml-1 normal-case font-normal text-dimmer">
                — not required for MARKET
              </span>
            )}
          </label>
          <div className="flex items-center gap-1.5">
            <input
              type="number"
              step="0.05"
              value={refLtp}
              onChange={(e) => {
                setRefLtp(e.target.value);
                setPremiumBadge(null);
                setPremiumNote(null);
                setAtmNote(null);
                resetTransient();
              }}
              placeholder={isMarket ? "optional" : "e.g. 85.00"}
              disabled={disabled || isMarket}
              className={`min-w-0 flex-1 ${inputCls} ${isMarket ? "opacity-50" : ""}`}
            />
            <button
              type="button"
              disabled={!canFetchPremium || fetchBusy || disabled || isMarket}
              onClick={handleFetchPremium}
              title="Fetch live or last-close premium from the backend"
              className="shrink-0 inline-flex items-center gap-1 px-2 py-1.5 rounded-md border border-line bg-bg-3 text-dim text-[10px] font-mono font-semibold hover:bg-bg-2 hover:text-foreground disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {fetchBusy ? <Loader2 className="w-3 h-3 animate-spin" /> : null}
              {fetchBusy ? "…" : "Fetch ₹"}
            </button>
          </div>
          {premiumBadge === "live" && (
            <span className="text-[10px] font-mono font-semibold text-emerald-400">
              ● live tick
            </span>
          )}
          {premiumBadge === "last_candle" && (
            <span className="text-[10px] font-mono font-semibold text-amber-400">
              ● last close
            </span>
          )}
          {premiumNote && (
            <span className="text-[10px] font-mono text-dimmer leading-tight">
              {premiumNote}
            </span>
          )}
        </div>

        {/* Band % */}
        <div className="flex flex-col gap-1">
          <label className={labelCls}>Band %</label>
          <input
            type="number"
            step="0.5"
            min="0"
            value={bandPct}
            onChange={(e) => {
              setBandPct(e.target.value);
              resetTransient();
            }}
            disabled={disabled}
            className={inputCls}
          />
        </div>

        {/* Protective Stop % — the software guard's stop (+ the SL-LMT trigger) */}
        <div className="flex flex-col gap-1">
          <label className={labelCls}>
            Stop %{isSlLmt ? " (SL trigger + guard)" : " (guard)"}
          </label>
          <input
            type="number"
            step="0.5"
            min="0"
            value={stopPct}
            onChange={(e) => {
              setStopPct(e.target.value);
              resetTransient();
            }}
            placeholder="e.g. 50"
            disabled={disabled}
            className={inputCls}
          />
        </div>

        {/* Optional guard Target % */}
        <div className="flex flex-col gap-1">
          <label className={labelCls}>Target % (optional)</label>
          <input
            type="number"
            step="0.5"
            min="0"
            value={targetPct}
            onChange={(e) => {
              setTargetPct(e.target.value);
              resetTransient();
            }}
            placeholder="none"
            disabled={disabled}
            className={inputCls}
          />
        </div>
      </div>

      {/* ── Qty line ───────────────────────────────────────────────────── */}
      <div className="flex items-center gap-3 text-xs font-mono flex-wrap">
        <span className="text-dimmer">
          qty = {Number.isFinite(lotsNum) && lotsNum > 0 ? lotsNum : "–"} × {lotSize} lot ={" "}
          <span className="text-foreground font-semibold tabular-nums">{qty || "–"}</span>
        </span>
        {refLtp && qty > 0 && !isMarket && (
          <span className="text-dimmer">
            ≈ {fmtINR(parseFloat(refLtp) * qty)} premium
          </span>
        )}
        {willSplit && (
          <span className="inline-flex items-center gap-1 text-amber-400">
            <AlertTriangle className="w-3 h-3 shrink-0" />
            splits into {childCount} child orders (freeze {freezeQty})
          </span>
        )}
      </div>

      {/* ── Payoff-at-expiry chart ─────────────────────────────────────── */}
      {!isMarket && (
        <div className="rounded-lg border border-line bg-bg-2/40 px-3 py-2">
          <PayoffChart
            underlying={underlying}
            strike={strike}
            premium={refLtp}
            optionType={optionSide}
            side={side}
            lotSize={lotSize}
            lots={lots}
          />
        </div>
      )}

      {/* ── Preview button ─────────────────────────────────────────────── */}
      <div className="flex items-center gap-2 flex-wrap">
        <button
          type="button"
          disabled={!canPreview}
          onClick={handlePreview}
          className="inline-flex items-center gap-1.5 px-4 py-1.5 rounded-md border border-info/50 bg-info/10 text-info text-xs font-mono font-semibold hover:bg-info/20 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {previewBusy ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : (
            <CheckCircle className="w-3.5 h-3.5" />
          )}
          {previewBusy ? "Previewing…" : "Preview"}
        </button>

        {previewError && (
          <span className="text-xs text-danger font-mono">{previewError}</span>
        )}
      </div>

      {/* ── Verdict list ───────────────────────────────────────────────── */}
      {previewResult && (
        <div className="rounded-lg border border-line bg-bg-2/50 px-3 py-2.5 space-y-1.5">
          <div className="text-[10px] uppercase tracking-wider text-dimmer font-semibold mb-1">
            Preview verdicts
            {previewResult.client_order_id && (
              <span className="ml-2 text-dimmer normal-case font-mono font-normal">
                ref: {previewResult.client_order_id}
              </span>
            )}
          </div>
          {previewResult.verdicts?.map((v, i) => (
            <div
              key={i}
              className={`flex items-start gap-2 text-xs font-mono ${
                v.ok ? "text-emerald-300" : "text-danger"
              }`}
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
          {previewResult.verdicts?.length === 0 && (
            <div className="text-xs text-dimmer font-mono">No verdicts returned.</div>
          )}

          {/* Children table — freeze-split + tick-rounded prices */}
          {previewResult.ok && previewResult.children?.length > 0 && (
            <div className="pt-2 mt-1 border-t border-line/60">
              <div className="text-[10px] uppercase tracking-wider text-dimmer font-semibold mb-1">
                Child orders ({previewResult.children.length})
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-xs font-mono tabular-nums">
                  <thead>
                    <tr className="border-b border-line text-dimmer uppercase tracking-wider text-[10px]">
                      <th className="text-right py-1 pr-3 pl-0">Qty</th>
                      <th className="text-left py-1 px-3">Type</th>
                      <th className="text-right py-1 px-3">Price</th>
                      <th className="text-right py-1 px-3">Trigger</th>
                      <th className="text-left py-1 pl-3 pr-0">Symbol</th>
                    </tr>
                  </thead>
                  <tbody>
                    {previewResult.children.map((c, i) => {
                      const isMkt = c.prctyp === "MKT" || c.prc === "0";
                      return (
                        <tr
                          key={i}
                          className="border-b border-line/50 last:border-0"
                        >
                          <td className="py-1 pr-3 pl-0 text-right text-foreground">
                            {c.qty}
                          </td>
                          <td className="py-1 px-3 text-dim">{c.prctyp ?? "–"}</td>
                          <td className="py-1 px-3 text-right text-foreground">
                            {isMkt ? "MKT" : c.prc}
                          </td>
                          <td className="py-1 px-3 text-right text-dim">
                            {c.trgprc != null && c.trgprc !== "" ? c.trgprc : "–"}
                          </td>
                          <td className="py-1 pl-3 pr-0 text-dimmer truncate max-w-[160px]">
                            {c.tsym ?? "–"}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── Place (direct, one-click) ──────────────────────────────────── */}
      {previewResult && !showPlaceConfirm && (
        <div className="space-y-2">
          {previewResult.ok !== true && (
            <div className="flex items-center gap-1.5 text-xs text-dimmer font-mono">
              <AlertTriangle className="w-3.5 h-3.5 text-amber-400 shrink-0" />
              Preview must pass all checks before this order can be placed.
            </div>
          )}
          <button
            type="button"
            disabled={!canPlace}
            onClick={() => { setShowPlaceConfirm(true); setPlaceResult(null); setQueueError(null); setQueueResult(null); }}
            className="inline-flex items-center gap-1.5 px-5 py-2 rounded-md border-2 border-danger/70 bg-danger text-white text-sm font-mono font-bold hover:bg-danger/90 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            <Send className="w-3.5 h-3.5" />
            Place order — REAL MONEY
          </button>
        </div>
      )}

      {/* Second confirm — placing a REAL order */}
      {showPlaceConfirm && (
        <div className="rounded-lg border-2 border-danger bg-danger/10 px-4 py-3 space-y-3">
          <div className="text-sm font-bold text-danger flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 shrink-0" />
            Final confirm — placing a REAL order
          </div>
          <div className="text-xs text-danger/80 font-mono space-y-0.5">
            <div>
              {side === "B" ? "Buy" : "Sell"} <span className="font-bold">{lots}</span> lot(s){" "}
              <span className="font-bold">{underlying} {strike} {optionSide}</span> · {orderType} · {product}
            </div>
            <div>
              {qty} qty {!isMarket && refLtp ? `@ ~${fmtINR(parseFloat(refLtp))} ±${bandPct}%` : "@ MARKET"}{" "}
              · guard stop {stopPct || 50}%{targetPct ? ` · target ${targetPct}%` : ""}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              disabled={queueBusy}
              onClick={handlePlaceConfirmed}
              className="inline-flex items-center gap-1.5 px-4 py-1.5 rounded-md border border-danger/60 bg-danger text-white text-xs font-mono font-bold hover:bg-danger/90 disabled:opacity-50 transition-colors"
            >
              {queueBusy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : null}
              {queueBusy ? "Placing…" : "Confirm — Place Order"}
            </button>
            <button
              type="button"
              disabled={queueBusy}
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
          <div className="font-bold flex items-center gap-1.5">
            {placeResult.placed ? <CheckCircle className="w-3.5 h-3.5 shrink-0" /> : <XCircle className="w-3.5 h-3.5 shrink-0" />}
            {placeResult.placed ? "Order placed" : "Not placed"}
          </div>
          {placeResult.norenordno && <div>Order ID: {placeResult.norenordno}</div>}
          {placeResult.reason && <div className="text-dimmer">Reason: {placeResult.reason}</div>}
        </div>
      )}

      {/* Queue returned ok:false — show the failing verdicts (NOT queued) */}
      {queueResult && queueResult.ok === false && (
        <div className="rounded-lg border border-danger/40 bg-danger/10 px-3 py-2.5 space-y-1.5">
          <div className="text-[10px] uppercase tracking-wider text-danger font-bold mb-1">
            Not queued — re-check failed
          </div>
          {queueResult.verdicts?.map((v, i) => (
            <div
              key={i}
              className={`flex items-start gap-2 text-xs font-mono ${
                v.ok ? "text-emerald-300" : "text-danger"
              }`}
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
          {(!queueResult.verdicts || queueResult.verdicts.length === 0) && (
            <div className="text-xs text-dimmer font-mono">No verdicts returned.</div>
          )}
        </div>
      )}

      {queueError && (
        <div className="text-xs text-danger font-mono px-2 py-1 rounded border border-danger/30 bg-danger/10">
          {queueError}
        </div>
      )}
    </div>
  );
}

function RuleChip({ label, value }) {
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md border border-line bg-bg-3 text-dim">
      <span className="text-dimmer uppercase tracking-wider">{label}</span>
      <span className="text-foreground font-semibold tabular-nums">{value}</span>
    </span>
  );
}
