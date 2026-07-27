# Market-session validation plan — 2026-07-27

> Today's running order. Monday's Phase-5B paper validation is still outstanding
> and is the **main event**; everything built since (live cockpit, market-analysis
> engine, and the C2/H1/audit safety fixes) has never seen a live market and is
> validated alongside it.
>
> Companion docs: `docs/phase5b-market-validation-runbook.md` (the detailed paper
> procedure — follow it, don't re-derive it), `docs/live-readback-checklist.md`,
> `docs/live-cockpit-audit-2026-07-25.md`.

---

## ⚠ Read first — today's risk posture

| Fact | Consequence |
|---|---|
| `LIVE_AUTOPLACE_ARMED=1` in `backend/.env` | Real transmission is **ARMED**. The only thing standing between a signal and a real order is that **no deployment is in live mode**. |
| **0 live deployments** (all paper / paused / archived) | Nothing can transmit today *unless you deliberately enable one*. |
| Flattrade token: `expired: true`, `regenerate_after_6am: true` | Needs a fresh login **through AlphaForge** after 06:00 IST. Never the MCP. |
| **C3 (account-global caps) is still OPEN** | Per-deployment caps are enforced; account-wide exposure is **not**. |

**Recommendation: make today a PAPER + READ-ONLY day. Do not enable any
deployment to live.** Two of the three pre-real-money blockers (C2, H1) are
closed but C3 is not, and the validations that need a real transmit (Item 1,
the C2 fence) are worth strictly less than the risk of trading real money
through an un-capped account. Defer those to the dedicated 1-lot live day after
C3 lands — that is exactly what §6 of the Phase-5B runbook is for.

---

## ⏱ T-60 → T-0 (before 09:15 IST): pre-market

1. **Log in to Flattrade** — Live Trading page → Flattrade chip → *Login to
   Flattrade* (must be after 06:00 IST). Confirm the chip turns green and
   `expired` goes false.
2. **Start the Upstox stream in FULL mode.** The market-analysis panel needs
   **open interest**, which only arrives in a full-mode feed — yesterday PCR and
   max-pain were correctly suppressed with `option_oi_unavailable` because the
   stream was down. Use the header's Start control (it starts full mode with tick
   persistence).
3. **Verify the candle roller is actually running** — the stream and the roller
   are different things, and a running stream with a dead roller produces "ACTIVE
   deployment, 0 signals all day" (a known past failure). The Live Signals page
   LED / feed-health banner is the tell.
4. **Confirm 0 live deployments** — cockpit → Deployments summary should read
   `0 live`. If anything says Live, stop and investigate before the open.
5. **Decide `LIVE_AUTOPLACE_ARMED` deliberately.** Leaving it at 1 is fine for a
   paper day (nothing is live). If you want belt-and-braces, set it to 0 and
   restart the backend — then even an accidental enable only dry-runs.
6. **Warehouse freshness** — run the catch-up if the last session is missing, so
   the analysis engine's daily/weekly/monthly trend has data.

---

## Tier A — zero-risk, do in the first 15 minutes of the session

These need only a live feed. They are the first real test of everything built in
the last few days.

- [ ] **Market Pulse populates**: structure label + regime meter + confidence,
      and the **multi-timeframe trend** (intraday / daily / weekly / monthly)
      shows real arrows, not "—".
- [ ] **S/R range bar**: spot marker sits between S1 and R1 and *moves* during
      the session; pivot shown.
- [ ] **Market Analysis tiles**: **PCR and max-pain now compute** (they were
      correctly suppressed with a warning while the feed was down — this is the
      key check that the honest-degradation path flips back on). IV rank shows
      its source (`VIX proxy` is expected — there is no stored ATM-IV history).
      ATM straddle + implied move populate.
- [ ] **Option chain** renders with live LTPs and OI, ATM row highlighted.
- [ ] **Account tabs**: Funds & Margin populates from the live session; Holdings
      loads (or says "no holdings" — *not* "Loading…" forever); Order book and
      Trade book render.
- [ ] **Broker module**: both chips green, token countdown sane.
- [ ] **Execution-state strip** (restored after the cockpit rewrite) shows the
      verdict: entries dry-run vs TRANSMIT, and the Stand-down control is present.
- [ ] **No console errors**; degraded banner absent while polls succeed.

---

## Tier B — the main event: Monday's Phase-5B paper validation

Follow **`docs/phase5b-market-validation-runbook.md` §1–§5** as written. Summary
of what it asks for:

- [ ] §2 — deploy the two paper `premium_momentum` deployments (NIFTY).
- [ ] §3 — in-session observables for deployment A (signals, entries, exits).
- [ ] §4 — the **deliberate mid-session restart with an open paper position**;
      this is the highest-value item because it exercises guard rehydrate +
      reboot reconcile.
- [ ] §5 — capture the evidence listed there.
- [ ] §5b — the authorization-model checks (explicitly **no real money needed**).

**New since that runbook was written — check while the restart drill runs:**

- [ ] **Item 3 pays off here.** After the §4 restart, any position re-attached by
      the guard should now show the amber **"DEFAULT STOP"** badge, a
      `N default-stop` count in the guard header, and a cockpit banner naming the
      symbol. This is the first chance to see that fix fire for real.
- [ ] **Paper lazy-leg arming** (built this cycle): if a `premium_momentum`
      primary leg stops out in paper, confirm the opposite lazy leg arms — this
      path previously existed only in backtest and live.

---

## Tier C — opportunistic, only if the data appears

- [ ] **Item 2**: if a resting OCO/GTT exists, confirm *Cancel backstop* now
      requires a second confirm and names the symbol. **Do not actually cancel a
      backstop protecting a real position** — arm it, read the warning, click
      *Keep it*.
- [ ] **Item 4**: only observable if the overall-settings read genuinely fails;
      don't manufacture it today.
- [ ] **H1**: the 409 needs a Stop to land inside the enable preflight — not
      practically reproducible by hand, and reproducing it requires enabling
      live. Covered by tests; leave it.

---

## Tier D — DEFERRED to the 1-lot live day (after C3)

Do **not** attempt these today:

- **Item 1 (transmission unconfirmed)** — needs a real transmit plus a forced
  lost response (DevTools offline mid-place). Real money.
- **C2 transmit fence** — the fence only executes on an armed real transmit; in
  dry-run the executor returns before reaching it. Real money.

Both are covered by tests and are the first two items on the live-day agenda.

---

## Evidence to capture (for the next session)

1. Screenshot of the cockpit mid-session with the analysis panels populated.
2. The `/api/market/analysis?instrument=NIFTY` payload at a live moment — confirm
   `pcr_oi` and `max_pain` are non-null and `warnings` no longer contains
   `option_oi_unavailable`.
3. Signals / paper trades produced, with entry+exit reasons.
4. The restart drill: guard state before and after, plus whether the DEFAULT-STOP
   badge appeared.
5. Anything that rendered "—", "Loading…" forever, or a stale value — those are
   the honest-degradation paths failing.

---

## After the bell

- Record results in `learning_log.md` and flip the relevant rows on
  `docs/AGENT_TODO.md`.
- If the paper day is clean, the next development step remains **C3** (the last
  pre-real-money blocker), then the 1-lot live day per Phase-5B §6.
