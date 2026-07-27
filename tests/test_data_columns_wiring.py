"""Wiring + end-to-end tests for load-time DATA columns.

`tests/test_data_columns.py` proves the JOIN is correct. This file proves it is
actually REACHED — which is the failure mode that started this whole line of
work: `explosive_reversal` read a `vix` column for months and nothing joined it,
so the branch behind it was dead while looking entirely alive.

Three layers here:

* **Source contract** — every frame-origination site must call
  `attach_required_data`. If someone adds a new backtest entry point (or drops
  the call from an existing one), a declaring strategy would silently get a
  column of NaN on that path only, and the difference between paths would be
  invisible. Pinned as a source assertion because the alternative is booting six
  subsystems.
* **Async seam** — `app.warehouse.attach_required_data` against a minimal fake
  db, including the `$gte`-only query shape the repo's fakes support.
* **End to end** — a declaring strategy really sees the values through
  `run_backtest`, and a non-declaring one gets an untouched frame.
"""
import asyncio
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.ai.capability import WAREHOUSE_MANIFEST, capability_report   # noqa: E402
from app.ai.compiler import allowed_columns                          # noqa: E402
from app.ai.grounding import build_grounding_catalog                 # noqa: E402
from app.backtest import run_backtest                                # noqa: E402
from app.data_columns import attach_data_columns, resolve_data_columns  # noqa: E402
from app.indicators import precompute_all_indicators                 # noqa: E402
from app.strategies.base import Signal, StrategyBase, get_registry   # noqa: E402
from app.warehouse import attach_required_data                       # noqa: E402

BACKEND = ROOT / "backend"

# Every place that builds a frame a strategy will be evaluated against.
FRAME_ORIGINATION_SITES = (
    "app/runtime.py",                 # Backtest Lab one-shot
    "app/routers/research.py",        # sync route + async job path
    "app/optimizer.py",               # every optimizer trial
    "app/wfo.py",                     # walk-forward windows
    "app/deployment_evaluator.py",    # live / paper, per bar
)


# ---------------------------------------------------------------------------
# source contract — the seam cannot silently become dead code
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("rel", FRAME_ORIGINATION_SITES)
def test_every_frame_origination_site_attaches_required_data(rel):
    src = (BACKEND / rel).read_text(encoding="utf-8")
    assert "attach_required_data(" in src, (
        f"{rel} builds a strategy-evaluation frame but never calls "
        "attach_required_data — a strategy declaring required_data would get an "
        "all-NaN column on THIS path only, which is invisible at runtime"
    )


def test_research_job_path_attaches_before_the_sync_compute_closure():
    """The job path does its CPU work in a thread via a sync closure; the await
    must therefore happen BEFORE it, or it could not happen at all."""
    src = (BACKEND / "app/routers/research.py").read_text(encoding="utf-8")
    attach = src.index("df, _ = await attach_required_data")
    compute = src.index("def _compute():")
    assert attach < compute


def test_attach_is_called_on_the_raw_frame_before_enrichment():
    """Joining AFTER enrichment would leave the optimizer's cached frames
    without the column (its cache is keyed on indicator params only)."""
    for rel in ("app/optimizer.py", "app/runtime.py", "app/wfo.py"):
        src = (BACKEND / rel).read_text(encoding="utf-8")
        attach = src.index("attach_required_data(df")
        enrich = min(
            (src.index(tok) for tok in ("precompute_all_indicators(df", "enrich_with_cache(raw_df")
             if tok in src),
            default=None,
        )
        if enrich is not None:
            assert attach < enrich, f"{rel} joins data columns after enrichment"


@pytest.mark.parametrize("rel,pool_call", [
    ("app/optimizer.py", "start_pool(raw_df"),
    ("app/wfo.py", "start_pool(df"),
])
def test_worker_pool_is_seeded_after_the_join_not_before(rel, pool_call):
    """`parallel_eval.start_pool` ships the frame to worker PROCESSES, which then
    evaluate trials against that copy. Seed it before the join and parallel trials
    would see an all-NaN column while sequential trials saw real values — an
    optimization result silently corrupted on only some paths, and invisible
    because both produce plausible numbers. The ordering is correct today; this
    pins it, because nothing else would catch the call being moved."""
    src = (BACKEND / rel).read_text(encoding="utf-8")
    assert src.index("attach_required_data(df") < src.index(pool_call), (
        f"{rel} seeds the worker pool from a frame captured before "
        "attach_required_data — declared data columns would be missing in workers"
    )


