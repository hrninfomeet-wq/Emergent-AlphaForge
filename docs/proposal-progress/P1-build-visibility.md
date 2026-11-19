# P1 — visible build identity checkpoint

> Final integration update: root reviewed this implementation, rebuilt both images, verified served source parity and checked the feature in Chrome. See `../PROPOSAL_IMPLEMENTATION_2026-10-24.md` and `rollout-verification.json`. Earlier local-only/build-pending notes below are retained as historical checkpoints.

Deliverable: visible frontend/backend captured-source identity for detecting mixed running builds; done when offline tests distinguish matching, mismatched and unknown metadata without runtime mutation.

Critical part (drives operational decisions): truthful identity evidence. A revision alone cannot identify this dirty checkout. Confirmed: Layout displays hard-coded `v1.0`; server exposes no build identity and Dockerfiles bake no metadata (source inspection this session).

Pieces and checks:
1. Deterministic, secret-free captured-source fingerprint — done when content changes alter the hash, generated/secret files do not, and both image recipes use the same input scope.
2. Read-only baked backend identity — done when missing/malformed metadata is unknown and route calls no runtime/database/broker dependency.
3. Visible comparison — done when matching/mismatched/unknown/unreachable states pass offline checks and no checkout-freshness claim is made.
4. Launcher/Compose wiring — done when static contracts show both builds use root allowlisted context and revision/dirty args; no build or service operation is run.

Scope: source changes and isolated tests only; no HTTP, DB, broker, service, build, auth, order or protected control action. Parent owns review and rollout. Preserve existing edits.

Plan approved by parent: root-context builds with explicit .dockerignore allowlist, shared Python fingerprint implementation; optional launcher Git metadata; direct Compose Git identity unknown. Fingerprints identify captured build source, not host-checkout freshness or runtime bind-mounted plugins.

Progress: implementation present, parent review pending. Reproduction checks first failed with missing module/route/Compose metadata; helper Jest first failed with missing helper. Focused tests then passed: 12 pytest and 12 Jest checks. No application runtime, network, DB, broker or build operation was used.

Changed files: `.dockerignore`, `backend/app/build_identity.py`, `backend/server.py`, backend/frontend Dockerfiles, `docker-compose.yml`, `start-app.bat`, `frontend/src/build-manifest.json`, `frontend/src/components/BuildIdentity.jsx`, `frontend/src/components/Layout.jsx`, `frontend/src/lib/buildIdentity.js`, their two Jest files and `tests/test_build_identity.py`.

Fingerprint input scope: exact relative path and byte content of backend app + Python scripts; frontend src/public/build plugins; explicit server, dependency lock/requirements, Dockerfiles, nginx/CRA/PostCSS/Tailwind/jsconfig/components config, Compose, launcher and root ignore files. Generated manifests are excluded. Secret-named files, env files, token files, cache/dependency/virtualenv trees and common DB/research archive formats are excluded. Full manifests include schema/scope, SHA-256, capture timestamp and validated optional Git revision/dirty flags. Direct Compose has unknown Git fields by default. Matching source hashes do not attest resolved dependencies, runtime environment, image identity, current host checkout or bind-mounted plugins.

Checks:
- `.venv/Scripts/python.exe -B -m pytest tests/test_build_identity.py -q -p no:cacheprovider --tb=short` with plugin autoload disabled: 12 passed in 0.24s. Includes independent SHA-256 framing re-derivation, byte/newline/uncommitted changes, exclusions, unknown/invalid metadata, read-only route AST and Docker source-capture contract.
- From frontend, `CI=true npm test -- --watchAll=false --runInBand --testPathPattern=buildIdentity.test.js|BuildIdentity.test.jsx --moduleNameMapper={...alias...}`: 12 passed, 2 suites, 2.144s. API entirely mocked; matching/mismatch/legacy/unavailable/unknown, polling replacement, stale result clearing and unmount cleanup checked.
- `git diff --check` for owned existing files: passed (Git reports the launcher's existing LF/CRLF conversion advisory).

Self-review caught and corrected: an allowlist excluding frontend optional health-check build plugins and backend Python helper scripts would remove existing packaged capabilities; both are now explicitly included in source capture. Invalid Git revision cannot imply a clean checkout. Full hash remains inspectable; the summary displays frontend/backend short hashes.

Core lesson for parent learning log: compare content fingerprints for dirty builds; a shared Git SHA cannot establish running-source parity. Confirmed approach: bake metadata from the same allowlisted snapshot in both Docker recipes, and treat missing/old metadata as unknown. Dead end to avoid: deriving the running build from the current working tree or equating matching captured source with current runtime/host freshness.

Risks: source changes remain unbuilt/unrolled; the running stack has no new identity until operator-approved rollout. Runtime bind mounts and dependencies are outside provenance scope. Docker recipes were statically checked only; parent must review and later perform approved builds.
