import { Slider } from "@/components/ui/slider";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

/**
 * Combined slider + manual number input.
 * User can drag the slider OR type a precise value in the number box.
 * Both stay in sync. Values are clamped to [min, max].
 */
export function NumberSliderInput({
  label,
  value,
  min = 0,
  max = 100,
  step = 1,
  decimals = 0,
  suffix = "",
  onChange,
  testid,
  disabled = false,
}) {
  const handleInput = (raw) => {
    const num = Number(raw);
    if (Number.isNaN(num)) return;
    const clamped = Math.max(min, Math.min(max, num));
    onChange(clamped);
  };

  return (
    <div className="space-y-1" data-testid={testid ? `${testid}-wrap` : undefined}>
      {label && (
        <div className="flex items-center justify-between">
          <Label className="text-xs text-dim">{label}</Label>
          <span className="text-[10px] font-mono text-dimmer">
            {min}–{max}
          </span>
        </div>
      )}
      <div className="flex items-center gap-2">
        <Slider
          value={[Number(value ?? min)]}
          min={min}
          max={max}
          step={step}
          disabled={disabled}
          onValueChange={(arr) => onChange(arr[0])}
          className="flex-1"
          data-testid={testid ? `${testid}-slider` : undefined}
        />
        <div className="relative shrink-0">
          <Input
            type="number"
            value={Number(value ?? min).toFixed(decimals)}
            min={min}
            max={max}
            step={step}
            disabled={disabled}
            onChange={(e) => handleInput(e.target.value)}
            className="w-24 h-7 bg-bg-2 border-line text-xs font-mono text-right pr-6"
            data-testid={testid ? `${testid}-input` : undefined}
          />
          {suffix && (
            <span className="absolute right-1.5 top-1/2 -translate-y-1/2 text-[10px] text-dimmer pointer-events-none">
              {suffix}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
