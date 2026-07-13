# NF CE PE EXP2 — Strategy Specification for AlphaForge

Decoded from AlgoTest export `nfcepeexp2.txt` (strategy name "NF CE PE EXP2", template base: Straddle920).
Backtest reference: PDF "NF CE PE Exp1", 26-Jan-2024 to 26-Jan-2025.
**Important: the code file is version EXP2, the PDF backtest is version EXP1 — they are not identical. See Section 7.**

---

## 1. Concept in one paragraph

An intraday NIFTY **options buying** strategy that trades momentum in both directions without predicting either. At 09:31 it starts watching one in-the-money call and one in-the-money put (current weekly expiry). It buys whichever option's premium rises 15% from its 09:31 price — i.e., it buys confirmed strength rather than guessing direction. Losses are cut at 20% with a ratcheting trailing stop. If a leg stops out, the strategy flips: it arms the opposite-side option and buys it on a 10% premium rise with a tighter 10% stop (one reversal per side, per day). Everything is force-closed at 15:13.

## 2. Instrument and session settings

| Setting | Value |
|---|---|
| Underlying | NIFTY 50 (strike selection from **spot/cash**, not futures) |
| Instruments | Current-week expiry options, CE and PE |
| Strike | ITM1 — one strike in the money (CE: first strike below spot; PE: first strike above spot) |
| Position size | 2 lots per leg (backtest qty 150 = 2 × 75) |
| Strategy type | Intraday, same-day exit |
| Signal start time | 09:31 |
| No new entries after | 14:40 (momentum triggers and reversal entries both blocked) |
| Hard square-off | 15:13 |
| Leg exits | Independent (partial square-off — one leg exiting does not close the other) |
| Trail SL to breakeven | Off |

## 3. Primary legs (both armed at 09:31, fully independent)

**Leg A — Call side**
1. At 09:31, select the ITM1 CE and record its LTP as the *reference price*. Strike stays fixed.
2. Entry trigger: option LTP ≥ reference × 1.15 (a +15% premium rise). Buy 2 lots at market.
3. Initial stop loss: 20% below the actual fill price.
4. Trailing: for every +5% the premium moves above entry, raise the SL by 5% of entry price (discrete 1:1 steps; SL never moves down).
5. No profit target — hold until the trailed SL hits or 15:13.
6. If the SL hits → arm Reversal Leg A′ (a **put** — Section 4).

**Leg B — Put side:** identical rules on the ITM1 PE; its stop-out arms Reversal Leg B′ (a **call**).

If a leg's +15% trigger never fires, that leg simply does not trade that day. On a whipsaw day both A and B can trigger.

## 4. Reversal ("lazy") legs — one shot each

Armed only at the moment the corresponding primary leg stops out, and only before 14:40.

| Rule | Value |
|---|---|
| A′ (after CE stop-out) | Fresh ITM1 **PE** selected from spot at that moment; its LTP becomes the new reference |
| B′ (after PE stop-out) | Fresh ITM1 **CE**, same procedure |
| Entry trigger | +10% premium rise from the new reference |
| Stop loss | 10% of fill price |
| Trailing | Same 5% / 5% ratchet |
| Target / further re-entry | None / none |
| Lots | 2 |

Logic: a stopped-out call means the up-move failed, so look for confirmed downside momentum instead (and vice versa). Worst case per day = 4 entries: A, B, A′, B′.

## 5. Portfolio-level rules (EXP2)

None. No overall stop loss, no overall target, no lock-and-trail. Risk is managed only per leg plus the 15:13 hard exit.

## 6. Quick parameter table (for AlphaForge config)

| Parameter | Primary legs | Reversal legs |
|---|---|---|
| Direction | Buy | Buy |
| Option type | CE (A) / PE (B) | PE (A′) / CE (B′) |
| Expiry | Weekly (current) | Weekly (current) |
| Strike | ITM1 at 09:31 | ITM1 at stop-out time |
| Momentum trigger | +15% from reference LTP | +10% |
| Stop loss | 20% | 10% |
| Trail step | +5% premium → SL +5% | Same |
| Target | None | None |
| Lots | 2 | 2 |
| Re-entry on SL | Opposite-side reversal leg, once | None |

## 7. Version mismatch — read before trusting the backtest

The PDF results are from **EXP1**, which had an extra rule the EXP2 code removed: an overall **Lock & Trail** (once day profit reached ≈ ₹1,000, lock ≈ ₹400; every further +₹1,000, trail the locked profit by ≈ ₹300 — values as read from the PDF screenshot). EXP2 has this switched off. The lazy-leg momentum/SL values in the PDF UI are also not fully legible.

**Action: re-run the backtest on AlgoTest from this exact EXP2 file before implementing, so the numbers you target are the numbers your code produces.**

## 8. Backtest summary (EXP1, 26-Jan-2024 → 26-Jan-2025)

Brokerage and taxes included (≈ ₹18,582); slippage set to 0%.

| Metric | Value |
|---|---|
| Net P&L | ≈ ₹2,79,200 |
| Trade entries in report | 242 (avg ≈ ₹1,154 per entry) |
| Win rate | ≈ 68.5% |
| Avg win / avg loss | ≈ +₹3,413 / −₹2,783 |
| Best / worst single trade | ≈ +₹49,559 / −₹10,438 |
| Max drawdown | −₹42,752 (17-Sep-2024 → 09-Oct-2024, ~22 days, 37 trades inside it) |
| Return / MaxDD | ≈ 6.5 |
| Losing months in 2024 | Jan (−5.5k), Feb (−4.7k), Sep (−28.8k), Nov (−4.5k) |
| Max win streak | 11 |

## 9. Red flags and risks (do not skip)

1. **Code ≠ backtest.** As per Section 7 — the headline numbers do not exactly validate the EXP2 code you exported.
2. **One-year sample, favourable year.** 2024 trended well. Sep-2024 alone lost ₹28.8k across a 3-week, 37-trade drawdown — that is what this strategy does in chop. Test out-of-sample (2022–23 and 2025–26) before capital.
3. **Curve-fitting risk.** Roughly ten tuned parameters (09:31, 15%, 20%, 5/5, 10%, 10%, ITM1, 14:40, 15:13, 2 lots) fitted on 12 months. A real edge should survive small perturbations (15→12%, ITM1→ATM); test that.
4. **Zero slippage is unrealistic here.** The strategy chases strength on entry and exits on stop — both fills land on the wrong side of the spread. Model ≥ 0.5–1% slippage per side in AlphaForge; it can cut the edge substantially.
5. **No daily loss cap in EXP2.** Worst case ≈ 4 stop-outs/day ≈ ₹20–25k at recent premiums. Recommend adding a daily max-loss circuit breaker in AlphaForge even though the original omits it.
6. **Market structure has changed since the test window.** NIFTY lot size moved 50 → 75 (Nov 2024) and the weekly expiry day has changed since ("WeeklyOldRegime: true" in the code). Expiry-day gamma/decay behaviour in live trading will differ from the backtest regime — verify current contract specs before going live.

## 10. Implementation notes for AlphaForge

- Momentum is measured on the **option premium**, not the index. Reference price = option LTP at the arming moment (09:31 for primary legs; stop-out moment for reversal legs).
- Strike is fixed at arming time; do not re-select when the trigger fires. Entries and stop exits are market orders.
- Trail moves in discrete 5% steps of entry price (AlgoTest style), not continuously.
- Trigger monitoring window: 09:31–14:40. Open positions run until trailed SL or 15:13.
- Model each leg as an independent state machine: IDLE → WATCHING → LONG → (STOPPED → arm reversal | trail-exit | EOD-exit).
