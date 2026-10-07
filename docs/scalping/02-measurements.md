# 02 — Measurements: market microstructure, NIFTY vs SENSEX, and every hypothesis tested

_All numbers here are **measured** on recorded data (Confirmed) unless labelled otherwise. Scripts:
`backend/scripts/scalping/research/` (M1–M7, read-only Mongo, host venv). Raw outputs:
`docs/scalping/results/`. Pre-registrations: `docs/scalping/prereg/`._

## Samples

| Sample | Sessions | What it holds | Used for |
|---|---|---|---|
| **Discovery** (`tick_archive`) | 2026-09-07 (full), 09-09, 09-15, 09-16, 09-29 (full), 10-06 (partial) | 1 Hz index prints + ATM±3 option quotes with 5-level depth | M1–M3, M5, M6, stage-2 replay |
| **Holdout** (`ticks`, ltpc era) | 22 sessions 2026-06-11 → 2026-07-17 | 1 Hz index prints + option LTP (no quotes) | M4, M7 — each pre-registered before it was run |
| Warehouse (`options_1m`) | ~430 sessions | 1-minute bars | prior research only (no sub-minute content) |

Caveats (Confirmed): only 2 discovery sessions are full days; the sessions before 2026-09-15 carry no local
`ingest_ts`, so the Upstox frame clock `received_ts` stands in (latency figures from those days are not local-clock);
**no SENSEX expiry day (DTE 0) was ever recorded with depth**; the holdout has no bid/ask, so its executable
prices are LTP ± half the measured spread.

## M1 — microstructure per session (ATM, nearest expiry)

| Day | Index | DTE | Premium p50 | Spread p50 / p90 (pts) | Spread p50 (ticks) | Spread % of mid | Touch qty p50 (lots) | Ask 5-level p10 (lots) | Quote inter-arrival p50 / p99 (ms) | Option ts→ingest p50 / p90 (ms) |
|---|---|---|---|---|---|---|---|---|---|---|
| 09-07 | NIFTY | 1 | 71.4 | 0.15 / 0.25 | 3 | 0.236 | 9 | 101 | 735 / 1,220 | 152 / 234 |
| 09-09 | NIFTY | 4 | 102.2 | 0.25 / 0.40 | 5 | 0.220 | 5 | 67 | 742 / 1,189 | 202 / 339 |
| 09-15 | NIFTY | 0 | 59.1 | 0.15 / 0.20 | 3 | 0.253 | 8 | 119 | 645 / 1,506 | 334 / 494 |
| 09-16 | NIFTY | 4 | 160.9 | 0.35 / 0.45 | 7 | 0.222 | 4 | 45 | 668 / 2,866 | 1,364 / 1,668 |
| 09-29 | NIFTY | 0 | 53.5 | 0.15 / 0.20 | 3 | 0.257 | 8 | 132 | 728 / 1,733 | 219 / 297 |
| 10-06 | NIFTY | 0 | 27.9 | 0.10 / 0.15 | 2 | 0.340 | 26 | 265 | 707 / 1,186 | 271 / 344 |
| 09-07 | SENSEX | 3 | 349.9 | 0.75 / 1.25 | 15 | 0.231 | 3 | 18 | 1,050 / 3,151 | 373 / 1,058 |
| 09-09 | SENSEX | 1 | 239.7 | 0.55 / 0.70 | 11 | 0.227 | 3 | 30 | 1,043 / 1,949 | 282 / 838 |
| 09-15 | SENSEX | 2 | 379.5 | 0.85 / 1.05 | 17 | 0.225 | 2 | 13 | 1,036 / 4,035 | 500 / 1,136 |
| 09-16 | SENSEX | 1 | 326.2 | 0.75 / 0.90 | 15 | 0.225 | 2 | 19 | 923 / 5,190 | 1,339 / 2,128 |
| 09-29 | SENSEX | 2 | 424.8 | 0.90 / 1.30 | 18 | 0.245 | 2 | 14 | 1,048 / 3,319 | 466 / 1,128 |
| 10-06 | SENSEX | 2 | 347.0 | 0.80 / 1.00 | 16 | 0.236 | 3 | 14 | 1,048 / 2,124 | 445 / 1,064 |

Index feed: inter-arrival p50 1,039–1,048 ms, p90 ≤ 1,090 ms on every session — a 1 Hz snapshot. NIFTY index
exchange-time → ingest p50 1.0–2.2 s; SENSEX 0.24–2.5 s (varies by day).

