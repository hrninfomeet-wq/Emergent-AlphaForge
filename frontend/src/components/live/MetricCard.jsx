/**
 * MetricCard — a compact, reusable dashboard metric tile for the Live Trading
 * terminal. Dense, monospaced, status-coloured. Purely presentational.
 *
 * Layout: an inset rounded panel (bg-bg-2 + border-line) with a tiny uppercase
 * micro-label + optional icon on top, a big tabular value below (coloured by
 * `tone`), and an optional dim sub-line.
 *
 * Props:
 *   label   string                                  — uppercase micro-label (required)
 *   value   string | number | ReactNode             — the big value; pre-format
 *           numbers with fmtINR/fmtNum before passing. `null`/`undefined` → "–".
 *   sub     string (optional)                        — small dim line under the value
 *   tone    "default" | "success" | "danger"          — value colour
 *           | "info" | "warn"                          (default "default")
 *   icon    ReactNode (optional)                     — a lucide icon node, e.g.
 *           <TrendingUp className="w-3.5 h-3.5" />. Rendered next to the label.
 *   loading boolean (optional)                       — show a dim "…" placeholder
 *
 * Example:
 *   <MetricCard label="Day P&L" value={fmtINR(pnl)} tone={pnl >= 0 ? "success" : "danger"}
 *               sub="realised + open" icon={<TrendingUp className="w-3.5 h-3.5" />} />
 */

const TONE_CLASS = {
  default: "text-foreground",
  success: "text-success",
  danger: "text-danger",
  info: "text-info",
  warn: "text-warning",
};

export default function MetricCard({
  label,
  value,
  sub,
  tone = "default",
  icon = null,
  loading = false,
}) {
  const toneCls = TONE_CLASS[tone] ?? TONE_CLASS.default;

  // Defensive: render an em-dash for empty values so the tile never collapses.
  const display =
    value === null || value === undefined || value === "" ? "–" : value;

  return (
    <div className="rounded-lg border border-line bg-bg-2 px-3 py-2.5 transition-colors hover:border-line/80 hover:bg-bg-2/80">
      {/* Micro-label + optional icon */}
      <div className="flex items-center gap-1.5">
        {icon ? (
          <span className="shrink-0 text-dimmer" aria-hidden="true">
            {icon}
          </span>
        ) : null}
        <span className="truncate text-[10px] font-semibold uppercase tracking-wider text-dimmer">
          {label}
        </span>
      </div>

      {/* Value */}
      <div
        className={`mt-1 text-lg font-mono font-semibold tabular-nums leading-tight sm:text-xl ${
          loading ? "text-dimmer" : toneCls
        }`}
      >
        {loading ? "…" : display}
      </div>

      {/* Optional sub-line */}
      {sub && !loading ? (
        <div className="mt-0.5 truncate text-[10px] font-mono text-dimmer leading-tight">
          {sub}
        </div>
      ) : null}
    </div>
  );
}
