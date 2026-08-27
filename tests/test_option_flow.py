"""Pure-layer tests for `app.option_flow` — the ATM CE/PE flow builder.

The module's whole job is to turn raw `options_1m` bars into per-spot-bar flow
columns whose 20-session baseline is computed HERE, in the data layer, and not
in the strategy — because the live window is hard-capped at 1,000 bars (under
three sessions) and a strategy-side baseline would therefore compute a different
number in backtest than live while both looked healthy.
"""
from __future__ import annotations

import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

IST = timezone(timedelta(hours=5, minutes=30))


def ts_at(date: str, hhmm: str) -> int:
    """Epoch ms for an IST wall-clock minute on a session date."""
    h, m = (int(x) for x in hhmm.split(":"))
    d = datetime.strptime(date, "%Y-%m-%d").replace(hour=h, minute=m, tzinfo=IST)
    return int(d.timestamp() * 1000)


def spot_bar(date: str, hhmm: str, close: float) -> dict:
    return {"ts": ts_at(date, hhmm), "close": close}


def opt_bar(date, hhmm, *, side, strike, expiry, volume, oi) -> dict:
    return {"ts": ts_at(date, hhmm), "side": side, "strike": float(strike),
            "expiry_date": expiry, "volume": float(volume), "oi": float(oi)}


class TestAtmStrike:
    def test_rounds_to_nearest_step(self):
        from app.option_flow import atm_strike
        assert atm_strike(24288.7, 50) == 24300
        assert atm_strike(24274.0, 50) == 24250   # 24 below vs 26 above
        assert atm_strike(24224.9, 50) == 24200
        assert atm_strike(81537.0, 100) == 81500

    def test_exact_midpoint_ties_match_the_screen_cli(self):
        """`screen_option_buying._atm_strike` is `int(round(spot/step)*step)`,
        so an exact midpoint takes Python's round-half-to-EVEN. The research
        screen and the live data layer must pick the same contract for the same
        spot, so this convention is pinned rather than left incidental."""
        from app.option_flow import atm_strike
        assert atm_strike(24275.0, 50) == 24300   # 485.5 -> 486 (even)
        assert atm_strike(24225.0, 50) == 24200   # 484.5 -> 484 (even)

    def test_rejects_a_non_positive_step(self):
        from app.option_flow import atm_strike
        with pytest.raises(ValueError):
            atm_strike(24000.0, 0)


class TestRawColumns:
    """One session, one frame bar: the raw CE/PE volume and OI must arrive."""

    def test_emits_raw_volume_and_oi_for_the_atm_strike(self):
        from app.option_flow import build_option_flow_rows
        d = "2025-01-06"
        spot = [spot_bar(d, "09:15", 24010.0), spot_bar(d, "09:16", 24012.0)]
        opts = [
            opt_bar(d, "09:15", side="CE", strike=24000, expiry=d, volume=100, oi=5000),
            opt_bar(d, "09:16", side="CE", strike=24000, expiry=d, volume=140, oi=5200),
            opt_bar(d, "09:15", side="PE", strike=24000, expiry=d, volume=200, oi=7000),
            opt_bar(d, "09:16", side="PE", strike=24000, expiry=d, volume=170, oi=6900),
        ]
        rows, diag = build_option_flow_rows(
            spot_rows=spot, option_rows=opts, expiries=[d], strike_step=50,
            frame_ts=[ts_at(d, "09:16")],
        )
        assert len(rows) == 1
        r = rows[0]
        assert r["ts"] == ts_at(d, "09:16")
        assert r["ce_volume"] == 140.0
        assert r["pe_volume"] == 170.0
        assert r["ce_oi"] == 5200.0
        assert r["pe_oi"] == 6900.0

    def test_oi_delta_is_within_session_and_nan_on_the_first_bar(self):
        from app.option_flow import build_option_flow_rows
        d = "2025-01-06"
        spot = [spot_bar(d, "09:15", 24010.0), spot_bar(d, "09:16", 24012.0)]
        opts = [
            opt_bar(d, "09:15", side="CE", strike=24000, expiry=d, volume=100, oi=5000),
            opt_bar(d, "09:16", side="CE", strike=24000, expiry=d, volume=140, oi=5200),
            opt_bar(d, "09:15", side="PE", strike=24000, expiry=d, volume=200, oi=7000),
            opt_bar(d, "09:16", side="PE", strike=24000, expiry=d, volume=170, oi=6900),
        ]
        rows, _ = build_option_flow_rows(
            spot_rows=spot, option_rows=opts, expiries=[d], strike_step=50,
            frame_ts=[ts_at(d, "09:15"), ts_at(d, "09:16")],
        )
        first, second = rows[0], rows[1]
        assert math.isnan(first["ce_oi_delta"]), "no prior bar => delta is unknown, not 0"
        assert math.isnan(first["pe_oi_delta"])
        assert second["ce_oi_delta"] == 200.0
        assert second["pe_oi_delta"] == -100.0


