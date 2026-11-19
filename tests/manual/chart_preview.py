"""Isolated browser smoke preview. Synthetic prices; no DB, tokens or runtime.

Build frontend with REACT_APP_BACKEND_URL=http://localhost:8012 and a separate
BUILD_PATH, then pass that directory here. Static UI :3011; read-only fixtures
:8012. Never bind either of AlphaForge's production ports (3000/8001).
"""
import argparse
import json
import math
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from app.chart_data import _aggregate

NOTICE = "ISOLATED UI TEST — synthetic data only; no broker connection."
INSTRUMENTS = [
    {"instrument_key": "NSE_INDEX|Nifty 50", "symbol": "NIFTY", "kind": "index", "exchange": "NSE", "segment": "NSE_INDEX"},
    {"instrument_key": "NSE_INDEX|Nifty Bank", "symbol": "BANKNIFTY", "kind": "index", "exchange": "NSE", "segment": "NSE_INDEX"},
    {"instrument_key": "BSE_INDEX|SENSEX", "symbol": "SENSEX", "kind": "index", "exchange": "BSE", "segment": "BSE_INDEX"},
    {"instrument_key": "NSE_EQ|SYNTHETIC", "symbol": "TEST EQUITY", "kind": "equity", "exchange": "NSE", "segment": "NSE_EQ"},
]
for instrument in INSTRUMENTS:
    instrument.update(name=NOTICE, volume_available=instrument["kind"] != "index")


class APIHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_headers(self, status=200, content_type="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "http://localhost:3011")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def respond(self, value, status=200):
        self.send_headers(status)
        self.wfile.write(json.dumps(value).encode())

    def do_GET(self):
        url = urlparse(self.path)
        query = parse_qs(url.query)
        key = query.get("instrument_key", [INSTRUMENTS[0]["instrument_key"]])[0]
        instrument = next((row for row in INSTRUMENTS if row["instrument_key"] == key), INSTRUMENTS[0])
        frame = query.get("timeframe", ["1m"])[0]
        now = int(time.time() * 1000)
        if url.path == "/api/charts/instruments":
            search = query.get("q", [""])[0].upper()
            return self.respond({"items": [row for row in INSTRUMENTS if search in row["symbol"]], "warnings": [NOTICE]})
        if url.path == "/api/charts/history":
            base = 25000 if instrument["kind"] == "index" else 200
            rows = []
            for day in range(10):
                start = datetime(2026, 10, 19, 9, 15, tzinfo=timezone(timedelta(hours=5, minutes=30))) + timedelta(days=day)
                if start.weekday() > 4:
                    continue
                for minute in range(375):
                    n = day * 375 + minute
                    opened = base + 12 * math.sin(n / 25) + n * 0.006
                    closed = opened + 1.5 * math.sin(n / 3)
                    rows.append({"time": int((start + timedelta(minutes=minute)).timestamp()), "open": opened,
                                 "high": max(opened, closed) + 1, "low": min(opened, closed) - 1,
                                 "close": closed, "volume": 1000 + n % 300 if instrument["volume_available"] else None})
            rows = _aggregate(rows, frame)
            if "before" in query:
                rows = [row for row in rows if row["time"] < int(query["before"][0])]
            more = len(rows) > 600
            rows = rows[-600:]
            return self.respond({"instrument": instrument, "timeframe": frame, "bars": rows,
                                 "source": "synthetic_ui_fixture", "warnings": [NOTICE], "as_of_ms": now,
                                 "volume_available": instrument["volume_available"], "has_more": more,
                                 "next_before": rows[0]["time"] if more and rows else None})
        if url.path == "/api/charts/positions":
            rows = [] if instrument["kind"] == "index" else [{
                "id": "synthetic-paper", "origin": "paper", "instrument_key": key, "symbol": instrument["symbol"],
                "quantity": 10, "entry_price": 205, "mark_price": 217, "pnl": 120,
                "stop_price": 195, "target_price": 230, "fresh": False,
                "label": "Synthetic fixture", "protection_source": "Synthetic plan only"}]
            return self.respond({"instrument_key": key, "positions": rows, "warnings": [NOTICE], "as_of_ms": now})
        if url.path == "/api/charts/stream":
            self.send_headers(content_type="text/event-stream")
            try:
                for _ in range(30):
                    payload = {"instrument_key": key, "timeframe": frame, "status": "disconnected",
                               "connected": False, "warnings": [NOTICE], "source": "synthetic_ui_fixture",
                               "emitted_at_ms": int(time.time() * 1000), "last_tick_age_ms": None}
                    self.wfile.write(("event: snapshot\ndata: " + json.dumps(payload) + "\n\n").encode())
                    self.wfile.flush()
                    time.sleep(2)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            return
        return self.respond({"detail": "Unavailable in the isolated UI preview"}, 404)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("build_directory", type=Path)
    args = parser.parse_args()
    directory = args.build_directory.resolve()
    if not (directory / "index.html").is_file():
        raise SystemExit("Build index.html missing")
    class StaticHandler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(directory), **kw)
        def do_GET(self):
            if urlparse(self.path).path in {"/charts", "/charts/"}:
                self.path = "/index.html"
            super().do_GET()
        def log_message(self, *args):
            pass
    servers = [ThreadingHTTPServer(("127.0.0.1", 8012), APIHandler), ThreadingHTTPServer(("127.0.0.1", 3011), StaticHandler)]
    for server in servers:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    print("SYNTHETIC preview ready: http://localhost:3011/charts", flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        for server in servers:
            server.shutdown()


if __name__ == "__main__":
    main()