# ---------------------------------------------------------------------------
# async seam
# ---------------------------------------------------------------------------

class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def sort(self, *a, **k):
        return self

    async def to_list(self, length=None):
        return list(self._rows)[: length or len(self._rows)]


class _FakeCollection:
    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    def find(self, q, proj=None):
        self.queries.append(q)
        inst = q.get("instrument")
        gte = (q.get("ts") or {}).get("$gte")
        out = [r for r in self.rows if r.get("instrument") == inst]
        if gte is not None:
            out = [r for r in out if int(r["ts"]) >= int(gte)]
        return _FakeCursor(sorted(out, key=lambda r: int(r["ts"])))


class _FakeDB:
    def __init__(self, rows):
        self.candles_1m = _FakeCollection(rows)


def _vix_rows(*pairs):
    return [{"instrument": "INDIAVIX", "ts": ts, "close": c} for ts, c in pairs]


def test_no_declaration_returns_the_identical_frame_and_never_queries():
    df = pd.DataFrame({"ts": [1_000, 2_000]})
    db = _FakeDB(_vix_rows((1_000, 12.0)))
    out, cov = asyncio.run(attach_required_data(df, [], db=db))
    assert out is df
    assert cov == {}
    assert db.candles_1m.queries == [], "queried the warehouse for a strategy that declared nothing"


def test_declaration_joins_from_the_aux_instrument():
    df = pd.DataFrame({"ts": [10_000, 20_000, 30_000]})
    db = _FakeDB(_vix_rows((5_000, 11.5), (20_000, 19.25)) +
                 [{"instrument": "NIFTY", "ts": 20_000, "close": 999.0}])
    out, cov = asyncio.run(attach_required_data(df, ["vix"], db=db))
    assert list(out["vix"]) == [11.5, 19.25, 19.25]
    assert cov["vix"]["coverage_pct"] == 100.0
    assert db.candles_1m.queries[0]["instrument"] == "INDIAVIX"


def test_query_uses_gte_only_so_repo_test_fakes_keep_working():
    """`$lte` is filtered in Python on purpose — the in-memory fakes used across
    this suite implement only $gte/$gt/$exists."""
    df = pd.DataFrame({"ts": [10_000]})
    db = _FakeDB(_vix_rows((10_000, 14.0)))
    asyncio.run(attach_required_data(df, ["vix"], db=db))
    q = db.candles_1m.queries[0]
    assert set(q["ts"].keys()) == {"$gte"}


def test_a_post_window_print_can_never_reach_a_bar():
    """Defense in depth, stated honestly. `attach_required_data` trims rows past
    the window in Python, but that trim is a MEMORY bound: every bar is <= hi by
    construction and the join only looks backward, so the result is the same with
    or without it. A mutation run confirmed removing the trim kills no test — so
    this asserts the invariant that IS real (a later print never reaches an
    earlier bar) rather than pretending the filter is what enforces it."""
    df = pd.DataFrame({"ts": [10_000]})
    db = _FakeDB(_vix_rows((10_000, 14.0), (99_000, 88.0)))
    out, _ = asyncio.run(attach_required_data(df, ["vix"], db=db))
    assert out["vix"].iloc[0] == 14.0


def test_declared_but_absent_data_is_nan_and_reported_not_silently_zero():
    df = pd.DataFrame({"ts": [10_000, 20_000]})
    out, cov = asyncio.run(attach_required_data(df, ["vix"], db=_FakeDB([])))
    assert out["vix"].isna().all()
    assert cov["vix"]["coverage_pct"] == 0.0


def test_empty_frame_short_circuits():
    out, cov = asyncio.run(attach_required_data(pd.DataFrame(), ["vix"], db=_FakeDB([])))
    assert cov == {}


# ---------------------------------------------------------------------------
# end to end through the real engine
# ---------------------------------------------------------------------------

