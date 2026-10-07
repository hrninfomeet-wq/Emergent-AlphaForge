# Pre-registration S1 — SENSEX joint-impulse momentum (written 2026-10-07 ~03:00 IST, BEFORE any run of it)

Rationale (measured, M1/M6): SENSEX options have a thin book (touch 2-3 lots), slower/staler quotes (ATM inter-arrival
p50 ~1.04 s, ts->ingest p90 ~1.1 s) and NIFTY/SENSEX 1-s index changes co-move (same-second corr 0.05-0.68 spot,
0.44-0.76 synthetic). A SENSEX-only blip on a thin book is more likely noise; a move confirmed by NIFTY in the same
second is market-wide information. Untested by any prior run.

Rule (index snapshots on both samples; executable quotes on discovery, LTP+measured spread on holdout):
z_w(X) = (spot_X[t]-spot_X[t-w]) / (std of 1-s changes over trailing 600 s * sqrt(w)).
Fire UP when z_w(SENSEX) >= k AND z_w(NIFTY) >= k at the same second -> buy SENSEX ATM nearest-expiry CE at t+1 s;
DOWN symmetric -> PE. Exit at t+1+H. Non-overlapping per side.
Primary cell: w=20 s, k=2.5, H=60 s.  Family: w in {10,20,30} x k in {2.0,2.5,3.0} x H in {30,60} (18 cells).
Costs: measured spread (holdout 0.231 % round trip), statutory at current rates (STT 0.15 %).

PASS requires ALL, on the HOLDOUT (22 ltpc sessions): primary net session-mean > 0 with t >= 2.0; >= 60 % of sessions
net-positive; >= 12/18 family cells net-positive; the same primary cell net > 0 on the 6-session discovery sample.
KILL if primary holdout net <= 0 or family < 9/18. Otherwise INCONCLUSIVE -> forward recording only.

## RESULT S1 (run 2026-10-07 ~03:10 IST): KILLED
Holdout primary MOM_20_2.5 H60: net -0.501 %/trade, t -0.89, 36.4 % sessions positive (n=302, 22 sessions).
Discovery primary: net -2.592 %, t -2.47, 0/6 sessions positive (n=56). Family: 3/18 holdout cells net-positive.
The mirror (REV) is positive in discovery (Sep-Oct) and ~0/negative in holdout (Jun-Jul) - the same regime flip
seen in H1. Nothing measurable in this data predicts which regime a session is in.
