import sys; from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT / "backend"))
from app.live.broker_protocol import OrderIntent, OrderResult, ORDER_STATES

def test_order_intent_defaults_and_validation_shape():
    oi = OrderIntent(client_order_id="cid1", trantype="B", prctyp="LMT", exch="NFO",
                     tsym="NIFTY25000CE", qty=65, prc=158.5, prd="I", ret="DAY")
    assert oi.trgprc is None and oi.prd == "I" and oi.ret == "DAY"
    d = oi.to_jdata(uid="U1", actid="U1")
    assert d["prctyp"] == "LMT" and d["qty"] == "65" and d["ordersource"] == "API"
    assert "trgprc" not in d  # omitted when None

def test_sl_lmt_jdata_includes_trigger():
    oi = OrderIntent(client_order_id="c", trantype="S", prctyp="SL-LMT", exch="NFO",
                     tsym="X", qty=65, prc=119.0, trgprc=120.0, prd="I", ret="DAY")
    d = oi.to_jdata(uid="U1", actid="U1")
    assert d["trgprc"] == "120" and d["prctyp"] == "SL-LMT"

def test_order_states_constant():
    assert {"INTENT","SUBMITTED","ACKED","OPEN","TRIGGER_PENDING","PARTIAL","COMPLETE","REJECTED","CANCELED"} <= set(ORDER_STATES)
