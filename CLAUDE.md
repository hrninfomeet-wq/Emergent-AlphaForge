# Project notes for Claude / AI agents

> Entry point for state & architecture is **`docs/HANDOFF.md`** + `CHANGELOG.md`. The notes
> below are always-loaded capabilities/assets that every session should know about.

## PDF reading capability (set up 2026-06-25)
This project has a reusable **PDF → Markdown** capability so any PDF can be read accurately.
Tools installed in the default system Python (3.12): `pymupdf4llm`, `pymupdf`, `pdfplumber`, `pypdf`.

- Decode any PDF:
  `python docs/Resources/flattrade-pi-api/_tooling/extract_pdf.py "<file.pdf>" --out <dir>`
  (add `--auto-boilerplate` for an unknown layout), then read `<dir>/full-text.md`; open
  `<dir>/pages/page-NN.png` when a table/figure is ambiguous.
- Recipe & details: `docs/Resources/flattrade-pi-api/_tooling/README.md`.

## Flattrade *pi* (PiConnect) API reference — decoded (2026-06-25)
The Flattrade live-trading API docs (93-page PDF) are decoded into **`docs/Resources/flattrade-pi-api/`**.
Use this instead of re-reading the PDF. Supports the Flattrade live-execution work (Noren OMS).

- **`INDEX.md`** — all 58 endpoints (REST + WebSocket): method, path, pages, status.
- **`catalog.json`** — machine-readable spec for every endpoint (params, required flags, possible
  values, response fields, sample request/response). **Query this from code.**
- `endpoints/NN-slug.{md,json}` — one clean doc + structured spec per endpoint.
- `reference/full-text.md` — complete decoded text.
- Base URL `https://piconnect.flattrade.in/PiConnectAPI`; WS `wss://piconnect.flattrade.in/PiConnectWSAPI/`.
- All 58 endpoints were **vision-verified** against page renders. Source-PDF typos (e.g. `Secondry`,
  `reciving`, curly-quote JSON artifacts) are preserved **verbatim** — do NOT auto-"fix" them.
  Re-confirm live-API specifics (static IP, limit/SL-limit only, daily OAuth) before relying.