class TestEveryFieldIsAlwaysPresent:
    """`app.vix.build_asof_index` falls back to 0.0 for a row that LACKS the
    field it is asked for. A z-score of 0.0 reads as 'perfectly typical', so an
    omitted field would make a strategy inert while looking healthy. Every row
    must therefore carry every field explicitly, NaN where unknown."""

    def test_all_fields_present_on_every_row_even_with_no_option_data(self):
        from app.option_flow import OPTION_FLOW_FIELDS, build_option_flow_rows
        d = "2025-01-06"
        rows, _ = build_option_flow_rows(
            spot_rows=[spot_bar(d, "09:15", 24010.0)], option_rows=[],
            expiries=[d], strike_step=50, frame_ts=[ts_at(d, "09:15")],
        )
        assert len(rows) == 1
        for field in OPTION_FLOW_FIELDS:
            assert field in rows[0], f"{field} missing => build_asof_index would read 0.0"
            assert isinstance(rows[0][field], float)
            assert math.isnan(rows[0][field]), f"{field} should be NaN with no option data"

    def test_never_emits_none(self):
        """`float(None)` raises inside build_asof_index — None must never appear."""
        from app.option_flow import OPTION_FLOW_FIELDS, build_option_flow_rows
        d = "2025-01-06"
        rows, _ = build_option_flow_rows(
            spot_rows=[spot_bar(d, "09:15", 24010.0)], option_rows=[],
            expiries=[d], strike_step=50, frame_ts=[ts_at(d, "09:15")],
        )
        for field in OPTION_FLOW_FIELDS:
            assert rows[0][field] is not None


# ---------------------------------------------------------------------------
# The causal baseline — the reason this module exists at all
# ---------------------------------------------------------------------------

def sessions(n: int, start: str = "2025-01-06") -> list:
    """`n` consecutive weekday session dates."""
    d = datetime.strptime(start, "%Y-%m-%d")
    out = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)
    return out


def warehouse(per_session: dict, *, strike=24000, spot=24010.0, buckets=("09:15", "09:16")):
    """Build (spot_rows, option_rows, expiries) for `{session: {bucket: (ce_vol, pe_vol, ce_oi, pe_oi)}}`."""
    spot_rows, option_rows, expiries = [], [], set()
    for session, by_bucket in per_session.items():
        expiries.add(session)
        for bucket in buckets:
            spot_rows.append(spot_bar(session, bucket, spot))
            vals = by_bucket.get(bucket)
            if vals is None:
                continue
            ce_v, pe_v, ce_oi, pe_oi = vals
            option_rows.append(opt_bar(session, bucket, side="CE", strike=strike,
                                       expiry=session, volume=ce_v, oi=ce_oi))
            option_rows.append(opt_bar(session, bucket, side="PE", strike=strike,
                                       expiry=session, volume=pe_v, oi=pe_oi))
    return spot_rows, option_rows, sorted(expiries)


def flat_history(n: int, *, ce_vol=100.0, buckets=("09:15", "09:16")) -> dict:
    return {s: {b: (ce_vol, 200.0, 5000.0, 7000.0) for b in buckets} for s in sessions(n)}


