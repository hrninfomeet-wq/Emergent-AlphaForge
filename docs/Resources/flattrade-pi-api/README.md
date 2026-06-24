# Flattrade *pi* API documentation — decoded & structured

Machine- and agent-friendly version of the Flattrade **pi** (PiConnect) REST + WebSocket
API, decoded from the source PDF
(`../pi _ API Documentation _ Flattrade pi - Free Stockmarket API _ Free Algo Trading API.pdf`,
a 93-page Chrome "Print to PDF" of <https://pi.flattrade.in/docs>).

**Base URL:** `https://piconnect.flattrade.in/PiConnectAPI`

## Why this exists
The raw PDF is hard to query: a fixed navigation sidebar + running header/footer are baked
onto every page, and the parameter tables are laid out spatially (so naive text extraction
flattens which "possible value" belongs to which field). This folder turns it into clean,
unambiguous, per-endpoint reference that an agent (or you) can read directly.

## Where to look

| Path | What it is |
|------|------------|
| [`INDEX.md`](INDEX.md) | **Start here.** All 58 sections with method, path, pages, and verification status. |
| [`catalog.json`](catalog.json) | Machine-readable spec for every verified endpoint (params, required flags, possible values, response fields, sample request/response). Query this from code. |
| [`endpoints/NN-slug.md`](endpoints/) | One clean human-readable doc per endpoint. |
| [`endpoints/NN-slug.json`](endpoints/) | Structured spec per endpoint (source for `catalog.json`). |
| [`reference/full-text.md`](reference/full-text.md) | The complete 93-page decoded text, boilerplate stripped (fallback / full-text search). |
| `_build/` | Regenerable intermediates (cleaned per-page text, page renders, screenshots, block geometry). **Git-ignored** — recreate with `_tooling/extract_pdf.py`. |
| [`_tooling/`](_tooling/) | The reusable PDF→Markdown extractor + assembler. See [`_tooling/README.md`](_tooling/README.md). |

## Verification tiers
Each endpoint in `INDEX.md` is marked:
- **✅ verified** — the structured spec was cross-checked field-by-field against the page
  *render image* by a second adversarial pass (catches flattened-table misalignment,
  hallucinated/missing fields, wrong required flags). These are trustworthy enough to code against.
- **⚠️ raw (pending)** — a clean text slice of the source pages, not yet table-verified. Complete
  but may need a careful read. Upgraded to ✅ when the extraction workflow finishes.

## How it was built
1. `python _tooling/extract_pdf.py <pdf> --out _build` — decode to cleaned text + page renders,
   stripping the nav sidebar / header / footer by page geometry.
2. A vision-verified extraction workflow: per endpoint, extract from the render + cleaned text,
   then adversarially verify every parameter against the authoritative page image, and write
   `endpoints/NN-slug.{md,json}`.
3. `python _tooling/assemble.py` — reconcile outputs, fill any gaps, build `INDEX.md` + `catalog.json`.

To regenerate everything from scratch, re-run steps 1 and 3 (step 2 is the agent workflow).

## Caveats
- The source PDF is a 2020-era sample doc with a few OCR/source typos (e.g. "Secondry",
  "reciving", curly-quote artifacts in sample JSON). These are preserved **verbatim** in the
  samples and flagged in each endpoint's `issues`/`notes`, so don't "fix" them blindly.
- Live-trading specifics worth re-checking against the current live API before relying on them:
  per the project's Flattrade notes, API v2 requires a **static IP**, supports **limit / SL-limit
  only (no market orders)**, and needs a **daily OAuth token**.
