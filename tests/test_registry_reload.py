"""Real registry reload: an EDITED plugin file must take effect after reload()."""
import sys, textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.strategies.base import get_registry
import app.strategies.plugins as _plugins_pkg

PLUGINS_DIR = Path(_plugins_pkg.__file__).parent


def _write(name, direction):
    (PLUGINS_DIR / f"{name}.py").write_text(textwrap.dedent(f'''
        from app.strategies.base import StrategyBase, Signal
        class ReloadProbe(StrategyBase):
            id = "{name}"
            is_builtin = False
            def evaluate(self, row, prev, params, ctx):
                return Signal(direction="{direction}")
    '''))


def test_edited_plugin_takes_effect_after_reload(tmp_path):
    name = "reload_probe_tmp"
    reg = get_registry()
    try:
        _write(name, "CE")
        reg.reload()
        s = reg.get(name)
        assert s is not None
        assert s.evaluate(None, None, {}, {}).direction == "CE"
        _write(name, "PE")
        reg.reload()
        assert reg.get(name).evaluate(None, None, {}, {}).direction == "PE"
        assert reg.get(name).meta()["origin"] == "custom"
    finally:
        (PLUGINS_DIR / f"{name}.py").unlink(missing_ok=True)
        reg.reload()
