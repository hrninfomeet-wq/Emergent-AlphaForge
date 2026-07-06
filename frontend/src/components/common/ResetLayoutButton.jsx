import { RotateCcw } from "lucide-react";

/** Small icon affordance pairing with `useInteractiveColumns` — clears the
 * persisted column layout (widths + order) for a table and reverts to its
 * default column set/widths/order. Only rendered meaningfully when a custom
 * layout is active, but always mountable so callers don't need to branch. */
export function ResetLayoutButton({ onReset, isCustomized, label = "table", testid }) {
  return (
    <button
      type="button"
      onClick={onReset}
      disabled={!isCustomized}
      className={`inline-flex items-center justify-center h-6 w-6 rounded border border-line bg-bg-2 text-dim hover:bg-bg-3 disabled:opacity-40 disabled:cursor-default disabled:hover:bg-bg-2`}
      title={isCustomized ? `Reset ${label} column layout (widths/order)` : `${label} column layout is default`}
      aria-label={`Reset ${label} column layout`}
      data-testid={testid}
    >
      <RotateCcw className="w-3 h-3" />
    </button>
  );
}
