# 03 — Strategy specifications: NIFTY N1 and SENSEX S1-E

_Both specs are implemented exactly as written in `backend/app/scalping/config.py` (`NIFTY_N1`,
`SENSEX_S1E`) and `signals.py`. Every threshold has a unit and a provenance tag:
**measured** (a named measurement in doc 02), **provisional** (a paper-only starting value), **policy** (a
deliberate safety rule), **hypothesis** (the rule under test)._

> **Read this first.** Neither spec has positive evidence. Five hypothesis families failed on the data available
> (doc 02), and **N1 was KILLED by its stage-2 replay gate** (doc 05). These are **pre-registered falsification tests**: the
> cheapest honest way to collect a clean forward sample, with kill criteria fixed in advance. They are not
> recommended for real money, and the engine cannot place a real order.

## Shared definitions

* **Decision grid:** one decision per wall/replay second, on data whose local `ingest_ts` is ≤ the decision
  instant. The feed is ~1 Hz (M1), so nothing finer is observable.
* **Synthetic forward** `F = mid(CE_K) − mid(PE_K) + K`, K = the ATM strike of the nearest expiry, valid only when
  both quotes are ≤ 2,000 ms old. Leads the index print by ~1 s (M3, measured).
* **Impulse z-score** `z_w(t) = (x[t] − x[t−w]) / (σ₁ · √w)`, σ₁ = sample std of 1-s changes of x over the trailing
  600 s, defined only after ≥ 300 valid 1-s changes (≈ 09:20). A missing second breaks the series.
* **DTE:** trading days to expiry from the holiday-aware calendar (`app/dte.compute_dte`); 0 = expiry day.
* **Executable prices:** entries cross the ask, exits cross the bid; stops, targets and trails are evaluated on
  the **bid** (what a seller can get), never on LTP.

## N1 — NIFTY options: synthetic-forward impulse momentum (taker)

**Opportunity (hypothesis).** When the options-implied forward moves ≥ 3σ in 30 s, order flow may persist for a
further ~30 s, long enough to buy the ATM option in the move's direction and sell it before friction dominates.
NIFTY is the only index where any momentum cell was positive out of sample (holdout IMP w30 k3.0: +1.34 % net per
trade at H30, t 1.50; 64 % of sessions positive) and it has the deepest book and fastest quotes.
**Why it might not survive costs (the honest case against):** the same rule on the index print was negative on the
discovery sample; the holdout cell was found by looking at the holdout (HARKing — that sample cannot confirm it);
round-trip friction is 0.47 % of premium, and limit entries tend to miss the trades that run away.
**Status: KILLED at stage 2** (−₹15.5 per trade over 39 replayed trades, median −₹36; doc 05). Kept for the record
and as the reference configuration of the engine; do not paper-run it as a candidate.

