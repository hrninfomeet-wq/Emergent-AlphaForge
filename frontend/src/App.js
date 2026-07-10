import { useEffect } from "react";
import "@/App.css";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { Toaster } from "sonner";
import { ThemeProvider, useTheme } from "@/lib/theme";
import { JobsProvider } from "@/lib/jobs";
import Layout from "@/components/Layout";
import Dashboard from "@/pages/Dashboard";
import BacktestLab from "@/pages/BacktestLab";
import StrategyLibrary from "@/pages/StrategyLibrary";
import DataWarehouse from "@/pages/DataWarehouse";
import PreTradeChecklist from "@/pages/PreTradeChecklist";
import SignalJournal from "@/pages/SignalJournal";
import PaperTrading from "@/pages/PaperTrading";
import Optimizer from "@/pages/Optimizer";
import PremiumMomentum from "@/pages/PremiumMomentum";
import SavedPresets from "@/pages/SavedPresets";
import LiveSignals from "@/pages/LiveSignals";
import LiveTrading from "@/pages/LiveTrading";

function App() {
  return (
    <ThemeProvider>
      <JobsProvider>
        <AppShell />
      </JobsProvider>
    </ThemeProvider>
  );
}

function AppShell() {
  const { effectiveTheme } = useTheme();

  useEffect(() => {
    document.title = "AlphaForge — Trading Lab";
  }, []);

  return (
    <div className="App" data-testid="app-root">
      <BrowserRouter>
        <Layout>
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/backtest" element={<BacktestLab />} />
            <Route path="/strategies" element={<StrategyLibrary />} />
            <Route path="/warehouse" element={<DataWarehouse />} />
            <Route path="/checklist" element={<PreTradeChecklist />} />
            <Route path="/journal" element={<SignalJournal />} />
            <Route path="/paper" element={<PaperTrading />} />
            <Route path="/optimizer" element={<Optimizer />} />
            <Route path="/premium-momentum" element={<PremiumMomentum />} />
            <Route path="/presets" element={<SavedPresets />} />
            <Route path="/live" element={<LiveSignals />} />
            <Route path="/live-trading" element={<LiveTrading />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Layout>
      </BrowserRouter>
      <Toaster theme={effectiveTheme === "light" ? "light" : "dark"} position="top-right" richColors closeButton />
    </div>
  );
}

export default App;
