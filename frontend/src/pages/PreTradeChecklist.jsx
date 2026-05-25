import { useEffect, useState } from "react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Skeleton } from "@/components/ui/skeleton";
import { NumberSliderInput } from "@/components/NumberSliderInput";
import { Save, RotateCcw, Info } from "lucide-react";

const PROFILES = ["Conservative", "Balanced", "Aggressive"];

const FIELD_DEFS = [
  { key: "min_confidence_score", label: "Minimum confidence score", type: "slider", min: 30, max: 95, step: 1, suffix: "" },
  { key: "max_vix", label: "Max India VIX (block if VIX above)", type: "slider", min: 10, max: 50, step: 0.5 },
  { key: "min_vix", label: "Min India VIX (block if VIX below)", type: "slider", min: 5, max: 20, step: 0.5 },
  { key: "news_block_before_min", label: "News block: minutes BEFORE event", type: "slider", min: 0, max: 120, step: 5, suffix: "min" },
  { key: "news_block_after_min", label: "News block: minutes AFTER event", type: "slider", min: 0, max: 60, step: 5, suffix: "min" },
  { key: "max_spread_pct", label: "Max bid-ask spread %", type: "slider", min: 0.5, max: 10, step: 0.1, suffix: "%" },
  { key: "cooldown_sec", label: "Cooldown between signals", type: "slider", min: 0, max: 3600, step: 30, suffix: "sec" },
  { key: "max_trades_per_day", label: "Max trades per day", type: "slider", min: 1, max: 50, step: 1 },
  { key: "daily_loss_cutoff_pct", label: "Daily loss cutoff (negative)", type: "slider", min: -10, max: 0, step: 0.1, suffix: "%" },
  { key: "min_confluence_reasons", label: "Min confluence reasons", type: "slider", min: 1, max: 10, step: 1 },
  { key: "trade_window_start", label: "Trade window start (IST)", type: "time" },
  { key: "trade_window_end", label: "Trade window end (IST)", type: "time" },
];

const REGIME_OPTIONS = ["TREND", "TREND_EXPANDING", "MIXED", "CHOP", "VOLATILE_CHOP"];
const CONFIRMATION_OPTIONS = ["off", "1m", "3m", "5m"];

