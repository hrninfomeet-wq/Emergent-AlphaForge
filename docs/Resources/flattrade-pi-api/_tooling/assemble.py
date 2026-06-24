#!/usr/bin/env python3
"""
assemble.py — reconcile the per-endpoint outputs and build INDEX.md + catalog.json.

- Verified endpoints have both <NN-slug>.md and <NN-slug>.json (written by the
  vision-verify workflow).
- Orphan .json (no .md) -> render .md deterministically from the JSON.
- Missing endpoints -> write a RAW stopgap .md from the cleaned page text, clearly
  banner-marked as pending vision verification (overwritten when the workflow resumes).
- Always (re)build INDEX.md (all 58 sections, with a status column) and catalog.json
  (every endpoint that has a valid structured JSON).

Re-run any time; it is idempotent and never overwrites a verified pair.
"""
from __future__ import annotations
import json, os, re, glob

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EP = os.path.join(ROOT, "endpoints")
PT = os.path.join(ROOT, "_build", "page-text")

# (name, category, kind, start_page, end_page) — must match the workflow UNITS list.
UNITS = [
    ("Introduction & App Registration", "Setup", "Info", 1, 4),
    ("Login Flow & Session Token", "Setup", "Info", 5, 7),
    ("Postman Collections", "Setup", "Info", 7, 7),
    ("Place Order", "Orders & Trades", "REST", 7, 9),
    ("Modify Order", "Orders & Trades", "REST", 10, 11),
    ("Cancel Order", "Orders & Trades", "REST", 11, 12),
    ("Exit SNO Order", "Orders & Trades", "REST", 12, 13),
    ("Order Margin", "Orders & Trades", "REST", 13, 15),
    ("Basket Margin", "Orders & Trades", "REST", 15, 17),
    ("Order Book", "Orders & Trades", "REST", 17, 20),
    ("Multi Leg Order Book", "Orders & Trades", "REST", 20, 22),
    ("Single Order History", "Orders & Trades", "REST", 22, 25),
    ("Trade Book", "Orders & Trades", "REST", 25, 27),
    ("Positions Book", "Orders & Trades", "REST", 27, 29),
    ("Product Conversion", "Orders & Trades", "REST", 29, 30),
    ("Place GTT Order", "Orders & Trades", "REST", 30, 31),
    ("Modify GTT Order", "Orders & Trades", "REST", 31, 33),
    ("Cancel GTT Order", "Orders & Trades", "REST", 33, 34),
    ("Get Pending GTT Order", "Orders & Trades", "REST", 33, 35),
    ("Get Enabled GTTs", "Orders & Trades", "REST", 35, 36),
    ("Place OCO Order", "Orders & Trades", "REST", 36, 38),
    ("Modify OCO Order", "Orders & Trades", "REST", 38, 40),
    ("Cancel OCO Order", "Orders & Trades", "REST", 40, 41),
    ("Holdings", "Holdings & Limits", "REST", 40, 42),
    ("Limits", "Holdings & Limits", "REST", 42, 47),
    ("Get Index List", "Market Info", "REST", 47, 48),
    ("Get Top List Names", "Market Info", "REST", 48, 50),
    ("Get Top List", "Market Info", "REST", 50, 51),
    ("Get Time Price Data", "Market Info", "REST", 51, 53),
    ("Get EOD Chart Data", "Market Info", "REST", 53, 54),
    ("Get Option Chain", "Market Info", "REST", 53, 55),
    ("Get Option Greek", "Market Info", "REST", 55, 56),
    ("Get Exchange Msg", "Market Info", "REST", 56, 57),
    ("Get Broker Msg", "Market Info", "REST", 57, 58),
    ("Span Calculator", "Market Info", "REST", 58, 59),
    ("Set Alert", "Alerts", "REST", 59, 60),
    ("Cancel Alert", "Alerts", "REST", 60, 61),
    ("Modify Alert", "Alerts", "REST", 61, 62),
    ("Get Pending Alert", "Alerts", "REST", 62, 63),
    ("Get Enabled Alert Types", "Alerts", "REST", 63, 64),
    ("Funds", "Funds", "REST", 64, 65),
    ("Funds Payout Request", "Funds", "REST", 65, 66),
    ("Get Payin Report", "Funds", "REST", 65, 66),
    ("Get Payout Report", "Funds", "REST", 66, 67),
    ("Cancel Payout", "Funds", "REST", 67, 68),
    ("WebSocket General Guidelines & Connect", "WebSocket", "WebSocket", 68, 69),
    ("WebSocket Touchline (Subscribe/Updates/Unsubscribe)", "WebSocket", "WebSocket", 69, 72),
    ("WebSocket Market Depth (Subscribe/Updates/Unsubscribe)", "WebSocket", "WebSocket", 72, 77),
    ("WebSocket Order Update (Subscribe/Updates/Unsubscribe)", "WebSocket", "WebSocket", 77, 80),
    ("WebSocket Position Update (Subscribe/Updates/Unsubscribe)", "WebSocket", "WebSocket", 80, 83),
    ("WebSocket Heartbeat", "WebSocket", "WebSocket", 83, 83),
    ("User Details", "Account & Reference", "REST", 83, 85),
    ("Search Scrips", "Account & Reference", "REST", 85, 86),
    ("Get Quotes", "Account & Reference", "REST", 86, 89),
    ("Postback / Webhook", "Account & Reference", "Info", 89, 91),
    ("Scrip Master", "Account & Reference", "Info", 91, 92),
    ("API Rate Limits", "Account & Reference", "Info", 92, 92),
    ("Change Log", "Account & Reference", "Info", 92, 93),
]


