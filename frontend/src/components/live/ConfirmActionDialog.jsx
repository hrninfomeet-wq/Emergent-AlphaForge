import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";

/**
 * A real confirmation for consequential live actions — replaces bare
 * window.confirm strings. It names WHAT will happen (contracts, quantities) in
 * the body the caller passes, and can require typing a word for the gravest
 * actions. One dialog, never stacked (the DeployToLivePanel lesson).
 *
 * Props: open, onOpenChange, title, children (the body), confirmLabel,
 * danger (bool), busy (bool), requireText (string | null), onConfirm().
 */
export default function ConfirmActionDialog({
  open, onOpenChange, title, children, confirmLabel = "Confirm",
  danger = true, busy = false, requireText = null, onConfirm,
}) {
  const [typed, setTyped] = useState("");
  const armed = !requireText || typed.trim().toUpperCase() === String(requireText).toUpperCase();
  // The typed word must not survive the dialog. A caller closes it by flipping `open`
  // after the action completes (not through onOpenChange), and a stale "STOP ALL"
  // would arm the very next opening before the operator typed anything.
  useEffect(() => {
    if (!open) setTyped("");
  }, [open]);
  const close = (v) => {
    if (!v) setTyped("");
    onOpenChange?.(v);
  };
  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent className="max-w-lg" data-testid="confirm-action-dialog">
        <DialogHeader>
          <DialogTitle className={danger ? "text-rose-300" : ""}>{title}</DialogTitle>
          <DialogDescription asChild>
            <div className="text-xs text-dim space-y-2 font-mono">{children}</div>
          </DialogDescription>
        </DialogHeader>
        {requireText && (
          <Input
            autoFocus
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            placeholder={`Type ${requireText} to confirm`}
            className="h-8 text-xs font-mono"
            data-testid="confirm-action-input"
          />
        )}
        <DialogFooter>
          <Button variant="ghost" size="sm" onClick={() => close(false)} disabled={busy}>
            Cancel
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={!armed || busy}
            onClick={onConfirm}
            className={danger ? "border-rose-500/50 text-rose-300 hover:text-rose-200" : ""}
            data-testid="confirm-action-go"
          >
            {busy && <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" />}
            {confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
