"""The cockpit's Market pulse + Market analysis cards switch between NIFTY, SENSEX and
BANKNIFTY (frontend/src/lib/marketAnalysisView.js), EXECUTED through node.

GET /market/analysis(/stream)?instrument=X already served all three indices (checked
live 2026-10-06: spot, trend and a 7-strike chain for each); the cockpit hard-coded
NIFTY. Two honesty rules come with the switch:
  * right after a tab switch the stream still holds the PREVIOUS index's payload —
    it must never render under the new tab;
  * the IV-rank "vix_proxy" is India VIX = NIFTY 50 implied vol — on SENSEX /
    BANKNIFTY it is borrowed and must say so.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_LIB = ROOT / "frontend" / "src" / "lib" / "marketAnalysisView.js"

node = pytest.mark.skipif(shutil.which("node") is None, reason="node required")


def _run_js(body: str):
    url = "file:///" + os.path.abspath(_LIB).replace("\\", "/")
    source = (f"import * as M from {url!r};\n"
              f"const out = await (async () => {{ {body} }})();\n"
              "process.stdout.write(JSON.stringify(out === undefined ? null : out));\n")
    proc = subprocess.run(["node", "--input-type=module", "-e", source],
                          capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError(f"node failed:\n{proc.stderr}")
    return json.loads(proc.stdout)


@node
def test_the_three_indices_in_tab_order():
    assert _run_js("return M.ANALYSIS_INSTRUMENTS;") == ["NIFTY", "SENSEX", "BANKNIFTY"]


@node
@pytest.mark.parametrize("raw,want", [
    ("SENSEX", "SENSEX"), ("banknifty", "BANKNIFTY"), (" nifty ", "NIFTY"),
    ("FINNIFTY", "NIFTY"), ("", "NIFTY"), (None, "NIFTY"),
])
def test_a_stored_choice_is_normalised_and_unknowns_fall_back_to_nifty(raw, want):
    assert _run_js(f"return M.normalizeAnalysisInstrument({json.dumps(raw)});") == want


@node
def test_the_payload_renders_only_under_its_own_index():
    out = _run_js("""
      const nifty = {instrument: "NIFTY", spot: 22776};
      return [
        M.analysisForInstrument(nifty, "NIFTY"),
        M.analysisForInstrument(nifty, "SENSEX"),        // just switched: stale NIFTY payload
        M.analysisForInstrument({instrument: "sensex"}, "SENSEX"),
        M.analysisForInstrument({spot: 1}, "NIFTY"),     // no instrument: cannot be attributed
        M.analysisForInstrument(null, "NIFTY"),
      ];""")
    assert out[0] == {"instrument": "NIFTY", "spot": 22776}
    assert out[1] is None, "a NIFTY payload rendered under the SENSEX tab"
    assert out[2] == {"instrument": "sensex"}
    assert out[3] is None and out[4] is None


@node
@pytest.mark.parametrize("source,inst,want", [
    ("atm_iv", "SENSEX", "ATM IV"),
    ("vix_proxy", "NIFTY", "VIX proxy"),
    ("vix_proxy", "SENSEX", "India VIX proxy (NIFTY 50 IV, not SENSEX)"),
    ("vix_proxy", "banknifty", "India VIX proxy (NIFTY 50 IV, not BANKNIFTY)"),
    (None, "NIFTY", "unavailable"),
    ("something_new", "NIFTY", "something_new"),
])
def test_the_iv_rank_label_says_when_india_vix_is_borrowed(source, inst, want):
    assert _run_js(f"return M.ivSourceLabel({json.dumps(source)}, {json.dumps(inst)});") == want


# --------------------------------------------------------------------------- #
# Wiring pins — the logic above only matters if the cockpit uses it.
# --------------------------------------------------------------------------- #

def _src(rel: str) -> str:
    return (ROOT / "frontend" / "src" / rel).read_text(encoding="utf-8")


def test_the_provider_streams_the_selected_index_not_a_hardcoded_nifty():
    src = _src("components/live/LiveDataProvider.jsx")
    assert "instrument=NIFTY" not in src and 'marketAnalysis("NIFTY")' not in src
    assert "/market/analysis/stream?instrument=${encodeURIComponent(analysisInstrument)}" in src
    assert "api.marketAnalysis(analysisInstrument)" in src
    assert "analysisInstrument, setAnalysisInstrument," in src


def test_the_cockpit_filters_the_payload_and_renders_the_tabs():
    src = _src("components/live/LiveCockpit.jsx")
    assert "analysisForInstrument(marketAnalysis, analysisInstrument)" in src
    assert "ANALYSIS_INSTRUMENTS.map" in src and 'role="tablist"' in src
    assert "<MarketPulse analysis={analysis} instrument={analysisInstrument} />" in src
    assert "<MarketAnalysis analysis={analysis} instrument={analysisInstrument} />" in src


def test_the_analysis_card_labels_iv_through_the_shared_helper():
    src = _src("components/live/cockpit/MarketAnalysis.jsx")
    assert "ivSourceLabel(options.iv_rank_source, analysis.instrument)" in src
    assert "humanizeIvSource" not in src
