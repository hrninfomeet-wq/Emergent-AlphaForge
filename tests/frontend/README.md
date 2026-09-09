# Node-driven test assets

Frontend logic and JSX structure that cannot be honestly checked from Python live
here. Every file is **driven from a pytest wrapper** so it runs in the one suite
the project actually gates on — an asset with no wrapper is dead weight, which is
exactly what happened to `liveStopState.test.mjs` between ed737d1 and 2026-09-09.

| Asset | Driven by | Checks |
|---|---|---|
| `liveStopState.test.mjs` | `tests/test_frontend_node_suites.py` | kill-switch two-stop reset logic (`frontend/src/lib/liveStopState.js`) |
| `consent_probe.cjs` | `tests/test_live_enable_consent_ui.py` | AST of `DeployToLivePanel.jsx` — is the live-consent checkbox rendered unconditionally? |

**Why AST, not grep.** "Is this element rendered unconditionally?" is structural:
`{cond && <label><input/></label>}` and a bare `<label><input/></label>` differ only
in the enclosing expression. A source grep cannot tell them apart, and twice during
development a grep-based assertion tripped on its own explanatory comment.

Non-`.py` files here are never collected by pytest (the repo has no pytest config,
so the default `test_*.py` / `*_test.py` patterns apply). Both wrappers skip rather
than fail when `node` is absent, so a container run still collects.
