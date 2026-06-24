# PDF → agent-readable Markdown tooling

This is the reusable PDF decoding capability for the project. It lets Claude (or any
agent) pull information out of **any** PDF reliably, not just the Flattrade doc.

## Installed libraries

Installed into the default system Python (`python` on PATH, 3.12):

| Library        | Role |
|----------------|------|
| `pymupdf`      | Fast, layout-aware PDF engine (text blocks + bbox, page rendering, image extraction). |
| `pymupdf4llm`  | PyMuPDF's PDF→Markdown converter tuned for LLM/RAG consumption. |
| `pdfplumber`   | Table extraction fallback for ruled tables. |
| `pypdf`        | Lightweight metadata / merge / split / page ops. |

Install (already done, here for reproducibility):

```bash
python -m pip install --upgrade pymupdf4llm pdfplumber pypdf
```

### Why this stack
For the goal "an agent can get any info easily from any PDF," the right tool is a
lightweight, pip-only, **no-model-download** converter that emits clean Markdown.
PyMuPDF4LLM fits exactly. Heavier ML converters (Marker, Docling, MinerU) give marginally
better tables but pull GBs of models and run slowly — overkill for a general capability.
`pdfplumber` covers the table gap when needed.

## The extractor: `extract_pdf.py`

Decodes a PDF into clean Markdown + page renders, with page **boilerplate removed**
(running headers/footers and fixed navigation sidebars — the #1 thing that pollutes naive
PDF text extraction).

```bash
# Full decode: cleaned per-page Markdown + full-text.md + page PNG renders + embedded images
python extract_pdf.py "<input.pdf>" --out <out_dir> --dpi 144

# Text only (skip the page renders)
python extract_pdf.py "<input.pdf>" --out <out_dir> --no-render

# Generic PDF (auto-detect repeating boilerplate instead of hand-measured crop bands)
python extract_pdf.py "<input.pdf>" --out <out_dir> --auto-boilerplate
```

Outputs under `<out_dir>/`:
- `page-text/page-NN.md` — cleaned text per page (sidebar/header/footer stripped, 2-column reading order)
- `full-text.md` — all pages concatenated
- `pages/page-NN.png` — one render per page (for visual/vision verification)
- `assets/` — embedded raster images (e.g. UI screenshots)
- `geometry.json` — per-page text blocks with bounding boxes (for further tooling)

### Tuning for a new fixed-layout PDF
The crop constants near the top of `extract_pdf.py` (`SIDEBAR_MAX_CENTER_X`,
`HEADER_MAX_Y`, `FOOTER_MIN_Y`, `COLUMN_SPLIT_X`) are measured for the Flattrade print.
For a different PDF, either pass `--auto-boilerplate` (generic repeat detection) or measure
the new layout once:

```python
import pymupdf
p = pymupdf.open("doc.pdf")[0]
print(p.rect)
for b in p.get_text("blocks"): print([round(v,1) for v in b[:4]], b[4][:50])
```

## Recommended recipe for "read any PDF"
1. `extract_pdf.py … --out _build` → get `full-text.md` + `pages/*.png`.
2. For quick Q&A, read `full-text.md` (or the relevant `page-text/page-NN.md`).
3. For ambiguous tables/figures, also `Read` the matching `pages/page-NN.png` — the render
   is authoritative for row/column alignment that flat text loses.
4. For native-text PDFs this needs no OCR. For scanned PDFs, add `pytesseract` + `pdf2image`
   (see the `anthropic-skills:pdf` guide) to OCR first.
