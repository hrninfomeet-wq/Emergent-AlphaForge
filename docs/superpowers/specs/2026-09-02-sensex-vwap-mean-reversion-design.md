# SENSEX VWAP Mean Reversion — design

Date: 2026-09-02
Status: approved, built as **capability**. NOT edge-validated.

## Why this exists

The user asked for a SENSEX-tailored derivative of `vwap_mean_reversion`, working
across DTE0/1/2 and the other weeklies. A measurement pass run before any code was
written falsified the premise. The plugin is built anyway, by explicit user
decision, on the same basis as `premium_momentum` (edge gate failed, capability
shipped): it gives the operator a correctly-engineered SENSEX instrument to search
with, and it does not claim profit.

## What was measured

441 SENSEX sessions, 2024-11-25 .. 2026-09-02. Forward 45-bar excursion, CAS-frozen
bars excluded, entries restricted to 09:30-14:50. Every signal compared against the
**same-DTE baseline** — does the rule beat doing nothing on that same day type?

Fade (the parent's premise), lift over baseline:

| DTE | side | n   | lift   | 1st half | 2nd half |
|-----|------|-----|--------|----------|----------|
| 0   | PE   | 625 | +0.319 | +0.617   | -0.040   |
| 1   | PE   | 772 | +0.229 | +0.505   | -0.063   |
| 1   | CE   | 790 | -0.268 |          |          |
| 2   | PE   | 843 | -0.186 |          |          |
| 3   | PE   | 734 | -0.208 |          |          |

The only positive cells are DTE0/DTE1 puts, and their lift is entirely first-half.
SENSEX fell 4.7% across the window, which manufactures a put-favourable base rate.
Raising the stretch threshold 1.5 -> 3.0 ATR does not help. RSI confirmation makes
it *worse* (0.898 vs a 0.951 unconditional baseline).

Best flow-conditioned variant (stretch > 2 ATR above VWAP + `ce_volume_z -
pe_volume_z` > 2 -> CE), by quarter:

| quarter | window                   | n   | signal | base  | lift   |
|---------|--------------------------|-----|--------|-------|--------|
| Q1      | 2024-11-25 .. 2025-05-07 | 389 | 1.059  | 0.920 | +0.139 |
| Q2      | 2025-05-08 .. 2025-10-13 | 172 | 0.897  | 1.021 | -0.124 |
| Q3      | 2025-10-14 .. 2026-03-23 |  99 | 0.523  | 0.943 | -0.419 |
| Q4      | 2026-03-24 .. 2026-09-02 | 197 | 2.612  | 1.018 | +1.594 |

One quarter carries it. The signal fires on 64 of 441 sessions, 51 of them DTE0.
Its mirror (below VWAP + put aggression -> PE) is -0.213, and the two triggers fire
near-symmetrically (7,694 vs 7,592 bars) with near-identical z-score means
(+0.254 / +0.250) — so the asymmetry is noise, not market structure.

**Verdict: no VWAP-anchored variant survives a 4-quarter stability test on SENSEX.**

## The DTE trap this design does not walk into

DTE buckets are close to a weekday relabel, and the mapping *changed mid-window*:

```
        1st half     2nd half
DTE0 =  Tue (34)     Thu (42)
DTE2 =  Fri (27)     Tue (38)
DTE3 =  Thu (26)     Mon (36)
```

Per-DTE optimization on ~46 sessions per half fits a weekday and deploys onto a
different one. DTE is therefore left to `option_backtest.dte_filter` (execution
policy, per user decision) and is deliberately absent from `evaluate()`.

## Design

New plugin `sensex_vwap_mean_reversion`, `supported_instruments = ["SENSEX"]`.
`vwap_mean_reversion.py` is not modified; a test pins that.

1. **Exits in ATR multiples.** The parent's `spot_target_pts` 5-80 /
   `spot_stop_pts` 3-60 cannot express SENSEX geometry at 3.28x NIFTY's point
   scale (`fde863a`). Returned as `atr * mult`; no engine change. Stop floored
   above 0.5 ATR — below that it sits inside one bar's noise.
2. **Direction searchable.** `fade_mode` bool (True = mean-reversion, the name's
   premise; False = continuation). A `str` param would be silently dropped by
   `_build_param_space`, so mode switches are bools to stay sweepable.
3. **Stretch basis searchable.** `use_sigma_basis` bool selects session
   VWAP sigma bands (already computed as `vwap_sigma`) instead of ATR.
4. **Regime gate made honest.** The parent's allow-list admits 83% of bars
   (MIXED alone is 75%), so it barely filters. Replaced by `block_trending`,
   and a *missing* `regime` column emits a named blocker instead of silently
   blocking every signal.
5. **Optional ATM option-flow conditioning** via `required_data`, off by default.
   Only >=97%-coverage columns; `ce_oi_delta_z` / `pe_oi_delta_z` are excluded at
   38% coverage, where a rule reading them is inert on most bars.

Carried over: `live_lookback_bars = 400` (session VWAP must reach 09:15 or live
signals invert after ~13:30), and a searchable ATR-rank band — low-ATR bars travel
more ATR-multiples but fewer points, and an option buyer is paid in points.

## Tests

Registration / SENSEX-only; ATR-rank causality (trailing, never session-wide);
regime gate actually gates; missing-regime emits a blocker rather than silence;
warmup bars not admitted; stop-basis blend moves between instant and baseline ATR;
**scale-transfer with a hardcoded-point mutant that must fail** (scaling one bar by
a constant is a tautology on its own); flow gate inert when columns are absent;
parent file untouched.

## What this design refuses to claim

That it is profitable. The docstring carries the verdict numbers so the next
session cannot mistake capability for edge.