**Findings.** (1) Spreads are proportional to premium: ~0.22–0.26 % of mid on BOTH indices (0.34 % on a ₹28 0DTE
NIFTY premium). (2) SENSEX spreads are 5× NIFTY's **in ticks** (11–18 vs 2–7) because SENSEX premiums are ~5×
larger; in % they are the same. (3) NIFTY's book is far deeper (touch 4–26 lots vs 2–3; 5-level ask p10 45–265
lots vs 13–30). (4) SENSEX quotes are slower (p50 ~1.04 s vs ~0.7 s) and staler (ts→ingest p90 ~1.1 s vs ~0.3 s).
(5) The app's cost model assumes a 1 % round-trip spread; the measured round-trip spread is ~0.23 %.

## M2 — the null baseline (random entry, executable prices)

Buy at the ask one second after a random decision second, sell at the bid H seconds later; every 5 s, both CE
and PE, 09:20–15:00. ~24,000 entries per index.

| | NIFTY | SENSEX |
|---|---|---|
| Round-trip spread at entry (% of premium, p50) | 0.234 | 0.231 |
| Statutory charges at current rates (% of premium, round trip) | 0.237 | 0.230 |
| **Total friction** | **0.47 %** | **0.46 %** |
| Gross return of a random round trip, any H from 5 to 120 s | −0.23 % to −0.25 % (≈ the spread; drift negligible) | −0.21 % |
| Median absolute mid move, 10 s / 30 s / 60 s / 120 s | 0.77 / 1.36 / 1.89 / 2.72 % | 0.54 / 0.94 / 1.36 / 1.90 % |
| Friction ÷ median 60-s move | 25 % | 34 % |
| Break-even hit rate for a symmetric bracket at the median 30-s / 60-s move | 67 % / 62 % | 74 % / 67 % |
| 60-s adverse excursion p50 / p90 / p95 / p99 | −1.9 / −7.2 / −10.3 / −17.2 % | −1.4 / −4.1 / −5.3 / −8.1 % |
| 60-s favourable excursion p50 / p90 | 1.4 / 6.7 % | 0.9 / 3.7 % |
| Premium p10 / p50 / p90 | ₹41 / ₹66 / ₹161 | ₹262 / ₹341 / ₹480 |

No random bracket (target 0.5–3 %, stop 0.5–2 %, hold 30–120 s) was positive on either index; t-statistics over
sessions −7 to −21. NIFTY's relative mobility is ~1.4–1.8× SENSEX's, partly because 3 of 6 NIFTY sessions were
expiry days and no SENSEX one was. (The M2 script's own net column used the pre-2026 0.10 % STT; the friction row
above is at the current 0.15 %.)

**What "small profit" and "small stop" must mean (derived).** A round trip costs ~0.47 % (NIFTY) / ~0.46 %
(SENSEX) of premium before any edge — ₹20 per NIFTY lot at a ₹66 premium, ₹31 per SENSEX lot at ₹341. A target
smaller than ~3× friction (≈1.4 %) needs an implausible hit rate; a target near the median 30–60 s move
(1.4–1.9 % NIFTY, 0.9–1.4 % SENSEX) needs a 62–74 % hit rate against an equal stop. Stops tighter than the median
30-s adverse move (−1.3 % NIFTY, −1.0 % SENSEX) are noise-triggered more often than not. That is why the specs
(doc 03) use a time exit as the primary exit and wide stop/target bands at the p90 excursions as tail protection.

## M3 — exploratory sub-minute signal screen (discovery sample only)

Families on the 1-s grid, decision at t, buy at ask(t+1 s), sell at bid(t+1+H), non-overlapping:
index impulse momentum (IMP) and fade (FADE) — z of a w-second index change against the trailing 600-s std of
1-s changes; the same on the options-implied synthetic forward (SYN); touch order-book imbalance (OBI).
4 windows × 4 thresholds × 3 horizons (+ OBI) = 360 cells.

* FADE dominated the top of both indices (best: SENSEX FADE w30 k3.0 H60 +2.66 % gross, t 2.28, 5/6 sessions;
  NIFTY FADE w30 k2.5 H30 +1.83 %, t 1.09); IMP the bottom (mirror).
* OBI carried **no information**: every cell ≈ the −0.2 % baseline on both indices.
* SYN ≈ 0.
* Lead–lag: the options-implied synthetic forward leads the index print by ~1 s (corr at 1-s lag 0.17 vs 0.08 the
  other way).

