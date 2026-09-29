import { Bell, BellOff } from "lucide-react";
import { useLiveNotifySettings } from "@/components/live/useLiveNotifications";

/**
 * Opt-in live alerts, DEFAULT OFF. The toast is always on when alerts are on;
 * desktop and sound are sub-options. Ticking "desktop" is what raises the browser's
 * permission prompt — nothing asks for permission on load.
 */
export default function LiveAlertsToggle() {
  const { settings, setEnabled, setDesktop, setSound } = useLiveNotifySettings();
  const on = settings.enabled;
  return (
    <span className="inline-flex items-center gap-1.5 text-[11px] font-mono shrink-0 flex-wrap" data-testid="live-alerts">
      <button
        type="button"
        onClick={() => setEnabled(!on)}
        aria-pressed={on}
        className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 ${
          on ? "border-amber-500/40 text-warning" : "border-line text-dimmer hover:text-foreground"
        }`}
        title="Opt-in alerts for fills, exits, refused entries, blocked entries and safety halts. They fire only while this tab is open — there is no alert when the PC is down."
        data-testid="live-alerts-toggle"
      >
        {on ? <Bell className="w-3 h-3" /> : <BellOff className="w-3 h-3" />}
        Alerts {on ? "on" : "off"}
      </button>
      {on && (
        <>
          <label className="inline-flex items-center gap-1 text-dimmer">
            <input
              type="checkbox"
              checked={settings.desktop}
              onChange={(e) => setDesktop(e.target.checked)}
              data-testid="live-alerts-desktop"
            />
            desktop
          </label>
          <label className="inline-flex items-center gap-1 text-dimmer">
            <input
              type="checkbox"
              checked={settings.sound}
              onChange={(e) => setSound(e.target.checked)}
              data-testid="live-alerts-sound"
            />
            sound
          </label>
          <span className="text-dimmer" data-testid="live-alerts-note">
            · only while this tab is open — no alert if the PC is down
          </span>
        </>
      )}
    </span>
  );
}