class TestCausalBaseline:
    def test_z_is_nan_below_the_minimum_baseline(self):
        """Nine prior sessions is not a distribution. Unknown is NaN, not 0."""
        from app.option_flow import MIN_BASELINE_SESSIONS, build_option_flow_rows
        hist = flat_history(MIN_BASELINE_SESSIONS)          # 9 prior + the last one
        days = sorted(hist)
        for i, s in enumerate(days):
            hist[s]["09:16"] = (100.0 + i, 200.0, 5000.0, 7000.0)
        spot, opts, exp = warehouse(hist)
        rows, _ = build_option_flow_rows(
            spot_rows=spot, option_rows=opts, expiries=exp, strike_step=50,
            frame_ts=[ts_at(days[-1], "09:16")],
        )
        assert math.isnan(rows[0]["ce_volume_z"])
        assert not math.isnan(rows[0]["ce_volume"]), "the RAW value is still known"

    def test_z_excludes_the_bars_own_session(self):
        """A bar must never enter its own baseline — that is lookahead, and it
        would also drag every z toward 0."""
        from app.option_flow import build_option_flow_rows
        hist = flat_history(21)
        days = sorted(hist)
        today = days[-1]
        # 20 prior sessions all at 100; today is a 10x outlier.
        hist[today]["09:16"] = (1000.0, 200.0, 5000.0, 7000.0)
        # give the prior sessions some spread so std > 0
        for i, s in enumerate(days[:-1]):
            hist[s]["09:16"] = (100.0 + (i % 5), 200.0, 5000.0, 7000.0)
        spot, opts, exp = warehouse(hist)
        rows, _ = build_option_flow_rows(
            spot_rows=spot, option_rows=opts, expiries=exp, strike_step=50,
            frame_ts=[ts_at(today, "09:16")],
        )
        z = rows[0]["ce_volume_z"]
        assert z > 100, f"a 10x outlier against a tight baseline must be huge, got {z}"

    def test_z_buckets_by_time_of_day(self):
        """09:16 must be compared with 09:16, not with the whole day."""
        from app.option_flow import build_option_flow_rows
        days = sorted(sessions(21))
        today = days[-1]
        hist = {}
        for i, s in enumerate(days):
            # 09:15 is a busy bucket, 09:16 a quiet one.
            hist[s] = {"09:15": (1000.0 + (i % 5), 200.0, 5000.0, 7000.0),
                       "09:16": (100.0 + (i % 5), 200.0, 5000.0, 7000.0)}
        # today's 09:16 prints a value that is TYPICAL for 09:15 but wild for 09:16
        hist[today]["09:16"] = (1000.0, 200.0, 5000.0, 7000.0)
        spot, opts, exp = warehouse(hist)
        rows, _ = build_option_flow_rows(
            spot_rows=spot, option_rows=opts, expiries=exp, strike_step=50,
            frame_ts=[ts_at(today, "09:16")],
        )
        assert rows[0]["ce_volume_z"] > 100, "an all-day baseline would score this ~0"

    def test_z_is_nan_when_the_baseline_has_no_spread(self):
        """std == 0 makes z undefined. Returning 0.0 would assert 'perfectly
        typical' about a bar that may be nothing of the sort."""
        from app.option_flow import build_option_flow_rows
        hist = flat_history(21)
        days = sorted(hist)
        hist[days[-1]]["09:16"] = (999.0, 200.0, 5000.0, 7000.0)
        spot, opts, exp = warehouse(hist)
        rows, _ = build_option_flow_rows(
            spot_rows=spot, option_rows=opts, expiries=exp, strike_step=50,
            frame_ts=[ts_at(days[-1], "09:16")],
        )
        assert math.isnan(rows[0]["ce_volume_z"])

    def test_z_matches_a_hand_computed_sample_z_score(self):
        from app.option_flow import build_option_flow_rows
        import statistics
        days = sorted(sessions(21))
        today = days[-1]
        prior = [100.0 + i for i in range(20)]
        hist = {}
        for i, s in enumerate(days[:-1]):
            hist[s] = {"09:15": (50.0, 200.0, 5000.0, 7000.0),
                       "09:16": (prior[i], 200.0, 5000.0, 7000.0)}
        hist[today] = {"09:15": (50.0, 200.0, 5000.0, 7000.0),
                       "09:16": (250.0, 200.0, 5000.0, 7000.0)}
        spot, opts, exp = warehouse(hist)
        rows, _ = build_option_flow_rows(
            spot_rows=spot, option_rows=opts, expiries=exp, strike_step=50,
            frame_ts=[ts_at(today, "09:16")],
        )
        expected = (250.0 - statistics.fmean(prior)) / statistics.stdev(prior)
        assert rows[0]["ce_volume_z"] == pytest.approx(expected, abs=0.01)

    def test_baseline_window_is_capped_at_baseline_sessions(self):
        """The 21st-prior session must not reach today. Without a cap the live
        and backtest baselines would differ in SIZE as well as content."""
        from app.option_flow import build_option_flow_rows
        days = sorted(sessions(26))
        today = days[-1]
        hist = {}
        for i, s in enumerate(days[:-1]):
            # the 5 OLDEST sessions are wild; the 20 most recent are tight
            wild = i < 5
            hist[s] = {"09:15": (50.0, 200.0, 5000.0, 7000.0),
                       "09:16": ((100000.0 if wild else 100.0 + (i % 5)),
                                 200.0, 5000.0, 7000.0)}
        hist[today] = {"09:15": (50.0, 200.0, 5000.0, 7000.0),
                       "09:16": (1000.0, 200.0, 5000.0, 7000.0)}
        spot, opts, exp = warehouse(hist)
        rows, _ = build_option_flow_rows(
            spot_rows=spot, option_rows=opts, expiries=exp, strike_step=50,
            frame_ts=[ts_at(today, "09:16")],
        )
        # Against the tight 20 the outlier is huge; if the 5 wild sessions leaked
        # in, the std would explode and z would collapse toward 0.
        assert rows[0]["ce_volume_z"] > 100


