"""Broker-empty ledger + band-exact catch-up (W1 of the warehouse-page review).

Two defects these tests pin:
1. The nightly catch-up option stage used the close-sampled moneyness preview,
   so wick-edge band strikes accumulated as "missing" daily until a manual
   Fill gaps — now it must be band-exact over the full rolling window.
2. Pairs the broker has PROVEN empty (clean fetch, zero candles) generated
   fill actions and pinned the hygiene status at amber forever — now they are
   ledgered, excluded, and reported separately so verified is reachable.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.completeness import band_completeness, missing_band_pairs  # noqa: E402
from app.data_hygiene import broker_empty_candidates, pairs_from_band_plan_items  # noqa: E402
from tests.contract_corpus import backend_api_text

DAY_ROWS = [
    {"date": "2026-06-10", "count": 375, "low": 23480.0, "high": 23560.0},
    {"date": "2026-06-11", "count": 375, "low": 23500.0, "high": 23540.0},
]
EXPIRIES = ["2026-06-16"]
STEP = 50


def _expected_pairs():
    return set(missing_band_pairs(
        DAY_ROWS, expiries_sorted=EXPIRIES, stored_pairs=set(), step=STEP,
        legs=("CE", "PE"), pad_steps=1, judge_until="2026-06-12", min_spot_minutes=60,
    ))


def test_known_empty_pairs_are_excluded_from_missing_and_counted():
    expected = _expected_pairs()
    assert expected, "fixture must demand pairs"
    excused = set(sorted(expected)[:3])
    band = band_completeness(
        DAY_ROWS, expiries_sorted=EXPIRIES, stored_pairs=set(), step=STEP,
        legs=("CE", "PE"), pad_steps=1, judge_until="2026-06-12",
        min_spot_minutes=60, known_empty=excused,
    )
    assert band["broker_empty_pairs"] == 3
    assert band["missing_pairs"] == len(expected) - 3
    assert band["planned_pairs"] == len(expected)


def test_coverage_reaches_100_when_all_residual_missing_is_broker_empty():
    expected = _expected_pairs()
    stored = set(sorted(expected)[3:])     # everything stored except 3 pairs...
    excused = expected - stored            # ...which the broker has proven empty
    band = band_completeness(
        DAY_ROWS, expiries_sorted=EXPIRIES, stored_pairs=stored, step=STEP,
        legs=("CE", "PE"), pad_steps=1, judge_until="2026-06-12",
        min_spot_minutes=60, known_empty=excused,
    )
    assert band["missing_pairs"] == 0          # -> no action, status verified
    assert band["coverage_pct"] == 100.0
    assert band["broker_empty_pairs"] == len(excused)


def test_missing_band_pairs_never_rerequests_ledgered_pairs():
    expected = _expected_pairs()
    excused = set(sorted(expected)[:2])
    remaining = missing_band_pairs(
        DAY_ROWS, expiries_sorted=EXPIRIES, stored_pairs=set(), step=STEP,
        legs=("CE", "PE"), pad_steps=1, judge_until="2026-06-12",
        min_spot_minutes=60, known_empty=excused,
    )
    assert set(remaining) == expected - excused


# ---------------------------------------------------------------------------
# Reconcile helpers (pure)
# ---------------------------------------------------------------------------

PLAN_ITEMS = [
    {"instrument_key": "NSE_FO|111", "expiry_date": "2026-06-16", "strike": 23550,
     "side": "CE", "fetch_dates": ["2026-06-10", "2026-06-11"]},
    {"instrument_key": "NSE_FO|222", "expiry_date": "2026-06-16", "strike": 23550,
     "side": "PE", "fetch_dates": ["2026-06-10"]},
]


def test_pairs_from_band_plan_items_maps_pair_to_instrument_key():
    requested = pairs_from_band_plan_items(PLAN_ITEMS)
    assert requested[("2026-06-10", "2026-06-16", "CE", 23550)] == "NSE_FO|111"
    assert requested[("2026-06-11", "2026-06-16", "CE", 23550)] == "NSE_FO|111"
    assert requested[("2026-06-10", "2026-06-16", "PE", 23550)] == "NSE_FO|222"
    assert len(requested) == 3


def test_broker_empty_candidates_excludes_stored_and_failed():
    requested = pairs_from_band_plan_items(PLAN_ITEMS)
    stored = {("2026-06-10", "2026-06-16", "CE", 23550)}              # this one landed
    failed = [{"instrument_key": "NSE_FO|222",                        # PE task failed
               "from_date": "2026-06-10", "to_date": "2026-06-10", "error": "429"}]
    out = broker_empty_candidates(requested, stored, failed)
    # Only the CE 06-11 pair: requested, not stored, and its task did not fail.
    assert out == [("2026-06-11", "2026-06-16", "CE", 23550)]


def test_broker_empty_candidates_grace_protects_the_latest_session():
    # Upstox serves historical F&O bars with a lag after the close: a
    # same-night sync sees yesterday's WHOLE band as empty (ATM included).
    # Pairs dated >= grace_from must stay actionable, not get ledgered.
    requested = pairs_from_band_plan_items(PLAN_ITEMS)
    out = broker_empty_candidates(requested, set(), [], grace_from="2026-06-11")
    assert out == [("2026-06-10", "2026-06-16", "CE", 23550),
                   ("2026-06-10", "2026-06-16", "PE", 23550)]
    # everything graced -> nothing ledgered
    assert broker_empty_candidates(requested, set(), [], grace_from="2026-06-10") == []


def test_broker_empty_candidates_failure_outside_pair_dates_does_not_protect():
    requested = pairs_from_band_plan_items(PLAN_ITEMS)
    failed = [{"instrument_key": "NSE_FO|111",
               "from_date": "2026-06-11", "to_date": "2026-06-11", "error": "x"}]
    out = broker_empty_candidates(requested, set(), failed)
    # CE 06-11 protected by the failure; CE 06-10 and PE 06-10 are proven empty.
    assert ("2026-06-11", "2026-06-16", "CE", 23550) not in out
    assert ("2026-06-10", "2026-06-16", "CE", 23550) in out
    assert ("2026-06-10", "2026-06-16", "PE", 23550) in out


# ---------------------------------------------------------------------------
# Contract pins (source text — tests never import server/runtime: motor)
# ---------------------------------------------------------------------------

def test_catch_up_chain_is_band_exact_not_preview_based():
    runtime = (ROOT / "backend" / "app" / "runtime.py").read_text(encoding="utf-8")
    assert "preview = await _build_option_warehouse_preview" not in runtime, (
        "catch-up stage 3 must use build_band_fetch_plan, not the close-sampled "
        "moneyness preview (wick-edge strikes silently skipped)"
    )
    chain = runtime[runtime.index("async def _run_catch_up_chain"):]
    chain = chain[:chain.index("\nasync def _fail_remaining_catch_up")]
    assert "build_band_fetch_plan" in chain
    assert "default_scope_start()" in chain
    assert "record_broker_empty_pairs" in chain


def test_sync_route_and_band_sweep_exist():
    server = backend_api_text()
    assert '@api.post("/warehouse/sync")' in server
    assert "band_sweeps" in server
    # the ledger index is ensured at startup
    db_src = (ROOT / "backend" / "app" / "db.py").read_text(encoding="utf-8")
    assert "option_known_empty" in db_src


def test_hygiene_panel_surfaces_broker_empty_footnote():
    panel = (ROOT / "frontend" / "src" / "components" / "DataHygienePanel.jsx").read_text(encoding="utf-8")
    assert "hygiene-broker-empty-" in panel
    assert "broker_empty_pairs" in panel