export default function PreTradeChecklist() {
  const [profiles, setProfiles] = useState([]);
  const [active, setActive] = useState("Balanced");
  const [draft, setDraft] = useState(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.listProfiles().then((d) => {
      setProfiles(d.items || []);
      const balanced = d.items.find((p) => p.name === "Balanced");
      if (balanced) setDraft({ ...balanced.settings });
    });
  }, []);

  useEffect(() => {
    const p = profiles.find((x) => x.name === active);
    if (p) setDraft({ ...p.settings });
  }, [active, profiles]);

  const set = (k, v) => setDraft((d) => ({ ...d, [k]: v }));

  const save = async () => {
    setSaving(true);
    try {
      const updated = await api.saveProfile(active, draft);
      setProfiles((ps) => ps.map((p) => (p.name === active ? updated : p)));
      toast.success(`${active} profile saved`);
    } catch (e) {
      toast.error("Save failed: " + (e.response?.data?.detail || e.message));
    } finally {
      setSaving(false);
    }
  };

  const reset = () => {
    const p = profiles.find((x) => x.name === active);
    if (p) setDraft({ ...p.settings });
    toast.info("Reverted unsaved changes");
  };

  if (!draft) return <Skeleton className="h-96 bg-bg-1" />;

  return (
    <div className="space-y-3" data-testid="pretrade-checklist-page">
      <div className="rounded-lg border border-line bg-bg-1 p-3 flex items-center gap-3 flex-wrap">
        <Tabs value={active} onValueChange={setActive} className="flex-1">
          <TabsList data-testid="pretrade-profile-tabs">
            {PROFILES.map((p) => (
              <TabsTrigger key={p} value={p} data-testid={`profile-tab-${p.toLowerCase()}`}>{p}</TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
        <div className="flex items-center gap-2">
          <Button onClick={reset} variant="secondary" size="sm" className="h-8 text-xs" data-testid="pretrade-reset-button">
            <RotateCcw className="w-3 h-3 mr-1" /> Reset
          </Button>
          <Button onClick={save} disabled={saving} size="sm" className="h-8 text-xs bg-info text-bg-0 hover:bg-info/90" data-testid="pretrade-save-button">
            <Save className="w-3 h-3 mr-1" />
            {saving ? "Saving…" : `Save ${active}`}
          </Button>
        </div>
      </div>

      <div className="rounded-md border border-info/30 bg-info/5 text-info p-3 text-xs flex items-start gap-2" data-testid="pretrade-info-banner">
        <Info className="w-4 h-4 mt-0.5 shrink-0" />
        <div>
          <span className="font-medium">Anti-over-filter safeguard:</span> If filters become too tight to generate any signals, the Backtest Lab will warn you with specific suggestions to loosen. You always retain full control.
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        {FIELD_DEFS.map((f) => (
          <FieldCard key={f.key} field={f} value={draft[f.key]} onChange={(v) => set(f.key, v)} />
        ))}
        <RegimeCard value={draft.allowed_regimes || []} onChange={(v) => set("allowed_regimes", v)} />
        <ConfirmationCard value={draft.bar_close_confirmation || "off"} onChange={(v) => set("bar_close_confirmation", v)} />
      </div>
    </div>
  );
}

function FieldCard({ field, value, onChange }) {
  return (
    <div className="rounded-lg border border-line bg-bg-1 p-3" data-testid={`field-${field.key}`}>
      {field.type === "slider" && (
        <NumberSliderInput
          label={field.label}
          value={Number(value ?? field.min)}
          min={field.min}
          max={field.max}
          step={field.step}
          decimals={field.step < 1 ? 1 : 0}
          suffix={field.suffix || ""}
          onChange={onChange}
          testid={`field-${field.key}`}
        />
      )}
      {field.type === "time" && (
        <div>
          <div className="flex items-center justify-between mb-2">
            <Label className="text-xs text-dim">{field.label}</Label>
            <span className="text-sm font-mono text-foreground">{value}</span>
          </div>
          <Input
            type="time"
            value={value || "09:25"}
            onChange={(e) => onChange(e.target.value)}
            className="bg-bg-2 border-line h-8"
            data-testid={`input-${field.key}`}
          />
        </div>
      )}
    </div>
  );
}

function RegimeCard({ value, onChange }) {
  const toggle = (r) => onChange(value.includes(r) ? value.filter((x) => x !== r) : [...value, r]);
  return (
    <div className="rounded-lg border border-line bg-bg-1 p-3" data-testid="field-allowed-regimes">
      <Label className="text-xs text-dim mb-2 block">Allowed regimes (only fire signals in these)</Label>
      <div className="flex flex-wrap gap-1.5">
        {REGIME_OPTIONS.map((r) => {
          const active = value.includes(r);
          return (
            <button
              key={r}
              onClick={() => toggle(r)}
              className={`text-[11px] px-2 py-1 rounded-md border font-mono transition-colors ${active ? "bg-info text-bg-0 border-info" : "bg-bg-2 text-dim border-line hover:bg-bg-3"}`}
              data-testid={`regime-toggle-${r}`}
            >
              {r}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function ConfirmationCard({ value, onChange }) {
  return (
    <div className="rounded-lg border border-line bg-bg-1 p-3" data-testid="field-bar-close-confirmation">
      <Label className="text-xs text-dim mb-2 block">Bar-close confirmation (gate signals)</Label>
      <div className="flex gap-1.5">
        {CONFIRMATION_OPTIONS.map((c) => (
          <button
            key={c}
            onClick={() => onChange(c)}
            className={`flex-1 text-xs px-2 py-1.5 rounded-md border font-mono transition-colors ${value === c ? "bg-info text-bg-0 border-info" : "bg-bg-2 text-dim border-line hover:bg-bg-3"}`}
            data-testid={`confirmation-${c}`}
          >
            {c}
          </button>
        ))}
      </div>
    </div>
  );
}
