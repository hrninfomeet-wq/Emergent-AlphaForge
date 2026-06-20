# Walk-Forward Panel Hints — Design

**Date:** 2026-06-20
**Branch:** `feat/wfo-parallel-workers` (same panel; rides with the parallel-workers work)
**Status:** Approved (design + copy approved in chat)

## 1. Goal

Add concise, on-demand `?` hint tooltips to every control in the Optimizer's
walk-forward settings panel, so a user new to walk-forward can set Train days,
Test days, Step, Window mode, Trials per window, Max windows, and Parallel
workers correctly — each hint says what the setting does, why it matters, and a
concrete suggested value tuned to the user's NIFTY intraday data. Do not clutter.

## 2. Affordance

- A small, muted lucide `HelpCircle` (~13px) `?` icon sits immediately after each
  field label and after the panel header. It is a real focusable `<button
  type="button">` (keyboard-accessible; `aria-label`), so hover **and** focus
  open the tooltip; on touch it opens on tap.
- Tooltip = the existing shadcn `frontend/src/components/ui/tooltip.jsx`
  (`@radix-ui/react-tooltip` ^1.2.4 — installed). NOTE: that component is
  currently UNUSED in the app (the app's existing hover hints all use the native
  `title=` attribute); this is its first wiring. It is chosen over native
  `title=` because the hint copy uses **bold** suggested values and multi-line
  content that native title cannot render.
- One `<TooltipProvider delayDuration={150}>` wraps the walk-forward panel
  content (local provider — does not touch the app root; lowest blast radius).
- `TooltipContent` is width-capped (`max-w-xs`) with small text, so tooltips stay
  compact.

## 3. Reusable helper

A single small helper keeps the JSX DRY and every hint consistent:

```jsx
const Hint = ({ children, label = "help" }) => (
  <Tooltip>
    <TooltipTrigger asChild>
      <button type="button" aria-label={label}
        className="ml-1 inline-flex align-middle text-dimmer hover:text-dim focus:outline-none">
        <HelpCircle className="h-3 w-3" />
      </button>
    </TooltipTrigger>
    <TooltipContent className="max-w-xs text-[11px] leading-snug">
      {children}
    </TooltipContent>
  </Tooltip>
);
```

(Final classNames follow the file's existing tokens — `text-dimmer`, `text-dim`,
`bg-bg-2`, etc.)

## 4. Placement & anti-clutter

- A `?` on the panel header ("Walk-forward windows (trading days)") for the
  concept, and one next to each label: Train days, Test days, Step, Window mode,
  Trials per window, Max windows, Parallel workers.
- **Declutter:** the two always-visible grey paragraphs are folded into hints:
  - The "Days are trading days actually present in the data … Window
    re-optimization runs on spot evaluation." paragraph → folded into the panel
    header hint (removed from always-on view).
  - The static workers "Speeds the per-window Bayesian search. 1 = sequential &
    reproducible …" note (shown when workers ≤ 1) → folded into the Parallel
    workers hint (removed from always-on view).
- **Kept visible:** the DYNAMIC workers warning shown when `opt_workers > 1`
  (`data-testid="opt-wf-workers-warning"`) — it is a live caution, not static
  help.

## 5. Hint copy (approved)

| Field | Tooltip |
|---|---|
| **Walk-forward** (header) | Re-optimizes the strategy on each **Train** window, scores those settings on the next **unseen Test** window, and stitches all Test results into one honest out-of-sample record. Answers: *would these params have worked on data they weren't fitted to?* All days below are trading days actually in your data (holiday-aware). |
| **Train days** | In-sample window the optimizer fits on. Bigger = steadier fit but fewer windows and slower to adapt. **Suggested: 40–60** (≈2–3 months) for intraday NIFTY. |
| **Test days** | Unseen window each fit is scored on. Long enough for meaningful trades, short enough to stay recent — about ¼–⅓ of Train. **Suggested: 15–20.** |
| **Step** | How far the window slides each cycle. **Leave blank** to step by Test days → back-to-back, non-overlapping tests (standard). Smaller = overlapping windows (more, but correlated). **Suggested: blank.** |
| **Window mode** | **Rolling**: Train is a fixed size that slides forward (adapts to the latest regime). **Anchored**: Train grows from day one (more data, but old regimes dilute it). **Suggested: Rolling.** |
| **Trials per window** | Bayesian search trials per Train window. Too few underfits the search; too many overfits the train window and is slow. **Suggested: 40–80** (toward 80+ when tuning many params). |
| **Max windows** | Caps the window count; if more fit, the oldest are dropped (recent data matters most; deployable params come from the last window). Your data + the suggested sizes give ~6–8 windows. **Suggested: 8–12** to keep them all. |
| **Parallel workers** | Runs several trial backtests at once to speed each window's search. **1 = sequential & reproducible** (best for the deploy decision); >1 is faster but makes the OOS result vary run-to-run. **Suggested: 1** (4–8 only for quick exploration). |

## 6. Scope / non-goals

- Walk-forward panel only (`frontend/src/pages/Optimizer.jsx`). Pure presentational
  JSX + the `Hint` helper + imports (`Tooltip*`, `HelpCircle`).
- No backend, no payload, no logic changes. Single-run controls untouched.
- No new dependency (radix-tooltip + lucide already installed).

## 7. Acceptance

- Each of the 8 `?` icons renders; hover/focus opens a compact styled tooltip with
  the exact copy above; bold suggested values render.
- The two folded grey paragraphs no longer show as always-on text.
- The dynamic `opt_workers > 1` warning still shows.
- No console errors; frontend builds clean (eslint-strict).
