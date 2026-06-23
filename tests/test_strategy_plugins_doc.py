import re
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.ai.grounding import build_grounding_catalog

DOC = ROOT / "docs" / "STRATEGY_PLUGINS.md"


def test_doc_documents_every_indicator_column():
    text = DOC.read_text(encoding="utf-8")
    cols = build_grounding_catalog()["indicator_columns"]
    missing = [c for c in cols if not re.search(rf"`{re.escape(c)}`", text)]
    assert not missing, f"STRATEGY_PLUGINS.md is missing indicator columns: {missing}"


def test_doc_documents_extra_signal_fields():
    text = DOC.read_text(encoding="utf-8")
    for f in ["scenario", "spot_target_level", "exit_mode"]:
        assert re.search(rf"`{re.escape(f)}`", text), f"doc missing Signal field {f}"


def test_doc_template_sets_is_builtin_false():
    text = DOC.read_text(encoding="utf-8")
    assert "is_builtin = False" in text, "Template must set is_builtin = False"


def test_doc_documents_session_precompute():
    text = DOC.read_text(encoding="utf-8")
    assert "session_precompute" in text
