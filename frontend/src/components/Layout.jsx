import { Link, NavLink, useLocation } from "react-router-dom";
import { useEffect, useState } from "react";
import {
  Home, LineChart, Database, ListChecks, BookOpen,
  Briefcase, Gauge, Activity, FlaskConical, Library,
  Zap, Monitor, Moon, Sun, Loader2, Clock, AlertTriangle, Bookmark,
} from "lucide-react";
import { useTheme } from "@/lib/theme";
import { useJobs } from "@/lib/jobs";
import { api } from "@/lib/api";
import MarketHeader from "@/components/MarketHeader";
import TokenCountdown from "@/components/TokenCountdown";

const NAV_GROUPS = [
  {
    label: "Overview",
    items: [
      { to: "/", label: "Dashboard", icon: Home, testid: "nav-dashboard" },
    ],
  },
  {
    label: "Research",
    items: [
      { to: "/backtest", label: "Backtest Lab", icon: FlaskConical, testid: "nav-backtest" },
      { to: "/strategies", label: "Strategy Library", icon: Library, testid: "nav-strategies" },
      { to: "/warehouse", label: "Data Warehouse", icon: Database, testid: "nav-warehouse" },
      { to: "/optimizer", label: "Optimizer", icon: Gauge, testid: "nav-optimizer" },
      { to: "/presets", label: "Saved Presets", icon: Bookmark, testid: "nav-presets" },
    ],
  },
  {
    label: "Execution",
    items: [
      { to: "/checklist", label: "Pre-Trade Checklist", icon: ListChecks, testid: "nav-checklist" },
      { to: "/live", label: "Live Signals", icon: Activity, testid: "nav-live", badge: "P4" },
      { to: "/journal", label: "Signal Journal", icon: BookOpen, testid: "nav-journal" },
      { to: "/paper", label: "Paper Trading", icon: Briefcase, testid: "nav-paper" },
    ],
  },
];

export default function Layout({ children }) {
  const loc = useLocation();
  return (
    <div className="min-h-screen flex bg-bg-0">
      <aside
        data-testid="app-sidebar"
        className="w-[260px] shrink-0 border-r border-line bg-bg-1 flex flex-col"
      >
        <Link to="/" className="px-4 h-14 flex items-center gap-2 border-b border-line" data-testid="app-logo">
          <div className="w-7 h-7 rounded-md bg-bg-3 border border-line-strong flex items-center justify-center">
            <Zap className="w-4 h-4 text-info" />
          </div>
          <div className="font-semibold tracking-tight">AlphaForge</div>
          <div className="ml-auto text-[10px] uppercase tracking-wider text-dimmer font-mono">v1.0</div>
        </Link>
        <nav className="flex-1 overflow-y-auto py-3 px-2 space-y-5">
          {NAV_GROUPS.map((group) => (
            <div key={group.label}>
              <div className="px-2 mb-1 text-[10px] font-semibold uppercase tracking-wider text-dimmer">
                {group.label}
              </div>
              <div className="space-y-0.5">
                {group.items.map((it) => (
                  <NavLink
                    key={it.to}
                    to={it.to}
                    data-testid={it.testid}
                    end={it.to === "/"}
                    className={({ isActive }) =>
                      [
                        "flex items-center gap-2 px-2 py-2 rounded-md text-sm transition-colors duration-150",
                        isActive
                          ? "bg-bg-3 text-foreground border border-line"
                          : "text-dim hover:text-foreground hover:bg-bg-2 border border-transparent",
                      ].join(" ")
                    }
                  >
                    <it.icon className="w-4 h-4 shrink-0" />
                    <span className="truncate">{it.label}</span>
                    {it.badge && (
                      <span className="ml-auto text-[9px] px-1.5 py-0.5 rounded bg-bg-2 border border-line text-dimmer font-mono">
                        {it.badge}
                      </span>
                    )}
                  </NavLink>
                ))}
              </div>
            </div>
          ))}
        </nav>
        <div className="px-3 py-3 border-t border-line text-[11px] text-dimmer">
          <div className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-emerald-500"></span>
            <span>local API live</span>
          </div>
          <div className="mt-1 font-mono">P4a prep · Docker verified</div>
        </div>
      </aside>

      <main className="flex-1 min-w-0 flex flex-col">
        <TopBar location={loc} />
        <MarketHeader />
        <div className="flex-1 min-w-0 overflow-y-auto p-4" data-testid="page-content">
          {children}
        </div>
      </main>
    </div>
  );
}

function TopBar({ location }) {
  const title = pageTitle(location.pathname);
  const { theme, setTheme } = useTheme();
  const themeIcon = theme === "white" ? Sun : theme === "black" ? Moon : Monitor;
  const ThemeIcon = themeIcon;

  return (
    <header className="h-14 border-b border-line bg-bg-1 flex items-center px-4 gap-3" data-testid="app-topbar">
      <h1 className="text-base font-semibold" data-testid="page-title">{title}</h1>
      <ActiveJobsIndicator />
      <TokenCountdown variant="button" />
      <div className="ml-auto flex items-center gap-3 text-[11px] font-mono text-dimmer">
        <label className="flex items-center gap-1.5">
          <ThemeIcon className="w-3.5 h-3.5 text-info" />
          <span className="sr-only">Theme</span>
          <select
            value={theme}
            onChange={(e) => setTheme(e.target.value)}
            className="h-8 rounded-md border border-line bg-bg-2 px-2 text-xs text-foreground outline-none focus:ring-1 focus:ring-ring"
            data-testid="theme-select"
            aria-label="Theme"
          >
            <option value="system">System</option>
            <option value="black">Black</option>
            <option value="white">White</option>
          </select>
        </label>
        <span data-testid="market-status-label">NSE · NIFTY 50 / BANKNIFTY / SENSEX</span>
      </div>
    </header>
  );
}

function ActiveJobsIndicator() {
  const { jobs, isJobActive } = useJobs();
  const active = [];
  if (isJobActive("upstox_ingest")) {
    active.push({ label: "Index ingest", pct: jobs.upstox_ingest?.progress_pct });
  }
  if (isJobActive("option_fetch")) {
    active.push({ label: "Option fetch", pct: jobs.option_fetch?.progress_pct });
  }
  if (active.length === 0) return null;

  return (
    <div
      className="flex items-center gap-2 px-2 py-1 rounded-md border border-line bg-bg-2 text-[11px] font-mono text-dim"
      data-testid="active-jobs-indicator"
      title="Background warehouse jobs are running"
    >
      <Loader2 className="w-3.5 h-3.5 text-info animate-spin" />
      {active.map((j, i) => (
        <span key={j.label}>
          {i > 0 && <span className="text-dimmer mr-2">·</span>}
          {j.label}
          {Number.isFinite(j.pct) ? ` ${Math.round(j.pct)}%` : ""}
        </span>
      ))}
    </div>
  );
}

function pageTitle(path) {
  switch (path) {
    case "/": return "Dashboard";
    case "/backtest": return "Backtest Lab";
    case "/strategies": return "Strategy Library";
    case "/warehouse": return "Data Warehouse";
    case "/checklist": return "Pre-Trade Checklist";
    case "/journal": return "Signal Journal";
    case "/paper": return "Paper Trading";
    case "/optimizer": return "Auto Optimizer";
    case "/presets": return "Saved Presets";
    case "/live": return "Live Signals";
    default: return "AlphaForge";
  }
}
