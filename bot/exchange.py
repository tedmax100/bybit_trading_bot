"""pybit 薄包裝。demo 走 api-demo.bybit.com,testnet 走 api-testnet。"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
from pybit.unified_trading import HTTP

from .config import SETTINGS

INTERVAL_MS = {"5": 300_000, "60": 3_600_000, "240": 14_400_000, "D": 86_400_000}


def client(authenticated: bool = True) -> HTTP:
    kw = dict(testnet=SETTINGS.env == "testnet", demo=SETTINGS.env == "demo")
    if authenticated:
        if SETTINGS.rsa_key_path:
            kw.update(api_key=SETTINGS.api_key, rsa_authentication=True,
                      api_secret=Path(SETTINGS.rsa_key_path).expanduser().read_text())
        else:
            kw.update(api_key=SETTINGS.api_key, api_secret=SETTINGS.api_secret)
    return HTTP(**kw)


def total_equity(c: HTTP) -> float:
    r = c.get_wallet_balance(accountType="UNIFIED")
    return float(r["result"]["list"][0]["totalEquity"])


def fetch_klines(symbol: str, interval: str = "240", category: str = "spot",
                 start_ms: int | None = None, end_ms: int | None = None, c: HTTP | None = None) -> pd.DataFrame:
    """分頁抓歷史 K 線 (公開端點,不需 key)。單次最多 1000 根,新到舊。"""
    c = c or HTTP(testnet=False)  # 歷史行情一律用 mainnet 公開資料
    step = INTERVAL_MS[interval] * 1000
    end = end_ms or int(pd.Timestamp.now(tz="UTC").timestamp() * 1000)
    start = start_ms or end - step * 3
    rows: list = []
    while end > start:
        r = c.get_kline(category=category, symbol=symbol, interval=interval,
                        start=max(start, end - step), end=end, limit=1000)["result"]["list"]
        if not r:
            break
        rows += r
        end = int(r[-1][0]) - 1
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume", "turnover"])
    df = df.astype({"ts": "int64", **{k: "float64" for k in ["open", "high", "low", "close", "volume", "turnover"]}})
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df.drop_duplicates("ts").sort_values("ts").set_index("ts")


def funding_rate(symbol: str, c: HTTP | None = None) -> float:
    c = c or HTTP(testnet=False)
    r = c.get_tickers(category="linear", symbol=symbol)["result"]["list"][0]
    return float(r["fundingRate"])
