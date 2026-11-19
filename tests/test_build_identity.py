"""Offline build identity checks: no app runtime, HTTP, DB or broker imports."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def module():
    spec = importlib.util.spec_from_file_location("build_identity", ROOT / "backend/app/build_identity.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_source_hash_is_deterministic_and_includes_uncommitted_source(tmp_path):
    mod = module()
    source = tmp_path / "backend/app/example.py"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"answer = 1\r\n")
    first = mod.source_fingerprint(tmp_path)
    assert first == mod.source_fingerprint(tmp_path)
    source.write_bytes(b"answer = 1\n")
    assert mod.source_fingerprint(tmp_path) != first  # exact bytes, no newline normalization
    source.write_text("answer = 2\n")
    assert mod.source_fingerprint(tmp_path) != first


def test_hash_independently_rederived_from_exact_path_and_bytes(tmp_path):
    import hashlib
    mod = module()
    path = tmp_path / "backend/app/example.py"
    path.parent.mkdir(parents=True)
    data = b"answer = 42\n"
    path.write_bytes(data)
    name = b"backend/app/example.py"
    raw = b"alphaforge-source-v1\0" + len(name).to_bytes(8, "big") + name + len(data).to_bytes(8, "big") + data
    assert mod.source_fingerprint(tmp_path) == hashlib.sha256(raw).hexdigest()


def test_generated_secrets_cache_and_research_are_not_fingerprinted(tmp_path):
    mod = module()
    first = mod.source_fingerprint(tmp_path)
    for name in ["backend/.env", "backend/app/.env", "backend/app/private.key", "backend/app/token.json", "backend/app/__pycache__/x.pyc", "frontend/src/build-manifest.json", "docs/research.json", ".git/config", "frontend/node_modules/test.js"]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not included")
    assert mod.source_fingerprint(tmp_path) == first


def test_manifests_have_same_source_identity_and_optional_git_metadata(tmp_path):
    mod = module()
    first = mod.make_manifest(tmp_path, revision="a" * 40, dirty="true")
    second = mod.make_manifest(tmp_path)
    assert first["source_sha256"] == second["source_sha256"]
    assert first["git_revision"] == "a" * 40
    assert first["git_dirty"] is True
    assert second["git_revision"] is None
    assert second["git_dirty"] is None
    assert first["checkout_status"] == "unverified"


@pytest.mark.parametrize("contents", [None, "not json", "[]", '{"source_sha256":"broken"}', '{"schema":1,"source_sha256":"' + "a" * 64 + '"}'])
def test_missing_or_partial_manifest_is_unknown(tmp_path, contents):
    mod = module()
    path = tmp_path / "manifest.json"
    if contents is not None:
        path.write_text(contents)
    assert mod.read_manifest(path)["status"] == "unknown"


def test_baked_manifest_round_trip_and_sanitization(tmp_path):
    mod = module()
    manifest = mod.make_manifest(tmp_path, revision="not a sha", dirty="maybe")
    manifest["secret"] = "must not be returned"
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    actual = mod.read_manifest(path)
    assert actual["status"] == "known"
    assert actual["git_revision"] is None
    assert actual["git_dirty"] is None
    assert "secret" not in actual


def test_both_docker_builds_share_allowlisted_source_capture():
    compose = (ROOT / "docker-compose.yml").read_text()
    assert compose.count("context: .\n") == 2
    for component in ["backend", "frontend"]:
        recipe = (ROOT / component / "Dockerfile").read_text()
        assert "COPY backend/ /source/backend/" in recipe
        assert "COPY frontend/ /source/frontend/" in recipe
        assert "COPY docker-compose.yml start-app.bat .dockerignore /source/" in recipe
        assert "python /source/backend/app/build_identity.py" in recipe
    ignore = (ROOT / ".dockerignore").read_text()
    assert ignore.startswith("#")
    assert "**\n" in ignore
    for excluded in ["**/.env*", "**/node_modules", "**/__pycache__", "**/*.key", "**/token*", ".git"]:
        assert excluded in ignore


def test_route_is_small_read_only_and_uses_baked_metadata():
    import ast
    tree = ast.parse((ROOT / "backend/server.py").read_text())
    route = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "build_info")
    assert len(route.body) == 2  # docstring + return; no runtime or database calls
    assert ast.unparse(route.body[-1]) == "return read_build_manifest()"
    assert any(ast.unparse(item) == "api.get('/build-info')" for item in route.decorator_list)
