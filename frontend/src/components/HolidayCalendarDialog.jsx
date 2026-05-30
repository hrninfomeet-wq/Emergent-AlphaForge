import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { CalendarDays } from "lucide-react";

/**
 * Holiday calendar modal. Shows NSE/BSE market holidays + special trading
 * sessions for a selected year. Data comes from the backend curated calendar
 * (`/api/calendar/holidays`), the same source the data-hygiene plan uses, so
 * the UI and the gap-detection logic never disagree.
 */
export default function HolidayCalendarDialog() {
  const [open, setOpen] = useState(false);
  const [years, setYears] = useState([]);
  const [year, setYear] = useState(null);
  const [calendar, setCalendar] = useState(null);
  const [loading, setLoading] = useState(false);

  const load = async (y) => {
    setLoading(true);
    try {
      const res = await api.marketHolidays(y);
      setYears(res.available_years || []);
      setCalendar(res.calendar || null);
      setYear(res.calendar?.year ?? y ?? null);
    } catch (e) {
      setCalendar(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (open && !calendar) load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button
          variant="secondary"
          size="sm"
          className="h-8 text-xs"
          data-testid="holiday-calendar-button"
        >
          <CalendarDays className="w-3.5 h-3.5 mr-1.5" />
          Holiday Calendar
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-lg bg-bg-1 border-line" data-testid="holiday-calendar-dialog">
        <DialogHeader>
          <DialogTitle className="text-sm font-semibold uppercase tracking-wider text-dim">
            Market Holiday Calendar
          </DialogTitle>
        </DialogHeader>

        <div className="flex flex-wrap items-center gap-2">
          {years.map((y) => (
            <Button
              key={y}
              size="sm"
              variant="secondary"
              onClick={() => load(y)}
              className={`h-7 px-3 text-xs border ${
                y === year ? "border-info bg-bg-3 text-foreground" : "border-line bg-bg-2 text-dim"
              }`}
              data-testid={`holiday-year-${y}`}
            >
              {y}
            </Button>
          ))}
          {calendar?.verified_through && (
            <span className="ml-auto text-[10px] text-dimmer font-mono">
              verified through {calendar.verified_through}
            </span>
          )}
        </div>

        <div className="mt-2 max-h-[55vh] overflow-y-auto">
          {loading && <div className="p-4 text-center text-dimmer text-sm">Loading…</div>}

          {!loading && calendar && (
            <>
              <div className="text-[11px] text-dimmer mb-1">
                {calendar.holiday_count} trading holidays in {calendar.year}
              </div>
              <table className="w-full text-xs" data-testid="holiday-calendar-table">
                <thead>
                  <tr className="text-dim border-b border-line">
                    <th className="text-left p-2">Date</th>
                    <th className="text-left p-2">Day</th>
                    <th className="text-left p-2">Holiday</th>
                  </tr>
                </thead>
                <tbody>
                  {(calendar.holidays || []).map((h) => (
                    <tr key={h.date} className="border-b border-line">
                      <td className="p-2 font-mono text-dim">{h.date}</td>
                      <td className="p-2 text-dimmer">{h.weekday}</td>
                      <td className="p-2">{h.label}</td>
                    </tr>
                  ))}
                </tbody>
              </table>

              {(calendar.special_sessions || []).length > 0 && (
                <div className="mt-3">
                  <div className="text-[10px] uppercase tracking-wider text-dimmer mb-1">
                    Special trading sessions (open on a weekend)
                  </div>
                  <table className="w-full text-xs">
                    <tbody>
                      {calendar.special_sessions.map((s) => (
                        <tr key={s.date} className="border-b border-line">
                          <td className="p-2 font-mono text-dim">{s.date}</td>
                          <td className="p-2 text-dimmer">{s.weekday}</td>
                          <td className="p-2 text-emerald-300">{s.label}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}

          {!loading && !calendar && (
            <div className="p-4 text-center text-dimmer text-sm">No calendar data available.</div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
