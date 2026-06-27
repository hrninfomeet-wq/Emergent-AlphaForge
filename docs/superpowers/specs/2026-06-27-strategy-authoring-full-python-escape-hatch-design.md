# Strategy authoring — Part 2: full-Python escape hatch + mode toggle

**Date:** 2026-06-27
**Status:** Design v2 (hardened by a 28-finding multi-adversarial audit) → ready for plan
**Branch:** `feat/strategy-full-python` (worktree `af-wt-strategy-library`, off `origin/main` which has Part 1 merged via PR #5).
**Builds on:** [Part 1 multi-provider authoring](2026-06-27-multi-provider-ai-authoring-design.md).

## 1. Context & motivation

Part 1 ships **Spec mode**: the FAST tier maps a description → a constrained `StrategySpec`, which a deterministic compiler turns into a *safe* `StrategyBase` plugin (whitelisted columns, `repr()`'d strings, no `eval`/`exec`). That covers strategies expressible in the condition/exit DSL.

Some strategies can't be: custom scoring, multi-indicator math, stateful logic, anything the DSL has no vocabulary for. **Part 2** adds **Full-Python mode**: the **POWERFUL tier** (Gemini `gemini-2.5-pro` live, Anthropic `claude-opus-4-8` when funded) writes a complete `StrategyBase` module. Because that output is *arbitrary Python*, safety comes from a **3-stage validation pipeline** (AST allowlist → subprocess smoke-test → install gate) plus mandatory human code review.

**Threat model (drives every safety decision):** the realistic risk is the AI *accidentally mis-generating* dangerous Python from the user's own description on a **single-user LOCAL tool** — not a remote attacker. So the goal is to catch accidents (a stray `import os`, an import-time side effect) with high confidence and contain anything that slips through, not to build a perfect jail.

## 2. Scope & decisions

**In scope:** Full-Python authoring path (generate → validate → install), the Spec/Full-Python mode toggle in the wizard, the AST + subprocess validation sandbox.

**Decided:**
- Tier escalation = **explicit mode toggle** (`Spec (fast)` | `Full Python (powerful)`), **Spec is the default selection**.
- **No env flag** — the toggle is always present (user chose "always available").
- Safety = **AST static allowlist → subprocess smoke-test → install gate**; the **server always re-validates** on install; **Install is disabled in the UI until a Validate pass**.
- The generated Python is shown in an **editable code panel** (hand-edit before validate/install).
- **Residual risk accepted:** once installed, a custom plugin's `evaluate()` runs **in-process** during paper/backtest like any other plugin. The pipeline guards *install*, not *ongoing evaluate() execution*. Acceptable for a single-user local tool with code review. **The audit established that the more dangerous risk — arbitrary code at IMPORT time, executed in-process by the install reload — is eliminated by the §4.2 module-top-level allowlist (imports run, but no module/class-level statements execute).** Deploy-time sandboxing of `evaluate()` is out of scope.

**Out of scope:** deploy-time/runtime sandboxing of installed plugins; multi-file strategies; package installs; the broader proposals from the audit that exceed this tool's accepted posture (seccomp/unshare subprocess jails, statically forbidding all non-user attribute access).

## 3. The generated-code contract

A single Python module defining **exactly one** `StrategyBase` subclass (a "strategy class" = subclass of `StrategyBase`, not `StrategyBase` itself, defined in this module, with a truthy literal `id` — matches `auto_discover`'s real filter, `base.py:161,164`):

```python
from __future__ import annotations
import pandas as pd            # numpy/math/typing/dataclasses also allowed
from app.strategies.base import StrategyBase, Signal

class MyStrategy(StrategyBase):
    id = "my_strategy"            # MUST be a string literal slug ^[a-z][a-z0-9_]*$
    name = "My Strategy"
    version = "1.0.0"
    description = "..."
    is_builtin = False            # required present + False (cosmetic for origin, which is path-derived)
    supported_instruments = ["NIFTY", "BANKNIFTY", "SENSEX"]
    supported_modes = ["SCALP", "INTRADAY"]
    supported_timeframes = ["1m", "3m", "5m"]
    parameter_schema = { ... }    # {name: {type, min, max, default}} literal

    def evaluate(self, row, prev, params, ctx) -> Signal:
        ...                       # pure function; returns a Signal
```

`Signal` fields: `direction` ("CE"|"PE"|"NONE"), `score`, `reasons`, `blockers`, `target_pct`, `stop_pct`, `time_stop_minutes`, `spot_target_pts`, `spot_stop_pts`, `scenario`, `spot_target_level`, `exit_mode`. The model references only **grounding-catalog columns** (`allowed_columns()`) on `row`/`prev`, sets at least one exit for SCALP/INTRADAY, and is told **not to access pandas/numpy submodules** (`pandas.io`, `numpy.f2py`, `numpy.ctypeslib`, …) — only the documented DataFrame/Series/ufunc surface.

## 4. Architecture

### 4.1 `app/ai/py_author.py` (new)
`author_python(source_text, provider=None) -> {code, fidelity, notes, suggested_id}` via `complete_structured(tier=POWERFUL, provider=provider, output_model=AuthoredPython, ...)`. `AuthoredPython` (pydantic): `code:str`, `fidelity:Fidelity` (reuse Part 1's), `notes:str`, `suggested_id:str`. System prompt = the §3 contract + `allowed_columns()` + the real `confluence_scalper` body as a worked example + the HARD rules (single module/class; imports limited to `pandas`/`numpy`/`math`/`typing`/`dataclasses`/`app.strategies.base`; no I/O/network/os/sys/subprocess/eval/exec; **no module-level statements except the class**; **no submodule walking**; pure function of the four args). Lazy imports → host-safe.

### 4.2 `app/ai/py_sandbox.py` (new) — the safety pipeline

**`static_check(code: str) -> list[str]`** — pure `ast`, no execution, **structural ALLOWLIST** (host-testable):

1. `ast.parse(code)`; `SyntaxError` → `["syntax error: …"]`.
2. **Module top-level allowlist** — `module.body` may contain ONLY: an optional leading docstring (`Expr`→`Constant` str); `ImportFrom __future__`; `Import`/`ImportFrom` of allowlisted modules; and **exactly one** `ClassDef` subclassing `StrategyBase`. **Reject any other top-level node** — `Assign`/`AnnAssign`/`AugAssign`, bare `Expr`/`Call`, `If`/`With`/`For`/`While`/`Try`/`Delete`/`FunctionDef`/`Async*`. (This is the §2 import-time-execution guard: imports run, nothing else does.)
3. **Imports**: top-level module name ∈ `ALLOWED_IMPORTS = {"pandas","numpy","math","typing","dataclasses","app","__future__"}`; a `from app…` import must target exactly `app.strategies.base` and import only from `{StrategyBase, Signal}`; reject relative imports (`level>0`). No `Import`/`ImportFrom` anywhere except module top-level (reject imports inside methods).
4. **The ClassDef**: bases == exactly `[Name "StrategyBase"]` (reject multiple inheritance, attribute/subscript bases); `decorator_list` empty (kills decorator-as-Call import-time execution); `keywords` empty (kills `metaclass=`); body contains ONLY class-var `Assign`/`AnnAssign` to literals/lists/dicts and `FunctionDef` methods (+ optional docstring) — **reject class-level `Call`/`If`/`For`/`With`/comprehension-sink** (would execute at class-def time); reject `__init_subclass__`/`__class_getitem__` defs.
5. **Whole-tree denylist** (`ast.walk`, applies inside methods too):
   - any `Attribute` whose `.attr` matches `^__.*__$` → reject (**one regex subsumes every dunder**; no builtin strategy uses dunder attr access).
   - any `Attribute` whose `.attr` ∈ `FORBIDDEN_MODULE_ATTRS = {"os","sys","subprocess","socket","shutil","importlib","ctypes","builtins","pathlib","pickle","marshal","runpy","pty","signal","io","f2py","ctypeslib","testing","compat"}` → reject (closes `pd.io.common.os.system` / `numpy.f2py.os.fork`).
   - any `Call` to a bare `Name` ∈ `FORBIDDEN_CALL_NAMES = {"eval","exec","compile","__import__","open","input","globals","locals","vars","getattr","setattr","delattr","type","breakpoint","memoryview"}` → reject.
   - any `Global`/`Nonlocal` → reject.
6. Return violations (empty == clean).

**`extract_strategy_id(code: str) -> str | None`** (pure AST, NEVER imports/execs): parse, find the single `StrategyBase` ClassDef, read its class-body `id = <ast.Constant str>` matching the slug; return it, else `None`. The install path uses this for the filename + the `strategy_id` match — **never** instantiates to read `.id` (that would defeat subprocess-only containment).

**`smoke_test(code, *, timeout=10) -> {ok, error, signal_repr}`** (patchable seam; mocked in host tests):
- Write `code` to a temp file in a fresh temp dir; spawn `python <driver> <codefile> <resultfile>` with `cwd="/app"`, minimal env (`PATH`, `PYTHONPATH="/app"`, `LANG`), `start_new_session=True`; on Linux a `preexec_fn` sets `RLIMIT_CPU=(timeout,timeout+1)` + `RLIMIT_AS≈1GiB` (guarded `try: import resource`). Hard wall-clock `timeout` on `communicate`; on `TimeoutExpired` → `os.killpg` the group on POSIX, else `proc.kill()` (**Windows host: timeout still hard-kills the child**).
- **Result channel is a FILE, not stdout** (`argv[2]`): the driver `json.dump`s its result dict to that file; the parent reads + parses THAT file, using stdout/stderr only for the diagnostic error tail — **print-proof regardless of what the candidate emits**.
- Outcome mapping is a separate pure helper **`_interpret_smoke_result(returncode, stdout, stderr, timed_out) -> {ok, error, signal_repr}`** (so it's host-testable without spawning): timed_out → `ok:False` "timeout"; returncode≠0 → `ok:False` stderr tail; missing/unparseable result file → `ok:False` bad-json; result `ok:False` → propagate its error; else `ok:True` + `signal_repr`.

**The driver** (`app/ai/_py_smoke_driver.py`): loads the candidate via `importlib.util.spec_from_file_location` under a **unique module name** (e.g. `_smoke_<uuid>` — avoids `sys.modules` collisions across repeated validate calls and the installed plugin); finds the **one** strategy class (the §3 canonical definition; 0/≠1 → result `ok:False`); instantiates; builds a **realistic ~2-session synthetic frame** (1m bars 09:15–15:30 IST across two dates, with `ts`/`datetime`/`ist_time`/`session_date` *plus* every `allowed_columns()` column, NaN-warmed for the first bars) and a **production-shaped `ctx`** (mirroring `backtest.py`); calls `evaluate(row, prev, params=default_params(), ctx)` on several rows (`prev` = the prior row; `params` includes bool/None defaults). **PASS = every call returns a `Signal` with a valid `direction` without raising**; an exception → `ok:False` + the traceback; a non-`Signal` return → `ok:False`. (A `direction:"NONE"` result is a PASS — many correct strategies return NONE on synthetic data.)

### 4.3 `app/routers/strategies_admin.py` — new endpoints
- `POST /strategies/author/python-from-source` → `{source, provider?}` → `ingest_source` → `author_python` → `{code, fidelity, notes, suggested_id, source_kind}`. No install. Mirrors Part 1's 503/400/502 mapping.
- `POST /strategies/author/python/validate` → `{code}` → `static_check`; if clean, `smoke_test` → `{ok, violations, smoke}`. **Never raises on bad code.** (Static-unclean → smoke skipped, `ok:false`, violations populated.)
- `POST /strategies/author/python/install` → `{code, strategy_id, overwrite?}`. **Strict order, server-authoritative:** (1) `static_check` → 400 with violations if non-empty; (2) `extract_strategy_id(code)` → 400 if None or ≠ `strategy_id`; (3) collision: if `reg.origin_of(strategy_id)` is not None (covers failed-import ghosts, mirrors delete) and not `overwrite` → 409; a builtin id → 403; (4) `smoke_test` → 422 with the smoke error if `ok:false`; (5) `_write_plugin_file(strategy_id, code)`; (6) `reg.reload()` (clean re-import per §4.4); (7) confirm `reg.get(strategy_id)` loaded, else 500 + best-effort file cleanup; (8) provenance: upsert `generated_strategies` `{strategy_id, source:"full_python", code, code_sha (sha256[:16], same convention as strategy_source_hash), model, created_at}`. The double static_check cost (validate + install) is intentional — install never trusts the client's prior validate.

### 4.4 Registry reload fix (`base.py`) — required
`StrategyRegistry.reload()`→`auto_discover()`→`importlib.import_module` is a **no-op for an already-imported module**, so overwriting a custom plugin won't pick up new code (Part 1's Spec-mode overwrite shares this latent bug). **Fix:** in `auto_discover`, for the **`app.strategies.plugins` package only**, before importing each plugin module do `sys.modules.pop(full, None)` then `importlib.import_module(full)` — a clean **fresh import** (never `importlib.reload`, which leaves a half-updated module on a broken re-import and can leave a renamed old class registered). A failed fresh import is auto-removed by CPython, leaving a clean slate. **Never pop/refresh `app.strategies.builtin`.** This makes both Spec-mode and Full-Python overwrite correct, and a class-rename can't leave two classes registered under one id.

### 4.5 Interaction with deployment source-drift (known, by-design)
Re-installing/editing a deployed custom plugin changes its `.py` bytes, so `hash_strategy_source()` changes and the evaluator **auto-pauses every deployment of that `strategy_id` with `strategy_source_drift`** on the next tick (the existing slice-8 guard). This is expected for the edit-and-reinstall loop, not a bug. Resolution: the operator re-pins via the **existing** `POST /deployments/{id}/repin-source`. Full-Python plugins otherwise participate in sizing-replay and `session_precompute` exactly like any plugin (they may omit `session_precompute`; the base returns `{}`). The spec stores the full `code` in provenance so a future "reinstall from provenance" is possible.

## 5. Frontend (`AuthoringWizard.jsx`)
- A **mode toggle** at the top: `Spec (fast)` | `Full Python (powerful)`, default `Spec` (state `mode`).
- **Spec mode**: the current wizard — unchanged.
- **Full-Python mode**: the ✨ box stays (text/transcript/URL + provider dropdown). **Generate** → `api.authorPythonFromSource(source, provider)` → fills an **editable monospace code panel** + renders `fidelity`/`notes`. A **Validate** button → `api.validatePython(code)` → renders violations (red) or "✓ passed" (green) + the smoke result. **Install** (`api.installPython(code, id, overwrite)`) is **disabled until the last Validate returned `ok:true`**; a per-edit **validation token** (incremented on every code edit; the Validate response is only honored if its token matches the current one) closes the async-race so a stale "pass" can't re-enable Install after an edit. The structured spec-form fields hide in Full-Python mode.
- `lib/api.js`: add `authorPythonFromSource` (LONG_TIMEOUT_MS), `validatePython`, `installPython`.

## 6. Host-safety
`py_author` lazy-imports llm_client/grounding; `static_check`/`extract_strategy_id`/`_interpret_smoke_result` are pure stdlib (host-testable directly); `smoke_test` is a patchable seam (host tests mock it — never spawn the real app subprocess). The router stays host-importable. Nothing new needs motor/SDKs.

## 7. Risks & mitigations
- **AST evasion:** the structural **allowlist** (module top-level = imports + the one class; no decorators/metaclass; class body = assigns + methods) means **no AI-authored code runs at import/class-def time** — only `evaluate()` runs, and only at deploy time. Inside methods: the `^__.*__$` dunder regex + `FORBIDDEN_MODULE_ATTRS` + forbidden-call-names + no-imports close the known escapes (incl. the verified `pandas.io.common.os.system` / `numpy.f2py.os.fork` re-export walk). A denylist on method bodies is still not a perfect jail, but (a) the threat model is accidental mis-generation, not a crafted attacker; (b) the subprocess smoke-test contains anything to a short-lived rlimited child; (c) the editable panel + install-after-validate forces human review. **During build, `static_check` gets an adversarial multi-agent evasion pass** seeded with the audit's named payloads (`pd.io.common.os.system(...)`, `numpy.f2py.os.fork()`, `type(...).__mro__` chains, decorator/metaclass/default-arg hooks); confirmed evasions become denylist rules + regression tests before merge.
- **In-process `evaluate()` post-install:** accepted (§2).
- **POWERFUL tier cost:** Gemini Pro is pricier than Flash; opt-in per generation.

## 8. Testing
**Host (pure, no seams):**
- `static_check` battery: every allowed import passes; `os`/`sys`/`subprocess`/`socket` import rejected; relative import rejected; method-level import rejected; each evasion seed rejected (`pd.io.common.os.system`, `numpy.f2py.os.fork`, `type(...)`, any `__x__` attr, `getattr`, decorator on class, `metaclass=`, multiple bases, module-level `Assign`/`Call`/`If`, `__init_subclass__`); 0/2 strategy classes rejected; missing `evaluate`/`id` / non-literal `id` rejected; a valid confluence-style module passes.
- `extract_strategy_id`: literal id returned; non-constant id / missing → None.
- `_interpret_smoke_result` table-driven matrix: timeout→fail; nonzero-exit→fail(stderr); no/!bad result file→bad-json; result `ok:false`→propagate; valid `Signal`→ok.

**Host (seams):** `smoke_test` mocked; `py_author` mocked at `complete_structured` (provider forwarded); endpoints — `python-from-source` (503/400/502 + provider), `validate` (clean vs violations vs static-clean-smoke-skipped; never-raises), `install` (re-validate→400/422, id-mismatch→400, collision via origin_of→409, builtin→403, provenance written, file written/reloaded/confirmed).

**Host (real, NOT seams — hermetic, no provider key):** the **deterministic install loop** — monkeypatch `_plugins_dir` to a tmp dir, take a hardcoded valid full-Python module string (trimmed confluence body, `id="custom_smoke_test"`), run REAL `static_check` (empty) → REAL `_write_plugin_file` → REAL `reg.reload()` → assert `reg.get(...)` is not None, `meta()["origin"]=="custom"`, appears in `list_all()`. Then **overwrite** the file with edited `evaluate` behavior → REAL `reg.reload()` → assert the NEW behavior is live (proves the §4.4 fresh-import fix); a broken edit → reload leaves a clean error entry, not a half-updated module; delete → gone. This portion is **not Windows-skipped** (only the real subprocess smoke is Linux-gated).

**Live (Gemini Pro):** generate a full-Python strategy from a description Spec mode can't express → Validate (static+smoke pass) → Install → confirm it appears in the Library, loads, deploys; Chrome-verify the toggle + code panel + validate/install gating + the token-race fix.

**Adversarial Workflow during build:** the `static_check` evasion pass (§7) before the final review.

## 9. Rollout
Build on `feat/strategy-full-python`. Subagent-driven per-task with spec+quality review; the adversarial sandbox-evasion Workflow before the final holistic review. Live-validate on Gemini Pro. PR to main.
