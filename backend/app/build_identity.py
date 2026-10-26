"""Baked build provenance, independent of application runtime and credentials.

Docker captures both source trees through the root .dockerignore allowlist. The
fingerprint is over exact path/content bytes in that snapshot, not a Git commit,
dependency/image attestation, current host checkout, or mounted plugin content.
Run: python backend/app/build_identity.py --root ROOT --out manifest.json
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

SCOPE = "alphaforge-source-v1"
FIXED_INPUTS = (
    ".dockerignore", "docker-compose.yml", "start-app.bat",
    "backend/server.py", "backend/requirements.txt", "backend/Dockerfile",
    "frontend/package.json", "frontend/yarn.lock", "frontend/Dockerfile",
    "frontend/nginx.conf", "frontend/craco.config.js", "frontend/jsconfig.json",
    "frontend/postcss.config.js", "frontend/tailwind.config.js", "frontend/components.json",
)
SOURCE_TREES = ("backend/app", "backend/scripts", "frontend/src", "frontend/public", "frontend/plugins")
SOURCE_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx", ".css", ".html", ".json", ".svg", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".txt", ".woff", ".woff2"}
EXCLUDED_PARTS = {"__pycache__", "node_modules", ".git", ".venv", "venv", ".pytest_cache"}
FIELDS = ("schema", "scope", "status", "source_sha256", "built_at", "git_revision", "git_dirty", "checkout_status")


def _included(path: Path) -> bool:
    return (
        path.suffix.lower() in SOURCE_SUFFIXES
        and not any(part in EXCLUDED_PARTS or part.startswith(".env") for part in path.parts)
        and not path.name.startswith("token")
        and not any("credentials" in part or "secret" in part or part.startswith("token") for part in path.parts)
        and path.name != "build-manifest.json"
    )


def source_fingerprint(root: Path) -> str:
    """Hash sorted relative paths and exact bytes; excludes generated manifests."""
    root = Path(root)
    paths = {root / name for name in FIXED_INPUTS if (root / name).is_file()}
    for tree in SOURCE_TREES:
        folder = root / tree
        if folder.exists():
            paths.update(path for path in folder.rglob("*") if path.is_file() and not path.is_symlink() and _included(path.relative_to(root)))
    digest = hashlib.sha256(SCOPE.encode("ascii") + b"\0")
    for path in sorted(paths, key=lambda item: item.relative_to(root).as_posix()):
        name = path.relative_to(root).as_posix().encode("utf-8")
        data = path.read_bytes()
        digest.update(len(name).to_bytes(8, "big") + name)
        digest.update(len(data).to_bytes(8, "big") + data)
    return digest.hexdigest()


def make_manifest(root: Path, revision: str = "", dirty: str = "") -> dict:
    revision = revision if re.fullmatch(r"[0-9a-f]{40,64}", revision) else None
    return {
        "schema": 1, "scope": SCOPE, "status": "known",
        "source_sha256": source_fingerprint(root),
        "built_at": datetime.now(timezone.utc).isoformat(),
        "git_revision": revision,
        "git_dirty": {"true": True, "false": False}.get(dirty) if revision else None,
        "checkout_status": "unverified",
    }


def read_manifest(path: Path | None = None) -> dict:
    """Never infer build provenance from today's source or environment variables."""
    unknown = {"schema": 1, "scope": SCOPE, "status": "unknown", "source_sha256": None,
               "built_at": None, "git_revision": None, "git_dirty": None, "checkout_status": "unverified"}
    try:
        data = json.loads((path or Path(__file__).resolve().parents[1] / "build-manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return unknown
    if not isinstance(data, dict) or data.get("schema") != 1 or data.get("scope") != SCOPE or data.get("status") != "known":
        return unknown
    if not isinstance(data.get("source_sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", data["source_sha256"]):
        return unknown
    if not isinstance(data.get("built_at"), str):
        return unknown
    result = {key: data.get(key) for key in FIELDS}
    result["git_revision"] = data.get("git_revision") if isinstance(data.get("git_revision"), str) and re.fullmatch(r"[0-9a-f]{40,64}", data["git_revision"]) else None
    result["git_dirty"] = data.get("git_dirty") if type(data.get("git_dirty")) is bool else None
    result["checkout_status"] = "unverified"
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--revision", default="")
    parser.add_argument("--dirty", default="")
    args = parser.parse_args()
    args.out.write_text(json.dumps(make_manifest(args.root, args.revision, args.dirty), sort_keys=True) + "\n", encoding="utf-8")
