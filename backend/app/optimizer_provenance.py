"""What an optimizer job must leave behind when it is deleted.

Register item #14, found by hitting it. A saved SENSEX backtest run recorded
``config.optimization_job_id = "cac0151c-..."`` and that job is not in
``optimization_jobs`` — 30 jobs stored, none of them it. Since there is a
``DELETE /api/optimize/jobs/{job_id}`` endpoint and a bulk-delete affordance in
the Job History pane, an ordinary user deletion is far more likely than a
persistence bug.

The consequence is not ordinary. That run reports **+₹87,721 on 480 trials**, and
with its job gone there is no way to recover *what was searched*: the parameter
space, the trial count, the parameter importance, the data-integrity blockers,
the quality warnings. **A result whose search space is unknowable cannot be
audited, and an unauditable result is worth less than no result — because it
still looks like evidence.**

The run already carries the job id and a trial count; everything an audit
actually reaches for lives on the job. So the job's audit-critical fields are
copied onto every referencing run *before* the job is deleted. Deletion stays
allowed and stops being destructive.

Pure: no motor, no I/O. The router does the writing.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Tuple

#: The fields an audit actually asks for, in the order it asks for them.
#:
#: Deliberately EXCLUDES ``trial_log``, ``heatmap``, ``best_so_far`` and the other
#: bulk payloads that make a job document large. The question an audit asks is
#: "what space was searched, and what did the guards say about it" — not "what
#: was every step taken through that space". Copying the bulk onto every
#: referencing run would multiply the collection for no audit value.
PROVENANCE_FIELDS: Tuple[str, ...] = (
    "strategy_id",
    "instrument",
    "objective",
    "method",
    "evaluation_mode",
    "lot_size",
    "n_trials_completed",
    "n_trials_total",
    "param_space",
    "parameter_importance",
    "research_eligibility",
    "best_quality",
    "best_value",
    "best_value_metric",
    "early_stopped",
)


def provenance_snapshot(job: Any) -> Dict[str, Any]:
    """The audit trail to preserve from ``job``, or ``{}`` if there is none.

    A field the job does not carry is simply ABSENT from the snapshot rather than
    present as ``None``. The distinction matters for the same reason it does in
    the chain recorder: a null reads as "we looked and there was nothing", which
    is a different claim from "this job never recorded it".
    """
    if not isinstance(job, Mapping):
        return {}
    snap: Dict[str, Any] = {}
    job_id = job.get("id")
    if job_id not in (None, ""):
        snap["optimization_job_id"] = str(job_id)
    for field in PROVENANCE_FIELDS:
        if field in job:
            snap[field] = job[field]
    return snap
