# AlphaForge Auto-Optimizer — User Guide

*A guide for a new user of the Optimizer page (`/optimizer`). Last updated 2026-06-16.*

## What this page does
The Optimizer searches a strategy's **parameter space** to find settings that performed well over your chosen history — *without manual tuning*. Its most powerful mode goes further: it re-scores the best candidates on **real paired-option rupees** (not just index points), runs them through a **survival gate** that rejects account-destroying configs out-of-sample, and can **auto-tune the exit controls** for each survivor. The output is a parameter set you can save as a preset and deploy for paper trading.

**Mental model — three escalating layers:**
1. **Spot search** (fast): score the index-points backtest across many parameter combos.
2. **Option re-rank** (realistic): take the top-K and re-score them on actual CE/PE premium P&L after costs/slippage — because the spot edge often dies on premium decay.
3. **Survival gate** (honest): keep only candidates that stay solvent out-of-sample (per walk-forward fold), then optionally auto-tune their exits.

> Rule of thumb: spot-only optimization tells you a strategy *signals* well; option re-rank + survival tells you it would *make money you could survive trading*. Always reach for layers 2–3 before trusting a result.

## The settings, top to bottom

**Run name** — auto-generated label (strategy · instrument · objective). Leave it; it keeps your Job History readable.

**Instrument** — NIFTY / BANKNIFTY / SENSEX. Pick what you have warehouse data for and intend to trade.

**Strategy** — the signal logic to optimize. Each strategy exposes its own tunable parameters.

**Method**
- **Bayesian (Optuna TPE)** — *recommended default.* Smart; focuses trials on promising regions. Best for ≥3 parameters.
- **Grid** — exhaustive over a coarse grid; predictable but explodes with parameters. Use only for 1–2 params or a final fine sweep.
- **Genetic** — evolutionary; occasionally better on rugged spaces, slower.

**Objective** — what the *spot search* maximizes:
- `Net P&L (pts)` / `Total P&L pts` — raw index points; ignores risk.
- `net_pnl_inr` — points × lot size (a rupee proxy, still spot-based).
- `Sharpe` / `risk_adjusted` (Calmar-like) — reward-per-risk; prefer these to avoid one-big-trade winners.
- `Profit factor`, `Win rate`, `neg_max_dd` — single-facet objectives; useful as secondary checks.

> **Recommendation:** use `risk_adjusted`/`Sharpe` for the spot search, and let the **Survivability objective** (below) make the real rupee decision. The spot objective only shortlists; survival selects.

**Pre-trade profile** — Conservative / Balanced / Aggressive / **None**. This applies the *same* signal filter you'd trade/deploy with, so optimized params match live behavior. **Important:** if you optimize with a profile, deploy with the *same* profile, or live results won't match. Use **None** to optimize the strategy's raw signals (and then deploy with None).

**Run type**
- **Single optimization** — one search over the whole window. Fast, but in-sample — *verify with walk-forward before trusting.*
- **Walk-forward** — re-optimizes per train window and evaluates on the unseen test window. Slower, far more honest about generalization.

**Evaluation**
- **Spot** — score the index backtest only. Fast, but blind to premium decay/costs — a spot winner can be an option loser.
- **Option re-rank (realistic)** — *strongly recommended.* Searches on spot, then re-ranks the top-K on real paired-option net rupees. This is the only mode that reflects what you'd actually earn buying options. **Survivability and Exit-Control Search require this mode.**

### Option Execution (under re-rank)
- **Re-rank top-K** — how many spot finalists get the expensive option re-score. Higher = more thorough, slower (option candles load once). 25–50 is a sensible range.
- **Moneyness** — ATM (default; matches the warehouse's maintained band), or OTM/ITM.
- **Diversity shortlist** — broadens the top-K with a diversity sample so an option-profitable-but-spot-mediocre config can surface. Leave on for re-rank.
- **DTE filter** — restrict to specific days-to-expiry (e.g. 0–2 for the expiry-week buying window). "All" = every weekly expiry.
- **Lots / sizing** — fixed lots, or premium-at-risk sizing (size so each trade risks a fixed % of capital).
- **Option exit + Level unit + Target/Stop (% of premium)** — the per-trade premium exit the re-rank simulates (e.g. target +60% / stop −40% of premium). **Level unit = Percent means fractions in the current convention** (0.60 = 60%).
- **Apply option costs** — charges + bid-ask spread. **Keep ON** — gross option P&L is fiction for index-option scalping, and ₹ caps/survival require it.

### Survivability (the survival gate)
Gates each finalist on the **rupee equity curve**, evaluated **per walk-forward OOS fold** — so it rejects configs that look great in-sample but blow up the account out-of-sample.
- **Enable survival gating** — turn on for any result you intend to deploy.
- **Equity floor (₹)** — reject if realized equity ever falls to/below this (default ₹0 = "never let the account go negative"). The primary, deterministic guard.
- **Max drawdown %** — reject if peak-to-trough drawdown exceeds this (e.g. 45%).
- **Max risk-of-ruin %** — reject if the Monte-Carlo upper-CI probability of hitting the ruin floor exceeds this (fail-closed: "can't prove safe" = rejected).
- **Objective** — how *survivors* are ranked: **Calmar** (return ÷ drawdown, risk-adjusted — recommended) or **Total ₹** (raw rupees).
- If **zero survivors**, the page says **"NO SURVIVOR"** and promotes nothing — that's the gate protecting you, not a bug. (Loosen the caps, widen bounds, **extend the date range**, or accept the strategy isn't survivable.)