# ---------------------------------------------------------------------------
# Discriminating tests - every one of these was written because a mutation
# SURVIVED the first sweep. A test that cannot tell a guard from its absence
# is not a test of that guard.
# ---------------------------------------------------------------------------

class TestIdentityJoin:
    """Deliverable 11.1: option bars join by underlying+expiry+strike+side+ts.
    A fixture holding only one strike and one expiry cannot tell an identity
    match from no match at all, so these put decoys in the way."""

    def test_a_neighbouring_strike_is_not_read(self):
        from app.option_flow import build_option_flow_rows
        d = "2025-01-06"
        spot = [spot_bar(d, "09:15", 24010.0)]
        opts = [
            opt_bar(d, "09:15", side="CE", strike=24000, expiry=d, volume=140, oi=5200),
            opt_bar(d, "09:15", side="PE", strike=24000, expiry=d, volume=170, oi=6900),
            # decoys one ladder step either side of the ATM
            opt_bar(d, "09:15", side="CE", strike=24050, expiry=d, volume=999999, oi=1),
            opt_bar(d, "09:15", side="CE", strike=23950, expiry=d, volume=888888, oi=2),
        ]
        rows, _ = build_option_flow_rows(
            spot_rows=spot, option_rows=opts, expiries=[d], strike_step=50,
            frame_ts=[ts_at(d, "09:15")],
        )
        assert rows[0]["ce_volume"] == 140.0
        assert rows[0]["ce_oi"] == 5200.0

    def test_a_later_expiry_at_the_same_strike_is_not_read(self):
        from app.option_flow import build_option_flow_rows
        d = "2025-01-06"
        far = "2025-01-30"
        spot = [spot_bar(d, "09:15", 24010.0)]
        opts = [
            opt_bar(d, "09:15", side="CE", strike=24000, expiry=d, volume=140, oi=5200),
            opt_bar(d, "09:15", side="PE", strike=24000, expiry=d, volume=170, oi=6900),
            opt_bar(d, "09:15", side="CE", strike=24000, expiry=far, volume=777777, oi=3),
            opt_bar(d, "09:15", side="PE", strike=24000, expiry=far, volume=666666, oi=4),
        ]
        rows, _ = build_option_flow_rows(
            spot_rows=spot, option_rows=opts, expiries=[d, far], strike_step=50,
            frame_ts=[ts_at(d, "09:15")],
        )
        assert rows[0]["ce_volume"] == 140.0, "the NEAREST upcoming expiry is the contract"
        assert rows[0]["pe_volume"] == 170.0