def slugify(s: str) -> str:
    return re.sub(r"^-|-$", "", re.sub(r"[^a-z0-9]+", "-", s.lower()))


def yn(v):
    if v is True:
        return "yes"
    if v is False:
        return "no"
    return "—"


def md_from_json(o: dict, pages: str) -> str:
    L = [f"# {o.get('name','')}", ""]
    meta = (f"**Category:** {o.get('category','')} · **Type:** {o.get('kind','')}"
            f" · **Method:** {o.get('method') or '—'}  ")
    L.append(meta)
    if o.get("path") or o.get("url"):
        L.append(f"**Path:** `{o.get('path') or '—'}` · **URL:** `{o.get('url') or '—'}`  ")
    L.append(f"**Source:** PDF pages {pages}")
    L.append("")
    if o.get("summary"):
        L += ["## Summary", o["summary"], ""]
    if o.get("request_params"):
        L += ["## Request — Query Parameters",
              "| Parameter | Required | Possible values | Description |",
              "|---|---|---|---|"]
        for p in o["request_params"]:
            L.append(f"| {p.get('name','')} | {yn(p.get('required'))} | "
                     f"{p.get('possible_values','') or ''} | {p.get('description','') or ''} |")
        L.append("")
    if o.get("json_fields"):
        L += ["## Request — jData JSON fields",
              "| Field | Required | Possible values | Description |",
              "|---|---|---|---|"]
        for p in o["json_fields"]:
            L.append(f"| {p.get('name','')} | {yn(p.get('required'))} | "
                     f"{p.get('possible_values','') or ''} | {p.get('description','') or ''} |")
        L.append("")
    if o.get("sample_request"):
        L += ["## Sample request", "```bash", o["sample_request"], "```", ""]
    if o.get("response_fields"):
        L += ["## Response fields",
              "| Field | Possible values | Description |",
              "|---|---|---|"]
        for p in o["response_fields"]:
            L.append(f"| {p.get('name','')} | {p.get('possible_values','') or ''} | "
                     f"{p.get('description','') or ''} |")
        L.append("")
    if o.get("sample_success_response"):
        L += ["## Sample success response", "```json", o["sample_success_response"], "```", ""]
    if o.get("sample_failure_response"):
        L += ["## Sample failure response", "```json", o["sample_failure_response"], "```", ""]
    if o.get("notes"):
        L += ["## Notes", o["notes"], ""]
    return "\n".join(L).rstrip() + "\n"


