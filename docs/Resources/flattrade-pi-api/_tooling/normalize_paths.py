#!/usr/bin/env python3
"""Normalize the `path` field to be consistent: for REST endpoints whose URL is on the
PiConnect base, path = the part after the base (so /ModifyOrder, not /PiConnectAPI/ModifyOrder).
Updates both the .json and the matching `**Path:**` line in the .md. Leaves login/websocket/
postback endpoints (different hosts) untouched. Idempotent."""
import glob, json, os, re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EP = os.path.join(ROOT, "endpoints")
BASE = "https://piconnect.flattrade.in/PiConnectAPI"

changed = 0
for js in sorted(glob.glob(os.path.join(EP, "*.json"))):
    o = json.load(open(js, encoding="utf-8"))
    url = (o.get("url") or "").strip()
    if not url.startswith(BASE):
        continue                          # auth host / wss / postback — leave as-is
    newpath = url[len(BASE):] or "/"
    if o.get("path") == newpath:
        continue
    o["path"] = newpath
    json.dump(o, open(js, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    md = js[:-5] + ".md"
    if os.path.exists(md):
        txt = open(md, encoding="utf-8").read()
        txt = re.sub(r"(\*\*Path:\*\*\s*)`[^`]*`", r"\1`" + newpath + "`", txt, count=1)
        open(md, "w", encoding="utf-8").write(txt)
    changed += 1

print(f"normalized path on {changed} endpoints")