class TestAtmAnchor:
    def test_strike_comes_from_the_sessions_first_bar_not_its_last(self):
        """Anchoring on any later bar lets a price the strategy has not seen yet
        decide which contract an earlier bar was watching - lookahead."""
        from app.option_flow import build_option_flow_rows
        d = "2025-01-06"
        spot = [spot_bar(d, "09:15", 24010.0),   # ATM 24000
                spot_bar(d, "09:16", 24120.0),
                spot_bar(d, "09:17", 24240.0)]   # ATM 24250
        opts = []
        for hhmm in ("09:15", "09:16", "09:17"):
            opts += [
                opt_bar(d, hhmm, side="CE", strike=24000, expiry=d, volume=100, oi=5000),
                opt_bar(d, hhmm, side="PE", strike=24000, expiry=d, volume=200, oi=7000),
                opt_bar(d, hhmm, side="CE", strike=24250, expiry=d, volume=555555, oi=9),
                opt_bar(d, hhmm, side="PE", strike=24250, expiry=d, volume=444444, oi=8),
            ]
        rows, _ = build_option_flow_rows(
            spot_rows=spot, option_rows=opts, expiries=[d], strike_step=50,
            frame_ts=[ts_at(d, "09:17")],
        )
        assert rows[0]["ce_volume"] == 100.0
        assert rows[0]["pe_volume"] == 200.0


class TestBaselineBoundary:
    """min_baseline_sessions prior sessions is enough; one fewer is not."""

    def _z_with(self, n_sessions: int):
        from app.option_flow import MIN_BASELINE_SESSIONS, build_option_flow_rows
        days = sorted(sessions(n_sessions))
        today = days[-1]
        hist = {}
        for i, s in enumerate(days[:-1]):
            hist[s] = {"09:15": (100.0 + (i % 4), 200.0, 5000.0, 7000.0)}
        hist[today] = {"09:15": (900.0, 200.0, 5000.0, 7000.0)}
        spot, opts, exp = warehouse(hist, buckets=("09:15",))
        rows, _ = build_option_flow_rows(
            spot_rows=spot, option_rows=opts, expiries=exp, strike_step=50,
            frame_ts=[ts_at(today, "09:15")],
            min_baseline_sessions=MIN_BASELINE_SESSIONS,
        )
        return rows[0]["ce_volume_z"]

    def test_exactly_the_minimum_prior_sessions_yields_a_score(self):
        from app.option_flow import MIN_BASELINE_SESSIONS
        z = self._z_with(MIN_BASELINE_SESSIONS + 1)
        assert not math.isnan(z), "min_baseline_sessions priors is ENOUGH"

    def test_one_fewer_than_the_minimum_yields_nan(self):
        from app.option_flow import MIN_BASELINE_SESSIONS
        assert math.isnan(self._z_with(MIN_BASELINE_SESSIONS))

    def test_growing_window_still_excludes_the_bars_own_session(self):
        """Between min_baseline and baseline_sessions the stats come from the
        growing-window branch, which the 21-session tests never reach."""
        from app.option_flow import build_option_flow_rows
        days = sorted(sessions(12))
        today = days[-1]
        hist = {}
        for i, s in enumerate(days[:-1]):
            hist[s] = {"09:15": (100.0 + (i % 4), 200.0, 5000.0, 7000.0)}
        hist[today] = {"09:15": (1000.0, 200.0, 5000.0, 7000.0)}
        spot, opts, exp = warehouse(hist, buckets=("09:15",))
        rows, _ = build_option_flow_rows(
            spot_rows=spot, option_rows=opts, expiries=exp, strike_step=50,
            frame_ts=[ts_at(today, "09:15")],
        )
        assert rows[0]["ce_volume_z"] > 100, "self in its own baseline collapses this to ~3"

    def test_a_min_count_below_two_is_refused(self):
        """ddof=1 over a single sample is a division by zero, and a z built from
        one observation is not a z at all."""
        from app.option_flow import build_option_flow_rows
        days = sorted(sessions(4))
        hist = {s: {"09:15": (100.0 + i, 200.0, 5000.0, 7000.0)}
                for i, s in enumerate(days)}
        spot, opts, exp = warehouse(hist, buckets=("09:15",))
        with pytest.raises(ValueError):
            build_option_flow_rows(
                spot_rows=spot, option_rows=opts, expiries=exp, strike_step=50,
                frame_ts=[ts_at(days[-1], "09:15")], min_baseline_sessions=1,
            )


