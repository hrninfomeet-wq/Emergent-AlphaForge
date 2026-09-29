import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { useLiveData } from "@/components/live/LiveDataProvider";
import {
  DEFAULT_NOTIFY_SETTINGS, NOTIFY_STORAGE_KEY, activeChannels, buildLiveSnapshot,
  desktopPermissionOutcome, diffLiveEvents, parseNotifySettings, serializeNotifySettings,
} from "@/lib/liveNotify";

/**
 * Opt-in live alerts — the side-effecting shell around lib/liveNotify.js, which
 * decides everything (what an event is, what the stored settings mean). Nothing here
 * runs unless the operator turned alerts ON in the Live Deployments header, and:
 *   - the desktop permission prompt is only ever raised from that checkbox's click;
 *   - the AudioContext is only ever created from a click (or, after a reload, on
 *     the first alert once the page has been interacted with) — never at load;
 *   - alerts exist only while THIS tab is open. A PC that is down alerts nobody.
 */

// One store shared by the cockpit-mounted hook and the header toggle, so a change
// in the toggle is seen by the notifier without a second source of truth.
let current = null;
const listeners = new Set();

function readStored() {
  try {
    return parseNotifySettings(window.localStorage.getItem(NOTIFY_STORAGE_KEY));
  } catch {
    return { ...DEFAULT_NOTIFY_SETTINGS };
  }
}

function currentSettings() {
  if (current === null) current = readStored();
  return current;
}

function commit(next) {
  current = next;
  try {
    window.localStorage.setItem(NOTIFY_STORAGE_KEY, serializeNotifySettings(next));
  } catch {
    // private mode / blocked storage: the choice still holds for this page load
  }
  listeners.forEach((fn) => fn(next));
}

const desktopSupported = () => {
  try {
    return typeof window !== "undefined" && "Notification" in window;
  } catch {
    return false;
  }
};

let audioCtx = null;

function ensureAudio() {
  try {
    if (!audioCtx) {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (Ctx) audioCtx = new Ctx();
    }
    if (audioCtx && audioCtx.state === "suspended") audioCtx.resume();
  } catch {
    audioCtx = null;
  }
  return audioCtx;
}

function playTone() {
  try {
    const ctx = ensureAudio();
    if (!ctx) return;
    const t = ctx.currentTime;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine";
    osc.frequency.value = 880;
    gain.gain.setValueAtTime(0.0001, t);
    gain.gain.exponentialRampToValueAtTime(0.2, t + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.4);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start(t);
    osc.stop(t + 0.42);
  } catch {
    // a beep is a convenience; never let it break the alert
  }
}

function showDesktop(ev) {
  try {
    if (desktopSupported() && window.Notification.permission === "granted") {
      const n = new window.Notification(ev.title, { body: ev.body, tag: ev.key });
      n.onclick = () => window.focus();
    }
  } catch {
    // a desktop notification is a convenience; the toast still shows
  }
}

/** The stored alert settings plus the three setters the header toggle calls. */
export function useLiveNotifySettings() {
  const [settings, setSettings] = useState(currentSettings);
  useEffect(() => {
    listeners.add(setSettings);
    setSettings(currentSettings());
    return () => { listeners.delete(setSettings); };
  }, []);

  const setEnabled = useCallback((on) => {
    if (on && currentSettings().sound) ensureAudio(); // inside the click that enables
    commit({ ...currentSettings(), enabled: Boolean(on) });
  }, []);

  const setSound = useCallback((on) => {
    if (on) {
      ensureAudio(); // created on the enabling click, so autoplay policy allows it
      playTone();    // and an audible confirmation that it works
    }
    commit({ ...currentSettings(), sound: Boolean(on) });
  }, []);

  // Called from the checkbox's onChange ONLY — requestPermission() must sit inside
  // the user gesture, and is never called anywhere else.
  const setDesktop = useCallback((on) => {
    if (!on) {
      commit({ ...currentSettings(), desktop: false });
      return;
    }
    const supported = desktopSupported();
    let asked = null;
    try {
      asked = supported ? window.Notification.requestPermission() : null;
    } catch {
      asked = null;
    }
    Promise.resolve(asked).then((answer) => {
      const permission = answer || (supported ? window.Notification.permission : null);
      const outcome = desktopPermissionOutcome(supported, permission);
      commit({ ...currentSettings(), desktop: outcome.granted });
      if (!outcome.granted) toast.error(outcome.reason, { duration: 12000 });
    });
  }, []);

  return { settings, setEnabled, setSound, setDesktop };
}

/**
 * Mount ONCE (LiveCockpit). Watches the shared live data, keeps the previous
 * snapshot in a ref, and — only when alerts are on — toasts (plus optional desktop
 * notification and tone) for what changed between two polls.
 */
export function useLiveNotifications() {
  const { deployLive, deployments } = useLiveData();
  const { settings } = useLiveNotifySettings();
  const prevRef = useRef(null);

  useEffect(() => {
    const next = buildLiveSnapshot(deployLive, deployments);
    const prev = prevRef.current;
    // Track the book even while alerts are off, so switching them on never treats
    // the state that already existed as news.
    prevRef.current = next;
    const channels = activeChannels(settings);
    if (!channels.toast) return;
    const events = diffLiveEvents(prev, next);
    if (events.length === 0) return;
    for (const ev of events) {
      const show = ev.kind === "fill" || ev.kind === "exit" ? toast.success : toast.error;
      show(`${ev.title} — ${ev.body}`, { duration: ev.kind === "halt" ? 20000 : 10000 });
      if (channels.desktop) showDesktop(ev);
    }
    if (channels.sound) playTone();
  }, [deployLive, deployments, settings]);
}
