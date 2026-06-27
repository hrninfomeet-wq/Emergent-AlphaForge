# Strategy authoring — Part 2: full-Python escape hatch + mode toggle

**Date:** 2026-06-27
**Status:** Design (pending adversarial audit → plan → build)
**Branch:** `feat/strategy-full-python` (worktree `af-wt-strategy-library`, off `origin/main` which has Part 1 merged via PR #5).
**Builds on:** [Part 1 multi-provider authoring](2026-06-27-multi-provider-ai-authoring-design.md).

## 1. Context & motivation

Part 1 ships **Spec mode**: the FAST tier maps a description → a constrained `StrategySpec`, which a deterministic compiler turns into a *safe* `StrategyBase` plugin (whitelisted columns, `repr()`'d strings, no `eval`/`exec`). That covers strategies expressible in the condition/exit DSL.

Some strategies can't be: custom scoring, multi-indicator math, stateful logic, anything the DSL has no vocabulary for. **Part 2** adds **Full-Python mode**: the **POWERFUL tier** (Gemini `gemini-2.5-pro` live, Anthropic `claude-opus-4-8` when funded) writes a complete `StrategyBase` module. Because that output is *arbitrary Python* (the opposite of the constrained compiler), safety comes from a **3-stage validation pipeline** before install, plus mandatory human code review.

## 2. Scope & decisions

**In scope:** Full-Python authoring path (generate → validate → install), the Spec/Full-Python mode toggle in the wizard, the AST + subprocess validation sandbox.

**Decided (this brainstorm + Part 1's recorded decisions):**
- Tier escalation = **explicit mode toggle** (`Spec (fast)` | `Full Python (powerful)`), **Spec is the default selection**.
- **No env flag** — the toggle is always present (user chose "always available").
- Safety = **AST static check → subprocess smoke-test → install gate**; **the server always re-validates** on install (never trusts the client); **Install is disabled in the UI until a Validate pass**.
- The generated Python is shown in an **editable code panel** — the user can hand-edit before validate/install.
- **Residual risk accepted:** once installed, a custom plugin runs **in-process** during paper/backtest like any other plugin. The pipeline guards *install*, not *ongoing execution*. Acceptable for a single-user local tool with code review. Deploy-time sandboxing is explicitly **out of scope** (would rearchitect strategy execution for marginal gain).

**Out of scope:** deploy-time/runtime sandboxing of installed plugins; multi-file strategies; package installs from generated code.

## 3. The generated-code contract

The POWERFUL model must emit a single Python module defining **exactly one** `StrategyBase` subclass:

```python
from __future__ import annotations
import pandas as pd
from app.strategies.base import StrategyBase, Signal

class MyStrategy(StrategyBase):
    id = "my_strategy"            # lowercase slug ^[a-z][a-z0-9_]*$
    name = "My Strategy"
    version = "1.0.0"
    description = "..."
    is_builtin = False            # REQUIRED False for custom plugins
    supported_instruments = ["NIFTY", "BANKNIFTY", "SENSEX"]
    supported_modes = ["SCALP", "INTRADAY"]
    supported_timeframes = ["1m", "3m", "5m"]
    parameter_schema = { ... }    # {name: {type, min, max, default}}

    def evaluate(self, row, prev, params, ctx) -> Signal:
        # pure function of row/prev/params/ctx; returns a Signal
        ...
```

`Signal` fields (from `app/strategies/base.py`): `direction` ("CE"|"PE"|"NONE"), `score`, `reasons`, `blockers`, `target_pct`, `stop_pct`, `time_stop_minutes`, `spot_target_pts`, `spot_stop_pts`, `scenario`, `spot_target_level`, `exit_mode`. The model is told to reference only **grounding-catalog columns** (the same `allowed_columns()` Part 1 exposes) on `row`/`prev`, and to set at least one exit for SCALP/INTRADAY.

## 4. Architecture

### 4.1 `app/ai/py_author.py` (new)
`author_python(source_text: str, provider: str | None = None) -> dict` → `{code, fidelity, notes, suggested_id}`.
- Uses `llm_client.complete_structured(tier=POWERFUL, provider=provider, output_model=AuthoredPython, ...)`.
- `AuthoredPython` (pydantic): `code: str`, `fidelity: Fidelity` (reuse Part 1's captured/couldnt_map/ambiguous), `notes: str`, `suggested_id: str`.
- System prompt (grounded): the `StrategyBase`/`Signal` contract above, the `allowed_columns()` list, a worked example (the real `confluence_scalper` body), and HARD rules — single module, single subclass, imports limited to `pandas`/`numpy`/`math`/`typing`/`app.strategies.base`, **no** I/O / network / `os` / `sys` / `subprocess` / `eval` / `exec`, pure function of the four args.
- Lazy-imports grounding/llm_client (host-safe).

### 4.2 `app/ai/py_sandbox.py` (new) — the safety pipeline

**`static_check(code: str) -> list[str]`** (pure `ast`, no execution; fully host-testable):
1. `ast.parse(code)`; on `SyntaxError` → `["syntax error: ..."]`.
2. Walk the tree. Violations collected:
   - **Imports**: any `Import`/`ImportFrom` whose top-level module is not in `ALLOWED_IMPORTS = {"pandas", "numpy", "math", "typing", "app", "__future__", "dataclasses"}` (note: `app.strategies.base` matches top-level `app`; we further require the `from app...` target to be `app.strategies.base`). Relative imports (`level>0`) rejected.
   - **Forbidden call names**: `eval`, `exec`, `compile`, `__import__`, `open`, `input`, `globals`, `locals`, `vars`, `getattr`, `setattr`, `delattr`, `breakpoint`, `memoryview` (called as bare `Name`).
   - **Dunder-escape attribute access**: any `Attribute` whose `attr` is in `FORBIDDEN_DUNDER = {"__globals__","__builtins__","__subclasses__","__bases__","__mro__","__class__","__dict__","__code__","__closure__","__func__","__self__","__module__","__loader__","__spec__","__import__","__getattribute__","__reduce__","__reduce_ex__"}`.
   - **Forbidden attribute names anywhere** (e.g. `mro`, `register` on a metaclass escape) — pragmatic denylist; see §7 risk.
   - **Structure**: exactly one top-level `ClassDef` subclassing `StrategyBase` (by base name); it must define an `evaluate` method and assign an `id`.
3. Return the list (empty == clean).

**`smoke_test(code: str, *, timeout: int = 10) -> dict`** → `{ok: bool, error: str|None, signal_repr: str|None}` (patchable seam; mocked in host tests):
- Write `code` to a temp `.py` file in a temp dir.
- Spawn a **fresh subprocess**: `python <driver.py> <tempfile>` with `cwd="/app"`, `env` = a minimal allowlist (`PATH`, `PYTHONPATH="/app"`, `LANG`), `start_new_session=True` (own process group), and on Linux a `preexec_fn` applying `resource.setrlimit(RLIMIT_CPU, (timeout, timeout+1))` and `RLIMIT_AS` (e.g. 1 GiB). Hard wall-clock `timeout` on `communicate`; on `TimeoutExpired`, `os.killpg` the group.
- The **driver** (shipped as `app/ai/_py_smoke_driver.py`): loads the candidate module via `importlib.util.spec_from_file_location` (NOT installed into the plugins package), finds the single `StrategyBase` subclass, instantiates it, builds a ~60-row synthetic OHLCV+indicator DataFrame covering `allowed_columns()`, calls `evaluate(row, prev, params, ctx)` on a handful of rows with `params=default_params()` and `ctx={}`, and asserts each return is a `Signal` with a valid `direction`. Prints a JSON result line to stdout.
- The parent parses the driver's JSON; non-zero exit / timeout / bad JSON → `{ok: False, error: <captured stderr/tail>}`.
- `resource` import is guarded (`try: import resource` — Linux only; on Windows host the rlimits are skipped, timeout still applies). The real run happens in the Linux backend container.

### 4.3 `app/routers/strategies_admin.py` — new endpoints
- `POST /strategies/author/python-from-source` → `{source, provider?}` → ingest (text/YouTube via Part 1's `ingest_source`) → `author_python(text, provider)` → `{code, fidelity, notes, suggested_id, source_kind}`. **No install.** 503 if no provider configured; 400/502 mirror Part 1's `/from-source` error mapping.
- `POST /strategies/author/python/validate` → `{code}` → `static_check` + (if clean) `smoke_test` → `{ok, violations, smoke}`. Never raises on bad code.
- `POST /strategies/author/python/install` → `{code, strategy_id, overwrite?}` → **re-run `static_check` + `smoke_test` server-side** (400 with violations / 422 with smoke error if they fail) → extract the class `id` from the code and confirm it matches `strategy_id` → write `<id>.py` to the plugins dir → **reload (with `importlib.reload` for an existing module — see §4.4)** → confirm it loaded → store provenance (`generated_strategies`: `source:"full_python"`, `code_sha`, `model`, `created_at`). 409 on id collision unless `overwrite`.

### 4.4 The `importlib.reload` gotcha (must fix)
`StrategyRegistry.reload()` calls `auto_discover()` → `importlib.import_module(full)`, which is a **no-op for an already-imported module**. So overwriting an existing custom plugin's `.py` and calling `reg.reload()` will NOT pick up the new code. Part 1's Spec-mode install has the same latent bug for overwrite. **Fix in `base.py`:** in `auto_discover`, if a plugin module is already in `sys.modules`, `importlib.reload(mod)` instead of a bare import — scoped to the `app.strategies.plugins` package only (never reload builtins). This makes both Spec-mode overwrite and Full-Python overwrite correct.

## 5. Frontend (`AuthoringWizard.jsx`)
- A **mode toggle** at the top: `Spec (fast)` | `Full Python (powerful)`, default `Spec`. State `mode`.
- **Spec mode**: the current wizard (deterministic form + ✨ AI Spec box) — unchanged.
- **Full-Python mode**: the ✨ box stays (text/transcript/URL + provider dropdown). **Generate** → `api.authorPythonFromSource(source, provider)` → fills an **editable `<textarea>` code panel** (monospace) + renders `fidelity`/`notes`. A **Validate** button → `api.validatePython(code)` → renders violations (red) or "✓ passed" (green) + the smoke result. **Install** (`api.installPython(code, id, overwrite)`) is **disabled until the last Validate returned `ok:true`**, and any edit to the code clears that pass. The structured spec-form fields are hidden in Full-Python mode.
- `lib/api.js`: add `authorPythonFromSource`, `validatePython`, `installPython` (the author one uses `LONG_TIMEOUT_MS`).

## 6. Host-safety
- `py_author` lazy-imports llm_client/grounding; `py_sandbox.static_check` is pure stdlib `ast`; `smoke_test` is a patchable seam (host tests mock it — they never spawn the real app subprocess). The router stays host-importable (lazy seams). The host venv has no motor/SDKs; nothing new changes that.

## 7. Risks & mitigations
- **AST-evasion (the core risk):** a denylist is not a perfect jail — a sufficiently clever construct (e.g. obscure dunder traversal, `type(...).__mro__` chains, f-string `eval`-equivalents) could slip past `static_check`. Mitigations: (a) the allowlisted-imports + forbidden-call + dunder-denylist together block the common escapes; (b) the subprocess smoke-test runs the code with rlimits+timeout in a throwaway process, so even an evasion is contained to a short-lived child; (c) the editable panel + install-after-validate forces human review; (d) **the threat model is the AI mis-generating from the user's own description, not a remote attacker** — so accidental dangerous code (a stray `import os`) is the realistic case, which the denylist catches. **During build, the `static_check` gets adversarial multi-agent review** (try-to-evade payloads) and the denylist is tightened to whatever they find.
- **In-process post-install execution:** accepted (§2).
- **Subprocess can't import the app on Windows host:** the real run is in the Linux container; host tests mock the seam, so this never bites CI.
- **POWERFUL tier cost:** Gemini Pro is pricier than Flash (prepaid credits); the user opts in per generation by choosing Full-Python mode.

## 8. Testing
- **Host (pure, extensive):** `static_check` battery — every allowed import passes; `os`/`sys`/`subprocess`/`socket` imports rejected; `eval`/`exec`/`open`/`getattr` calls rejected; each forbidden dunder rejected; relative import rejected; zero/two `StrategyBase` subclasses rejected; missing `evaluate`/`id` rejected; a valid confluence-style module passes. A **fuzz/evasion set** seeded by the adversarial review.
- **Host (seams):** `smoke_test` mocked; `py_author` mocked at `complete_structured`; endpoints (`python-from-source` forwards provider + 503/400/502; `validate` returns violations vs ok; `install` re-validates + 400 on bad code + 409 on collision + writes provenance). `base.py` reload-of-edited-plugin test (write a plugin, reload, edit it, reload → new behavior).
- **Live (Gemini Pro):** generate a real full-Python strategy from a description that Spec mode can't express → Validate (static+smoke pass) → Install → confirm it appears in the Strategy Library, loads, and is deployable; Chrome-verify the toggle + code panel + validate/install gating.
- **Adversarial Workflow during build:** a try-to-break-the-sandbox phase (evade `static_check`; make `smoke_test` hang or escape) → confirmed evasions become new denylist rules + tests before merge.

## 9. Rollout
Build on `feat/strategy-full-python` (off merged main). Subagent-driven per-task with spec+quality review; an adversarial sandbox-evasion Workflow before the final review. Live-validate on Gemini Pro. Open a PR to main.