class TestLiquidityMedian:
    """atm_volume_median_20d - the 20-session causal median behind the
    liquidity floor in the frozen Candidate A spec."""

    def _hist(self, n_prior: int, today_vol: float):
        days = sorted(sessions(n_prior + 1))
        today = days[-1]
        hist, k = {}, 1
        for s in days[:-1]:
            per = {}
            for b in ("09:15", "09:16"):
                # straddle volume walks 1..2*n_prior across the prior sessions
                per[b] = (float(k), 0.0, 5000.0, 7000.0)
                k += 1
            hist[s] = per
        hist[today] = {b: (today_vol, 0.0, 5000.0, 7000.0) for b in ("09:15", "09:16")}
        return hist, today

    def test_median_is_the_causal_median_of_prior_sessions_only(self):
        from app.option_flow import build_option_flow_rows
        hist, today = self._hist(10, 10000.0)
        spot, opts, exp = warehouse(hist)
        rows, _ = build_option_flow_rows(
            spot_rows=spot, option_rows=opts, expiries=exp, strike_step=50,
            frame_ts=[ts_at(today, "09:15")],
        )
        # 10 prior sessions x 2 bars = straddle volumes 1..20 -> median 10.5.
        # Including today's two 10000s would move it to 11.5.
        assert rows[0]["atm_volume_median_20d"] == pytest.approx(10.5)

    def test_median_is_nan_below_the_minimum_prior_sessions(self):
        from app.option_flow import build_option_flow_rows
        hist, today = self._hist(5, 10000.0)
        spot, opts, exp = warehouse(hist)
        rows, _ = build_option_flow_rows(
            spot_rows=spot, option_rows=opts, expiries=exp, strike_step=50,
            frame_ts=[ts_at(today, "09:15")],
        )
        assert math.isnan(rows[0]["atm_volume_median_20d"])

    def test_a_bar_missing_one_leg_is_excluded_not_half_counted(self):
        """Straddle volume with a missing put leg is UNKNOWN, not the call
        leg's volume."""
        from app.option_flow import build_option_flow_rows
        days = sorted(sessions(11))
        today = days[-1]
        spot_rows, option_rows, expiries = [], [], set()
        for s in days:
            expiries.add(s)
            for b in ("09:15", "09:16"):
                spot_rows.append(spot_bar(s, b, 24010.0))
            # 09:15 has both legs (straddle 300); 09:16 has ONLY a call leg.
            option_rows += [
                opt_bar(s, "09:15", side="CE", strike=24000, expiry=s, volume=100, oi=5000),
                opt_bar(s, "09:15", side="PE", strike=24000, expiry=s, volume=200, oi=7000),
                opt_bar(s, "09:16", side="CE", strike=24000, expiry=s, volume=90000, oi=5000),
            ]
        rows, _ = build_option_flow_rows(
            spot_rows=spot_rows, option_rows=option_rows, expiries=sorted(expiries),
            strike_step=50, frame_ts=[ts_at(today, "09:15")],
        )
        assert rows[0]["atm_volume_median_20d"] == pytest.approx(300.0)


class TestRawSanitising:
    """The reachable coercion guard lives in _atm_series_by_session."""

    def test_unparseable_volume_becomes_nan_not_zero(self):
        from app.option_flow import build_option_flow_rows
        d = "2025-01-06"
        spot = [spot_bar(d, "09:15", 24010.0)]
        opts = [
            {"ts": ts_at(d, "09:15"), "side": "CE", "strike": 24000.0,
             "expiry_date": d, "volume": "not-a-number", "oi": None},
            opt_bar(d, "09:15", side="PE", strike=24000, expiry=d, volume=170, oi=6900),
        ]
        rows, _ = build_option_flow_rows(
            spot_rows=spot, option_rows=opts, expiries=[d], strike_step=50,
            frame_ts=[ts_at(d, "09:15")],
        )
        assert math.isnan(rows[0]["ce_volume"]), "junk is UNKNOWN, not 0"
        assert math.isnan(rows[0]["ce_oi"])
        assert rows[0]["pe_volume"] == 170.0, "the good leg still arrives"
