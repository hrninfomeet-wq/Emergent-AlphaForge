"""yfinance data source for Indian indices.
NIFTY=^NSEI, BANKNIFTY=^NSEBANK, SENSEX=^BSESN.
yfinance 1m limit ~30 days. For longer history users must use Upstox (Phase 4).
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import logging
import pandas as pd
import yfinance as yf

log = logging.getLogger(__name__)

YF_SYMBOLS = {
    "NIFTY": "^NSEI",
    "BANKNIFTY": "^NSEBANK",
    "SENSEX": "^BSESN",
}


def yf_symbol(instrument: str) -> str:
    s = YF_SYMBOLS.get(instrument.upper())
    if not s:
        raise ValueError(f"Unknown instrument {instrument}. Allowed: {list(YF_SYMBOLS.keys())}")
    return s


def fetch_1m(instrument: str, days: int = 7, end: datetime | None = None) -> pd.DataFrame:
    sym = yf_symbol(instrument)
    end = end or datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    log.info(f"yfinance fetch {instrument}({sym}) start={start.date()} end={end.date()}")
    df = yf.download(
        sym,
        start=start.strftime("%Y-%m-%d"),
        end=end.strftime("%Y-%m-%d"),
        interval="1m",
        progress=False,
        auto_adjust=False,
    )
    if df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    df = df.reset_index()
    df.columns = [str(c).lower() for c in df.columns]
    # Find datetime column
    dt_col = None
    for c in df.columns:
        if c in ("datetime", "date", "index", "") or pd.api.types.is_datetime64_any_dtype(df[c]):
            dt_col = c
            break
    if dt_col is None:
        raise RuntimeError(f"No datetime in yfinance df: {df.columns.tolist()}")
    if dt_col != "datetime":
        df = df.rename(columns={dt_col: "datetime"})
    for required in ("open", "high", "low", "close"):
        if required not in df.columns:
            raise RuntimeError(f"Missing {required}")
    if "volume" not in df.columns:
        df["volume"] = 0
    dt_series = pd.to_datetime(df["datetime"], utc=True).astype("datetime64[ns, UTC]")
    df["ts"] = (dt_series.astype("int64") // 10**6).astype("int64")
    df["instrument"] = instrument.upper()
    df["datetime"] = df["datetime"].astype(str)
    df = df[["instrument", "ts", "datetime", "open", "high", "low", "close", "volume"]].copy()
    return df
