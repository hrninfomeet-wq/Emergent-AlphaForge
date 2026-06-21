Good call to stop and understand before re-aligning. Let me read the actual implementation so my explanations match what the code really does, not what the labels suggest — starting with the Backtest Lab form and the sizing/exit engine behind it.

Sizing logic captured. Now the exit-mode engine and the rest of the Backtest Lab form:

Now let me read the full Option Execution form section in the UI, plus the Optimizer page defaults, to answer the remaining questions precisely:

I've now read all the relevant code (the sizing engine, the option exit engine, both page forms, and the WFO module), so everything below is verified against what actually runs — including a couple of places where the answer is not what the labels suggest. No changes made.

---

## A. Backtest Lab

### A1. Capital & position sizing

First, the one rule that holds everywhere: **lot size** (75 for NIFTY, etc.) always comes from the option contract metadata. You never choose it. All these settings only decide the **lot count** — how many lots per trade.

**a. Fixed lots + Max lots (in fixed-lots mode)**

- **Fixed lots** = trade this many lots on every signal, always. Fixed lots = 2 on NIFTY → every trade is 2 × 75 = 150 quantity, whether the premium is ₹40 or ₹400.
- **Max lots** = a hard safety ceiling. The engine trades `min(fixed lots, max lots)`.

In fixed-lots mode, max lots is honestly almost decorative — if you type Fixed = 2 and Max = 10, max never bites. It only protects you from typing Fixed = 50 with Max = 10. Its real job is in premium-at-risk mode (below).

**b. What "Premium-at-risk" means**

When you buy an option, the realistic worst case on a trade is the part of the premium you'll lose before your stop takes you out. Premium-at-risk mode sizes each trade so that this worst case stays within a fixed percentage of your capital — the classic "risk 1% per trade" discipline, adapted to long options.

The formula per trade:

1. Risk per unit = entry premium − stop level (if a premium stop exists), else entry premium × assumed-stop %.
2. Risk per lot = risk per unit × lot size.
3. Lots = your risk budget (capital × risk/trade %) ÷ risk per lot, rounded down, capped at Max lots.

Practical use: set Capital to what you'd actually fund the account with (₹2,00,000 default), Risk/trade to 0.5–2%, and let the engine vary lot count trade-by-trade. Expensive premium or wide stop → fewer lots; cheap premium or tight stop → more lots. The point of running a backtest this way is that the **rupee equity curve, drawdown %, and daily Sharpe** then describe a real account with consistent risk, not an arbitrary "always 1 lot" account.

One deliberate behavior to know: sizing **never skips a trade**. If even 1 lot exceeds the risk budget, it still takes 1 lot and tags the trade `risk_exceeded` — so you can see the discipline breach instead of the backtest silently trading a different signal set than fixed-lots mode would.

**c. Max lots + Assumed stop (%) in premium-at-risk mode**

- **Assumed stop (%)** answers: "what's the risk per unit when there is **no premium stop level**?" That happens whenever your exit mode is "Mirror spot exit" (the option exits when the index hits the strategy's spot levels — there's no fixed premium stop to measure risk against). Default 50% means: assume a losing trade costs you about half the premium. Entry ₹150 → assumed risk ₹75/unit.
- **Max lots** is what stops cheap-premium absurdity. Worked example, NIFTY (lot 75), capital ₹5,00,000, risk 1% → budget ₹5,000/trade:
  - Premium ₹150, premium stop at ₹120 → risk ₹30 × 75 = ₹2,250/lot → **2 lots** (₹4,500 risk).
  - Premium ₹100, no stop, assumed 50% → risk ₹50 × 75 = ₹3,750/lot → **1 lot**.
  - Premium ₹6 (0DTE lottery ticket), assumed 50% → risk ₹3 × 75 = ₹225/lot → formula says 22 lots = 1,650 quantity. **Max lots = 10 caps this** — liquidity and slippage on 22 lots of a ₹6 option would be nothing like the backtest fill.

So: assumed stop makes risk measurable when there's no hard premium stop; max lots keeps the formula from doing something you'd never do live.

### A2. Premium SL/target vs the strategy's spot_target_pts / spot_stop_pts

Your guess is **half right**, and the half that's wrong matters.

The backtest is built in two layers. The strategy layer first generates the **index trade** — entry bar, and exit driven by `spot_target_pts`/`spot_stop_pts`. The option layer then buys the option at the signal bar and decides when to sell it:

