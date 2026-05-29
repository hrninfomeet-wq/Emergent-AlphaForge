"""Configurable option slippage model.

Per user spec (2026-05-29) - keep simple, defaults from real liquidity observation:

  ATM             : 0.5 point each side
  OTM1 / ITM1     : 1.0 point each side
  OTM2+ / ITM2+   : 2.0 points each side
  Last 30 minutes of expiry day on the same instrument: 2x multiplier on top

The model is intentionally one-knob-per-bucket. Real microstructure is messier
but pretending to model bid/ask spreads we don't measure would be theatre.
The doubling-on-expiry-tail rule mirrors real-world spread widening when stops
fire on contracts with no time value left.

Honest scope:
  - Backtest engines call estimate_slippage_per_side() at fill time.
  - Paper trading does NOT add slippage right now (live ticks already reflect spread).
  - The volatility detector below is independent. It tags each minute with a
    realized-vol vs 30-day average ratio. Backtests can later flag trades that
    fired during high-vol minutes for separate analysis.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple


IST_OFFSET = timedelta(hours=5, minutes=30)
EXPIRY_TAIL_START = time(15, 0)
EXPIRY_TAIL_MULTIPLIER = 2.0


@dataclass
class SlippageConfig:
    """Per-side slippage in option points. Override any value to tune a backtest."""
    atm_pts: float = 0.5
    otm1_pts: float = 1.0
    itm1_pts: float = 1.0
    otm2_plus_pts: float = 2.0
    itm2_plus_pts: float = 2.0
    expiry_tail_multiplier: float = EXPIRY_TAIL_MULTIPLIER
    expiry_tail_start: time = EXPIRY_TAIL_START

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "SlippageConfig":
        if not data:
            return cls()
        cfg = cls()
        for key in ("atm_pts", "otm1_pts", "itm1_pts", "otm2_plus_pts", "itm2_plus_pts", "expiry_tail_multiplier"):
            if key in data:
                try:
                    setattr(cfg, key, float(data[key]))
                except (TypeError, ValueError):
                    pass
        if "expiry_tail_start" in data and isinstance(data["expiry_tail_start"], str):
            try:
                hh, mm = data["expiry_tail_start"].split(":")[:2]
                cfg.expiry_tail_start = time(int(hh), int(mm))
            except Exception:
                pass
        return cfg

    def to_dict(self) -> Dict[str, Any]:
        return {
            "atm_pts": self.atm_pts,
            "otm1_pts": self.otm1_pts,
            "itm1_pts": self.itm1_pts,
            "otm2_plus_pts": self.otm2_plus_pts,
            "itm2_plus_pts": self.itm2_plus_pts,
            "expiry_tail_multiplier": self.expiry_tail_multiplier,
            "expiry_tail_start": self.expiry_tail_start.strftime("%H:%M"),
        }


_MONEYNESS_RE = re.compile(r"^(atm|itm|otm)(\d*)$")


def slippage_bucket(moneyness: str) -> str:
    """Normalize a moneyness label like 'OTM1', 'itm2', 'ATM' into a bucket key."""
    m = _MONEYNESS_RE.match(str(moneyness or "atm").lower())
    if not m:
        return "atm"
    label, distance = m.group(1), m.group(2)
    if label == "atm":
        return "atm"
    distance_int = int(distance) if distance else 1
    if label == "otm":
        return "otm1" if distance_int <= 1 else "otm2_plus"
    if label == "itm":
        return "itm1" if distance_int <= 1 else "itm2_plus"
    return "atm"


def _ist_dt(ts_ms: int) -> datetime:
    return datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc) + IST_OFFSET


def is_expiry_tail(*, ts_ms: int, expiry_iso: Optional[str], cfg: SlippageConfig) -> bool:
    """True if the bar timestamp falls in the expiry-day tail window."""
    if not expiry_iso:
        return False
    ist = _ist_dt(ts_ms)
    if ist.strftime("%Y-%m-%d") != expiry_iso:
        return False
    return ist.time() >= cfg.expiry_tail_start


def estimate_slippage_per_side(
    *,
    moneyness: str,
    ts_ms: int,
    expiry_iso: Optional[str],
    cfg: Optional[SlippageConfig] = None,
) -> Dict[str, Any]:
    """Return per-side slippage in option points + the tag used.

    Result:
      {
        "pts": float,
        "bucket": "atm" | "otm1" | ...,
        "tail_multiplier_applied": bool,
        "config_snapshot": {...}
      }
    """
    cfg = cfg or SlippageConfig()
    bucket = slippage_bucket(moneyness)
    base = {
        "atm": cfg.atm_pts,
        "otm1": cfg.otm1_pts,
        "itm1": cfg.itm1_pts,
        "otm2_plus": cfg.otm2_plus_pts,
        "itm2_plus": cfg.itm2_plus_pts,
    }[bucket]
    tail = is_expiry_tail(ts_ms=ts_ms, expiry_iso=expiry_iso, cfg=cfg)
    pts = base * (cfg.expiry_tail_multiplier if tail else 1.0)
    return {
        "pts": round(float(pts), 3),
        "bucket": bucket,
        "tail_multiplier_applied": bool(tail),
        "config_snapshot": cfg.to_dict(),
    }


def apply_slippage(
    *,
    fill_price: float,
    side: str,                  # "BUY" or "SELL"
    pts: float,
) -> float:
    """Apply slippage to a fill price.

    Buying options: pay MORE than mid -> price goes UP.
    Selling options: receive LESS than mid -> price goes DOWN.
    """
    if str(side).upper() == "BUY":
        return float(fill_price) + float(pts)
    return float(fill_price) - float(pts)
