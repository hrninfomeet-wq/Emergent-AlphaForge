"""The incumbent seeding must actually be wired into the optimizer, in the right branch.

`backend/app/optimizer.py` cannot be imported on the host (it pulls in `motor` via
`app.db`), so these are SOURCE-level guards on the wiring. The behaviour itself is
covered two other ways: `tests/test_incumbent_seed.py` unit-tests the pure helpers,
and an end-to-end optimizer run asserts `incumbent_seeds` lands on the job document
and that the result beats the previously known-good preset.

The specific mistakes these lock out:
  * seeding the RESUME branch too (it rebuilds from trial_log; re-enqueueing would
    re-pay for trials already bought), and
  * letting a malformed preset document abort a whole optimization run.
"""
from __future__ import annotations

import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPT = os.path.join(ROOT, "backend", "app", "optimizer.py")


@pytest.fixture(scope="module")
def src() -> str:
    with open(OPT, encoding="utf-8") as fh:
        return fh.read()


def test_seeding_helpers_are_imported(src):
    assert "from app.incumbent_seed import build_seed_trials" in src
    assert "async def _gather_incumbents(" in src


def test_enqueue_trial_is_actually_called(src):
    """The whole defect was that no code path called enqueue_trial."""
    assert "study.enqueue_trial(" in src
    assert "skip_if_exists=True" in src


def test_seeding_happens_only_on_a_fresh_study(src):
    """Resume rebuilds from trial_log; seeding there would redo paid-for trials."""
    fresh = src.index("study = optuna.create_study(\n                direction=\"maximize\", sampler=_sampler,"
                      .replace("\n", "\r\n")) if "\r\n" in src else src.index(
        'study = optuna.create_study(\n                direction="maximize", sampler=_sampler,')
    enq = src.index("study.enqueue_trial(")
    rebuild = src.index("def _rebuild_study(")
    assert fresh < enq, "enqueue must follow the fresh study creation"
    # the resume path calls _rebuild_study; it must not enqueue seeds
    resume_block = src[rebuild:rebuild + 2000]
    assert "enqueue_trial" not in resume_block


def test_seeding_failure_cannot_abort_the_job(src):
    """A bad preset document must cost one seed, never the whole run."""
    i = src.index("study.enqueue_trial(")
    window = src[i - 700:i + 700]
    assert "except Exception" in window
    assert "incumbent seeding skipped" in window


def test_seeds_are_persisted_for_operator_visibility(src):
    """The operator asked to SEE which bounds were in force; the seed record
    carries both the point tried and the dimensions the bounds made unreachable."""
    assert '"incumbent_seeds": seed_records' in src


def test_gather_incumbents_covers_presets_prior_jobs_and_defaults(src):
    i = src.index("async def _gather_incumbents(")
    body = src[i:i + 3000]
    assert "db.presets.find(" in body
    assert "db.optimization_jobs.find(" in body
    assert "strategy.merged_params({})" in body
    # prior jobs must be ranked best-first and bounded
    assert '.sort([("best_value", -1)]).limit(' in body
    # instrument must be respected so a SENSEX preset cannot seed a NIFTY job
    assert "instrument" in body


def test_gather_incumbents_is_individually_fault_tolerant(src):
    """Three independent lookups; one failing source must not lose the others."""
    i = src.index("async def _gather_incumbents(")
    body = src[i:i + 3000]
    assert body.count("except Exception") >= 3