- **Mirror spot exit** mode: option sells exactly when the index trade exits. Spot params fully in charge.
- **Option premium SL/target** mode: from entry onward, the engine scans the option's own candles for the first premium target/stop hit (entry ₹150, target 40 pts → sell at ₹190; stop 30 pts → sell at ₹120; if one bar spans both, the **stop is assumed to fill first** — pessimistic on purpose). **But** — and this is the part your guess misses — the spot exit is still there as a **backstop**: if neither premium level is hit by the time the index trade exits, the option is sold at that moment anyway (exit reason `OPTION_SIGNAL_EXIT`).

So `spot_target_pts`/`spot_stop_pts` are never ignored. They still (1) define the index trade whose timing bounds the option trade, and (2) close any option position whose premium levels never trigger. The results panel shows the split: target exits / stop exits / signal exits.

**Is this consistent across the rest of the app? Only partially — this is a genuine alignment gap for your course-correction list:**

| Place | Premium-level exits supported? |
|---|---|
| Backtest Lab | Yes — points **or** percent, set in the form |
| Optimizer (option re-rank) | Yes — but **percent only** |
| Live auto-paper trading | Premium levels come from the **strategy's** `target_pct`/`stop_pct` hints or the **deployment's** fallback %, in **percent only**. The backtest form's pts values never reach live. |
| Walk-forward optimization | Not at all — WFO scores on spot points only |

