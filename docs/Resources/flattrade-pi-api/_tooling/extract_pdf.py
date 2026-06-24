#!/usr/bin/env python3
"""
extract_pdf.py — Decode a PDF into clean, agent-friendly Markdown + page renders.

Built for the Flattrade *pi* API documentation (a Chrome "Print to PDF" of
https://pi.flattrade.in/docs), but written to be reusable on any fixed-layout PDF.

What it does
------------
1. Reads text as positioned *blocks* (bbox + text) via PyMuPDF.
2. Strips page boilerplate (a fixed left navigation sidebar + running header +
   footer) using geometric crop bands. This is the key trick: layout-aware
   markdown converters otherwise weave the repeated sidebar into every page.
3. Reconstructs reading order with simple 2-column detection (left = prose /
   parameter tables, right = curl + sample-response code blocks).
4. Renders every page to PNG (for vision verification / "render" the doc).
5. Extracts embedded raster images (UI screenshots) to assets/.
6. Emits: per-page Markdown, a concatenated full-text Markdown, and a JSON
   sidecar with per-page block geometry for downstream tooling.

Usage
-----
    python extract_pdf.py "<input.pdf>" --out <out_dir> [--dpi 144]
    python extract_pdf.py "<input.pdf>" --out <out_dir> --no-render   # text only

Generic mode (auto-detect boilerplate instead of the Flattrade crop bands):
    python extract_pdf.py "<input.pdf>" --out <out_dir> --auto-boilerplate
"""
from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter

import pymupdf


# --- Flattrade pi layout constants (points; page is 1191.12 x 841.92) ---------
SIDEBAR_MAX_CENTER_X = 240.0   # blocks whose horizontal center is left of this = nav sidebar
HEADER_MAX_Y = 28.0            # blocks ending above this = running header
FOOTER_MIN_Y = 812.0          # blocks starting below this = footer
COLUMN_SPLIT_X = 712.0        # gutter between left (prose/params) and right (code) columns


def _norm(text: str) -> str:
    """Normalize a block's text for repeat-detection: drop digits, collapse ws."""
    return re.sub(r"\s+", " ", re.sub(r"\d+", "", text)).strip().lower()


def detect_boilerplate(doc, min_fraction=0.6):
    """Auto-detect repeating boilerplate blocks by (normalized text + position band).

    Returns a set of (norm_text, x_band, y_band) keys that repeat on >= min_fraction
    of pages. Generic fallback for PDFs whose layout we have not hand-measured.
    """
    n = doc.page_count
    counter = Counter()
    for page in doc:
        for x0, y0, x1, y1, txt, *_ in page.get_text("blocks"):
            nt = _norm(txt)
            if not nt:
                continue
            key = (nt, round(x0 / 20), round(y0 / 20))
            counter[key] += 1
    thresh = max(2, int(min_fraction * n))
    return {k for k, c in counter.items() if c >= thresh}


def keep_block_geometric(x0, y0, x1, y1) -> bool:
    cx = (x0 + x1) / 2
    if cx < SIDEBAR_MAX_CENTER_X:        # left navigation sidebar
        return False
    if y1 <= HEADER_MAX_Y:               # running header
        return False
    if y0 >= FOOTER_MIN_Y:               # footer
        return False
    return True


def page_to_markdown(page, boilerplate=None):
    """Return (markdown_text, kept_blocks_meta) for one page."""
    raw = page.get_text("blocks")
    kept = []
    for x0, y0, x1, y1, txt, bno, btype in raw:
        if btype != 0:  # image block; handled separately
            continue
        if not txt.strip():
            continue
        if boilerplate is not None:
            key = (_norm(txt), round(x0 / 20), round(y0 / 20))
            if key in boilerplate:
                continue
        else:
            if not keep_block_geometric(x0, y0, x1, y1):
                continue
        kept.append((x0, y0, x1, y1, txt.strip()))

    # 2-column reading order: left column (by y), then right column (by y).
    left = sorted([b for b in kept if (b[0] + b[2]) / 2 < COLUMN_SPLIT_X],
                  key=lambda b: (round(b[1] / 4), b[0]))
    right = sorted([b for b in kept if (b[0] + b[2]) / 2 >= COLUMN_SPLIT_X],
                   key=lambda b: (round(b[1] / 4), b[0]))

    lines = []
    if left:
        for b in left:
            lines.append(b[4])
    if right:
        if left:
            lines.append("")
            lines.append("<!-- right column: code / sample responses -->")
        for b in right:
            lines.append(b[4])

    meta = [
        {"bbox": [round(v, 1) for v in b[:4]], "text": b[4]}
        for b in (left + right)
    ]
    return "\n\n".join(lines).strip(), meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--out", required=True)
    ap.add_argument("--dpi", type=int, default=144)
    ap.add_argument("--no-render", action="store_true")
    ap.add_argument("--auto-boilerplate", action="store_true",
                    help="auto-detect repeating boilerplate instead of Flattrade crop bands")
    args = ap.parse_args()

    out = args.out
    pages_dir = os.path.join(out, "pages")
    text_dir = os.path.join(out, "page-text")
    assets_dir = os.path.join(out, "assets")
    for d in (out, pages_dir, text_dir, assets_dir):
        os.makedirs(d, exist_ok=True)

    doc = pymupdf.open(args.pdf)
    boilerplate = detect_boilerplate(doc) if args.auto_boilerplate else None
    if boilerplate is not None:
        print(f"[auto] detected {len(boilerplate)} boilerplate block patterns")

    zoom = args.dpi / 72.0
    mat = pymupdf.Matrix(zoom, zoom)

    full_parts = []
    geometry = {}
    img_saved = 0
    for pno in range(doc.page_count):
        page = doc[pno]
        md, meta = page_to_markdown(page, boilerplate)
        geometry[pno + 1] = meta

        per_page = f"<!-- page {pno + 1} of {doc.page_count} -->\n\n{md}\n"
        with open(os.path.join(text_dir, f"page-{pno + 1:02d}.md"), "w", encoding="utf-8") as f:
            f.write(per_page)
        full_parts.append(f"\n\n---\n\n## Page {pno + 1}\n\n{md}\n")

        if not args.no_render:
            pix = page.get_pixmap(matrix=mat)
            pix.save(os.path.join(pages_dir, f"page-{pno + 1:02d}.png"))

        # embedded raster images (UI screenshots, pages 1-6)
        for i, info in enumerate(page.get_images(full=True)):
            xref = info[0]
            try:
                base = doc.extract_image(xref)
            except Exception:
                continue
            ext = base.get("ext", "png")
            fn = f"page{pno + 1:02d}-img{i + 1}.{ext}"
            with open(os.path.join(assets_dir, fn), "wb") as f:
                f.write(base["image"])
            img_saved += 1

    with open(os.path.join(out, "full-text.md"), "w", encoding="utf-8") as f:
        f.write("# Flattrade pi API — full decoded text (boilerplate stripped)\n")
        f.write("".join(full_parts))

    with open(os.path.join(out, "geometry.json"), "w", encoding="utf-8") as f:
        json.dump(geometry, f, ensure_ascii=False, indent=1)

    print(f"pages: {doc.page_count}  rendered: {0 if args.no_render else doc.page_count}"
          f"  embedded-images: {img_saved}")
    print(f"out -> {out}")


if __name__ == "__main__":
    main()