> **Caution — OOS vs full-window.** A "profitable survivor" is profitable on the stitched *out-of-sample* folds. A candidate can be OOS-positive yet lose money over the full window (the recent folds carried it). Prefer survivors whose **full-window option P&L is also positive** — they're far more trustworthy than ones that profited only on the recent slice.

### Exit-Control Search (auto-tune exits)
*Requires Survivability ON + Option re-rank.* For each surviving finalist, sweeps a small grid of **trailing-stop distances** and **breakeven triggers** and keeps the **best-surviving** exit config (shown as "auto-tuned exit" on the result). Leave the grid bounds blank to use the safe defaults (trail 0.20/0.35, breakeven 0.0/0.30 — **fractions**). This can turn a marginal strategy survivable by cutting its losing tail. Costs more time (survivors × folds × grid), so it only runs on already-surviving finalists.

**Trial budget** — 10–5000 trials. More ≠ better: gains flatten after a few hundred for small spaces and over-fit risk rises. Scale to how many parameters you're searching (≈ 40–80 for 3–5 params, more if you also optimize indicator periods).

**Apply realistic costs** — spot-side costs. Keep on.

**Optimize indicator periods** — also tune RSI/MACD/ATR/EMA/ADX lengths (recomputed per trial — slower, but searches the real space). Turn on when you want the indicators themselves tuned, not just thresholds.

**Guard rails**
- **Min trades** — reject degenerate 1–2-trade "winners". Set ≥ 30 for a meaningful sample; ≥ 100 if you want the risk-of-ruin statistic to be reliable.
- **Min CE/PE side %** — force a minimum share of the minority direction, rejecting all-PE/all-CE flukes.

## Recommended workflow
1. Strategy + Instrument; **Method = Bayesian**; **Objective = risk_adjusted**.
2. **Pre-trade profile = None** (and deploy with None later), unless you have a profile you'll always trade with.
3. **Evaluation = Option re-rank**; ATM; **costs ON**; sensible target/stop (e.g. +60% / −40%).
4. **Survivability ON** — floor ₹0, max DD ~40–45%, max RoR ~10–15%, **objective Calmar**.
5. **Exit-Control Search ON** (default grid).
6. **Trial budget ~40**, **Min trades ≥ 30**.
7. **Use a date range with complete option data and enough OOS trades** — a clean ~12-month window is the sweet spot (long enough for ≥100 OOS paired trades, short enough to avoid the long-range load limit). *Too short → `insufficient_sample`; an over-long range can drop the newest months on the plain backtest.*
8. Run → read the result. If **survivors**, the best one's params + chosen exit are promoted; **Apply as preset** → deploy. If **NO SURVIVOR**, follow the on-screen suggestions (loosen caps / extend range / widen bounds) or accept the strategy isn't survivable here.

## Reading the results
- **Job History** shows status (`DONE`, **`NO SURVIVOR`**, `ANALYZING`, `FAILED`), trials, and best objective.
- A survivor result shows, per finalist: **Survived/Disqualified (+reason)**, OOS **return %**, **max DD %**, **min equity ₹**, **risk-of-ruin**, **Calmar**, option ₹ P&L, and any **auto-tuned exit**. The reasons matter:
  - `insufficient_sample` = too few OOS trades (extend range / lower the bar).
  - `max_drawdown` / `equity_floor` / `risk_of_ruin` = genuinely unsafe.
  - `ok` but excluded = survived safety but wasn't profitable.

## Common pitfalls
- **Trusting a spot-only result** — it ignores premium decay; always re-rank on options before deploying.
- **Optimizing with one profile, deploying with another** — results won't match.
- **Too-short a window** → `insufficient_sample` no-survivor; **too-long** → the plain-backtest load cap can drop the newest months (a known issue — keep ranges ≤ ~12 months for now).
- **Reading "NO SURVIVOR" as failure** — it's the gate refusing to hand you a money-loser.
- **Promoting an OOS-positive but full-window-negative survivor** — check the full-window option P&L too; prefer survivors positive on both.
- **Cranking the trial budget** expecting better results — past a few hundred it mostly adds over-fit risk.
