# PC-Down OCO Backstop — Implementation Plan (Phases A–C) — v2 (post-adversarial-audit)

> **For agentic workers:** REQUIRED SUB-SKILL — execute with `superpowers:subagent-driven-development`
> (fresh implementer per task + two-stage review). Steps use `- [ ]` checkboxes.
> Spec: `docs/superpowers/specs/2026-06-26-pc-down-oco-backstop-design.md`.
> **v2 incorporates the adversarial audit (2026-06-27): 4 BLOCKER + 4 MAJOR + MINOR fixes folded in.**

**Goal:** Rest a broker OCO (stop+target) on every deployed live option-buy fill so a PC-down
position is loss-capped and profit-booked without a local process — plus a broker margin
pre-check, depth-aware square pricing, and transient-safe reboot reconciliation.

**Tech stack:** FastAPI + motor (Python 3.12). **All tests live at `<repo>/tests/`** (NOT
`backend/tests/`); run **from the repo root** so the AST test's relative `backend/app/...` path
resolves: `"<repo>/.venv/Scripts/python.exe" -m pytest tests/<file> -q`. Each test file
`sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))`, declares its own
FakeDB, drives async via `asyncio.run`, monkeypatches module-level seams with `raising=False`.

---

## The four safety invariants this plan must hold (audit-derived — do not regress)

1. **Catastrophe band is STRUCTURALLY wider than the guard stop.** The OCO SL trigger must be a
   *lower premium* than the software guard's actual stop for that deployment — for EVERY config
   path including the deep-default (guard default stop = **50%**, `auto_live.py:46`). A fixed 50%
   catastrophe default COLLIDES with it. Therefore the catastrophe stop is **derived**:
   `eff_stop_pct = max(configured_pct, guard_stop_pct + MIN_GAP_PP)` (MIN_GAP_PP default 15).
2. **Never square a stale netqty.** Before placing any square, re-confirm the position is still
   non-flat from a *fresh* read; if flat (e.g. the OCO already fired), abort with no order.
3. **Cancel the OCO only AFTER a confirmed real square fill.** The guard square is dry-run when
   `LIVE_GUARD_ARMED=0` (the default while validating); cancelling the resting OCO on a dry-run
   breach would strip protection. Cancel only when `result["squared"] and not result["dry_run"]`.
4. **Exit product must match the open position.** Deployed entries become NRML (`prd="M"`); the
   shared `square_position` must exit with the position's own product, not a hardcoded MIS.

Plus: **dry-run transmits nothing new** (OCO place sits behind the entry's `LIVE_AUTOPLACE_ARMED`
gate; margin pre-check is a read); **the AST `exactly one place_order in executor.py` invariant
holds** (OCO `place_oco` lives in `live_deploy_context.py`); **OCO-place failure never unwinds a
filled+guarded entry** (wrap ONLY the OCO in try/except inside `_arm`, AFTER the mandatory
register, returning `oco_al_id=None`; the register-failure→`_abort_protect` contract is unchanged).

## File structure

**Phase A** — `flattrade_client.py` (+`order_margin`,`get_quotes`), `mock_noren.py` (+ the two,
`or {}` fixtures), `auto_square.py` (exit prd = position's prd), `order_builder.py` (`product`
kwarg → failing verdict on unknown), `executor.py` (NRML for deployed + broker-margin gate),
`auto_live.py` (thread `product="NRML"`), `margin.py` (`broker_margin_verdict`),
`routers/live_broker.py` (`/margin-probe`, M-leg only).

**Phase B** — `oco_levels.py` (new, derived band + invariant), `live_position_guard.py`
(`register(oco_al_id,token,prd)` + pre-square netqty re-confirm + cancel-OCO-after-real-fill),
`live_deploy_context.py` (place OCO in `_arm`, client bound at context; pct via per-signal call),
`executor.py` (capture `arm()`→`oco_al_id`), `auto_live.py` (read `risk.live` pct → `arm_for(...)`;
write `oco_al_id`/`oco_error` on the doc), `routers/deployments.py` (`_LiveArmBody` pct + persist +
cancel-OCO in `_square_live_positions_for_deployment`), `routers/live_broker.py` (kill-switch
GTT/OCO sweep ONLY), `live/live_blotter.py` + frontend (surface `oco_error`).

