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
import re
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
    # The job path now KEEPS the coverage report it used to discard
    # (`df, _ = ...` became `df, data_coverage = ...`), so anchor on the call
    # rather than on the discarding tuple shape. The ordering property is what
    # this test is actually about.
    attach = src.index("await attach_required_data", src.index("async def run_backtest_job"))
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


@pytest.mark.parametrize("rel,frame_binding", [
    # optimizer.py seeds pools from `raw_df`. It now calls start_pool from TWO
    # places — the trial loop, and the re-rank's step-1 fan-out — and the re-rank
    # is DEFINED EARLIER in the file while receiving the frame as a parameter, so
    # the position of a call site proves nothing either way. Pin the binding that
    # every call site ultimately depends on instead (checked exhaustively below).
    ("app/optimizer.py", "raw_df = df"),
    # wfo.py passes the joined frame directly, so its call site is the binding.
    ("app/wfo.py", "start_pool(df"),
])
def test_worker_pool_is_seeded_after_the_join_not_before(rel, frame_binding):
    """`parallel_eval.start_pool` ships the frame to worker PROCESSES, which then
    evaluate against that copy. Seed it before the join and parallel trials would
    see an all-NaN column while sequential trials saw real values — an optimization
    result silently corrupted on only some paths, and invisible because both
    produce plausible numbers. The ordering is correct today; this pins it,
    because nothing else would catch the frame being captured earlier."""
    src = (BACKEND / rel).read_text(encoding="utf-8")
    assert src.index("attach_required_data(df") < src.index(frame_binding), (
        f"{rel} binds the worker-pool frame before attach_required_data — "
        "declared data columns would be missing in workers"
    )


