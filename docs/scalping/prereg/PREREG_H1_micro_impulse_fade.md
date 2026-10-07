# Pre-registration H1 — sub-minute index-impulse FADE (written 2026-10-07 ~02:30 IST, BEFORE the holdout run)

Discovery sample: 6 recorded depth sessions 2026-09-07 .. 2026-10-06 (`tick_archive`), M3 grid of
4 windows x 4 thresholds x 3 horizons x {IMP, FADE, SYN, OBI}. Best-of-grid on 6 sessions => selection bias.
The FADE family dominated the top of BOTH indices; IMP (momentum) the bottom.

Holdout sample: the `ticks` collection, mode `ltpc` (LTP only, no quotes), every session 2026-05-27 .. 2026-07-17
with >= 50,000 option ticks for the index. These sessions were never looked at in M1-M3.

Execution model on the holdout (LTP has no bid/ask): entry = LTP(t+1s) x (1 + s/2), exit = LTP(t+1+H) x (1 - s/2),
s = measured ATM round-trip spread % of mid from M1 (NIFTY 0.234 %, SENSEX 0.231 %). Statutory = current rates
(STT 0.15 % sell, NSE txn 0.03553 % / BSE 0.0325 %, SEBI 0.0001 %, stamp 0.003 % buy, GST 18 %).
Index z-score uses the index snapshot clock (received_ts; the holdout has no ingest_ts).

Primary cells (fixed now, not re-tuned):
* NIFTY  FADE w=30 s, k=2.5, H=30 s   (discovery gross +1.83 %, 4/6 sessions, n=86)
* SENSEX FADE w=30 s, k=3.0, H=60 s   (discovery gross +2.66 %, 5/6 sessions, n=44)
Family check: all FADE cells with w in {10,20,30}, k in {2.5,3.0}, H in {30,60} (12 per index).

PASS (per index) requires ALL of:
1. primary cell NET (after spread + statutory) session-mean > 0 with session-level t >= 2.0;
2. >= 60 % of holdout sessions with positive net mean (sessions with >= 1 trade);
3. family: >= 9 of 12 FADE cells net-positive by session mean (direction consistency, not cherry-picking);
4. IMP mirror cells net-negative (the mechanism is reversal, not noise).
KILL if the primary cell's net session-mean <= 0 OR family < 6/12 positive.
Anything in between = INCONCLUSIVE -> forward recording only, no paper promotion.

## RESULT H1 (run 2026-10-07 ~02:40 IST): KILLED on both indices
Holdout 22 sessions (2026-06-11..2026-07-17). NIFTY primary net -0.775 %/trade (t -1.88, 31.8 % sessions +), SENSEX
primary net -0.521 % (t -1.14, 40.9 %). Family: 0/12 FADE cells net-positive on EITHER index. NIFTY IMP mirror turned
positive-but-insignificant (best t 1.50) = sign flip between samples = noise. SENSEX IMP: 12/12 net-negative.
