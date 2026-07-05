"""Strategy read + lifecycle routes (list/get/retire/un-retire/delete/reload).

Host-importable: heavy deps (motor DB, square-off, deployment status) are behind
module-level seams that import lazily, so router tests can patch them without
importing motor. Mirrors the isolation pattern in app/routers/live_broker.py.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException

from app.strategies.base import get_registry
from app.schemas import (
    StrategyAuthorReq,
    StrategyFromSourceReq,
    ConverseReq,
    PythonFromSourceReq,
    PythonValidateReq,
    PythonInstallReq,
)

api = APIRouter()
log = logging.getLogger(__name__)


def _db():
    from app.db import get_db  # lazy: app.db imports motor at top
    return get_db()


def _delete_plugin_file(strategy_id: str) -> bool:
    """Remove the .py for a custom plugin. Returns True if a file was removed.
    Only deletes files physically under .../strategies/plugins/ as a safety net."""
    from app.strategy_source_hash import strategy_file_path
    s = get_registry().get(strategy_id)
    if s is None:
        return False
    path = strategy_file_path(s)
    plugins_marker = os.path.join("strategies", "plugins")
    if path and os.path.isfile(path) and plugins_marker in str(path):
        os.remove(path)
        return True
    return False


async def _square_off_strategy_deployments(strategy_id: str) -> List[Dict[str, Any]]:
    """Pause + scoped square-off every ACTIVE deployment of a strategy.
    Lazily imports the heavy deployment/paper modules."""
    from app.paper_squareoff import square_off_open_paper_trades
    from app.runtime import _set_deployment_status, upstox_stream_manager
    db = _db()
    active = await db.strategy_deployments.find(
        {"strategy_id": strategy_id, "status": "ACTIVE"}, {"_id": 0, "id": 1}
    ).to_list(length=None)
    summaries: List[Dict[str, Any]] = []
    for d in active:
        s = await square_off_open_paper_trades(
            db, deployment_id=d["id"],
            latest_tick_lookup=upstox_stream_manager.latest_tick_map().get,
            reason="manual_retire",
        )
        summaries.extend(s)
        await _set_deployment_status(d["id"], "PAUSED")
    return summaries


@api.get("/strategies")
async def list_strategies():
    items = get_registry().list_all()
    db = _db()
    rows = await db.strategy_lifecycle.find({"retired": True}, {"_id": 0}).to_list(length=None)
    retired = {r["strategy_id"] for r in rows}
    for it in items:
        it["is_retired"] = it["id"] in retired
    return {"items": items}


@api.get("/strategies/catalog")
async def author_catalog():
    """Vocabulary for the authoring wizard: valid columns/ops/regimes/exit fields.
    Pure + host-safe (no DB). Lazy-imports the catalog helpers."""
    from app.ai.compiler import allowed_columns
    from app.ai.spec_schema import CMP_OPS
    from app.ai.capability import capability_summary
    return {
        "columns": sorted(allowed_columns()),
        "ops": list(CMP_OPS),
        "regimes": ["TREND", "TREND_EXPANDING", "CHOP", "VOLATILE_CHOP", "MIXED", "UNKNOWN"],
        "exit_fields": ["spot_target_pts", "spot_stop_pts", "target_pct", "stop_pct", "time_stop_minutes"],
        "param_types": ["int", "float", "bool"],
        # What the engine can/can't build — shown up-front in the wizard so users
        # set expectations before writing a description (not only after a reject).
        "capability": capability_summary(),
    }


@api.get("/strategies/{strategy_id}")
async def get_strategy(strategy_id: str):
    s = get_registry().get(strategy_id)
    if not s:
        raise HTTPException(404, f"Strategy {strategy_id} not found")
    meta = s.meta()
    life = await _db().strategy_lifecycle.find_one({"strategy_id": strategy_id}, {"_id": 0})
    meta["is_retired"] = bool(life and life.get("retired"))
    return meta


def _exists(strategy_id: str) -> bool:
    reg = get_registry()
    return reg.get(strategy_id) is not None or reg.origin_of(strategy_id) is not None


@api.post("/strategies/{strategy_id}/retire")
async def retire_strategy(strategy_id: str):
    if not _exists(strategy_id):
        raise HTTPException(404, f"Strategy {strategy_id} not found")
    summaries = await _square_off_strategy_deployments(strategy_id)
    now = datetime.now(timezone.utc).isoformat()
    await _db().strategy_lifecycle.update_one(
        {"strategy_id": strategy_id},
        {"$set": {"strategy_id": strategy_id, "retired": True, "retired_at": now}},
        upsert=True,
    )
    return {"strategy_id": strategy_id, "retired": True,
            "squared_off": summaries, "squared_off_count": len(summaries)}


@api.post("/strategies/{strategy_id}/un-retire")
async def unretire_strategy(strategy_id: str):
    if not _exists(strategy_id):
        raise HTTPException(404, f"Strategy {strategy_id} not found")
    await _db().strategy_lifecycle.update_one(
        {"strategy_id": strategy_id},
        {"$set": {"strategy_id": strategy_id, "retired": False, "retired_at": None}},
        upsert=True,
    )
    return {"strategy_id": strategy_id, "retired": False}


@api.delete("/strategies/{strategy_id}")
async def delete_strategy(strategy_id: str):
    reg = get_registry()
    origin = reg.origin_of(strategy_id)
    if origin is None:
        raise HTTPException(404, f"Strategy {strategy_id} not found")
    if origin != "custom":
        raise HTTPException(403, "Built-in strategies cannot be deleted — retire them instead")
    db = _db()
    life = await db.strategy_lifecycle.find_one({"strategy_id": strategy_id}, {"_id": 0})
    if not (life and life.get("retired")):
        raise HTTPException(409, "Retire the strategy before deleting its file")
    deps = await db.strategy_deployments.find({"strategy_id": strategy_id}, {"_id": 0}).to_list(length=None)
    blocking = [d for d in deps if d.get("status") != "ARCHIVED"]
    if blocking:
        raise HTTPException(409, f"{len(blocking)} deployment(s) still reference this strategy; archive them first")
    # Order matters: _delete_plugin_file resolves the file path via the registry
    # (get_registry().get), so it MUST run before unregister() clears the entry.
    # NOTE: these 3 teardown steps are not atomic — a mid-step failure can leave
    # file/registry/lifecycle partially removed. Acceptable for V1 (single-user, rare op).
    _delete_plugin_file(strategy_id)
    reg.unregister(strategy_id)
    await db.strategy_lifecycle.delete_one({"strategy_id": strategy_id})
    return {"strategy_id": strategy_id, "deleted": True}


@api.post("/strategies/reload")
async def reload_strategies():
    reg = get_registry()
    reg.reload()
    return {"count": len(reg.list_all())}


async def is_retired(strategy_id: str) -> bool:
    life = await _db().strategy_lifecycle.find_one({"strategy_id": strategy_id}, {"_id": 0})
    return bool(life and life.get("retired"))


# ---------------------------------------------------------------------------
# Strategy Authoring — patchable seams (Phase 2A)
# ---------------------------------------------------------------------------

def _plugins_dir() -> str:
    """Absolute path to backend/app/strategies/plugins (where custom plugins live)."""
    import app.strategies.plugins as _pkg
    return os.path.dirname(_pkg.__file__)


def _write_plugin_file(strategy_id: str, code: str) -> str:
    """Write <strategy_id>.py into the plugins dir; return the path."""
    path = os.path.join(_plugins_dir(), f"{strategy_id}.py")
    with open(path, "w", encoding="utf-8") as f:
        f.write(code)
    return path


# ---------------------------------------------------------------------------
# Strategy Authoring — endpoints (Phase 2A)
# ---------------------------------------------------------------------------

@api.post("/strategies/author/compile")
async def author_compile(req: StrategyAuthorReq):
    """Validate + compile a spec to source WITHOUT installing. Returns errors (if any)
    so the wizard can show them; never raises on a bad spec."""
    from app.ai.spec_schema import StrategySpec
    from app.ai.compiler import validate_spec, compile_spec
    try:
        spec = StrategySpec(**req.spec)
    except Exception as e:
        return {"ok": False, "errors": [f"spec parse error: {e}"], "code": None}
    errors = validate_spec(spec)
    if errors:
        return {"ok": False, "errors": errors, "code": None}
    code = compile_spec(spec)
    return {"ok": True, "errors": [], "code": code, "strategy_id": spec.id}


@api.post("/strategies/author/install")
async def author_install(req: StrategyAuthorReq):
    """Validate + compile + write the plugin file + reload registry + store provenance.
    409 if the id already exists (unless overwrite=True). 400 on invalid spec."""
    import hashlib
    from datetime import datetime, timezone
    from app.ai.spec_schema import StrategySpec
    from app.ai.compiler import validate_spec, compile_spec
    try:
        spec = StrategySpec(**req.spec)
    except Exception as e:
        raise HTTPException(400, f"spec parse error: {e}")
    errors = validate_spec(spec)
    if errors:
        raise HTTPException(400, "; ".join(errors))
    reg = get_registry()
    if reg.get(spec.id) is not None and not req.overwrite:
        raise HTTPException(409, f"Strategy id '{spec.id}' already exists — choose another id or set overwrite")
    code = compile_spec(spec)
    _write_plugin_file(spec.id, code)
    reg.reload()
    if reg.get(spec.id) is None:
        raise HTTPException(500, f"Strategy '{spec.id}' failed to load after install — check the generated code")
    now = datetime.now(timezone.utc).isoformat()
    code_sha = hashlib.sha256(code.encode("utf-8")).hexdigest()[:16]
    await _db().generated_strategies.update_one(
        {"strategy_id": spec.id},
        {"$set": {"strategy_id": spec.id, "spec": req.spec, "code_sha": code_sha,
                  "source": "spec", "created_at": now}},
        upsert=True,
    )
    return {"strategy_id": spec.id, "installed": True, "code_sha": code_sha}


# ---------------------------------------------------------------------------
# AI spec-mapper — text -> StrategySpec + fidelity (Phase 2B)
# ---------------------------------------------------------------------------

@api.get("/strategies/author/providers")
async def author_providers():
    """Configured AI providers + the active default. Host-safe (env only)."""
    from app.ai import llm_client
    return llm_client.providers_status()


@api.post("/strategies/author/from-source")
async def author_from_source(req: StrategyFromSourceReq):
    """Ingest pasted text or a YouTube link, then map to a constrained StrategySpec +
    fidelity via the FAST tier of the selected provider (or the configured default)."""
    from app.ai import llm_client
    from app.ai.source_ingest import ingest_source
    from app.ai.strategy_author import map_source_to_spec
    if not llm_client.any_configured():
        raise HTTPException(503, "AI authoring is not configured — set GEMINI_API_KEY or ANTHROPIC_API_KEY in backend/.env")
    if not (req.source or "").strip():
        raise HTTPException(400, "source is empty")
    if req.provider:
        try:
            llm_client.resolve_provider(req.provider)   # 400 if named provider is unknown or lacks a key
        except RuntimeError as e:
            raise HTTPException(400, str(e))
    try:
        ing = ingest_source(req.source)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(502, f"Transcript fetch failed: {e}")
    try:
        out = map_source_to_spec(ing["text"], provider=req.provider)
    except RuntimeError as e:
        raise HTTPException(502, f"AI mapping failed: {e}")
    out["source_kind"] = ing["kind"]
    return out


@api.post("/strategies/author/converse")
async def author_converse(req: ConverseReq):
    """Collaborative gate: parse source -> per-rule feasibility -> BUILD/ASK/ADVISE/REJECT."""
    from app.ai import llm_client
    from app.ai.source_ingest import ingest_source
    from app.ai.authoring_agent import map_source_to_ruleset
    if not llm_client.any_configured():
        raise HTTPException(503, "AI authoring is not configured — set GEMINI_API_KEY or ANTHROPIC_API_KEY in backend/.env")
    if not (req.source or "").strip():
        raise HTTPException(400, "source is empty")
    if req.provider:
        try:
            llm_client.resolve_provider(req.provider)
        except RuntimeError as e:
            raise HTTPException(400, str(e))
    try:
        ing = ingest_source(req.source)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(502, f"Transcript fetch failed: {e}")
    try:
        return map_source_to_ruleset(ing["text"], provider=req.provider)
    except RuntimeError as e:
        raise HTTPException(503, detail=str(e))
    except Exception as e:
        raise HTTPException(502, detail=f"author/converse failed: {e}")


# ---------------------------------------------------------------------------
# Full-Python authoring — generate / validate / install (Task 7)
# ---------------------------------------------------------------------------

@api.post("/strategies/author/python-from-source")
async def author_python_from_source(req: PythonFromSourceReq):
    """Generate an arbitrary StrategyBase module via the POWERFUL tier. No install."""
    from app.ai import llm_client
    from app.ai.source_ingest import ingest_source
    from app.ai.py_author import author_python
    if not llm_client.any_configured():
        raise HTTPException(503, "AI authoring is not configured — set GEMINI_API_KEY or ANTHROPIC_API_KEY in backend/.env")
    if not (req.source or "").strip():
        raise HTTPException(400, "source is empty")
    if req.provider:
        try:
            llm_client.resolve_provider(req.provider)
        except RuntimeError as e:
            raise HTTPException(400, str(e))
    try:
        ing = ingest_source(req.source)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(502, f"Transcript fetch failed: {e}")
    try:
        out = author_python(ing["text"], provider=req.provider)
    except RuntimeError as e:
        raise HTTPException(502, f"AI generation failed: {e}")
    out["source_kind"] = ing["kind"]
    return out


@api.post("/strategies/author/python/validate")
async def author_python_validate(req: PythonValidateReq):
    """Static-check; if clean, smoke-test. Never raises on bad code."""
    from app.ai.py_sandbox import static_check, smoke_test
    violations = static_check(req.code or "")
    if violations:
        return {"ok": False, "violations": violations, "smoke": None}
    smoke = smoke_test(req.code)
    return {"ok": bool(smoke.get("ok")), "violations": [], "smoke": smoke}


@api.post("/strategies/author/python/install")
async def author_python_install(req: PythonInstallReq):
    """Server re-validates (static + smoke), then writes + reloads + records provenance."""
    import hashlib
    from datetime import datetime, timezone
    from app.ai.py_sandbox import static_check, smoke_test, extract_strategy_id
    violations = static_check(req.code or "")
    if violations:
        raise HTTPException(400, "static check failed: " + "; ".join(violations))
    cid = extract_strategy_id(req.code)
    if cid is None or cid != req.strategy_id:
        raise HTTPException(400, f"the module's class id ({cid!r}) must be a literal slug equal to strategy_id ({req.strategy_id!r})")
    reg = get_registry()
    origin = reg.origin_of(req.strategy_id)
    if origin == "builtin":
        raise HTTPException(403, "cannot overwrite a built-in strategy id")
    if origin is not None and not req.overwrite:
        raise HTTPException(409, f"Strategy id '{req.strategy_id}' already exists — choose another id or set overwrite")
    smoke = smoke_test(req.code)
    if not smoke.get("ok"):
        raise HTTPException(422, f"smoke-test failed: {smoke.get('error')}")
    _write_plugin_file(req.strategy_id, req.code)
    reg.reload()
    if reg.get(req.strategy_id) is None:
        try:
            os.remove(os.path.join(_plugins_dir(), f"{req.strategy_id}.py"))
        except OSError:
            pass
        raise HTTPException(500, f"Strategy '{req.strategy_id}' failed to load after install")
    now = datetime.now(timezone.utc).isoformat()
    code_sha = hashlib.sha256(req.code.encode("utf-8")).hexdigest()[:16]
    await _db().generated_strategies.update_one(
        {"strategy_id": req.strategy_id},
        {"$set": {"strategy_id": req.strategy_id, "source": "full_python", "code": req.code,
                  "code_sha": code_sha, "model": None, "created_at": now}},
        upsert=True,
    )
    return {"strategy_id": req.strategy_id, "installed": True, "code_sha": code_sha}
