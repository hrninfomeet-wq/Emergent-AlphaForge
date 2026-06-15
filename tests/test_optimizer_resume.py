"""Regression: POST /optimize/jobs/{id}/resume must find a real non-WFO job.

The resume endpoint projected {"_id":0,"kind":1}; a regular optimizer job has no
`kind` field, so find_one returns an EMPTY dict for a job that exists, and the old
`if not doc:` falsy-check raised 404 "Job not found" — breaking resume for every
non-WFO optimization (WFO jobs have kind="wfo" so projected non-empty and worked).
"""
from tests.contract_corpus import backend_api_text


def _resume_body() -> str:
    src = backend_api_text()
    i = src.index("async def resume_opt_job")
    return src[i:i + 700]


def test_resume_existence_check_is_is_none_not_falsy():
    body = _resume_body()
    assert "is None" in body                 # genuine "not found" check
    assert "if not doc" not in body          # the falsy-empty-dict bug must be gone


def test_resume_projects_a_stable_field():
    # Projecting `id` (always present) keeps the found-doc non-empty regardless of kind.
    body = _resume_body()
    assert '"id": 1' in body