def raw_slice(a: int, b: int) -> str:
    out = []
    for p in range(a, b + 1):
        fp = os.path.join(PT, f"page-{p:02d}.md")
        if os.path.exists(fp):
            with open(fp, encoding="utf-8") as f:
                txt = f.read()
            txt = re.sub(r"<!-- page \d+ of \d+ -->\n*", "", txt)
            out.append(f"\n<!-- ---- page {p} ---- -->\n\n{txt.strip()}")
    return "\n".join(out).strip()


def main():
    rows = []
    catalog = []
    stats = {"verified": 0, "rendered": 0, "md_only": 0, "raw": 0}
    pending = []
    for i, (name, cat, kind, a, b) in enumerate(UNITS):
        n = i + 1
        sl = f"{n:02d}-{slugify(name)}"
        md_p = os.path.join(EP, f"{sl}.md")
        js_p = os.path.join(EP, f"{sl}.json")
        pages = f"{a}-{b}" if a != b else f"{a}"
        has_md, has_js = os.path.exists(md_p), os.path.exists(js_p)
        obj = None
        if has_js:
            try:
                obj = json.load(open(js_p, encoding="utf-8"))
            except Exception:
                obj = None
                has_js = False

        if has_md and has_js:
            status = "✅ verified"
            stats["verified"] += 1
        elif has_js and not has_md:                      # orphan json -> render md
            open(md_p, "w", encoding="utf-8").write(md_from_json(obj, pages))
            status = "✅ verified"
            stats["rendered"] += 1
            has_md = True
        elif has_md and not has_js:                      # md written, json lost to limit
            status = "🟦 md only (json pending)"
            stats["md_only"] += 1
            pending.append(sl)
        else:                                            # missing -> raw stopgap
            banner = (f"> ⚠️ **RAW auto-extraction — pending vision verification.**\n"
                      f"> Cleaned text sliced from PDF pages {pages}; the parameter tables are NOT yet\n"
                      f"> row-aligned and may include adjacent sections. This file is replaced by a\n"
                      f"> verified version when the extraction workflow is resumed.\n")
            body = raw_slice(a, b)
            open(md_p, "w", encoding="utf-8").write(
                f"# {name}\n\n**Category:** {cat} · **Type:** {kind} · "
                f"**Source:** PDF pages {pages}\n\n{banner}\n---\n\n{body}\n")
            status = "⚠️ raw (pending)"
            stats["raw"] += 1
            pending.append(sl)

        if obj:
            obj.setdefault("pages", pages)
            obj["_slug"] = sl
            obj["_index"] = n
            catalog.append(obj)

        rows.append((n, sl, name, cat, kind,
                     (obj or {}).get("method", ""), (obj or {}).get("path", ""), pages, status))

    # catalog.json
    json.dump(catalog, open(os.path.join(ROOT, "catalog.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    # INDEX.md
    L = ["# Flattrade *pi* API — endpoint index", "",
         f"{len(UNITS)} sections decoded from the source PDF. "
         "✅ = vision-verified (cross-checked against the page render). "
         "⚠️ = raw text slice pending verification.", "",
         "| # | Endpoint | Category | Type | Method | Path | Pages | Status |",
         "|---|---|---|---|---|---|---|---|"]
    for (n, sl, name, cat, kind, method, path, pages, status) in rows:
        L.append(f"| {n} | [{name}](endpoints/{sl}.md) | {cat} | {kind} | "
                 f"{method or ''} | {('`'+path+'`') if path else ''} | {pages} | {status} |")
    L += ["", "## Machine-readable catalog",
          "`catalog.json` holds the structured spec (params, required flags, possible values, "
          "response fields, sample request/response) for every verified endpoint.",
          "", "## Full decoded text", "`reference/full-text.md` — all 93 pages, boilerplate stripped."]
    open(os.path.join(ROOT, "INDEX.md"), "w", encoding="utf-8").write("\n".join(L) + "\n")

    print("status:", stats)
    print(f"catalog.json endpoints: {len(catalog)}")
    print(f"pending (need workflow resume): {len(pending)}")
    for s in pending:
        print("   -", s)


if __name__ == "__main__":
    main()