Best-of-360 on 6 sessions is selection-biased by construction; these were hypotheses, not results.

## M4 — PRE-REGISTERED H1 (impulse FADE) on the 22-session holdout: **KILLED** (both indices)

Pre-registration: `prereg/PREREG_H1_micro_impulse_fade.md` (written before the run).

| | NIFTY primary (w30 k2.5 H30) | SENSEX primary (w30 k3.0 H60) |
|---|---|---|
| Net per trade (session mean) | **−0.775 %** (t −1.88) | **−0.521 %** (t −1.14) |
| Sessions net-positive | 31.8 % | 40.9 % |
| FADE family cells net-positive | **0 / 12** | **0 / 12** |
| IMP mirror | turned positive, best t 1.50 (sign flip vs discovery = noise) | 12 / 12 net-negative |

## M5 — passive (inside-spread) entry: **rejected** (exploratory, discovery sample)

A resting buy at bid + 1 tick fills, but adverse selection exceeds the spread saved: filled passive entries
returned −0.48 to −0.77 % (NIFTY) and −0.30 to −0.46 % (SENSEX) with a taker exit, vs −0.23 / −0.21 % for a plain
taker round trip. A passive exit was worse still (it caps winners and leaves losers running). Caveat: at 1 Hz the
"conservative" fill rule (an offer reaches our price) overstates adverse selection and the "optimistic" one
(a trade prints at/below our price) ignores queue priority — both were worse than taking.

## M6 — cross-index lead–lag: real but not monetisable

Same-second correlation of the two synthetic forwards 0.44–0.76; a 1-s lagged correlation of ~0.37 exists
(SENSEX changes slightly ahead on this feed) and nothing beyond 1 s. One second of predictable move is ~0.1 % of
premium against ~0.46 % friction. The "SENSEX laggard" trade was positive only on tiny samples (n ≤ 47) and ≈ 0
unconditioned — not pre-registered.

## M7 — PRE-REGISTERED S1 (joint NIFTY+SENSEX impulse → SENSEX option): **KILLED**

Pre-registration: `prereg/PREREG_S1_joint_impulse.md`. Holdout primary (w20 k2.5 H60): net **−0.50 %** per trade,
t −0.89, 36 % sessions positive (n = 302); discovery: −2.59 %, 0/6 sessions positive; family 3/18 cells positive.
The mirror (fade) was positive in Sep–Oct and ≈ 0/negative in Jun–Jul — the same regime flip seen in H1. Nothing
in this data predicts which regime a session is in.

## NIFTY vs SENSEX — the measured differences that shape the specs

| Dimension | NIFTY | SENSEX | Design consequence |
|---|---|---|---|
| Lot / premium | 65 / ₹28–161 | 20 / ₹240–425 | per-lot rupee risk similar (~₹4.3k vs ~₹6.8k notional) |
| Spread | 2–7 ticks, 0.22–0.34 % | 11–18 ticks, 0.22–0.25 % | tick-based filters must differ; % filters can be similar |
| Depth | touch 4–26 lots | touch 2–3 lots | SENSEX: 1 lot only; NIFTY can absorb a few lots |
| Quote cadence / staleness | ~0.7 s, ts→ingest p90 ~0.3 s | ~1.04 s, p90 ~1.1 s | SENSEX needs a looser freshness gate and a wider exit ladder |
| Mobility (median 60-s move) | 1.89 % | 1.36 % | friction is 25 % vs 34 % of a minute's move — SENSEX is structurally harder |
| Tail (60-s adverse p99) | −17 % | −8 % | NIFTY stop bands wider in % |
| Impulse signals | discovery negative, holdout weakly positive (contradictory) | negative in BOTH samples, both directions | no SENSEX impulse rule has support; SENSEX spec restricted to its only untested regime |
| Expiry | Tuesday | Thursday | SENSEX expiry day never recorded — must be recorded before any SENSEX conclusion |

## Prior evidence this work builds on (not re-litigated)

`docs/OPTION_BUYING_MICROSTRUCTURE_2026-08.md`, `docs/INTRADAY_OPTION_BUYING_CANDIDATES_2026-08.md` and the
2026-06/07 option-buying verdicts: on 1-minute data, long ATM options have MFE/MAE 0.85–0.95 at 3–30 min horizons
(negative gross payoff before costs) on both indices, across breakout, fade and volatility-compression entries.
This work extends that to the 1-second scale with executable quotes and finds the same answer.