| Rule | Value | Unit | Provenance |
|---|---|---|---|
| **May trade** | NIFTY weekly options; DTE ∈ {0, 1, 2}; 09:20–14:45 IST | — | provisional (0DTE has the tightest spreads in ticks and the deepest touch, M1) |
| **No trade** when | outside the window; DTE 3–4; synthetic unavailable or > 2,000 ms old; quote > 1,500 ms old; spread > 8 ticks or > 0.35 % of mid; premium < ₹20 or > ₹400; best-ask qty < 1× order qty; 5-level ask qty < 3× order qty; limit price beyond the LPP band; reconcile pending / halted / paused / daily cap / cooldown / order-rate budget | — | measured (M1: spread p90 0.32 %, p99 0.41 %; quote inter-arrival p90 1.05 s; ₹28 premium had spread p90 0.61 %) + policy |
| **Entry signal** | z₃₀(F) ≥ +3.0 → buy ATM **CE**; z₃₀(F) ≤ −3.0 → buy ATM **PE** | σ | hypothesis |
| Strike | ATM = round(F / 50) × 50, nearest weekly expiry | — | policy |
| Rollover | always the nearest listed expiry; on expiry day the same-day contract (DTE 0) is used until the 14:45 entry cutoff; a position never carries past 15:00 | — | policy |
| **Entry order** | BUY LMT, price = min(ask + 2 ticks, ask × 1.005), rounded **up** to ₹0.05; `ret=DAY` (IOC not permitted by the app); qty = 1 lot (65) | ₹ | policy (the only API order type, doc 01 §3) |
| Entry timeout | unfilled remainder cancelled after 2,000 ms; a partial fill becomes the position | ms | provisional |
| Ack timeout | no broker acknowledgement within 3,000 ms → status UNKNOWN → reconcile | ms | policy |
| **Initial stop** | bid ≤ entry × (1 − 5.0 %) | % of entry fill | provisional, anchored to measured 30-s adverse excursion p90 −5.1 % (M2) |
| **Profit target** | bid ≥ entry × (1 + 6.0 %) | % | provisional, anchored to measured 60-s favourable excursion p90 6.7 % (M2) |
| **Trailing** | armed once the peak bid ≥ entry × 1.03; exit when bid ≤ entry + 50 % of the peak gain | % | provisional |
| **Maximum hold** | 30 s from the first fill (time exit) — the primary exit | s | hypothesis cell H = 30 |
| **Forced exit** | 15:00 IST; kill switch; halt; daily-loss pause; no quote for 5,000 ms while holding | — | policy |
| Exit order | SELL LMT at bid − {2, 6, 20, 60} ticks (escalating each 1,500 ms after cancel-confirm), never below the LPP floor; the last rung repeats until filled | ticks, ms | provisional + policy |
| **Sizing** | 1 lot; one position at a time | lots | PROVISIONAL-PAPER |
| Max trades / day | 20 | trades | provisional (bound by on 2026-09-29 in replay) |
| **Daily loss limit** | realized net + open MTM at the bid ≤ −₹2,000 → no more entries today, exit any position | ₹ | **PROVISIONAL-PAPER — not an approved monetary limit** |
| Loss-streak pause | 4 consecutive net losses → no entries for 1,800 s | trades, s | provisional |
| Cooldown | 5 s after a round trip closes | s | provisional |
| Order rate | ≤ 3 orders/s and ≤ 15/min per engine (two engines ⇒ ≤ 30/min of Flattrade's 40/min per key) with 2 orders held back for exits | orders | policy |

## S1-E — SENSEX options: expiry-day joint-impulse momentum (taker)

**Opportunity (hypothesis).** A SENSEX move confirmed by NIFTY in the same 20-s window is market-wide information
rather than a thin-book blip, and on expiry day the cheap, convex 0DTE premium may react enough to clear friction.
**Why expiry-day only:** the all-days version of this rule was pre-registered and **killed** (M7: holdout net
−0.50 %/trade, 3/18 cells positive). The one SENSEX regime with no evidence either way is expiry day (Thursday),
because none has been recorded with depth. This spec exists to test exactly that and nothing else.
**Why it might not survive costs:** every SENSEX impulse rule, in both directions, was negative in both samples;
friction is 34 % of the median 60-s move (vs 25 % on NIFTY); the book is thin (touch 2–3 lots).

| Rule | Value | Unit | Provenance |
|---|---|---|---|
| **May trade** | SENSEX weekly options, **DTE = 0 only** (expiry Thursday), 09:20–14:30 IST | — | hypothesis (the untested regime) |
| **No trade** when | not expiry day; outside the window; SENSEX or NIFTY index print > 2,000 ms old; quote > 2,000 ms old; spread > 20 ticks or > 0.30 % of mid; premium < ₹20 or > ₹600; best-ask qty < 1× order qty; 5-level ask < 3× order qty; plus every engine gate as N1 | — | measured (M1: SENSEX spread p90 0.28 %, p99 0.31 %; spread p50 11–18 ticks; quote inter-arrival p50 1.04 s) + policy |
| **Entry signal** | z₂₀(SENSEX index) ≥ +2.5 **and** z₂₀(NIFTY index) ≥ +2.5 → buy ATM **CE**; both ≤ −2.5 → buy ATM **PE** | σ | hypothesis (M7 primary cell) |
| Strike / expiry | ATM = round(SENSEX / 100) × 100, the same-day expiry | — | policy |
| **Entry order** | BUY LMT at min(ask + 3 ticks, ask × 1.005), rounded up; qty 1 lot (20) | ₹ | policy (wider cross: SENSEX spreads are ~5× NIFTY in ticks) |
| Entry timeout / ack timeout | 2,500 ms / 3,000 ms | ms | provisional (slower quotes) / policy |
| **Initial stop** | bid ≤ entry × (1 − 4.0 %) | % | provisional (non-expiry SENSEX 30-s adverse p90 −2.9 %, p95 −3.8 %; expiry day is unmeasured and likely wider) |
| **Profit target** | bid ≥ entry × (1 + 5.0 %) | % | provisional (non-expiry 60-s favourable p90 3.7 %) |
| **Trailing** | armed at +2.5 %; give back 50 % of the peak gain | % | provisional |
| **Maximum hold** | 30 s from first fill | s | provisional (0DTE moves faster; the killed all-days cell used 60 s) |
| **Forced exit** | 15:00 IST; kill; halt; daily-loss pause; no quote for 6,000 ms while holding | — | policy |
| Exit order | SELL LMT at bid − {3, 10, 30, 90} ticks, re-priced every 2,000 ms after cancel-confirm, LPP-clamped, never gives up | ticks, ms | provisional + policy |
| **Sizing** | 1 lot (touch depth 2–3 lots, M1); one position at a time | lots | measured + PROVISIONAL-PAPER |
| Max trades / day | 6 | trades | provisional |
| **Daily loss limit** | −₹1,500 (realized net + open MTM at bid) | ₹ | **PROVISIONAL-PAPER** |
| Loss-streak pause | 3 consecutive losses → 1,800 s | — | provisional |
| Order rate | ≤ 3/s, ≤ 15/min, exit reserve 2 | orders | policy |

## What is deliberately NOT copied between the two

| Parameter | N1 (NIFTY) | S1-E (SENSEX) | Why different |
|---|---|---|---|
| Reference | options-implied synthetic forward | both index prints | synthetic leads the NIFTY index (M3); the SENSEX hypothesis is cross-index confirmation |
| Spread cap | 8 ticks / 0.35 % | 20 ticks / 0.30 % | spreads 5× wider in ticks on SENSEX; same in % (M1) |
| Quote freshness | 1,500 ms | 2,000 ms | SENSEX quotes arrive every ~1.04 s vs ~0.7 s (M1) |
| Stop / target / trail arm | 5 / 6 / 3 % | 4 / 5 / 2.5 % | NIFTY's relative excursions are ~1.4–1.8× SENSEX's (M2) |
| Exit ladder | 2/6/20/60 ticks, 1.5 s | 3/10/30/90 ticks, 2 s | spread and quote-cadence differences |
| DTE | 0–2 | 0 only | SENSEX all-days rule killed; expiry day untested |
| Trades / day, loss cap | 20, ₹2,000 | 6, ₹1,500 | thinner book, fewer expected signals |

## Pre-registered acceptance criteria for the forward paper test (stage 3)

Fixed now, before any forward session is observed. Counted only on sessions recorded AFTER 2026-10-07.

| | N1 | S1-E |
|---|---|---|
| Minimum sample before any verdict | 20 complete sessions and ≥ 150 closed trades | 12 SENSEX expiry sessions and ≥ 40 closed trades |
| PASS (all) | net expectancy > ₹0/trade after charges; session-block bootstrap 95 % CI lower bound of daily net > ₹0; ≥ 60 % of sessions net-positive; result holds with fills re-simulated at 3× latency and 20 % depth; zero safety violations | same, on expiry sessions |
| KILL (any) | net expectancy ≤ ₹0 at the minimum sample; or median trade ≤ −₹(statutory charges); or any unintended short / unprotected position | same |
| A PASS does not authorise real money | it authorises only a readiness review (doc 06) | same |
