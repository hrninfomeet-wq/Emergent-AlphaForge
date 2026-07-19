"""Manual/recovery sync of the Flattrade MCP session file from AlphaForge's token.

The auto-sync (auth callback -> mcp_session_sync) covers the daily login. Run
this host-side script when the MCP wedges with a stale session (its known
lockout failure, flattrademcp issue #1) or after the MCP's logout tool wiped
session.json:

    .venv/Scripts/python.exe backend/scripts/resync_mcp_session.py --clean

Reads the jKey from Mongo (live_broker_tokens) and writes
%USERPROFILE%/.flattrade/session.json. Requires a valid AlphaForge login first
(the script syncs a token; it cannot mint one).
"""

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pymongo import MongoClient  # noqa: E402

from app.live.mcp_session_sync import SESSION_DIR_ENV, SESSION_FILENAME, sync_session_file  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mongo-url", default=os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    ap.add_argument("--db-name", default=os.environ.get("DB_NAME", "alphaforge"))
    ap.add_argument("--user", default="default", help="AlphaForge token-store user id")
    ap.add_argument("--dir", default=str(Path.home() / ".flattrade"),
                    help="MCP session dir (default: ~/.flattrade)")
    ap.add_argument("--clean", action="store_true",
                    help="delete session.json first (stale-session recovery)")
    args = ap.parse_args()

    doc = MongoClient(args.mongo_url)[args.db_name].live_broker_tokens.find_one(
        {"user": args.user, "broker": "flattrade"}
    )
    if not doc or not doc.get("jKey"):
        print("No stored Flattrade token found — log in via AlphaForge first "
              "(Live page → Connect Flattrade), then re-run.")
        return 1

    expires_at = doc.get("expires_at")
    if expires_at:
        try:
            exp = datetime.fromisoformat(str(expires_at))
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if exp <= datetime.now(timezone.utc):
                print(f"WARNING: stored token expired at {expires_at} — the MCP will "
                      "get auth rejections until you log in via AlphaForge again.")
        except ValueError:
            pass

    target = Path(args.dir) / SESSION_FILENAME
    if args.clean and target.exists():
        target.unlink()
        print(f"removed stale {target}")

    os.environ[SESSION_DIR_ENV] = args.dir
    wrote = sync_session_file(
        uid=str(doc.get("uid") or ""),
        actid=str(doc.get("actid") or doc.get("uid") or ""),
        jkey=str(doc["jKey"]),
        api_key=os.environ.get("FLATTRADE_API_KEY"),
    )
    print(f"{'wrote' if wrote else 'unchanged (same token already present)'}: {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