def test_every_optimizer_pool_is_seeded_from_the_joined_frame():
    """Companion to the ordering check above: since call-site POSITION no longer
    proves anything in optimizer.py, prove instead that no call site can pass a
    different frame. Every start_pool must be seeded from `raw_df` — the one name
    bound after attach_required_data."""
    src = (BACKEND / "app/optimizer.py").read_text(encoding="utf-8")
    seeds = re.findall(r"start_pool\(\s*([A-Za-z_][A-Za-z0-9_]*)", src)
    assert seeds, "no start_pool call found in optimizer.py"
    assert set(seeds) == {"raw_df"}, (
        f"optimizer.py seeds a worker pool from {sorted(set(seeds) - {'raw_df'})} "
        "instead of the post-join `raw_df` — workers would miss declared data columns"
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

#: Strategies that declare `required_data` ON PURPOSE. The tripwire below is
#: about ACCIDENTAL declarations — a column added to a plugin without anyone
#: deciding that its runs should now depend on a warehouse join. Adding an id
#: here is the deliberate act the original message asked for.
INTENTIONAL_DATA_DECLARERS = {"atm_premium_flow_scalp"}


def test_only_intentional_strategies_declare_required_data():
    """Originally "nothing declares it"; updated deliberately when
    `atm_premium_flow_scalp` became the first strategy to read option flow.

    The tripwire still does its job: any OTHER strategy picking up a
    `required_data` entry fails here. Declaring is not free — it changes what
    the engine fetches on every path and makes the run depend on warehouse
    coverage — so it should never happen by accident.
    """
    reg = get_registry()
    if not reg.list_all():
        reg.auto_discover()
    for meta in reg.list_all():
        declared = meta.get("required_data", []) or []
        if meta["id"] in INTENTIONAL_DATA_DECLARERS:
            continue
        assert declared == [], (
            f"{meta['id']} declares required_data={declared}; that is fine, but "
            "it must be deliberate — add the id to INTENTIONAL_DATA_DECLARERS"
        )


def test_every_declared_data_column_actually_resolves():
    """A declaration the registry cannot serve is a DataColumnError at load
    time on every path — better to find it here than in a live evaluation."""
    from app.data_columns import DATA_COLUMN_REGISTRY
    reg = get_registry()
    if not reg.list_all():
        reg.auto_discover()
    for meta in reg.list_all():
        for name in meta.get("required_data", []) or []:
            assert name in DATA_COLUMN_REGISTRY, (
                f"{meta['id']} declares unknown data column {name!r}")
        # and resolving the whole declaration must not raise
        resolve_data_columns(meta.get("required_data", []) or [])


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


# ---------------------------------------------------------------------------
# the DECLARING path — end to end through AI authoring
# ---------------------------------------------------------------------------

class _AuthoredVixStrategy(StrategyBase):
    """Stands in for a full-Python authored strategy: declares the column and
    actually reads it, which is the whole point of required_data."""
    id = "_test_authored_vix"
    required_data = ["vix"]
    parameter_schema = {"thr": {"type": "float", "min": 8, "max": 30, "default": 15.0}}

    def evaluate(self, row, prev, params, ctx) -> Signal:
        return Signal(direction="CE" if row["vix"] >= params["thr"] else "NONE", score=60)


def test_install_smoke_frame_contains_declared_data_columns():
    """REGRESSION. The install-time smoke driver built its frame from
    `allowed_columns(required_features)` only, so a strategy declaring
    required_data=["vix"] and reading row["vix"] — the exact use case this
    feature exists for — was REJECTED at install with KeyError: 'vix'. The
    `required_data` parameter existed on allowed_columns but NO production caller
    passed it; only a test did."""
    from app.ai._py_smoke_driver import run_smoke

    inst = _AuthoredVixStrategy()
    cols = sorted(allowed_columns(getattr(inst, "required_features", ()),
                                  getattr(inst, "required_data", ())))
    assert "vix" in cols
    result = run_smoke(inst, cols)
    assert result.get("ok") is True, result


def test_smoke_driver_source_threads_required_data():
    """Pinned at the source, because the failure is silent: without this the
    driver still builds a frame and still runs — it just omits the column."""
    src = (BACKEND / "app/ai/_py_smoke_driver.py").read_text(encoding="utf-8")
    assert 'getattr(inst, "required_data", ())' in src


def test_undeclared_vix_rule_is_buildable_with_declaration_not_impossible():
    """The wizard must not report `vix` as missing data — the warehouse holds it
    for the full spot window. It is buildable; it just needs the declaration."""
    from app.ai.capability import FeasibilityClass, RuleTokens, classify_rule

    v = classify_rule(RuleTokens(cols=frozenset({"vix"}), barspan=1))
    assert v.feasibility is FeasibilityClass.BUILDABLE_WITH_FEATURE
    assert v.feature == "vix"
    assert v.live_feasible is True
    assert "required_data" in v.message


def test_declared_vix_rule_is_buildable_now():
    from app.ai.capability import FeasibilityClass, RuleTokens, classify_rule

    v = classify_rule(RuleTokens(cols=frozenset({"vix", "close"}), barspan=1),
                      required_data=("vix",))
    assert v.feasibility is FeasibilityClass.BUILDABLE_NOW


def test_spec_can_declare_required_data_and_it_survives_codegen():
    """Declaring must reach the INSTALLED class — without it the engine never
    joins the column and every read is NaN forever."""
    from app.ai.compiler import compile_spec, validate_spec
    from app.ai.spec_schema import Condition, ExitSpec, StrategySpec

    spec = StrategySpec(
        id="vix_spec_demo", name="VIX Spec Demo", description="declares vix",
        entry_ce=[Condition(left="vix", op=">", right=15.0)],
        exits=ExitSpec(spot_target_pts=30, spot_stop_pts=15),
        required_data=["vix"],
    )
    assert validate_spec(spec) == []
    assert "required_data = ['vix']" in compile_spec(spec)


def test_spec_referencing_vix_without_declaring_it_is_rejected():
    """Advertise != allow, enforced at compile time too."""
    from app.ai.compiler import validate_spec
    from app.ai.spec_schema import Condition, ExitSpec, StrategySpec

    spec = StrategySpec(
        id="vix_undeclared", name="Undeclared", description="reads vix without asking",
        entry_ce=[Condition(left="vix", op=">", right=15.0)],
        exits=ExitSpec(spot_target_pts=30, spot_stop_pts=15),
    )
    assert any("vix" in e for e in validate_spec(spec))


def test_unknown_data_column_in_a_spec_is_a_clean_validation_error():
    from app.ai.compiler import validate_spec
    from app.ai.spec_schema import Condition, ExitSpec, StrategySpec

    spec = StrategySpec(
        id="bad_data", name="Bad", description="asks for something that does not exist",
        entry_ce=[Condition(left="close", op=">", right="100")],
        exits=ExitSpec(spot_target_pts=30, spot_stop_pts=15),
        required_data=["not_a_column"],
    )
    errors = validate_spec(spec)
    assert any("required_data" in e and "not_a_column" in e for e in errors)


def test_manifest_no_longer_denies_vix_history():
    """It was False while the warehouse held 280 sessions of it, which made the
    authoring wizard refuse rules against real data. Backfilled 2026-07-27 to
    cover the full spot window."""
    assert WAREHOUSE_MANIFEST["has_vix_history"] is True
