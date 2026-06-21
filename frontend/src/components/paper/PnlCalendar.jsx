import { useState } from "react";
import { Button } from "@/components/ui/button";
import { fmtNum } from "@/lib/fmt";
import { CalendarDays } from "lucide-react";

const pad = (n) => String(n).padStart(2, "0");

// GitHub-style P&L calendar heat-grid: weekday rows (Mon–Fri) × week columns,
// each cell colored by that IST day's realized ₹ (green positive, red negative).
function CalendarHeatGrid({ dayPnl }) {
  const days = [...dayPnl.keys()].sort();
  if (days.length === 0) {
    return <div className="text-[11px] text-dimmer font-mono">No closed trades to chart yet.</div>;
  }
  const dayToUTC = (s) => { const [y, m, d] = s.split("-").map(Number); return Date.UTC(y, m - 1, d); };
  const utcToDay = (ms) => {
    const d = new Date(ms);
    return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
  };
  const DAY_MS = 86400000;
  let startMs = dayToUTC(days[0]);
  const endMs = dayToUTC(days[days.length - 1]);
  // Align the first column to Monday (getUTCDay: 0=Sun..6=Sat).
  const startDow = new Date(startMs).getUTCDay();
  startMs -= ((startDow + 6) % 7) * DAY_MS;
  // Cap to the most recent ~16 weeks to keep the grid compact.
  const MAX_WEEKS = 16;
  const minStart = endMs - (MAX_WEEKS * 7 - 1) * DAY_MS;
  if (startMs < minStart) {
    const ms = new Date(minStart);
    startMs = minStart - ((ms.getUTCDay() + 6) % 7) * DAY_MS;
  }
  const maxAbs = Math.max(1, ...[...dayPnl.values()].map((v) => Math.abs(v.pnl)));

  const weeks = [];
  for (let wkMs = startMs; wkMs <= endMs; wkMs += 7 * DAY_MS) {
    const cells = [];
    for (let i = 0; i < 5; i++) { // Mon..Fri (trading days)
      const cellMs = wkMs + i * DAY_MS;
      const day = utcToDay(cellMs);
      cells.push({ day, future: cellMs > Date.now(), info: dayPnl.get(day) || null });
    }
    weeks.push({ key: utcToDay(wkMs), cells });
  }

  const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"];
  const cellStyle = (info) => {
    if (!info || info.count === 0) return {};
    const intensity = 0.25 + 0.75 * (Math.abs(info.pnl) / maxAbs);
    if (info.pnl === 0) return {};
    return {
      backgroundColor: info.pnl > 0 ? "var(--color-success)" : "var(--color-danger)",
      opacity: intensity,
    };
  };

  return (
    <div className="flex items-start gap-2">
      <div className="flex flex-col gap-1 pt-0.5 mr-1">
        {WEEKDAYS.map((d) => <div key={d} className="text-[9px] text-dimmer h-3.5 leading-3.5">{d}</div>)}
      </div>
      <div className="flex gap-1 overflow-x-auto">
        {weeks.map((wk) => (
          <div key={wk.key} className="flex flex-col gap-1">
            {wk.cells.map((c) => (
              <div
                key={c.day}
                className={`w-3.5 h-3.5 rounded-sm border ${c.info && c.info.count ? "border-transparent" : "border-line bg-bg-3"} ${c.future ? "opacity-20" : ""}`}
                style={cellStyle(c.info)}
                title={c.info && c.info.count
                  ? `${c.day}: ₹${fmtNum(c.info.pnl, 0)} · ${c.info.count} trade${c.info.count === 1 ? "" : "s"}`
                  : `${c.day}: no trades`}
                data-testid="paper-calendar-cell"
              />
            ))}
          </div>
        ))}
      </div>
      <div className="flex items-center gap-1 ml-3 self-end text-[9px] text-dimmer">
        <span>loss</span>
        <span className="w-3 h-3 rounded-sm" style={{ backgroundColor: "var(--color-danger)" }} />
        <span className="w-3 h-3 rounded-sm border border-line bg-bg-3" />
        <span className="w-3 h-3 rounded-sm" style={{ backgroundColor: "var(--color-success)" }} />
        <span>profit</span>
      </div>
    </div>
  );
}

function MonthlyBars({ dayPnl }) {
  const months = new Map(); // 'YYYY-MM' -> pnl
  for (const [day, info] of dayPnl.entries()) {
    const m = day.slice(0, 7);
    months.set(m, (months.get(m) || 0) + Number(info.pnl || 0));
  }
  const entries = [...months.entries()].sort().slice(-6);
  if (entries.length === 0) return null;
  const maxAbs = Math.max(1, ...entries.map(([, v]) => Math.abs(v)));
  return (
    <div className="mt-3 pt-3 border-t border-line" data-testid="paper-monthly-bars">
      <div className="text-[10px] uppercase tracking-wider text-dimmer mb-2">Monthly P&amp;L</div>
      <div className="flex items-end gap-3 h-20">
        {entries.map(([m, v]) => {
          const h = Math.round((Math.abs(v) / maxAbs) * 56) + 2;
          const pos = v >= 0;
          return (
            <div key={m} className="flex flex-col items-center justify-end gap-1" title={`${m}: ₹${fmtNum(v, 0)}`}>
              {pos && <div className="text-[9px] font-mono text-success">{fmtNum(v, 0)}</div>}
              <div style={{ height: `${h}px`, backgroundColor: pos ? "var(--color-success)" : "var(--color-danger)" }} className="w-7 rounded-sm" />
              {!pos && <div className="text-[9px] font-mono text-danger">{fmtNum(v, 0)}</div>}
              <div className="text-[9px] font-mono text-dimmer">{m.slice(5)}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// P&L calendar card: per-day realized ₹ heat-grid with an internal Hide/Show toggle.
export default function PnlCalendar({ dayPnl }) {
  const [showCalendar, setShowCalendar] = useState(true);
  return (
    <div className="rounded-lg border border-line bg-bg-1" data-testid="paper-pnl-calendar">
      <div className="px-3 py-2 border-b border-line flex items-center gap-2">
        <CalendarDays className="w-4 h-4 text-info" />
        <div className="text-xs font-semibold uppercase tracking-wider text-dim">P&amp;L Calendar</div>
        <span className="text-[11px] text-dimmer">realized ₹ per IST day</span>
        <Button variant="ghost" size="sm" onClick={() => setShowCalendar((v) => !v)} className="ml-auto h-6 text-[11px]" data-testid="paper-calendar-toggle">
          {showCalendar ? "Hide" : "Show"}
        </Button>
      </div>
      {showCalendar && (
        <div className="p-3">
          <CalendarHeatGrid dayPnl={dayPnl} />
          <MonthlyBars dayPnl={dayPnl} />
        </div>
      )}
    </div>
  );
}