The mode that the live system reproduces *exactly* is "Mirror spot exit" (live trades carry spot-mirror levels: index hits the strategy's level → option sold at current premium). If you backtest with premium SL/target in **points**, there is no path that carries those exact exits into a deployment — the closest live approximation is the deployment's fallback target/stop as a % of premium, which is a different rule. Conclusion: a strategy validated in `option_levels`-pts mode is validated under exit rules the forward system can't fully replicate yet.

### A3. The three UI questions

i. **ATM as default moneyness** — easy and, frankly, correct. Today the Backtest Lab default is **OTM1**, which is inconsistent twice over: the Optimizer's option panel defaults to ATM, deployments default to ATM, and — more important — your Data Hygiene scope only auto-maintains **ATM** candles. So the current default points at data the warehouse doesn't keep current, which shows up as poor pairing coverage until you manually fetch OTM1 data.

ii. **Multi-select moneyness** — technically buildable, but the question is what it should *mean*. Each signal pairs with exactly one contract, so "ATM + OTM1 selected" has two possible semantics: (a) run the same backtest once per selected moneyness and show results side by side — meaningful, honest, basically a comparison feature; cost is N× runtime and N× option-data coverage requirements; or (b) a fallback chain (use ATM, else OTM1 where data is missing) — I'd advise against it, because the result is then a blend of different instruments and the P&L stops meaning one thing. If the goal is "which moneyness suits this strategy?", (a) is the useful version.

iii. **Multi-select DTE** — yes, and this one is genuinely practical for your 0–2 DTE style ("only trade Tue+Wed", say). Two notes: the backtest currently sends a single token (`dte0`…`dte6` or all), so the backend filter needs a small change to accept a set — and deployments already store DTE as a list (`[0..6]`), so a multi-select would actually make backtest and deployment *more* consistent, not less. Selecting all boxes = today's "All".

### A4. Systematic review of the page — issues found

Verified against code, ordered by how much they matter to your objective:

1. **Backtest↔live exit-rule gap** (described in A2). Spot-mirror is faithful end-to-end; premium-level exits are not. Biggest honesty gap on the page.
2. **Default moneyness (OTM1) contradicts the warehouse's ATM-only maintenance scope** (A3i). Default settings should produce well-paired backtests out of the box.
3. **Two competing "lots" inputs.** The Option Execution panel has "Lots" at the top, and the sizing panel has "Fixed lots." When sizing is enabled in fixed-lots mode, the top "Lots" input is silently ignored (the sizing panel's value wins); when sizing is off, the top one applies. Nothing in the UI tells you this.
4. **Rupee risk is an estimate in the very mode you trade.** In spot-mirror mode (your builtin strategies' mode), premium-at-risk sizing has no real stop level, so it sizes off the assumed-stop % — fine as a model, but worth knowing the equity curve's "1% risk" is approximate there, while in premium-SL mode it's exact.
5. **Two different things are both called "walk-forward."** The Backtest Lab's walk-forward toggle is the old IS/OOS split check on one fixed parameter set; the Optimizer's "Walk-forward (honest OOS)" is per-window re-optimization. Both names on screen, different meanings — easy to believe you've done the honest one when you haven't.
6. Minor: the optimizer re-rank supports premium exits in % only while this page supports pts and % (already counted in A2's table); and the fixed-expiry mode pins *all* trades to one expiry (the form warns about this correctly).

What I did **not** find: unit mismatches in the sizing math, look-ahead in the premium-level scan (it walks strictly forward, stop-first), or costs/slippage being skipped in either exit mode. The engine itself is sound; the issues are defaults, naming, and backtest↔live parity.

---

## B. Optimizer

### B1. Multi-select for Method

Not as checkboxes, and here the reason is conceptual rather than technical: Bayesian, Grid, and CMA-ES aren't filters to combine — each is a complete, self-contained search procedure, and a job is one procedure. "Select all three" can only honestly mean "run three jobs," which you can already do today by launching them back-to-back (Job History keeps each result, and the clone button re-fills the setup). If course-alignment makes method comparison a frequent ritual, the cleaner feature is a "run same config across methods" button that queues three jobs — same effect, honest framing, no ambiguity about what one result means. Worth knowing: for your purpose the method choice matters less than evaluation mode (spot vs option re-rank) and run type (single vs walk-forward) — those change *what is measured*, not just how the space is searched.

### B2. Default objective = Maximize Net P&L

Trivial to do — the default lives in one place in the page config (current default: Risk-Adjusted Return). Two caveats so the change does what you intend: (1) the setup panel persists to your browser's localStorage, so your already-saved setup would keep whatever objective it has until changed once by hand; (2) "Maximize Net P&L (₹)" is the honest objective **only with costs enabled** — it's net points × lot size, so without costs it just scales the points objective. And since pure-P&L objectives happily pick high-drawdown parameter sets, the guard rails + walk-forward become more important, not less, once this is the default.

### B3. Walk-forward (honest OOS) — what it actually does

**The problem it solves.** A single optimization is allowed to see the entire history while choosing parameters. Whatever it returns is, by construction, the set that fit *that* history best — including its noise. The beautiful backtest you see is "in-sample." The number you actually want is: *if I had been re-optimizing as time passed, using only the past, how would the chosen parameters have done on data nobody had seen yet?* Walk-forward computes exactly that.

**The mechanics, with your defaults (train 60, test 20, rolling).** Say the window is roughly Jul 2025 → Jun 2026 (~240 trading days — all counts are trading days actually in your data, holidays handled automatically):

- Window 1: optimize on days 1–60 (40 trials), take the winner, run it **untouched** on days 61–80. Those 20 days are out-of-sample — the optimizer never saw them.
- Window 2: slide forward by 20 → train on days 21–80, test on 81–100.
- … and so on, ~9 windows across the year.
- All the test-window trades are stitched, in order, into **one OOS equity curve**. That curve is the honest answer.

**The three verdict numbers:**

- **WF Efficiency** = OOS profit-per-day ÷ in-sample profit-per-day. It answers "how much of the optimized edge survives on unseen data?" ≥ 0.7 (green) — most of it; < 0.4 (red) — mostly curve-fitting; negative — the optimized params *lost* money out of sample. Your one existing walk-fwd job in Job History shows −1.06 — which is the system doing its job: that config looked profitable in a single optimization and is exposed as overfit here.
- **Consistency** = fraction of windows with positive OOS P&L. 7/9 windows positive is an edge that shows up across regimes; 2/9 means one lucky stretch carried the average.
- **Parameter stability** — whether each chosen parameter stayed in the same neighborhood across windows. RSI threshold 60-62-61-63 across windows = the parameter reflects market structure. 45-72-51-68 = the optimizer chasing noise; don't trust that parameter's "optimal" value.

**Rolling vs anchored:** rolling drops old data as it slides (train always = last 60 days) — fits your intraday 0–2 DTE style, where last quarter matters more than last year. Anchored keeps growing the train set from a fixed start — for strategies meant to be stable across years. Stay rolling.

**My recommendations for your use:**

- Defaults (60/20/step 20/rolling/40 trials/12 windows) are sensible for you. Train shorter than ~40 days starves the optimizer of trades; test shorter than ~10 days makes each window's OOS verdict mostly luck.
- Don't raise trials-per-window much — 40 is deliberate. Per-window over-searching re-introduces overfitting *inside* each window.
- Read the numbers in this order: efficiency → consistency → stability → only then the OOS P&L. A big OOS number with 2/9 consistency is still a no.
- What it doesn't cover: WFO scores on **spot points** (v1). So the full honest pipeline for your objective is: WFO to prove the edge survives time → option re-rank or option backtest on the final preset to prove it survives premium/theta/costs → paper-mode deployment to prove it survives live execution. The deployable params it saves are from the most recent train window — i.e., tuned on the newest data, which is exactly what you'd deploy tomorrow.

---

One closing observation for your alignment exercise: the recurring theme in everything above is that the app has **one fully honest path** — spot-defined exits → WFO → option re-rank → auto-paper (which mirrors spot exits exactly) — and several side options (premium-pts exits, OTM1 default, single-run optimization) that quietly step off that path. Tightening the defaults and either completing or fencing off the side options would be the core of the course correction, whenever you're ready to make changes.