**Phase C** — `close_loop.py` (`fill_price`), `live/reboot_reconcile.py` (new, transient-safe),
`runtime.py` (Block 3 wiring), `auto_square.py` + `live_position_guard.py` (GetQuotes square price +
capture `token` into `entry["position"]`).

---

# PHASE A — NRML product + exit-product alignment + broker margin pre-check

### Task A1 — `order_margin` + `get_quotes` on client + mock (margin returns RAW, incl Not_Ok)

**Files:** `backend/app/live/flattrade_client.py`, `backend/app/live/mock_noren.py`.
Test: `tests/test_live_flattrade_quotes_margin.py` (new), using the `_client(scripted)` `_post`
seam from `tests/test_live_flattrade_gtt.py:29`.

- [ ] **Step 1 — failing tests:** `order_margin(...)` POSTs `GetOrderMargin` with
  `{uid,actid,exch,tsym,qty(str),prc("100.00"),prd,trantype,prctyp}` and **returns the raw response
  dict including a `stat:"Not_Ok"`** (so the verdict can fail-closed); `get_quotes(exch,token)` POSTs
  `GetQuotes` `{uid,exch,token}` and returns `{}` on non-Ok (it's only a price read).
- [ ] **Step 2 — run, expect FAIL.**
- [ ] **Step 3 — implement on `FlattradeClient`** (mirror `limits()`@`:135`; `_post` not `_post_alert`):

```python
async def order_margin(self, *, exch, tsym, qty, prc, prd, trantype, prctyp,
                       trgprc=None) -> Dict[str, Any]:
    """GetOrderMargin (#8). Returns the RAW response (incl. stat:Not_Ok) so the
    caller can fail-CLOSED when the broker rejects the product/order."""
    jdata = {"uid": self._uid, "actid": self._actid, "exch": exch, "tsym": tsym,
             "qty": str(int(qty)), "prc": f"{float(prc):.2f}",
             "prd": prd, "trantype": trantype, "prctyp": prctyp}
    if trgprc is not None:
        jdata["trgprc"] = f"{float(trgprc):.2f}"
    data = await self._post("GetOrderMargin", jdata)
    return data if isinstance(data, dict) else {}

async def get_quotes(self, exch, token) -> Dict[str, Any]:
    """GetQuotes (#54): fresh LTP + depth (uid mandatory)."""
    data = await self._post("GetQuotes", {"uid": self._uid, "exch": exch, "token": str(token)})
    return data if isinstance(data, dict) and data.get("stat") == "Ok" else {}
```

- [ ] **Step 4 — `MockNoren`:** ctor kwargs `order_margin_data=None, quotes_data=None` stored with
  **`or {}`** (`self._order_margin_data = order_margin_data or {}`) to avoid `dict(None)`; setters
  `set_order_margin`/`set_quotes`; async `order_margin(self, **kw)` → `dict(self._order_margin_data)`,
  `get_quotes(self, exch, token)` → `dict(self._quotes_data)`. (place_oco/cancel_oco/gtt_book already
  exist — no change.)
- [ ] **Step 5 — run, PASS; commit** `feat(live): GetOrderMargin (raw) + GetQuotes client/mock`.

### Task A2 — exit-product alignment (square the position in ITS product)  ⚠ must precede A3-NRML

**Files:** `backend/app/live/auto_square.py`. Test: **extend `tests/test_live_auto_square.py`**
(it already has ~50 `square_position` assertions — do NOT create a new file; do NOT regress them).

- [ ] **Step 1 — failing test:** `square_position(client, position={...,"prd":"M","netqty":"65",
  "lp":100}, ...)` builds the exit `OrderIntent` with `prd="M"`; a position dict **without** `prd`
  still yields `prd="I"` (the existing token-less fixtures stay green).
- [ ] **Step 2 — run, expect FAIL.**
- [ ] **Step 3 — implement:** at `auto_square.py:577` (the exit `OrderIntent` ctor inside `_try_place`)
  replace `prd="I"` with `prd=(position.get("prd") or "I")`. (All callers already pass a `position`
  dict carrying `prd`: the guard's `entry["position"]` — `live_position_guard.py:178-180`; the
  broker `position_book` row in the manual/deployment/abort paths.)
- [ ] **Step 4 — run, PASS; commit** `fix(live): square exits the position in its own product (NRML vs MIS)`.

### Task A3 — `product` kwarg on `build_intent`; NRML for deployed entries

**Files:** `backend/app/live/order_builder.py`, `backend/app/live/executor.py`, `backend/app/auto_live.py`.
Test: `tests/test_order_builder_product.py` (new) + extend `tests/test_live_executor_deployed.py`.

- [ ] **Step 1 — failing test:** `build_intent(..., product="NRML")` → `intent.prd=="M"`; default →
  `"I"`; an **unknown** product → `(None, verdicts, None)` with a failing `{"check":"product"...}`
  verdict (NOT a raised `KeyError`). Deployed dry-run (`would_send["prd"]=="M"`).
- [ ] **Step 2 — run, expect FAIL.**
- [ ] **Step 3 — implement:** add kw `product: str = "MIS"` to `build_intent` (@`:152`); near the top,
  `if product not in _PRODUCT_TO_PRD: return _fail(verdicts, "product", f"unknown product {product}")`;
  set `prd=_PRODUCT_TO_PRD[product]` at `:296`. Thread `product="NRML"` in
  `executor.place_deployed_order`'s `build_intent` call (@`:420-433`) and add a `product="NRML"`
  kwarg to `place_deployed_order` (@`:352`, default `"MIS"`) forwarded from
  `auto_live` (@`:372-390`). **Leave `place_live_test_order` on default MIS.** `intent.prd` flows
  unchanged into `arm_for`'s `register(prd=intent.prd)`.
- [ ] **Step 4 — run, PASS; commit** `feat(live): deployed entries placed NRML (prd=M) for the OCO backstop`.

### Task A4 — broker margin pre-check (Gate 3) — fail-CLOSED on broker reject, fail-OPEN on transport

**Files:** `backend/app/live/margin.py`, `backend/app/live/executor.py`.
Test: extend `tests/test_live_margin.py` + `tests/test_live_executor_deployed.py`.

- [ ] **Step 1 — failing test:** `broker_margin_verdict({"stat":"Ok","cash":"20000","marginused":
  "13000"})` → `ok=True`; `{"stat":"Ok","cash":"5000","marginused":"13000"}` → `ok=False`
  (insufficient); `{"stat":"Not_Ok","emsg":"product not allowed"}` → **`ok=False` (fail-closed)**;
  `{}` (transport/exception) → **`ok=True` (fail-open)** detail "broker margin unavailable". Executor:
  with `order_margin` reporting `Not_Ok`, `place_deployed_order` blocks pre-transmit (no `place_order`).
- [ ] **Step 2 — run, expect FAIL.**
- [ ] **Step 3 — implement:** pure `broker_margin_verdict(resp)` in `margin.py` (string-tolerant
  cash/marginused via the existing `parse_cash` style): `{}`→ok (fail-open); `stat!="Ok"`→block
  (fail-closed); else `ok = cash>=marginused`. In `executor.place_deployed_order`, inside the
  **existing `if resolved_lot is not None:` block** (right after the `margin_verdict` append @`:441`,
  so `intent` is guaranteed non-None):

```python
try:
    mresp = await client.order_margin(
        exch=intent.exch, tsym=intent.tsym, qty=intent.qty, prc=intent.prc,
        prd=intent.prd, trantype=intent.trantype, prctyp=intent.prctyp)
except Exception:
    mresp = {}
verdicts.append(broker_margin_verdict(mresp))
```

  Gate 4's `any(not v["ok"] ...)` then blocks pre-transmit (zero broker write).
- [ ] **Step 4 — run, PASS; commit** `feat(live): broker GetOrderMargin gate (fail-closed on reject)`.

### Task A5 — `/margin-probe` readback route (M-leg only)

**Files:** `backend/app/routers/live_broker.py`. Test: a **direct route test** (import + call the
handler, or assert `@api.get("/live-broker/margin-probe")` present) — NOT a contract-corpus needle
(none exists).

- [ ] **Step 1 — implement** `GET /live-broker/margin-probe?exch&tsym&qty&prc` → `order_margin(prd="M",
  trantype="B", prctyp="LMT", ...)` and return `{cash, marginused, stat, emsg}` (connected-only,
  400 if not). **Drop the `prd="I"` leg** — GetOrderMargin's `prd` enum is `C/M/H`; an MIS leg is
  likely rejected. Note in the route docstring that M-vs-I parity, if wanted, is confirmed by reading
  Limits, not this probe.
- [ ] **Step 2 — commit** `feat(live): /margin-probe (NRML) readback route`.
- [ ] **Phase A gate:** FULL host suite green.

---

# PHASE B — OCO place-on-fill + safe coordination + alert

### Task B1 — `oco_levels.py`: catastrophe band DERIVED strictly wider than the guard stop

**Files:** Add `backend/app/live/oco_levels.py`. Test: `tests/test_oco_levels.py` (new).

- [ ] **Step 1 — failing test:** `compute_catastrophe_band(entry=100.0, guard_stop_pct=50,
  stop_pct=48, target_pct=135)` → because configured 48 ≤ guard 50, `eff_stop_pct` clamps to
  `50+MIN_GAP_PP(15)=65` → `sl_trigger≈35.0` (`100*(1-0.65)`), strictly **below** the guard stop
  level `50.0`. With `guard_stop_pct=30, stop_pct=48` → 48>30+15 → `eff=48` → `sl_trigger≈52.0` (still
  below guard 70.0). **Invariant test:** for a grid of (guard_stop_pct, configured) the returned
  `sl_trigger < entry*(1-guard_stop_pct/100)` ALWAYS. Plus: non-finite/≤0 entry → None; defaults
  applied when args None; TP leg marketable (limit ≤ trigger); all tick-rounded.
- [ ] **Step 2 — run, expect FAIL.**
- [ ] **Step 3 — implement** a pure module (inline a local finite check — do NOT import
  `_finite_pos` from the guard):

```python
import math
from app.live.order_builder import round_to_tick

DEFAULT_STOP_PCT = 50.0      # baseline catastrophe loss %; widened past the guard below
DEFAULT_TARGET_PCT = 135.0
MIN_GAP_PP = 15.0            # catastrophe stop must be >= guard stop + this many points
CROSS_PCT = 2.0             # marketable buffer so the fired SELL leg clears

def _finite_pos(x):
    try: v = float(x)
    except (TypeError, ValueError): return None
    return v if math.isfinite(v) and v > 0 else None

def compute_catastrophe_band(entry, *, guard_stop_pct, stop_pct=None, target_pct=None, tick=0.05):
    """SELL OCO levels strictly WIDER than the guard stop. Returns
    (sl_trigger, sl_limit, tp_trigger, tp_limit) or None. eff_stop_pct =
    max(configured|default, guard_stop_pct + MIN_GAP_PP) so sl_trigger is always a
    lower premium than the guard's stop level — preventing a same-level double-fire."""
    e = _finite_pos(entry)
    if e is None: return None
    cfg = DEFAULT_STOP_PCT if stop_pct is None else float(stop_pct)
    gsp = float(guard_stop_pct or 0.0)
    eff = max(cfg, gsp + MIN_GAP_PP)
    eff = min(eff, 95.0)                      # never below ~5% of premium
    tp = DEFAULT_TARGET_PCT if target_pct is None else float(target_pct)
    sl_trigger = round_to_tick(e * (1 - eff / 100.0), tick, mode="down")
    tp_trigger = round_to_tick(e * (1 + tp / 100.0), tick, mode="down")
    sl_limit = round_to_tick(sl_trigger * (1 - CROSS_PCT / 100.0), tick, mode="down")
    tp_limit = round_to_tick(tp_trigger * (1 - CROSS_PCT / 100.0), tick, mode="down")
    if min(sl_trigger, sl_limit, tp_trigger, tp_limit) <= 0: return None
    return sl_trigger, sl_limit, tp_trigger, tp_limit
```

- [ ] **Step 4 — run, PASS; commit** `feat(live): derived catastrophe-band OCO levels (strictly wider than guard stop)`.

### Task B2 — registry entry carries `oco_al_id` + `token` + `prd` exit hint

**Files:** `backend/app/live/live_position_guard.py`. Test: extend `tests/test_live_position_guard.py`.

- [ ] **Step 1 — failing test:** `register(..., oco_al_id="AL1", token="999")` → snapshot entry has
  `oco_al_id=="AL1"` and `entry["position"]["token"]=="999"`; omitting → None; existing callers
  unaffected. (`prd` is already stored — confirm it's in `entry["position"]`.)
- [ ] **Step 2 — run, expect FAIL.**
- [ ] **Step 3 — implement:** add kw-only `oco_al_id=None, token=None` to `register()` (@`:118-132`);
  add `"oco_al_id": oco_al_id` to the item; add `"token": token` to the nested `position` dict (and
  confirm `prd` is already in that nested dict). The two other call-sites pass nothing → default None.
- [ ] **Step 4 — run, PASS; commit** `feat(live): guard entry carries oco_al_id + token`.

### Task B3 — place the OCO on fill (`arm_for._arm`), pct via the per-signal call

**Files:** `backend/app/live_deploy_context.py`, `backend/app/live/executor.py`, `backend/app/auto_live.py`.
Test: `tests/test_live_deploy_context.py` + `tests/test_live_executor_deployed.py` + `tests/test_auto_live.py`.

- [ ] **Step 1 — failing test (`test_live_deploy_context.py`):** call
  `arm_for(plan, signal_doc, ref_ltp, catastrophe_stop_pct=48, catastrophe_target_pct=140)`
  where `arm_for` was partial-bound with `client=fake, uid, actid`; run `_arm(intent_prd_M, "N1")`;
  assert `fake.place_oco` got an OCO whose triggers match `compute_catastrophe_band(ref_ltp,
  guard_stop_pct=<from plan/levels>, stop_pct=48, target_pct=140)`, `prd=="M"`, both legs SELL; entry
  `oco_al_id=="OCO1"`; `_arm` returns `"OCO1"`. Failure test: `place_oco`→`{"ok":False}` → entry
  `oco_al_id is None`, `_arm` returns None, **does not raise**. **Register-failure test:** if
  `register` raises, `_arm` STILL propagates (the `_abort_protect` contract is unchanged).
- [ ] **Step 2 — run, expect FAIL.**
- [ ] **Step 3 — implement:**
  - `arm_for` (@`:57`): add kw-only `client=None, uid="", actid="", catastrophe_stop_pct=None,
    catastrophe_target_pct=None`. The `guard_stop_pct` used by the band = the resolved guard
    `stop_pct` already computed at `:89-96` (reuse that value).
  - In `_arm`, **after** the mandatory `register(...)` (which still propagates on failure), in a
    SEPARATE `try/except` (best-effort, never raises): if `client` present, compute the band via
    `compute_catastrophe_band(ref_ltp, guard_stop_pct=<resolved>, stop_pct=catastrophe_stop_pct,
    target_pct=catastrophe_target_pct)`; if not None, `build_oco_intent(exch=intent.exch,
    tsym=intent.tsym, qty=intent.qty, prd="M", sl_trigger/sl_limit/tp_trigger/tp_limit=...,
    remarks=f"oco:{norenordno}")`; `res = await client.place_oco(intent_oco)`; on
    `res.get("ok")` set `oco_al_id=res.get("al_id")` and mutate the live registry entry
    `get_registry().get(norenordno)["oco_al_id"]=oco_al_id`; on failure/exception log + leave None.
    `return oco_al_id`.
  - `build_live_deploy_context` (@`:129-196`): bind ONLY `client`/`uid`/`actid` into `arm_for` via
    `functools.partial` (it has no deployment — pct comes per-signal).
  - `auto_live.auto_live_trade_for_signal` (@`:356`): read
    `risk_live = (deployment.get("risk") or {}).get("live") or {}` and call
    `arm = arm_for(plan, signal_doc, ref_ltp, catastrophe_stop_pct=risk_live.get(
    "catastrophe_stop_pct"), catastrophe_target_pct=risk_live.get("catastrophe_target_pct"))`.
  - `executor._transmit_and_arm` (@`:152`): `oco_al_id = await arm(intent, result.norenordno)`
    (manual path's `_make_arm` returns None — fine); include `"oco_al_id": oco_al_id` in the success
    result dict. (No new `place_order` — AST-safe.)
  - `auto_live` doc build (@`:445-470`): add `"oco_al_id": result.get("oco_al_id")` and
    `"oco_error": None if result.get("oco_al_id") else "no_broker_backstop"`.
- [ ] **Step 4 — run, PASS (3 files); commit** `feat(live): auto-place resting OCO on each deployed fill (per-deployment band)`.

### Task B4 — safe square coordination: re-confirm netqty; cancel OCO AFTER a real fill

**Files:** `backend/app/live/auto_square.py`, `backend/app/live/live_position_guard.py`.
Test: extend `tests/test_live_auto_square.py` + `tests/test_live_position_guard.py`.

- [ ] **Step 1 — failing tests:**
  - **(square re-confirm)** `square_position(client, position={tsym,"netqty":"65","lp":100,...})`
    where `client.position_book()` now reports that tsym **flat (netqty 0)** → returns
    `squared=True, via="already_flat"` and places **no** order (prevents double-sell when the OCO
    already fired). When the fresh read still shows non-flat → squares as before.
  - **(cancel after real fill)** guard `_cycle` with a fake client exposing `cancel_oco`; an entry
    with `oco_al_id="OCO1"` tripping the stop: when the square result is real
    (`{"squared":True}`) → `cancel_oco("OCO1")` IS called; when the square result is dry-run
    (`{"squared":False,"dry_run":True}`, i.e. `LIVE_GUARD_ARMED` off) → `cancel_oco` is **NOT** called.
- [ ] **Step 2 — run, expect FAIL.**
- [ ] **Step 3 — implement:**
  - `square_position` (@`:378`): immediately before the cancel/place block (after the `lp`
    validation, ~`:516`), re-read `book = await client.position_book()`, find this `tsym`, parse
    `netqty`; if it is `0`/absent in a **non-empty Ok** book → return
    `{"squared":True,"via":"already_flat","note":"position already flat"}` with no order. (Guard:
    only treat as flat when the book is non-empty — an empty book is "unknown", fall through to the
    existing path which itself re-validates.)
  - `_square_and_record` (`live_position_guard.py:368-393`): move the OCO cancel to **after**
    `self._square_fn(...)` returns; cancel only when `entry.get("oco_al_id")` AND
    `hasattr(client,"cancel_oco")` AND `result.get("squared") and not result.get("dry_run")`,
    wrapped in try/except (a cancel failure never breaks the loop). (The derived band keeps the two
    legs apart so the brief both-resting window before cancel is benign; the netqty re-confirm
    prevents the double-sell if the OCO beat the guard.)
- [ ] **Step 4 — run, PASS; commit** `fix(live): re-confirm netqty before square + cancel OCO only after a real fill`.

### Task B5 — cancel the OCO on deployment stop / stop-all

**Files:** `backend/app/routers/deployments.py`. Test: extend `tests/test_deployment_live_routes.py`
(monkeypatch **`app.routers.live_broker._get_client`** — the real import target — to a fake exposing
`cancel_oco`; uid/actid still via `dep._live_get_token_doc`).

- [ ] **Step 1 — failing test:** a deployed entry with `oco_al_id="OCO1"`; `stop_deployment_live`
  calls `cancel_oco("OCO1")` (best-effort) in addition to the square + journal.
- [ ] **Step 2 — run, expect FAIL.**
- [ ] **Step 3 — implement:** in `_square_live_positions_for_deployment` (@`:112-165`), after
  `reg.remove(entry["id"])` (@`:143`): if `entry.get("oco_al_id")` AND the resolved `client` has
  `cancel_oco`, `await client.cancel_oco(al_id)` wrapped (best-effort). (Here the cancel-then-square
  order is fine: this is a user-initiated flatten, always armed/transmitting; `square_position`'s
  netqty re-confirm still guards a double-sell.)
- [ ] **Step 4 — run, PASS; commit** `feat(live): deployment stop/stop-all cancels the resting OCO`.

### Task B6 — kill-switch sweeps ALL resting GTT/OCO (manual square does NOT)

**Files:** `backend/app/routers/live_broker.py`. Test: extend the kill-switch route test.

- [ ] **Step 1 — failing test:** with a connected client whose `gtt_book()` returns resting OCOs,
  `live_kill_switch` cancels each (best-effort) in addition to `panic_squareoff`. (The **manual
  single-shot square** is MIS and never places an OCO — it does **not** sweep, avoiding cancelling a
  deployed position's OCO that merely shares the tsym.)
- [ ] **Step 2 — run, expect FAIL.**
- [ ] **Step 3 — implement:** in `live_kill_switch` (@~`:1852`, after the position fetch), best-effort
  `for row in await client.gtt_book(): cancel by al_id via cancel_oco/cancel_gtt`. Do **not** add a
  sweep to `live_order_square`.
- [ ] **Step 4 — run, PASS; commit** `feat(live): kill-switch sweeps resting GTT/OCO`.

### Task B7 — catastrophe-band config (arm payload + persist)

**Files:** `backend/app/routers/deployments.py`. Test: extend `tests/test_deployment_live_routes.py`.

- [ ] **Step 1 — failing test:** arm with `_LiveArmBody(..., catastrophe_stop_pct=48,
  catastrophe_target_pct=140)` → `risk.live.catastrophe_stop_pct==48` persisted (via the whole-`risk`
  `$set` @`:802-807`); omitting → `None`.
- [ ] **Step 2 — run, expect FAIL.**
- [ ] **Step 3 — implement:** add `catastrophe_stop_pct: Optional[float]=None` +
  `catastrophe_target_pct: Optional[float]=None` to `_LiveArmBody` (@`:179`); include in the `live`
  dict (@`:788-798`). (They are READ per-signal in `auto_live` per B3 — no other plumbing.)
- [ ] **Step 4 — run, PASS; commit** `feat(live): per-deployment catastrophe-band config in the arm payload`.

### Task B8 — "no broker backstop" alert (backend passthrough + frontend)

**Files:** `backend/app/live/live_blotter.py`, `frontend/src/components/live/LiveBlotter.jsx` +
`LiveDashboard.jsx`. Verify: host test for the blotter row field + `CI=true npm run build` + Chrome.

- [ ] **Step 1 — backend:** include `oco_error` (passthrough from the live_trade doc) on the blotter
  row in `build_live_blotter`; add a host-test assertion. **Step 2 — frontend:** LIVE rows with
  `oco_error` show an amber "no broker net" chip; a dashboard banner counts OPEN deployed positions
  with `oco_error` ("N live position(s) software-guard-only"). `CI=true npm run build`; Chrome smoke.
- [ ] **Step 3 — commit** `feat(live): surface "no broker backstop" when OCO placement failed`.
- [ ] **Phase B gate:** FULL host suite green + FE build green.

---

# PHASE C — transient-safe reboot reconciliation + depth-aware square

### Task C1 — `close_live_trade` accepts a true `fill_price`

**Files:** `backend/app/live/close_loop.py`. Test: extend `tests/test_live_close_loop.py`.

- [ ] **Step 1 — failing test:** `close_live_trade(db, norenordno="N1", exit_price=None,
  fill_price=132.0, exit_reason="reconciled")` → `realized_pnl` from `fill_price`; `fill_price=None`
  → current behavior.
- [ ] **Step 2 — run, expect FAIL.** **Step 3 — implement:** add kw `fill_price: Optional[float]=None`
  (@`:57-64`); prefer it for exit price + `realized_pnl=qty*(fill_price-entry_price)`; else current.
- [ ] **Step 4 — run, PASS; commit** `feat(live): close_live_trade accepts a broker-true fill_price`.

### Task C2 — transient-safe reboot reconciliation (OPEN+flat → trade-book by norenordno; orphan sweep)

**Files:** Add `backend/app/live/reboot_reconcile.py` (pure-ish, host-testable); wire in
`backend/app/runtime.py`. Test: `tests/test_reboot_reconcile.py` (new — **copy the `$in`/`$ne`-capable
FakeDB from `tests/test_deployment_live_routes.py:39-126`**).

- [ ] **Step 1 — failing tests:**
  - OPEN doc `norenordno="N1",tsym="X",qty=65,entry=100`; broker `position_book` **non-empty + Ok**
    but flat for X; `trade_book` has a SELL fill **with `norenordno` linked to the OCO child** (or
    matched to the entry) `flprc=130` → `close_live_trade(... fill_price=130 ...)`; doc CLOSED with
    `realized_pnl=(130-100)*65`.
  - **Transient-empty guard:** `position_book` returns `[]` (Not_Ok/hiccup) → reconciler does
    **NOTHING** (no close, no cancel).
  - **Orphan sweep:** an OCO whose linked entry doc is **confirmed CLOSED** (and no open position) →
    cancelled; an OCO whose entry is still OPEN/position held → left.
- [ ] **Step 2 — run, expect FAIL.**
- [ ] **Step 3 — implement** `async def reconcile_on_startup(db, client) -> dict`:
  - read `book = await client.position_book()`; **if not book (empty) → return early** (empty =
    "unknown", never "flat" — mirrors the false-close lesson).
  - build `held_tsyms = {row tsym : netqty!=0}`. For each `live_trades` doc `status not in
    ("CLOSED",)` whose tsym is **absent/zero in the non-empty book**: find its exit fill in
    `trade_book` **keyed by the doc's `norenordno` / the OCO child's order** (NOT newest-by-tsym);
    only if a matching SELL fill exists → `close_live_trade(db, norenordno=doc["norenordno"],
    exit_price=None, fill_price=<flprc/avgprc>, exit_reason="reconciled_closed")`.
  - orphan-OCO sweep: `for row in gtt_book()`: cancel ONLY when the row's linked entry doc is
    confirmed CLOSED (or its tsym is flat in the non-empty book AND has no OPEN live_trade).
  - Wire ONE call **inside** `runtime.live_startup_recovery` as Block 3 — after the rehydrate
    try/except (i.e. before the function ends, ~`:245`), reusing the already-built `client` +
    `get_db()`, wrapped in its own try/except (best-effort, never raises).
- [ ] **Step 4 — run, PASS; commit** `feat(live): transient-safe reboot reconciliation (journal OCO-fired-while-down + orphan sweep)`.

### Task C3 — depth-aware square price via GetQuotes (extend the existing square tests)

**Files:** `backend/app/live/auto_square.py`, `backend/app/live/live_position_guard.py`.
Test: **extend `tests/test_live_auto_square.py`** (the get_quotes refresh is gated on
`position.get("token")`, so the existing token-less fixtures stay green) + the guard test for token capture.

- [ ] **Step 1 — failing test:** `square_position(client, position={...,"token":"999","lp":100}, ...)`
  with `client.get_quotes("NFO","999")→{"lp":"98.5"}` → the marketable limit prices off **98.5**;
  no token / no `get_quotes` → falls back to `position["lp"]`. Guard: after `_cycle`,
  `entry["position"]["token"]` is populated.
- [ ] **Step 2 — run, expect FAIL.**
- [ ] **Step 3 — implement:** in `square_position`, before the lp-validation, if
  `position.get("token")` and `hasattr(client,"get_quotes")`: `q = await client.get_quotes(exch,
  token)`; if `q.get("lp")` finite → use it as `ref` (try/except; fall back to `position["lp"]`).
  In the guard `_cycle` `entry["position"].update({...})` at **`live_position_guard.py:331-335`**
  add `"token": pos.get("token")`.
- [ ] **Step 4 — run, PASS; commit** `feat(live): depth-aware square price via GetQuotes`.
- [ ] **Phase C gate:** FULL host suite green.

---

## Final review & validation

- [ ] Final code-reviewer over the branch — re-verify the four safety invariants hold in code
  (band-wider, netqty-re-confirm, cancel-after-real-fill, exit-product), dry-run transmits nothing,
  AST one-place_order intact, reconciliation transient-safe.
- [ ] `CI=true npm run build` + Chrome smoke (the "no broker net" path).
- [ ] Update `docs/live-readback-checklist.md`: `/margin-probe` (NRML); OCO **rests** after a
  deployed fill (`GetPendingGTTOrder`); a real software square **cancels** it; (separately) the OCO
  **fires + clears**; reboot reconciliation journals an OCO-fired-while-down. Confirm `LTP_A_O`
  above-trigger only if a single-above GTT is ever used (OCO does not depend on it).
- [ ] `finishing-a-development-branch`: present merge/PR options (user merges + pushes).

## Open items to confirm in the readback (not build blockers)
1. NRML `marginused` for the real option via `/margin-probe`.
2. The OCO fires + its catastrophe-band sell-to-close limit clears (`CROSS_PCT`).
3. `cancel_oco` success + removes the resting alert (`Al_id` casing handled by `_parse_alert_response`).