def _synth_frame(n=260):
    # Anchored to 09:30 IST so the bars fall INSIDE run_backtest's 09:25-15:00
    # trade window; an arbitrary epoch lands outside it and evaluate() is then
    # never called, which would make this test silently vacuous.
    start = pd.Timestamp("2026-01-05 09:30", tz="Asia/Kolkata")
    ts0 = int(start.tz_convert("UTC").timestamp() * 1000)
    ts = ts0 + pd.RangeIndex(n).to_numpy() * 60_000
    base = 20_000 + pd.Series(range(n)).mod(20).to_numpy() * 3.0
    return pd.DataFrame({"ts": ts, "open": base, "high": base + 6.0,
                         "low": base - 6.0, "close": base + 1.0,
                         "volume": [1000.0] * n})


class _VixReader(StrategyBase):
    """Declares the column and records what it actually saw per bar."""
    id = "_test_vix_reader"
    name = "VIX reader (test)"
    required_data = ["vix"]
    parameter_schema = {"signal_threshold": {"type": "int", "min": 0, "max": 100, "default": 0}}

    def __init__(self):
        self.seen = []

    def evaluate(self, row, prev, params, ctx) -> Signal:
        self.seen.append(row.get("vix"))
        return Signal(direction="NONE")


def test_declared_column_reaches_the_strategy_through_run_backtest():
    raw = _synth_frame()
    specs = resolve_data_columns(["vix"])
    src = [{"ts": int(raw["ts"].iloc[0]) - 60_000, "close": 17.5}]
    joined, _ = attach_data_columns(raw, specs, {"vix": src})
    enriched = precompute_all_indicators(joined, {})

    strat = _VixReader()
    run_backtest(enriched, strat, strat.default_params(), instrument="NIFTY")

    assert strat.seen, "strategy was never evaluated"
    assert all(v == 17.5 for v in strat.seen), "column did not survive to the row dicts"


def test_enrichment_preserves_the_joined_column():
    """precompute_all_indicators copies-and-adds; an extra raw column must ride
    through untouched (this is what makes the join cache-neutral)."""
    raw = _synth_frame()
    joined, _ = attach_data_columns(raw, resolve_data_columns(["vix"]),
                                    {"vix": [{"ts": int(raw["ts"].iloc[0]), "close": 13.0}]})
    enriched = precompute_all_indicators(joined, {})
    assert "vix" in enriched.columns
    assert enriched["vix"].dropna().unique().tolist() == [13.0]


def test_non_declaring_strategy_gets_a_byte_identical_frame():
    """The guarantee every existing strategy relies on."""
    raw = _synth_frame()
    before = raw.copy(deep=True)
    out, cov = attach_data_columns(raw, resolve_data_columns([]), {})
    assert out is raw
    assert cov == {}
    pd.testing.assert_frame_equal(raw, before)


# ---------------------------------------------------------------------------
# capability surface
# ---------------------------------------------------------------------------

def test_required_data_defaults_empty_for_every_shipped_strategy():
    reg = get_registry()
    if not reg.list_all():
        reg.auto_discover()
    for meta in reg.list_all():
        assert meta.get("required_data", []) == [], (
            f"{meta['id']} declares required_data; that is fine, but this test "
            "pins that NOTHING declares it by default — update deliberately"
        )


def test_meta_exposes_required_data():
    assert "required_data" in StrategyBase().meta()


def test_advertise_is_not_allow():
    """`vix` is advertised in the capability report but only ALLOWED to a spec
    that declares it — the same rule structural features follow."""
    assert "vix" not in allowed_columns()
    assert "vix" in allowed_columns((), ("vix",))


def test_grounding_advertises_the_data_column():
    cat = build_grounding_catalog()
    assert "vix" in cat["data_columns"]
    assert "vix" in cat["all_columns_including_features"]
    entry = next(e for e in cat["data_column_entries"] if e["name"] == "vix")
    assert entry["needs_declaration"] is True
    assert entry["causal"] is True
    assert entry["instrument"] == "INDIAVIX"


def test_capability_report_carries_data_columns():
    assert any(d["name"] == "vix" for d in capability_report()["data_columns"])


def test_manifest_no_longer_denies_vix_history():
    """It was False while the warehouse held 280 sessions of it, which made the
    authoring wizard refuse rules against real data. Backfilled 2026-07-27 to
    cover the full spot window."""
    assert WAREHOUSE_MANIFEST["has_vix_history"] is True
